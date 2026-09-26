from datetime import datetime, timezone

from growthops.db import connect
from growthops.seed import seed
from growthops.workflow import PaymentEvent, process_payment


def _event() -> PaymentEvent:
    return PaymentEvent(
        event_id="evt-new-1",
        event_type="payment.succeeded",
        payment_id="pay-new-1",
        customer_id="c-00001",
        amount_cents=50000,
        paid_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )


def test_partial_failure_resumes_without_duplicate_side_effects(tmp_path):
    database = tmp_path / "workflow.db"
    seed(str(database), people=8)
    connection = connect(database)
    failed = process_payment(connection, _event(), fail_step="grant_access")
    assert failed["status"] == "failed"
    assert connection.execute("SELECT COUNT(*) FROM payments WHERE payment_id='pay-new-1'").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM access_entitlements WHERE customer_id='c-00001'").fetchone()[0] == 0
    resumed = process_payment(connection, _event())
    duplicate = process_payment(connection, _event())
    assert resumed["status"] == "completed"
    assert duplicate["duplicate"] is True
    assert connection.execute("SELECT COUNT(*) FROM payments WHERE payment_id='pay-new-1'").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM access_entitlements WHERE customer_id='c-00001'").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM lifecycle_events WHERE lifecycle_event_id='evt-new-1:customer'").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM lifecycle_events WHERE lifecycle_event_id='evt-new-1:paid'").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM lifecycle_events WHERE lifecycle_event_id='evt-new-1:activated'").fetchone()[0] == 1
    connection.close()


def test_missing_crm_contact_is_retryable(tmp_path):
    database = tmp_path / "workflow.db"
    seed(str(database), people=2)
    connection = connect(database)
    event = _event().model_copy(update={"customer_id": "c-late", "event_id": "evt-late", "payment_id": "pay-late"})
    failed = process_payment(connection, event)
    assert failed["status"] == "failed"
    assert "CRM contact not found" in failed["last_error"]
    connection.execute("INSERT INTO contacts VALUES ('c-late','late@synthetic.scalelab.test',NULL,NULL,NULL,'lead')")
    resumed = process_payment(connection, event)
    assert resumed["status"] == "completed"
    connection.close()


def test_active_claim_blocks_redelivery_and_expired_claim_resumes(tmp_path):
    database = tmp_path / "workflow.db"
    seed(str(database), people=2)
    connection = connect(database)
    event = _event()
    from growthops.workflow import _payload_digest, utcnow

    connection.execute(
        """INSERT INTO processed_events
           (event_id,event_type,payment_id,customer_id,payload_sha256,status,received_at,claimed_at)
           VALUES (?,?,?,?,?,'processing',?,?)""",
        (event.event_id, event.event_type, event.payment_id, event.customer_id,
         _payload_digest(event), utcnow(), utcnow()),
    )
    in_progress = process_payment(connection, event)
    assert in_progress == {"event_id": event.event_id, "status": "processing", "duplicate": True}
    assert connection.execute("SELECT COUNT(*) FROM payments WHERE payment_id=?", (event.payment_id,)).fetchone()[0] == 0
    connection.execute("UPDATE processed_events SET claimed_at='2000-01-01T00:00:00+00:00' WHERE event_id=?", (event.event_id,))
    assert process_payment(connection, event)["status"] == "completed"
    connection.close()
