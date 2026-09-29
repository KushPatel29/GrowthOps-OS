"""The v2.1 overlay must preserve cash truth and expose explainable CRM state."""

from fastapi.testclient import TestClient

from growthops.api import app
from growthops.control_plane import (
    crm_health,
    person_journey,
    qualified_pipeline,
    quality_queue,
)
from growthops.db import SCHEMA_VERSION, schema_version
from growthops.reconciliation import four_numbers


def test_identity_pipeline_and_health_are_materialized(connection):
    assert schema_version(connection) == SCHEMA_VERSION
    contacts = connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    assert connection.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == contacts
    assert connection.execute(
        "SELECT COUNT(*) FROM identity_links WHERE id_type='contact_id' AND state='active'"
    ).fetchone()[0] == contacts
    assert connection.execute(
        "SELECT COUNT(*) FROM identity_links WHERE id_type='email' AND id_hash LIKE '%@%'"
    ).fetchone()[0] == 0

    pipeline = qualified_pipeline(connection)
    assert pipeline["qualified_deals"] > 0
    assert pipeline["created_minor"] == sum(row["created_minor"] for row in pipeline["by_campaign"])
    assert pipeline["open_minor"] == sum(row["open_minor"] for row in pipeline["by_campaign"])
    assert pipeline["won_minor"] <= four_numbers(connection)["crm_booked_cents"]
    assert pipeline["unqualified_deals"] > 0

    health = crm_health(connection)
    assert health["contacts"] == contacts
    assert 0 <= health["score"] <= 100
    assert health["open_issues"] == quality_queue(connection, limit=1)["total"]
    assert all(0 <= part["passing"] <= part["eligible"] for part in health["components"])

    person = connection.execute("SELECT contact_id FROM deals ORDER BY deal_id LIMIT 1").fetchone()[0]
    journey = person_journey(connection, person)
    assert journey and journey["identity_evidence"] and journey["deals"]
    assert all("email" not in evidence for evidence in journey["identity_evidence"])


