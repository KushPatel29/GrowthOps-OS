import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from growthops.api import MAX_WEBHOOK_BYTES, app


def _signed(body: dict, secret: bytes = b"test-secret") -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    return raw, {"X-GrowthOps-Signature": hmac.new(secret, raw, hashlib.sha256).hexdigest()}


def test_webhook_security_idempotency_and_lookup(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    monkeypatch.setenv("GROWTHOPS_WEBHOOK_SECRET", "test-secret")
    event = {"event_id": "evt-api-1", "event_type": "payment.succeeded", "payment_id": "pay-api-1",
             "customer_id": "c-000002", "amount_cents": 32000, "paid_at": "2026-09-26T00:00:00Z"}
    body, headers = _signed(event)
    with TestClient(app) as client:
        assert client.post("/webhooks/payments", content=body).status_code == 401
        first = client.post("/webhooks/payments", content=body, headers=headers)
        assert first.status_code == 202 and first.json()["status"] == "completed"
        assert client.post("/webhooks/payments", content=body, headers=headers).json()["duplicate"] is True
        customer = client.get("/ops/customers/c-000002").json()
        assert customer["crm"]["current_stage"] == "customer" and customer["access"]["status"] == "active"
        assert [p for p in customer["payments"] if p["payment_id"] == "pay-api-1"]
        reuse, reuse_headers = _signed({**event, "event_id": "evt-api-2"})
        assert client.post("/webhooks/payments", content=reuse, headers=reuse_headers).status_code == 409
        trace = client.get("/ops/events/evt-api-1").json()
        assert trace["deliveries"] == 2 and len(trace["attempts_log"]) == 4


def test_webhook_rejects_oversized_and_unexpected_payloads(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    monkeypatch.setenv("GROWTHOPS_WEBHOOK_SECRET", "test-secret")
    event = {"event_id": "evt-bounded", "event_type": "payment.succeeded",
             "payment_id": "pay-bounded", "customer_id": "c-000002",
             "amount_cents": 32000, "paid_at": "2026-09-26T00:00:00Z"}
    with TestClient(app) as client:
        too_large, headers = _signed({**event, "padding": "x" * MAX_WEBHOOK_BYTES})
        assert client.post("/webhooks/payments", content=too_large, headers=headers).status_code == 413
        extra, headers = _signed({**event, "unexpected": "ignored by old parser"})
        assert client.post("/webhooks/payments", content=extra, headers=headers).status_code == 422
        long_id, headers = _signed({**event, "event_id": "e" * 129})
        assert client.post("/webhooks/payments", content=long_id, headers=headers).status_code == 422
        assert client.get("/ops/events/evt-bounded").status_code == 404


def test_campaign_links_enforce_taxonomy(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    with TestClient(app) as client:
        link = client.post("/campaign-links", json={
            "campaign_id": "meta_prospecting_founder",
            "destination_url": "https://scalelab.test/guide?ref=home&utm_source=bad#form",
            "content": "video_hook_03"})
        assert link.status_code == 200
        url = link.json()["url"]
        assert "utm_source=meta" in url and "utm_campaign=meta_prospecting_founder" in url
        assert "ref=home" in url and "utm_source=bad" not in url and url.endswith("#form")
        invalid = client.post("/campaign-links", json={
            "campaign_id": "FB-Broad", "destination_url": "https://scalelab.test/guide", "content": "video_hook_03"})
        assert invalid.status_code == 422


def test_analytics_and_operations_endpoints(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    monkeypatch.setenv("GROWTHOPS_OPS_TOKEN", "ops-secret")
    with TestClient(app) as client:
        executive = client.get("/metrics/executive").json()
        assert executive["metrics"]["leads"] > 10_000
        assert client.get("/metrics/funnel").json()[0]["stage"] == "lead"
        for model in ("lead_creation", "linear"):
            assert client.get(f"/metrics/attribution/{model}").status_code == 200
        assert client.get("/metrics/attribution/unsupported").status_code == 422
        assert len(client.get("/metrics/daily?days=14").json()) == 14
        assert client.get("/metrics/brief").json()["findings"][0]["id"] == "ops_dead_letter"
        truth = client.get("/metrics/revenue-truth").json()
        assert truth["platform_bridge"]["residual_cents"] == 0 and truth["crm_bridge"]["residual_cents"] == 0
        anomalies = client.get("/metrics/anomalies").json()
        assert all(item["root_cause_correct"] for item in anomalies["ground_truth_check"])
        assert client.get("/metrics/narrative").json()["mode"] == "deterministic"
        assert client.get("/ops/migration").json()["missing_contacts"] > 0
        assert len(client.get("/metrics/content").json()) == 30
        assert client.get("/metrics/experiments/cta_growth_plan").status_code == 200
        assert client.get("/ops/workflows").json()["dead_letter"] > 0
        queue = client.get("/ops/paid-without-access").json()
        stuck = next(row for row in queue if row["workflow_status"] == "dead_letter")
        event_id = f"evt-{stuck['payment_id']}"
        assert client.post(f"/ops/events/{event_id}/replay").status_code == 403
        replayed = client.post(f"/ops/events/{event_id}/replay", headers={"X-GrowthOps-Ops-Token": "ops-secret"})
        assert replayed.status_code == 200 and replayed.json()["status"] == "completed"
        assert client.get(f"/ops/customers/{stuck['customer_id']}").json()["diagnosis"] == "OK"
        page = client.get("/dashboard")
        assert page.status_code == 200 and "Synthetic scenario" in page.text


def test_ask_answers_with_what_it_understood_and_serves_suggestions(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    with TestClient(app) as client:
        themes = client.get("/ask/suggestions").json()["themes"]
        assert "Paid media" in themes and all(themes.values())
        body = client.get("/ask", params={"q": "What does a lead cost on Google?"}).json()
        assert body["metric_id"] == "paid_efficiency" and body["answer"].startswith("Google,")
        assert "Google" in body["understood"] and body["follow_ups"]
        refused = client.get("/ask", params={"q": "TikTok cost per lead"}).json()
        assert refused["route"] == "refused" and "TikTok ads are not bought" in refused["answer"]
