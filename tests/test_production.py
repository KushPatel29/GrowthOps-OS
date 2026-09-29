import hashlib
import hmac
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from growthops.adapters import Adapters
from growthops.api import app
from growthops.config import ConfigError, get_settings
from growthops.db import (
    LEGACY_COLUMNS,
    SCHEMA_VERSION,
    connect,
    connect_readonly,
    initialize,
    schema_version,
)
from growthops.freshness import check as freshness_check
from growthops.observability import METRICS
from growthops.ops import backup, restore, verify

KEY = "k" * 32
SAFE = {
    "GROWTHOPS_ENV": "production", "GROWTHOPS_DATA_MODE": "live", "GROWTHOPS_WEBHOOK_SECRET": "s" * 40,
    "GROWTHOPS_API_KEYS": f"{KEY},{'j' * 32}", "GROWTHOPS_OPS_TOKEN": "o" * 32, "GROWTHOPS_LOG_FORMAT": "text",
    "GROWTHOPS_CRM_ADAPTER": "hubspot", "HUBSPOT_ACCESS_TOKEN": "test-provider-token",
    "GROWTHOPS_ACCESS_ADAPTER": "webhook", "GROWTHOPS_ACCESS_WEBHOOK_URL": "https://access.example.test/action",
    "GROWTHOPS_ACCESS_WEBHOOK_SECRET": "a" * 40,
    "GROWTHOPS_MESSAGING_ADAPTER": "webhook", "GROWTHOPS_MESSAGING_WEBHOOK_URL": "https://mail.example.test/action",
    "GROWTHOPS_MESSAGING_WEBHOOK_SECRET": "m" * 40,
}


def _production(monkeypatch, db_path, *, verified_fixture=True, **extra):
    for name, value in {**SAFE, "GROWTHOPS_DATABASE": str(db_path), **extra}.items():
        monkeypatch.setenv(name, value)
    if verified_fixture:
        # Stand-in for a provider-backed ingestion. It exercises API security,
        # not the truth of the synthetic scenario as real source evidence.
        connection = connect(db_path)
        connection.execute(
            "UPDATE dataset_origin SET origin='live_verified', evidence_ref='test:provider_ingestion'",
        )
        connection.close()


def test_production_rejects_synthetic_or_unmarked_data(monkeypatch, db_path):
    _production(monkeypatch, db_path, verified_fixture=False)
    with pytest.raises(ConfigError, match="synthetic"), TestClient(app):
        pass
    connection = connect(db_path)
    connection.execute("DELETE FROM dataset_origin")
    connection.close()
    with pytest.raises(ConfigError, match="unverified"), TestClient(app):
        pass


def test_production_refuses_unsafe_defaults(monkeypatch, db_path):
    monkeypatch.setenv("GROWTHOPS_ENV", "production")
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    problems = get_settings().problems()
    assert any("WEBHOOK_SECRET" in p for p in problems) and any("API_KEYS" in p for p in problems)
    assert any("DATA_MODE=synthetic" in p for p in problems)
    with pytest.raises(ConfigError), TestClient(app):
        pass
    _production(monkeypatch, db_path, HUBSPOT_ACCESS_TOKEN="")
    assert get_settings().problems() == ["HUBSPOT_ACCESS_TOKEN is required when GROWTHOPS_CRM_ADAPTER=hubspot"]
    _production(monkeypatch, db_path, GROWTHOPS_CRM_ADAPTER="simulated",
                GROWTHOPS_ACCESS_ADAPTER="simulated", GROWTHOPS_MESSAGING_ADAPTER="simulated")
    assert len([issue for issue in get_settings().problems() if "required in production" in issue]) == 3
    with pytest.raises(ConfigError), TestClient(app):
        pass
    client_without_lifespan = TestClient(app)
    try:
        assert client_without_lifespan.get("/health").status_code == 503
    finally:
        client_without_lifespan.close()
    _production(monkeypatch, db_path)
    redacted = json.dumps(get_settings().redacted())
    assert all(secret not in redacted for secret in ("s" * 40, "a" * 40, "m" * 40, KEY))


def test_production_requires_separate_bridge_secrets(monkeypatch, db_path):
    _production(monkeypatch, db_path, GROWTHOPS_ACCESS_WEBHOOK_SECRET=SAFE["GROWTHOPS_WEBHOOK_SECRET"],
                GROWTHOPS_MESSAGING_WEBHOOK_SECRET=SAFE["GROWTHOPS_WEBHOOK_SECRET"])
    problems = get_settings().problems()
    assert any("ACCESS_WEBHOOK_SECRET" in issue for issue in problems)
    assert any("MESSAGING_WEBHOOK_SECRET" in issue for issue in problems)
    with pytest.raises(ConfigError), TestClient(app):
        pass
    _production(monkeypatch, db_path, GROWTHOPS_ACCESS_WEBHOOK_URL="https://user:password@access.example.test/key")
    assert any("ACCESS_WEBHOOK_URL" in issue for issue in get_settings().problems())
    assert "password" not in json.dumps(get_settings().redacted())


