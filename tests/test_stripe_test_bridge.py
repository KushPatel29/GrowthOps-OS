"""Stripe-shaped snapshots reach the ledger only after signed, test-only validation."""

import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from growthops.api import app
from growthops.db import connect
from growthops.stripe_test_bridge import (
    StripeEventError,
    normalize_stripe_test_event,
    verify_stripe_signature,
)

SECRET = "whsec_" + "s" * 40


def stripe_event(event_id, event_type, obj, *, livemode=False, data=None):
    return json.dumps({"id": event_id, "object": "event", "type": event_type,
                       "created": 1_780_146_000, "livemode": livemode,
                       "data": data or {"object": obj}}, separators=(",", ":")).encode()


def signature(body, timestamp):
    digest = hmac.new(SECRET.encode(), str(timestamp).encode() + b"." + body,
                      hashlib.sha256).hexdigest()
    return f"t={timestamp},v0=fake,v1={digest}"


def test_stripe_signature_requires_raw_body_v1_and_recent_timestamp():
    body = b'{"id":"evt_test"}'
    now = datetime.fromtimestamp(1_780_146_000, UTC)
    header = signature(body, 1_780_146_000)
    assert verify_stripe_signature(body, header, SECRET, now=now)
    assert not verify_stripe_signature(body + b" ", header, SECRET, now=now)
    assert not verify_stripe_signature(body, header.replace("v1=", "v0="), SECRET, now=now)
    assert not verify_stripe_signature(body, signature(body, 1_780_145_699), SECRET, now=now)
    assert not verify_stripe_signature(body, header, "short", now=now)


def test_stripe_normalizer_requires_test_mode_mapping_and_settled_refund():
    payment = {"id": "pi_test_1", "object": "payment_intent", "status": "succeeded",
               "currency": "usd", "amount_received": 1200,
               "metadata": {"growthops_customer_id": "c-000001"}}
    event = normalize_stripe_test_event(stripe_event("evt_test_1", "payment_intent.succeeded", payment))
    assert event.payment_id == "pi_test_1" and event.amount_cents == 1200
    with pytest.raises(StripeEventError, match="test-mode"):
        normalize_stripe_test_event(stripe_event("evt_live", "payment_intent.succeeded",
                                                 payment, livemode=True))
    with pytest.raises(StripeEventError, match="metadata.growthops_customer_id"):
        normalize_stripe_test_event(stripe_event("evt_unmapped", "payment_intent.succeeded",
                                                 {**payment, "metadata": {}}))
    refund = {"id": "re_test_1", "object": "refund", "status": "pending",
              "payment_intent": "pi_test_1", "currency": "usd", "amount": 100,
              "metadata": {"growthops_customer_id": "c-000001"}}
    with pytest.raises(StripeEventError, match="not succeeded"):
        normalize_stripe_test_event(stripe_event("evt_pending", "refund.created", refund))
    assert normalize_stripe_test_event(stripe_event("evt_other", "customer.created", {})) is None


def test_stripe_subscription_changes_need_explicit_identity_and_tier_evidence():
    subscription = {"id": "sub_stripe_1", "object": "subscription", "status": "active",
                    "metadata": {"growthops_customer_id": "c-000001",
                                 "growthops_subscription_id": "sub-000001",
                                 "growthops_tier_change": "upgrade", "growthops_new_tier": "pro"}}
    updated = normalize_stripe_test_event(stripe_event(
        "evt_tier", "customer.subscription.updated", subscription,
        data={"object": subscription, "previous_attributes": {"items": {"data": []}}},
    ))
    assert updated.event_type == "subscription.upgraded" and updated.new_tier == "pro"
    with pytest.raises(StripeEventError, match="changed items"):
        normalize_stripe_test_event(stripe_event("evt_status", "customer.subscription.updated", subscription))
    deleted = normalize_stripe_test_event(stripe_event(
        "evt_deleted", "customer.subscription.deleted", {**subscription, "status": "canceled"},
    ))
    assert deleted.event_type == "subscription.cancelled"


def test_signed_stripe_test_route_processes_payment_once(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    monkeypatch.setenv("GROWTHOPS_STRIPE_TEST_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("GROWTHOPS_API_KEYS", "local-test-api-key")
    payment = {"id": "pi_test_bridge", "object": "payment_intent", "status": "succeeded",
               "currency": "usd", "amount_received": 1200,
               "metadata": {"growthops_customer_id": "c-000001"}}
    body = stripe_event("evt_test_bridge", "payment_intent.succeeded", payment)
    now = int(datetime.now(UTC).timestamp())
    headers = {"Stripe-Signature": signature(body, now)}
    with TestClient(app) as client:
        assert client.post("/v2/webhooks/stripe-test", content=body).status_code == 401
        accepted = client.post("/v2/webhooks/stripe-test", content=body, headers=headers)
        assert accepted.status_code == 202 and accepted.json()["status"] == "completed"
        repeated = client.post("/v2/webhooks/stripe-test", content=body, headers=headers)
        assert repeated.status_code == 202 and repeated.json()["duplicate"]
        refund = {"id": "re_test_bridge", "object": "refund", "status": "succeeded",
                  "payment_intent": "pi_test_bridge", "currency": "usd", "amount": 100,
                  "metadata": {"growthops_customer_id": "c-000001"}}
        refund_body = stripe_event("evt_test_refund", "refund.created", refund)
        refund_headers = {"Stripe-Signature": signature(refund_body, now)}
        settled = client.post("/v2/webhooks/stripe-test", content=refund_body, headers=refund_headers)
        assert settled.status_code == 202 and settled.json()["status"] == "completed"
        assert client.post("/v2/webhooks/stripe-test", content=refund_body,
                           headers=refund_headers).json()["duplicate"]
    connection = connect(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM payments WHERE payment_id='pi_test_bridge'").fetchone()[0] == 1
        assert connection.execute("SELECT amount_cents FROM refunds WHERE refund_id='re_test_bridge'").fetchone()[0] == 100
    finally:
        connection.close()


def test_stripe_test_route_is_disabled_without_separate_secret(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    monkeypatch.delenv("GROWTHOPS_STRIPE_TEST_WEBHOOK_SECRET", raising=False)
    with TestClient(app) as client:
        assert client.post("/v2/webhooks/stripe-test", content=b"{}").status_code == 404


def test_stripe_test_route_is_disabled_in_production_even_with_secret(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("growthops.api.get_settings", lambda: SimpleNamespace(
        production=True, problems=list, api_keys=[], stripe_test_webhook_secret=SECRET,
    ))
    client = TestClient(app)
    try:
        assert client.post("/v2/webhooks/stripe-test", content=b"{}").status_code == 404
    finally:
        client.close()
