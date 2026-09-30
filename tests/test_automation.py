import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest

from growthops.adapters import (
    Adapters,
    HubSpotCRM,
    ProviderError,
    SignedWebhook,
    build_adapters,
)
from growthops.alerts import deliver
from growthops.config import ConfigError, get_settings
from growthops.worker import run_once
from growthops.workflow import MAX_ATTEMPTS, PaymentEvent, process_payment, run_due

NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


class Recorder:
    """Fake HTTP transport: records requests and replays scripted responses."""

    def __init__(self, *responses):
        self.responses = list(responses) or [(200, b"{}")]
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(body) if body else None, "timeout": timeout})
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def _event(suffix: str) -> PaymentEvent:
    return PaymentEvent(event_id=f"evt-live-{suffix}", event_type="payment.succeeded", payment_id=f"pay-live-{suffix}",
                        customer_id="c-000002", amount_cents=480000, paid_at=NOW, product_id="accelerator")


def _found(stage: str) -> tuple[int, bytes]:
    return 200, json.dumps({"results": [{"id": "101", "properties": {"lifecyclestage": stage}}]}).encode()


def test_hubspot_adapter_moves_lifecycle_forward_and_classifies_errors():
    transport = Recorder(_found("opportunity"), (200, b'{"status":"COMPLETE","results":[{"id":"101"}]}'))
    HubSpotCRM("pat-token", 5, transport).mark_customer(_event("h1"))
    read, update = transport.calls
    assert read["url"] == "https://api.hubapi.com/crm/v3/objects/contacts/batch/read"
    assert read["body"] == {"idProperty": "growthops_contact_id", "properties": ["lifecyclestage"],
                            "inputs": [{"id": "c-000002"}]}
    assert update["url"] == "https://api.hubapi.com/crm/v3/objects/contacts/batch/update"
    assert update["headers"]["Authorization"] == "Bearer pat-token" and update["timeout"] == 5
    assert update["body"]["inputs"][0] == {"idProperty": "growthops_contact_id", "id": "c-000002",
                                           "properties": {"lifecyclestage": "customer"}}
    # Already a customer (or beyond): read only, nothing written, never moved backwards.
    for stage in ("customer", "evangelist"):
        already = Recorder(_found(stage))
        HubSpotCRM("t", 5, already).mark_customer(_event("h4"))
        assert [call["url"].rsplit("/", 1)[1] for call in already.calls] == ["read"]
    # Unknown to the CRM: a permanent failure, not a blank contact.
    missing = Recorder((207, b'{"results":[],"errors":[{"category":"OBJECT_NOT_FOUND"}]}'))
    with pytest.raises(ProviderError, match="not in the CRM \\(permanent\\)"):
        HubSpotCRM("t", 5, missing).mark_customer(_event("h5"))
    assert len(missing.calls) == 1
    # A 207 on the write is a failure even though it is a 2xx.
    partial = Recorder(_found("lead"), (207, b'{"results":[],"errors":[{"category":"VALIDATION_ERROR"}]}'))
    with pytest.raises(ProviderError, match="VALIDATION_ERROR"):
        HubSpotCRM("t", 5, partial).mark_customer(_event("h6"))
    with pytest.raises(ProviderError, match="HTTP 429 \\(transient\\)"):
        HubSpotCRM("t", 5, Recorder((429, b"rate limited"))).mark_customer(_event("h2"))
    with pytest.raises(ProviderError, match="HTTP 400 \\(permanent\\)"):
        HubSpotCRM("t", 5, Recorder((400, b"bad property"))).mark_customer(_event("h3"))


def test_signed_webhook_adapter_is_verifiable_and_idempotent():
    transport = Recorder()
    SignedWebhook("access", "https://bridge.test/access", "secret-1", 5, transport, clock=lambda: 1_790_000_000) \
        .grant(_event("w1"))
    call = transport.calls[0]
    body = json.dumps(call["body"], sort_keys=True).encode()
    expected = hmac.new(b"secret-1", b"1790000000." + body, hashlib.sha256).hexdigest()
    assert call["headers"]["X-GrowthOps-Signature"] == expected
    assert call["headers"]["Idempotency-Key"] == "evt-live-w1:grant_access"
    assert call["body"]["action"] == "grant_access"


def test_build_adapters_follows_settings(monkeypatch):
    assert build_adapters(get_settings()).simulated
    monkeypatch.setenv("GROWTHOPS_CRM_ADAPTER", "hubspot")
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "pat")
    monkeypatch.setenv("GROWTHOPS_ACCESS_ADAPTER", "webhook")
    monkeypatch.setenv("GROWTHOPS_ACCESS_WEBHOOK_URL", "https://bridge.test/access")
    with pytest.raises(ConfigError, match="ACCESS_WEBHOOK_SECRET"):
        build_adapters(get_settings())
    monkeypatch.setenv("GROWTHOPS_ACCESS_WEBHOOK_SECRET", "access-secret")
    adapters = build_adapters(get_settings())
    assert isinstance(adapters.crm, HubSpotCRM) and isinstance(adapters.access, SignedWebhook)
    assert adapters.access.secret == "access-secret"
    assert not adapters.simulated