def test_production_api_requires_keys_and_reports_readiness(monkeypatch, db_path):
    _production(monkeypatch, db_path)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/metrics/funnel").status_code == 401
        assert client.get("/metrics/funnel", headers={"X-API-Key": "wrong" * 8}).status_code == 401
        assert client.get("/openapi.json").status_code == 401
        ok = client.get("/metrics/funnel", headers={"X-API-Key": KEY, "X-Request-ID": "req-123"})
        assert ok.status_code == 200 and ok.headers["X-Request-ID"] == "req-123"
        invalid_id = client.get("/health", headers={"X-Request-ID": "bad id"})
        assert invalid_id.headers["X-Request-ID"] != "bad id"
        assert ok.headers["X-Content-Type-Options"] == "nosniff"
        assert client.get("/metrics/funnel", headers={"Authorization": f"Bearer {'j' * 32}"}).status_code == 200
        assert client.get("/dashboard").status_code == 404
        person_url = "/v2/people/c-000002/journey"
        assert client.get(person_url, headers={"X-API-Key": KEY}).status_code == 403
        operator_headers = {"X-API-Key": KEY, "X-GrowthOps-Ops-Token": SAFE["GROWTHOPS_OPS_TOKEN"]}
        assert client.get(person_url, headers=operator_headers).status_code == 200
        response = client.get("/ready")
        ready = response.json()
        assert response.status_code == 503 and ready["status"] == "stale_sources"
        assert ready["schema_version"] == SCHEMA_VERSION and ready["origin_verified"] is True
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
    # Signature and replay policy are under test here; provider transport is
    # exercised separately in test_automation.py.
    monkeypatch.setattr("growthops.api.build_adapters", lambda settings: Adapters())
    body = json.dumps({"event_id": "evt-prod-1", "event_type": "payment.succeeded", "payment_id": "pay-prod-1",
                       "customer_id": "c-000002", "amount_cents": 32000,
                       "paid_at": "2026-09-26T00:00:00Z"}).encode()
    secret = SAFE["GROWTHOPS_WEBHOOK_SECRET"]
    before = dict(METRICS.counters)
    with TestClient(app) as client:
        post = lambda headers: client.post("/webhooks/payments", content=body, headers=headers)
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


def test_migrations_upgrade_original_full_schema_before_creating_new_indexes(tmp_path):
    """A pre-upgrade database has existing tables without the new indexed fields."""
    path = tmp_path / "original.db"
    legacy = sqlite3.connect(path)
    legacy.executescript("""
        CREATE TABLE campaigns (campaign_id TEXT PRIMARY KEY, source TEXT NOT NULL,
            medium TEXT NOT NULL, campaign_name TEXT NOT NULL, spend_cents INTEGER NOT NULL,
            registry_valid INTEGER NOT NULL);
        CREATE TABLE content_items (content_id TEXT PRIMARY KEY, title TEXT NOT NULL,
            platform TEXT NOT NULL, published_at TEXT NOT NULL, views INTEGER NOT NULL,
            clicks INTEGER NOT NULL, offer_id TEXT NOT NULL);
        CREATE TABLE contacts (contact_id TEXT PRIMARY KEY, email TEXT NOT NULL, legacy_id TEXT,
            owner_id TEXT, original_source TEXT, current_stage TEXT NOT NULL);
        CREATE TABLE touches (touch_id TEXT PRIMARY KEY, contact_id TEXT NOT NULL,
            campaign_id TEXT, occurred_at TEXT NOT NULL, touch_type TEXT NOT NULL, utm_source TEXT);
        CREATE TABLE deals (deal_id TEXT PRIMARY KEY, contact_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL, stage TEXT NOT NULL, closed_at TEXT);
        CREATE TABLE payments (payment_id TEXT PRIMARY KEY, deal_id TEXT, customer_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL, status TEXT NOT NULL, paid_at TEXT NOT NULL);
        CREATE TABLE processed_events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
            payment_id TEXT NOT NULL, customer_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
            received_at TEXT NOT NULL, claimed_at TEXT, completed_at TEXT);
        INSERT INTO contacts VALUES ('c-old', 'old@example.test', NULL, NULL, NULL, 'lead');
        INSERT INTO deals VALUES ('deal-old', 'c-old', 5000, 'closed_won', '2026-01-01');
        INSERT INTO payments VALUES ('pay-old', 'deal-old', 'c-old', 5000, 'succeeded', '2026-01-01');
        INSERT INTO processed_events VALUES ('evt-old', 'payment.succeeded', 'pay-old', 'c-old',
            'digest', 'completed', 1, NULL, '2026-01-01', NULL, '2026-01-01');
    """)
    legacy.close()
    connection = connect(path)
    initialize(connection)
    initialize(connection)
    assert schema_version(connection) == SCHEMA_VERSION
    for table, columns in LEGACY_COLUMNS.items():
        present = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        assert set(columns) <= present, table
    assert connection.execute("SELECT email FROM contacts WHERE contact_id='c-old'").fetchone()[0] == "old@example.test"
    assert connection.execute("SELECT payment_type FROM payments WHERE payment_id='pay-old'").fetchone()[0] == "new"
    assert connection.execute("SELECT deliveries FROM processed_events WHERE event_id='evt-old'").fetchone()[0] == 1
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.execute("INSERT INTO schema_migrations VALUES (?, 'future')", (SCHEMA_VERSION + 1,))
    with pytest.raises(RuntimeError, match="newer than supported"):
        initialize(connection)
    connection.close()


