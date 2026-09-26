import hashlib
import hmac
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from growthops.api import app
from growthops.config import ConfigError, get_settings
from growthops.db import SCHEMA_VERSION, connect, initialize, schema_version
from growthops.freshness import check as freshness_check
from growthops.observability import METRICS
from growthops.ops import backup, restore, verify

KEY = "k" * 32
SAFE = {
    "GROWTHOPS_ENV": "production", "GROWTHOPS_DATA_MODE": "live", "GROWTHOPS_WEBHOOK_SECRET": "s" * 40,
    "GROWTHOPS_API_KEYS": f"{KEY},{'j' * 32}", "GROWTHOPS_OPS_TOKEN": "o" * 32, "GROWTHOPS_LOG_FORMAT": "text",
}


def _production(monkeypatch, db_path, **extra):
    for name, value in {**SAFE, "GROWTHOPS_DATABASE": str(db_path), **extra}.items():
        monkeypatch.setenv(name, value)


def test_production_refuses_unsafe_defaults(monkeypatch, db_path):
    monkeypatch.setenv("GROWTHOPS_ENV", "production")
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    problems = get_settings().problems()
    assert any("WEBHOOK_SECRET" in p for p in problems) and any("API_KEYS" in p for p in problems)
    assert any("DATA_MODE=synthetic" in p for p in problems)
    with pytest.raises(ConfigError):
        with TestClient(app):
            pass
    _production(monkeypatch, db_path, GROWTHOPS_CRM_ADAPTER="hubspot")
    assert get_settings().problems() == ["HUBSPOT_ACCESS_TOKEN is required when GROWTHOPS_CRM_ADAPTER=hubspot"]
    redacted = json.dumps(get_settings().redacted())
    assert "s" * 40 not in redacted and KEY not in redacted


def test_production_api_requires_keys_and_reports_readiness(monkeypatch, db_path):
    _production(monkeypatch, db_path)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/metrics/funnel").status_code == 401
        assert client.get("/metrics/funnel", headers={"X-API-Key": "wrong" * 8}).status_code == 401
        assert client.get("/openapi.json").status_code == 401
        ok = client.get("/metrics/funnel", headers={"X-API-Key": KEY, "X-Request-ID": "req-123"})
        assert ok.status_code == 200 and ok.headers["X-Request-ID"] == "req-123"
        assert ok.headers["X-Content-Type-Options"] == "nosniff"
        assert client.get("/metrics/funnel", headers={"Authorization": f"Bearer {'j' * 32}"}).status_code == 200
        assert client.get("/dashboard").status_code == 404
        ready = client.get("/ready").json()
        assert ready["status"] == "ready" and ready["schema_version"] == SCHEMA_VERSION
        assert {item["source"] for item in ready["sources"]} >= {"payments", "ad_spend", "email_sends"}
        text = client.get("/metrics", headers={"X-API-Key": KEY}).text
        assert 'growthops_http_requests_total{method="GET",route="/metrics/funnel",status="200"}' in text
        assert 'growthops_workflow_events{status="dead_letter"}' in text
        assert "growthops_paid_without_access_customers" in text and "growthops_source_stale" in text


def _sign(body: bytes, secret: str, timestamp: int | None) -> dict:
    signed = body if timestamp is None else f"{timestamp}.".encode() + body
    headers = {"X-GrowthOps-Signature": hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()}
    if timestamp is not None:
        headers["X-GrowthOps-Timestamp"] = str(timestamp)
    return headers