class FlakyAccess:
    def __init__(self, failures: int):
        self.failures, self.calls = failures, 0

    def grant(self, event):
        self.calls += 1
        if self.calls <= self.failures:
            raise ProviderError("access: HTTP 503 (transient) maintenance")


class RecordingCRM:
    def __init__(self):
        self.customers = []

    def mark_customer(self, event):
        self.customers.append(event.customer_id)


def test_workflow_calls_live_adapters_and_retries_provider_failures(connection):
    crm, access = RecordingCRM(), FlakyAccess(failures=1)
    adapters = Adapters(crm=crm, access=access)
    first = process_payment(connection, _event("r1"), now=NOW, adapters=adapters)
    assert first["status"] == "failed" and "HTTP 503" in first["last_error"]
    assert crm.customers == ["c-000002"]  # the CRM step committed before access failed
    retried = run_due(connection, datetime.fromisoformat(first["next_attempt_at"]), adapters=adapters)
    assert [item["status"] for item in retried] == ["completed"] and access.calls == 2
    assert crm.customers == ["c-000002"]  # a completed step is never repeated
    errors = connection.execute("SELECT error FROM workflow_step_attempts WHERE event_id='evt-live-r1' "
                                "AND status='failed'").fetchall()
    assert [row[0] for row in errors] == ["access: HTTP 503 (transient) maintenance"]


def test_persistent_provider_failure_dead_letters(connection):
    adapters = Adapters(access=FlakyAccess(failures=99))
    result = process_payment(connection, _event("d1"), now=NOW, adapters=adapters)
    for _ in range(MAX_ATTEMPTS - 1):
        result = run_due(connection, datetime.fromisoformat(result["next_attempt_at"]), adapters=adapters)[0]
    assert result["status"] == "dead_letter" and result["attempts"] == MAX_ATTEMPTS


def test_alerts_are_deduplicated_and_resent_when_due(connection, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_ALERT_WEBHOOK_URL", "https://hooks.test/alerts")
    settings = get_settings()
    alerts = [{"key": "ops_dead_letter", "title": "9 paying customers have no access.", "detail": "12 events",
               "action": "Replay them.", "priority": 100}]
    transport = Recorder()
    assert [item["status"] for item in deliver(connection, settings, alerts, now=NOW, transport=transport)] == ["sent"]
    assert deliver(connection, settings, alerts, now=NOW + timedelta(hours=1), transport=transport) == []
    changed = [{**alerts[0], "title": "10 paying customers have no access."}]
    assert len(deliver(connection, settings, changed, now=NOW + timedelta(hours=1), transport=transport)) == 1
    assert len(deliver(connection, settings, alerts, now=NOW + timedelta(hours=25), transport=transport)) == 1
    assert transport.calls[0]["body"]["text"].startswith("*GrowthOps alert:* 9 paying customers")
    failing = Recorder((500, b"down"))
    other = [{**alerts[0], "key": "renewal_risk"}]
    assert deliver(connection, settings, other, now=NOW, transport=failing)[0]["status"] == "failed"
    assert deliver(connection, settings, other, now=NOW, transport=Recorder())[0]["status"] == "sent"  # retried
    monkeypatch.delenv("GROWTHOPS_ALERT_WEBHOOK_URL")
    dry = deliver(connection, get_settings(), [{**alerts[0], "key": "x"}], now=NOW, transport=transport)
    assert dry[0]["status"] == "dry_run"


def test_worker_pass_runs_each_scheduled_job_once(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    monkeypatch.setenv("GROWTHOPS_ALERT_WEBHOOK_URL", "https://hooks.test/alerts")
    monkeypatch.setenv("GROWTHOPS_ALERT_MIN_PRIORITY", "95")
    transport = Recorder()
    first = run_once(get_settings(), now=NOW, transport=transport)
    assert first["alerts"] == "succeeded" and first["daily_update"] == "succeeded"
    texts = [call["body"]["text"] for call in transport.calls]
    assert any(text.startswith("*GrowthOps alert:*") and "community access" in text for text in texts)
    assert any(text.startswith("Daily performance update") for text in texts)
    again = run_once(get_settings(), now=NOW + timedelta(minutes=5), transport=transport)
    assert again["alerts"] is None and again["daily_update"] is None
    early = run_once(get_settings(), now=NOW.replace(day=27, hour=6), transport=transport)
    assert "daily_update" not in early  # before the scheduled time