def test_control_plane_routes_expose_distinct_values(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    with TestClient(app) as client:
        health = client.get("/v2/crm/health")
        assert health.status_code == 200 and health.json()["scope"] == "full_synthetic_scenario"
        issues = client.get("/v2/quality/issues?limit=2")
        assert issues.status_code == 200 and len(issues.json()["results"]) == 2
        assert issues.json()["total"] > 2
        assert sum(item["count"] for item in issues.json()["rule_counts"]) == issues.json()["total"]
        assert client.get("/v2/quality/issues?limit=201").status_code == 422
        pipeline = client.get("/v2/metrics/qualified-pipeline").json()
        truth = client.get("/v2/metrics/revenue-truth").json()
        assert truth["qualified_pipeline_created_minor"] == pipeline["created_minor"]
        assert truth["crm_booked_minor"] != truth["net_collected_minor"]
        assert truth["crm_bridge"]["residual_cents"] == 0
        journey = client.get("/v2/people/c-000001/journey")
        assert journey.status_code == 200 and journey.json()["person_key"] == "c-000001"
        assert client.get("/v2/people/nonexistent/journey").status_code == 404
        center = client.get("/v2/decision-center")
        assert center.status_code == 200
        assert center.json()["revenue_truth"]["qualified_pipeline_created_minor"] == pipeline["created_minor"]
        assert center.json()["revenue_truth"]["crm_bridge_residual_minor"] == 0
        assert center.json()["brief"]["mode"] == "deterministic"
        governance = client.get("/v2/crm/marketing-contacts/audit")
        assert governance.status_code == 200
        counts = governance.json()["counts"]
        assert counts["eligibility_unknown"] == health.json()["contacts"]
        assert counts["consent_granted"] == 0
        assert counts["dormant_candidates"] > 0
        assert center.json()["marketing_contacts"]["counts"] == counts
        campaign = client.get("/v2/campaigns/qa")
        assert campaign.status_code == 200
        assert campaign.json()["short_links_with_issues"] == 4
        assert center.json()["campaign_qa"] == campaign.json()
        purchase = {"event_name": "purchase", "source": "web",
                    "parameters": {"transaction_id": "tx-1", "person_key": "c-000001",
                                   "currency": "USD", "value_minor": 1000, "product_id": "accelerator"}}
        invalid = client.post("/v2/instrumentation/validate", json=purchase).json()
        assert not invalid["valid"] and "source_must_be_payment_bridge" in invalid["issues"]
        purchase["source"] = "payment_bridge"
        valid = client.post("/v2/instrumentation/validate", json=purchase).json()
        assert valid["valid"] and valid["emits_to_ad_platform"] is False
        purchase["parameters"]["campaign_id"] = ["invalid"]
        malformed = client.post("/v2/instrumentation/validate", json=purchase)
        assert malformed.status_code == 200
        assert "campaign_id_must_be_string" in malformed.json()["issues"]
        assert client.get("/v2/console").status_code == 200
        console = client.get("/v2/console").text
        assert "Decision Center" in console and "Open workflow incidents" in console

        incidents = client.get("/v2/ops/incidents?limit=2")
        assert incidents.status_code == 200
        assert incidents.json()["scope"] == "full_synthetic_scenario"
        assert incidents.json()["total"] >= len(incidents.json()["results"]) == 2
        assert all(row["status"] in {"failed", "dead_letter"}
                   for row in incidents.json()["results"])
        assert all("email" not in row for row in incidents.json()["results"])
        assert client.get("/v2/ops/incidents?limit=101").status_code == 422

        issue = issues.json()["results"][0]
        filtered = client.get(f"/v2/quality/issues?rule={issue['rule_id']}&entity_type={issue['entity_type']}")
        assert filtered.status_code == 200
        assert all(item["rule_id"] == issue["rule_id"] for item in filtered.json()["results"])
        proposal = client.post(f"/v2/quality/issues/{issue['issue_id']}/propose-repair")
        assert proposal.status_code == 200 and proposal.json()["mode"] == "dry_run"
        assert proposal.json()["can_apply_automatically"] is False
        assert proposal.json()["proposed_value"] is None
        versions = client.get("/v2/registries/campaign/versions")
        assert versions.status_code == 200 and versions.json()["versions"][0]["version"] == "2.1"
        properties = client.get("/v2/registries/property/versions").json()["versions"][0]["definition"]["objects"]
        assert len(properties["contacts"]) + len(properties["deals"]) == 33
        workflows = client.get("/v2/registries/workflow/versions").json()["versions"][0]["definition"]
        assert len(workflows["workflows"]) == 3


def test_audited_replay_is_idempotent_and_exposes_outbox(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    monkeypatch.setenv("GROWTHOPS_OPS_TOKEN", "v21-ops-secret")
    with TestClient(app) as client:
        stuck = next(
            row for row in client.get("/ops/paid-without-access").json()
            if row["workflow_status"] == "dead_letter"
        )
        event_id = f"evt-{stuck['payment_id']}"
        endpoint = f"/v2/ops/events/{event_id}/replay"
        assert client.post(endpoint, json={"reason": "Provider recovered"}).status_code == 403
        headers = {"X-GrowthOps-Ops-Token": "v21-ops-secret",
                   "X-GrowthOps-Actor": "demo_operator",
                   "Idempotency-Key": "replay-demo-001"}
        first = client.post(endpoint, json={"reason": "Provider recovered"}, headers=headers)
        assert first.status_code == 200 and first.json()["result"]["status"] == "completed"
        second = client.post(endpoint, json={"reason": "Provider recovered"}, headers=headers)
        assert second.status_code == 200 and second.json()["duplicate"] is True
        trace = client.get(f"/v2/ops/events/{event_id}").json()
        assert trace["envelope"]["correlation_id"] and trace["outbox"]
        assert all(item["status"] == "delivered" for item in trace["outbox"])
