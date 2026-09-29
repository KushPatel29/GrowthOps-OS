"""The local ad conversion outbox must fail closed on cash, campaign and consent."""

from __future__ import annotations

from fastapi.testclient import TestClient

from growthops.api import app
from growthops.communications import communication_health
from growthops.conversion_router import (
    conversion_health,
    preview_conversion,
    queue_conversion,
)


def _candidate(connection, consent_status: str) -> str:
    row = connection.execute(
        """SELECT p.payment_id FROM payments p
           JOIN consent_ledger l ON l.person_key=p.customer_id AND l.channel='ads'
           JOIN touches t ON t.contact_id=p.customer_id AND t.touch_type='lead_creation'
           JOIN campaigns c ON c.campaign_id=t.campaign_id
           WHERE p.status='succeeded' AND l.status=? AND c.platform IN ('meta','google','linkedin')
           ORDER BY p.payment_id LIMIT 1""",
        (consent_status,),
    ).fetchone()
    assert row is not None
    return row[0]


def test_conversion_queue_requires_latest_consent_and_is_idempotent(connection):
    granted = _candidate(connection, "granted")
    denied = _candidate(connection, "denied")
    assert preview_conversion(connection, granted)["eligible"]
    assert not preview_conversion(connection, denied)["eligible"]
    denied_person = preview_conversion(connection, denied)["person_key"]
    connection.execute(
        "INSERT INTO consent_ledger VALUES (?, ?, 'ads', 'granted', 'test', ?, 'test:late')",
        ("late-grant-test", denied_person, "2026-09-30T00:00:00+00:00"),
    )
    assert "ads_consent_missing_at_purchase" in preview_conversion(connection, denied)["reasons"]
    first = queue_conversion(connection, granted)
    second = queue_conversion(connection, granted)
    assert first["conversion_id"] == second["conversion_id"]
    assert not first["duplicate"] and second["duplicate"]
    assert conversion_health(connection)["queued"] == 1
    assert conversion_health(connection)["provider_verified_deliveries"] == 0
    person = preview_conversion(connection, granted)["person_key"]
    connection.execute(
        "INSERT INTO consent_ledger VALUES (?, ?, 'ads', 'revoked', 'test', ?, 'test:revoked')",
        ("revoke-test", person, "2026-09-30T00:00:00+00:00"),
    )
    assert not preview_conversion(connection, granted)["eligible"]
    assert "ads_consent_not_granted" in preview_conversion(connection, granted)["reasons"]
    assert conversion_health(connection)["queued_blocked_by_current_evidence"] == 1
    assert len(communication_health(connection)["consent"]) >= 3


def test_conversion_api_is_local_and_role_gated(db_path, connection, monkeypatch):
    payment = _candidate(connection, "granted")
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    monkeypatch.setenv("GROWTHOPS_OPS_TOKEN", "conversion-test-secret")
    with TestClient(app) as client:
        assert client.get("/v2/conversions/health").json()["provider_connected"] is False
        assert client.get("/v2/communications/health").status_code == 200
        assert client.get(f"/v2/conversions/preview/{payment}").json()["eligible"]
        assert client.get("/v2/conversions/preview/missing").status_code == 404
        assert client.post(f"/v2/conversions/queue/{payment}").status_code == 403
        response = client.post(f"/v2/conversions/queue/{payment}",
                               headers={"X-GrowthOps-Ops-Token": "conversion-test-secret"})
        assert response.status_code == 202
        assert response.json()["provider_delivered"] is False
        assert client.post(f"/v2/conversions/queue/{payment}",
                           headers={"X-GrowthOps-Ops-Token": "conversion-test-secret"}).json()["duplicate"]