def test_production_webhook_requires_fresh_timestamped_signature(monkeypatch, db_path):
    _production(monkeypatch, db_path)
    body = json.dumps({"event_id": "evt-prod-1", "event_type": "payment.succeeded", "payment_id": "pay-prod-1",
                       "customer_id": "c-000002", "amount_cents": 32000,
                       "paid_at": "2026-09-26T00:00:00Z"}).encode()
    secret = SAFE["GROWTHOPS_WEBHOOK_SECRET"]
    before = dict(METRICS.counters)
    with TestClient(app) as client:
        post = lambda headers: client.post("/webhooks/payments", content=body, headers=headers)  # noqa: E731
        assert post(_sign(body, secret, None)).status_code == 401  # legacy body-only: dev only
        assert post(_sign(body, secret, int(time.time()) - 3600)).status_code == 401  # replayed
        assert post(_sign(body, "x" * 40, int(time.time()))).status_code == 401
        accepted = post(_sign(body, secret, int(time.time())))
        assert accepted.status_code == 202 and accepted.json()["status"] == "completed"
        assert "growthops_webhook_rejected_total" in client.get("/metrics", headers={"X-API-Key": KEY}).text
    assert METRICS.counters["webhook_rejected"] - before.get("webhook_rejected", 0) == 3
    assert METRICS.counters["webhook_accepted"] - before.get("webhook_accepted", 0) == 1


def test_migrations_upgrade_an_old_database_once(tmp_path):
    path = tmp_path / "old.db"
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE contacts (contact_id TEXT PRIMARY KEY, email TEXT NOT NULL, legacy_id TEXT, "
                   "owner_id TEXT, original_source TEXT, current_stage TEXT NOT NULL, created_at TEXT)")
    legacy.execute("INSERT INTO contacts VALUES ('c-1','a@b.test',NULL,NULL,NULL,'lead',NULL)")
    legacy.commit()
    legacy.close()
    connection = connect(path)
    assert schema_version(connection) is None
    initialize(connection)
    initialize(connection)  # idempotent
    assert schema_version(connection) == SCHEMA_VERSION
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == SCHEMA_VERSION
    assert connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 1  # data kept
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    connection.close()


def test_backup_verify_and_restore_round_trip(db_path, tmp_path):
    copy = backup(str(db_path), str(tmp_path / "backups"))
    assert verify(str(copy)) == []
    target = tmp_path / "restored.db"
    restore(str(copy), str(target))
    with pytest.raises(RuntimeError, match="--force"):
        restore(str(copy), str(target))
    restore(str(copy), str(target), force=True)
    assert (tmp_path / "restored.pre-restore.db").exists()
    handle = connect(target)
    assert handle.execute("SELECT COUNT(*) FROM payments").fetchone()[0] > 800
    handle.close()
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database")
    with pytest.raises(RuntimeError, match="unhealthy"):
        restore(str(broken), str(tmp_path / "x.db"))


def test_freshness_flags_sources_past_their_sla(connection, monkeypatch):
    fresh = {item["source"]: item for item in freshness_check(connection)}
    assert all(item["status"] == "fresh" for item in fresh.values()), fresh
    monkeypatch.setenv("GROWTHOPS_FRESHNESS_SLA_HOURS", "1")
    stale = {item["source"] for item in freshness_check(connection) if item["status"] == "stale"}
    assert "payments" in stale and "email_sends" not in stale  # email keeps its weekly SLA


def test_ask_endpoint_is_keyless_protected_and_audited(monkeypatch, db_path):
    _production(monkeypatch, db_path)
    with TestClient(app) as client:
        assert client.get("/ask", params={"q": "What does a lead cost on Google?"}).status_code == 401
        headers = {"X-API-Key": KEY}
        answered = client.get("/ask", params={"q": "What does a lead cost on Google?"}, headers=headers).json()
        assert answered["route"] == "metric" and answered["metric_id"] == "paid_efficiency"
        assert "Google: CPL $" in answered["answer"]
        refused = client.get("/ask", params={"q": "select * from payments"}, headers=headers).json()
        assert refused["route"] == "refused"
        assert client.get("/ask", params={"q": "x" * 301}, headers=headers).status_code == 422
        usage = client.get("/ops/ask-usage", headers=headers).json()
        assert {row["route"] for row in usage["by_route"]} == {"metric", "refused"}
