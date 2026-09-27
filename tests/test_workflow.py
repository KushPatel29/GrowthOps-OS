from datetime import datetime, timedelta, timezone

import pytest

from growthops.workflow import (
    MAX_ATTEMPTS,
    EventConflict,
    PaymentEvent,
    _payload_digest,
    health,
    process_payment,
    replay_dead_letter,
    run_due,
    trace,
    utcnow,
)

T0 = datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)


def _event(**overrides) -> PaymentEvent:
    values = {"event_id": "evt-new-1", "event_type": "payment.succeeded", "payment_id": "pay-new-1",
              "customer_id": "c-000001", "amount_cents": 50000, "paid_at": T0}
    return PaymentEvent(**{**values, **overrides})


def _count(connection, sql, *args):
    return connection.execute(sql, args).fetchone()[0]


def test_partial_failure_resumes_without_duplicate_side_effects(connection):
    connection.execute("DELETE FROM access_entitlements WHERE customer_id='c-000001'")
    failed = process_payment(connection, _event(), fail_step="grant_access", now=T0)
    assert failed["status"] == "failed" and failed["next_attempt_at"]
    assert _count(connection, "SELECT COUNT(*) FROM payments WHERE payment_id='pay-new-1'") == 1
    assert _count(connection, "SELECT COUNT(*) FROM access_entitlements WHERE customer_id='c-000001'") == 0
    resumed = process_payment(connection, _event(), now=T0 + timedelta(minutes=6))
    duplicate = process_payment(connection, _event(), now=T0 + timedelta(minutes=7))
    assert resumed["status"] == "completed" and duplicate["duplicate"] is True
    assert _count(connection, "SELECT COUNT(*) FROM payments WHERE payment_id='pay-new-1'") == 1
    assert _count(connection, "SELECT COUNT(*) FROM access_entitlements WHERE customer_id='c-000001'") == 1
    for stage in ("customer", "paid", "activated"):
        assert _count(connection, "SELECT COUNT(*) FROM lifecycle_events WHERE lifecycle_event_id=?",
                      f"evt-new-1:{stage}") == 1
    log = trace(connection, "evt-new-1")
    assert [(a["step_name"], a["status"]) for a in log["attempts_log"]].count(("grant_access", "failed")) == 1
    assert log["deliveries"] == 3


def test_missing_crm_contact_is_retryable(connection):
    event = _event(customer_id="c-late", event_id="evt-late", payment_id="pay-late")
    connection.execute("PRAGMA foreign_keys = OFF")  # payments.customer_id has no FK; contact arrives later
    failed = process_payment(connection, event, now=T0)
    assert failed["status"] == "failed" and "CRM contact not found" in failed["last_error"]
    connection.execute("INSERT INTO contacts VALUES ('c-late','late@synthetic.scalelab.test',NULL,NULL,NULL,'lead',NULL)")
    assert process_payment(connection, event, now=T0 + timedelta(minutes=10))["status"] == "completed"


def test_active_claim_blocks_redelivery_and_expired_claim_resumes(connection):
    event = _event()
    connection.execute(
        """INSERT INTO processed_events (event_id,event_type,payment_id,customer_id,payload_sha256,status,
           received_at,claimed_at,payload_json) VALUES (?,?,?,?,?,'processing',?,?,?)""",
        (event.event_id, event.event_type, event.payment_id, event.customer_id, _payload_digest(event),
         utcnow(), T0.isoformat(), event.model_dump_json()))
    assert process_payment(connection, event, now=T0 + timedelta(minutes=1))["status"] == "processing"
    assert _count(connection, "SELECT COUNT(*) FROM payments WHERE payment_id=?", event.payment_id) == 0
    assert process_payment(connection, event, now=T0 + timedelta(minutes=6))["status"] == "completed"


def test_backoff_worker_then_dead_letter_then_operator_replay(connection):
    outage = lambda step, event, attempt, now: "HTTP 503" if step == "grant_access" else None
    result = process_payment(connection, _event(), now=T0, faults=outage)
    now = T0
    delays = []
    while result["status"] == "failed":
        due = datetime.fromisoformat(result["next_attempt_at"])
        delays.append(round((due - now).total_seconds() / 60))
        now = due
        assert run_due(connection, due - timedelta(seconds=1), faults=outage) == []  # not yet due
        result = run_due(connection, due, faults=outage)[0]
    assert result["status"] == "dead_letter" and result["attempts"] == MAX_ATTEMPTS
    assert delays == [5, 10, 20, 40]  # exponential backoff (plus a few seconds of step latency)
    assert process_payment(connection, _event(), now=now)["duplicate"] is True
    replayed = replay_dead_letter(connection, "evt-new-1", now=now + timedelta(hours=1))
    assert replayed["status"] == "completed"
    assert _count(connection, "SELECT COUNT(*) FROM payments WHERE payment_id='pay-new-1'") == 1
    with pytest.raises(ValueError):
        replay_dead_letter(connection, "evt-new-1")


def test_reused_payment_id_is_a_conflict(connection):
    process_payment(connection, _event(), now=T0)
    with pytest.raises(EventConflict):
        process_payment(connection, _event(event_id="evt-other"), now=T0)


def test_scenario_replay_shows_the_outage_in_operations_health(connection):
    ops = health(connection)
    assert ops["dead_letter"] > 0 and ops["completed"] > ops["dead_letter"]
    assert ops["duplicate_deliveries_absorbed"] > 0
    assert 0 < ops["success_rate"] < 1
    assert "community_access: HTTP 503 Service Unavailable" in ops["error_types"]
    duplicates = _count(connection, "SELECT COUNT(*) FROM (SELECT payment_id FROM payments GROUP BY payment_id HAVING COUNT(*)>1)")
    assert duplicates == 0
    stuck = connection.execute(
        """SELECT COUNT(*) FROM processed_events e JOIN payments p ON p.payment_id=e.payment_id
           LEFT JOIN access_entitlements a ON a.customer_id=p.customer_id
           WHERE e.status='dead_letter' AND a.customer_id IS NULL""").fetchone()[0]
    assert 0 < stuck <= ops["dead_letter"]  # most dead letters are paying customers without access
    from growthops.brief import findings
    top = findings(connection)[0]
    assert top["id"] == "ops_dead_letter" and top["customers_without_access"] == stuck
    assert top["finding"].startswith(f"{stuck} paying customers have no community access")
