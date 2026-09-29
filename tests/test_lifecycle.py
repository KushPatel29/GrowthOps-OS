"""Subscription actions preserve payment truth and scope community access."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from growthops.adapters import Adapters, SignedWebhook
from growthops.api import app
from growthops.db import MIGRATIONS, SCHEMA, connect, initialize, schema_version
from growthops.lifecycle import LifecycleEvent, process_lifecycle
from growthops.workflow import (
    EventConflict,
    PaymentEvent,
    process_payment,
    run_due,
    trace,
)

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
CUSTOMER = "c-000001"


def _subscription(connection, suffix: str, amount: int = 10_000) -> tuple[str, str]:
    sub_id = f"sub-lifecycle-{suffix}"
    payment_id = f"pay-lifecycle-{suffix}"
    connection.execute(
        """INSERT INTO subscriptions VALUES (?, ?, 'starter', ?, ?, 'active')""",
        (sub_id, CUSTOMER, T0.isoformat(), (T0 + timedelta(days=30)).isoformat()),
    )
    event = PaymentEvent(event_id=f"evt-lifecycle-payment-{suffix}", event_type="payment.succeeded",
                         payment_id=payment_id, customer_id=CUSTOMER, amount_cents=amount,
                         paid_at=T0, subscription_id=sub_id, product_id="community")
    assert process_payment(connection, event, now=T0)["status"] == "completed"
    return sub_id, payment_id


def _event(event_id: str, event_type: str, **kwargs) -> LifecycleEvent:
    return LifecycleEvent(event_id=event_id, event_type=event_type, customer_id=CUSTOMER,
                          occurred_at=T0 + timedelta(hours=1), **kwargs)


def _status(connection, table: str, key: str, value: str) -> str:
    return connection.execute(f"SELECT status FROM {table} WHERE {key}=?", (value,)).fetchone()[0]


def test_cancel_and_refund_are_scoped_to_each_subscription(connection):
    first, first_payment = _subscription(connection, "first")
    second, second_payment = _subscription(connection, "second")

    changed = _event("evt-upgrade-first", "subscription.upgraded", subscription_id=first,
                     new_tier="premium")
    assert process_lifecycle(connection, changed, now=T0 + timedelta(hours=1))["status"] == "completed"
    assert connection.execute("SELECT plan_id FROM subscriptions WHERE subscription_id=?", (first,)
                              ).fetchone()[0] == "premium"
    assert connection.execute("SELECT tier FROM subscription_entitlements WHERE subscription_id=?", (first,)
                              ).fetchone()[0] == "premium"
    lowered = _event("evt-downgrade-first", "subscription.downgraded", subscription_id=first,
                     new_tier="starter")
    assert process_lifecycle(connection, lowered, now=T0 + timedelta(hours=1, minutes=30)
                             )["action"] == "change_tier"
    assert connection.execute("SELECT tier FROM subscription_entitlements WHERE subscription_id=?", (first,)
                              ).fetchone()[0] == "starter"

    canceled = _event("evt-cancel-first", "subscription.cancelled", subscription_id=first)
    assert process_lifecycle(connection, canceled, now=T0 + timedelta(hours=2))["status"] == "completed"
    assert _status(connection, "subscription_entitlements", "subscription_id", first) == "revoked"
    assert _status(connection, "subscription_entitlements", "subscription_id", second) == "active"
    assert _status(connection, "access_entitlements", "customer_id", CUSTOMER) == "active"
    assert process_lifecycle(connection, canceled, now=T0 + timedelta(hours=3))["duplicate"] is True
    repeated = _event("evt-cancel-first-again", "subscription.cancelled", subscription_id=first)
    assert process_lifecycle(connection, repeated, now=T0 + timedelta(hours=3))["action"] == "no_external_change"
    assert connection.execute("SELECT COUNT(*) FROM event_outbox WHERE event_id=?",
                              (repeated.event_id,)).fetchone()[0] == 0

    half = _event("evt-refund-half", "refund.created", payment_id=second_payment,
                  refund_id="refund-half", amount_cents=5_000)
    assert process_lifecycle(connection, half, now=T0 + timedelta(hours=4))["action"] == "record_refund"
    assert _status(connection, "subscription_entitlements", "subscription_id", second) == "active"
    rest = _event("evt-refund-rest", "refund.created", payment_id=second_payment,
                  refund_id="refund-rest", amount_cents=5_000)
    result = process_lifecycle(connection, rest, now=T0 + timedelta(hours=5))
    assert result["status"] == "completed" and result["action"] == "revoke_subscription"
    assert _status(connection, "subscription_entitlements", "subscription_id", second) == "revoked"
    assert _status(connection, "access_entitlements", "customer_id", CUSTOMER) == "revoked"
    assert connection.execute("SELECT COUNT(*) FROM refunds WHERE payment_id=?", (second_payment,)
                              ).fetchone()[0] == 2
    assert connection.execute("SELECT SUM(amount_cents) FROM refunds WHERE payment_id=?", (second_payment,)
                              ).fetchone()[0] == 10_000
    assert connection.execute("SELECT COUNT(*) FROM payments WHERE payment_id=?", (first_payment,)
                              ).fetchone()[0] == 1
    assert trace(connection, rest.event_id)["outbox"][0]["status"] == "delivered"


def test_access_outage_retries_without_reapplying_local_effect(connection):
    sub_id, _ = _subscription(connection, "retry")
    canceled = _event("evt-cancel-retry", "subscription.cancelled", subscription_id=sub_id)
    first = process_lifecycle(connection, canceled, now=T0 + timedelta(hours=1), fail_action=True)
    assert first["status"] == "failed"
    assert _status(connection, "subscriptions", "subscription_id", sub_id) == "canceled"
    assert connection.execute("SELECT COUNT(*) FROM lifecycle_action_log WHERE event_id=?",
                              (canceled.event_id,)).fetchone()[0] == 1
    assert run_due(connection, T0 + timedelta(hours=1, minutes=4)) == []
    recovered = run_due(connection, T0 + timedelta(hours=1, minutes=6))
    assert recovered[0]["status"] == "completed"
    assert connection.execute("SELECT COUNT(*) FROM lifecycle_action_log WHERE event_id=?",
                              (canceled.event_id,)).fetchone()[0] == 1
    assert trace(connection, canceled.event_id)["outbox"][0]["attempts"] == 2


def test_refund_then_cancel_closes_last_valid_access(connection):
    refunded_sub, payment_id = _subscription(connection, "refund-first")
    canceled_sub, _ = _subscription(connection, "cancel-last")
    full = _event("evt-refund-first-full", "refund.created", payment_id=payment_id,
                  refund_id="refund-first-full", amount_cents=10_000)
    process_lifecycle(connection, full, now=T0 + timedelta(hours=1))
    assert _status(connection, "subscription_entitlements", "subscription_id", refunded_sub) == "revoked"
    assert _status(connection, "access_entitlements", "customer_id", CUSTOMER) == "active"
    canceled = _event("evt-cancel-last", "subscription.cancelled", subscription_id=canceled_sub)
    process_lifecycle(connection, canceled, now=T0 + timedelta(hours=2))
    assert _status(connection, "access_entitlements", "customer_id", CUSTOMER) == "revoked"


def test_provider_retry_uses_same_scoped_idempotency_key(connection):
    sub_id, _ = _subscription(connection, "provider")
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append({"method": method, "headers": headers, "body": json.loads(body)})
        return (503, b"unavailable") if len(calls) == 1 else (200, b"ok")

    adapters = Adapters(access=SignedWebhook("access", "https://bridge.test/access", "secret",
                                            transport=transport, clock=lambda: 1_790_000_000))
    event = _event("evt-provider-cancel", "subscription.cancelled", subscription_id=sub_id)
    failed = process_lifecycle(connection, event, now=T0 + timedelta(hours=1), adapters=adapters)
    assert failed["status"] == "failed"
    recovered = run_due(connection, T0 + timedelta(hours=1, minutes=6), adapters=adapters)
    assert recovered[0]["status"] == "completed"
    assert len(calls) == 2
    assert calls[0]["headers"]["Idempotency-Key"] == calls[1]["headers"]["Idempotency-Key"]
    assert calls[0]["body"]["subscription_id"] == sub_id
    assert calls[1]["body"]["action"] == "revoke_subscription"
    assert connection.execute("SELECT COUNT(*) FROM lifecycle_action_log WHERE event_id=?",
                              (event.event_id,)).fetchone()[0] == 1


def test_stale_claim_recovers_after_local_commit(connection):
    sub_id, _ = _subscription(connection, "stale-claim")
    event = _event("evt-stale-cancel", "subscription.cancelled", subscription_id=sub_id)
    process_lifecycle(connection, event, now=T0 + timedelta(hours=1), fail_action=True)
    connection.execute(
        """UPDATE processed_events SET status='processing', claimed_at=?, next_attempt_at=NULL
           WHERE event_id=?""",
        ((T0 + timedelta(hours=1)).isoformat(), event.event_id),
    )
    assert run_due(connection, T0 + timedelta(hours=1, minutes=4)) == []
    result = run_due(connection, T0 + timedelta(hours=1, minutes=6))
    assert result[0]["status"] == "completed"
    assert connection.execute("SELECT COUNT(*) FROM lifecycle_action_log WHERE event_id=?",
                              (event.event_id,)).fetchone()[0] == 1


def test_refund_bounds_and_reused_event_ids_fail_closed(connection):
    sub_id, payment_id = _subscription(connection, "bounds")
    too_large = _event("evt-refund-oversize", "refund.created", payment_id=payment_id,
                       refund_id="refund-over", amount_cents=10_001)
    with pytest.raises(EventConflict, match="exceed"):
        process_lifecycle(connection, too_large, now=T0 + timedelta(hours=1))
    assert connection.execute("SELECT COUNT(*) FROM processed_events WHERE event_id=?",
                              (too_large.event_id,)).fetchone()[0] == 0
    cancel = _event("evt-cancel-bounds", "subscription.cancelled", subscription_id=sub_id)
    process_lifecycle(connection, cancel, now=T0 + timedelta(hours=2))
    with pytest.raises(EventConflict, match="different payload"):
        process_lifecycle(connection, cancel.model_copy(update={"subscription_id": "other"}),
                          now=T0 + timedelta(hours=3))


def test_lifecycle_event_fields_are_type_checked():
    with pytest.raises(ValueError, match="refund requires"):
        _event("evt-bad-refund", "refund.created", payment_id="missing")
    with pytest.raises(ValueError, match="tier change requires"):
        _event("evt-bad-tier", "subscription.upgraded", subscription_id="sub-1")


def test_signed_lifecycle_webhook_and_customer_trace(db_path, monkeypatch):
    connection = connect(db_path)
    try:
        initialize(connection)
        sub_id, _ = _subscription(connection, "webhook")
    finally:
        connection.close()
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    monkeypatch.setenv("GROWTHOPS_WEBHOOK_SECRET", "lifecycle-test-secret")
    monkeypatch.setenv("GROWTHOPS_API_KEYS", "lifecycle-api-key")
    event = _event("evt-cancel-webhook", "subscription.cancelled", subscription_id=sub_id)
    body = event.model_dump_json().encode()
    headers = {"X-GrowthOps-Signature": hmac.new(b"lifecycle-test-secret", body,
                                                 hashlib.sha256).hexdigest()}
    with TestClient(app) as client:
        assert client.post("/v2/webhooks/lifecycle", content=body).status_code == 401
        accepted = client.post("/v2/webhooks/lifecycle", content=body, headers=headers)
        assert accepted.status_code == 202 and accepted.json()["status"] == "completed"
        assert client.post("/v2/webhooks/lifecycle", content=body, headers=headers).json()["duplicate"]
        assert client.get(f"/v2/ops/customers/{CUSTOMER}").status_code == 401
        access_headers = {"X-API-Key": "lifecycle-api-key"}
        customer = client.get(f"/v2/ops/customers/{CUSTOMER}", headers=access_headers).json()
        scoped = customer["operations"]["subscription_entitlements"]
        assert next(row for row in scoped if row["subscription_id"] == sub_id)["status"] == "revoked"
        trace_result = client.get(f"/v2/ops/events/{event.event_id}", headers=access_headers).json()
        assert trace_result["envelope"]["entity_id"] == sub_id
        assert trace_result["outbox"][0]["status"] == "delivered"


def test_v5_event_migration_preserves_related_rows(tmp_path):
    database = tmp_path / "v5.sqlite"
    connection = connect(database)
    try:
        old_schema = SCHEMA.replace("  payment_id TEXT,\n  customer_id TEXT NOT NULL,",
                                    "  payment_id TEXT NOT NULL,\n  customer_id TEXT NOT NULL,")
        connection.executescript(old_schema)
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_migrations VALUES (1, 'old')")
        for version, sql in MIGRATIONS:
            if version == 6:
                break
            connection.executescript(sql)
            connection.execute("INSERT INTO schema_migrations VALUES (?, 'old')", (version,))
        connection.execute(
            """INSERT INTO processed_events
               (event_id, event_type, payment_id, customer_id, payload_sha256, status, received_at)
               VALUES ('legacy-event', 'payment.succeeded', 'legacy-payment', 'legacy-customer',
                       'digest', 'completed', '2026-09-29T00:00:00+00:00')"""
        )
        connection.execute(
            "INSERT INTO workflow_steps VALUES ('legacy-event', 'record_payment', '2026-09-29')"
        )
        connection.execute(
            """INSERT INTO contacts (contact_id, email, current_stage)
               VALUES ('legacy-customer', 'legacy@synthetic.test', 'customer')"""
        )
        connection.execute(
            """INSERT INTO subscriptions VALUES
               ('legacy-sub', 'legacy-customer', 'starter', '2026-09-01',
                '2026-10-01', 'active')"""
        )
        connection.execute(
            """INSERT INTO payments (payment_id, customer_id, amount_cents, status,
               paid_at, payment_type, subscription_id, product_id)
               VALUES ('legacy-payment', 'legacy-customer', 10000, 'succeeded',
                       '2026-09-29', 'new', 'legacy-sub', 'community')"""
        )
        connection.execute(
            "INSERT INTO access_entitlements VALUES ('legacy-customer', 'active', '2026-09-29', 'legacy-event')"
        )
        connection.execute(
            "INSERT INTO workflow_steps VALUES ('legacy-event', 'grant_access', '2026-09-29')"
        )
        connection.execute(
            """INSERT INTO event_outbox (outbox_id, event_id, destination, action,
               idempotency_key, status) VALUES ('legacy-outbox', 'legacy-event', 'hubspot',
               'update_crm', 'legacy-key', 'delivered')"""
        )
        initialize(connection)
        assert schema_version(connection) == 7
        assert next(row for row in connection.execute("PRAGMA table_info(processed_events)")
                    if row["name"] == "payment_id")["notnull"] == 0
        assert connection.execute("SELECT payment_id FROM processed_events WHERE event_id='legacy-event'"
                                  ).fetchone()[0] == "legacy-payment"
        assert connection.execute("SELECT COUNT(*) FROM workflow_steps WHERE event_id='legacy-event'"
                                  ).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM event_outbox WHERE event_id='legacy-event'"
                                  ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM subscription_entitlements WHERE subscription_id='legacy-sub'"
        ).fetchone()[0] == "active"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        initialize(connection)
    finally:
        connection.close()