def test_readonly_dashboard_connection_does_not_create_or_modify_store(db_path, tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        connect_readonly(tmp_path / "absent.db")
    connection = connect_readonly(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] > 0
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("INSERT INTO contacts (contact_id,email,current_stage) VALUES ('x','x@y.test','lead')")
    finally:
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


def test_future_source_timestamps_do_not_pass_freshness(connection):
    connection.execute(
        """UPDATE payments SET paid_at='2035-01-01T00:00:00+00:00' WHERE payment_id=(
             SELECT payment_id FROM payments ORDER BY paid_at DESC LIMIT 1)"""
    )
    payment = next(row for row in freshness_check(connection) if row["source"] == "payments")
    assert payment["status"] == "future"


def test_invalid_source_timestamps_do_not_pass_freshness(connection):
    connection.execute(
        """UPDATE payments SET paid_at='zz-not-a-date' WHERE payment_id=(
             SELECT payment_id FROM payments ORDER BY paid_at DESC LIMIT 1)"""
    )
    payment = next(row for row in freshness_check(connection) if row["source"] == "payments")
    assert payment["status"] == "invalid"


def test_ask_endpoint_is_keyless_protected_and_audited(monkeypatch, db_path):
    _production(monkeypatch, db_path)
    with TestClient(app) as client:
        assert client.get("/ask", params={"q": "What does a lead cost on Google?"}).status_code == 401
        headers = {"X-API-Key": KEY}
        answered = client.get("/ask", params={"q": "What does a lead cost on Google?"}, headers=headers).json()
        assert answered["route"] == "metric" and answered["metric_id"] == "paid_efficiency"
        assert answered["answer"].startswith("Google,") and "cost per lead $" in answered["answer"]
        refused = client.get("/ask", params={"q": "select * from payments"}, headers=headers).json()
        assert refused["route"] == "refused"
        assert client.get("/ask", params={"q": "x" * 301}, headers=headers).status_code == 422
        usage = client.get("/ops/ask-usage", headers={
            **headers, "X-GrowthOps-Ops-Token": SAFE["GROWTHOPS_OPS_TOKEN"],
        }).json()
        assert {row["route"] for row in usage["by_route"]} == {"metric", "refused"}
    connection = connect(db_path)
    try:
        stored = [row[0] for row in connection.execute("SELECT question FROM ask_log")]
        assert stored and all(item.startswith("sha256:") for item in stored)
        assert all("Google" not in item for item in stored)
    finally:
        connection.close()


def test_dashboard_password_gate_blocks_before_any_data_loads(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("GROWTHOPS_DASHBOARD_PASSWORD", "correct horse battery staple")
    app = AppTest.from_file("../streamlit_app.py").run(timeout=60)
    assert not app.exception and len(app.tabs) == 0
    app.text_input[0].input("wrong").run(timeout=60)
    assert app.error and len(app.tabs) == 0


def test_production_dashboard_never_generates_demo_data(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("GROWTHOPS_ENV", "production")
    monkeypatch.delenv("GROWTHOPS_DASHBOARD_DATABASE", raising=False)
    app = AppTest.from_file("../streamlit_app.py").run(timeout=60)
    assert not app.exception and app.error and len(app.tabs) == 0
    assert "verified live database" in app.error[0].value


def test_worker_healthcheck_reads_the_heartbeat(monkeypatch, tmp_path):
    from growthops import worker

    monkeypatch.setattr(worker, "HEARTBEAT", tmp_path / "hb")
    assert worker.heartbeat_age_seconds() is None
    worker.HEARTBEAT.touch()
    assert worker.heartbeat_age_seconds() < 5
