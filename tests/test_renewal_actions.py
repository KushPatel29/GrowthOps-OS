"""Renewal recommendations are traceable and never dispatch messages."""

from __future__ import annotations

from fastapi.testclient import TestClient

from growthops.api import app
from growthops.communications import latest_consent
from growthops.renewals import action_proposals, monitor


def test_renewal_proposals_follow_risk_and_consent(connection):
    risks = monitor(connection)
    result = action_proposals(connection)
    assert result["count"] == len(risks["issues"])
    assert result["count"] > 0
    for item in result["proposals"]:
        assert item["provider_dispatched"] is False
        assert item["action_status"] == "proposal_only"
        consent = latest_consent(connection, item["person_key"], "email")
        expected = "email" if consent and consent["status"] == "granted" else "manual_call"
        assert item["suggested_channel"] == expected


def test_renewal_proposal_api(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    with TestClient(app) as client:
        result = client.get("/v2/renewals/action-proposals")
        assert result.status_code == 200
        assert result.json()["count"] == len(result.json()["proposals"])
