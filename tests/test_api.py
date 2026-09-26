import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from growthops.api import app
from growthops.seed import seed


def test_signed_webhook_and_customer_lookup(tmp_path, monkeypatch):
    database = tmp_path / "api.db"
    seed(str(database), people=8)
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(database))
    monkeypatch.setenv("GROWTHOPS_WEBHOOK_SECRET", "test-secret")
    body = json.dumps({
        "event_id": "evt-api-1",
        "event_type": "payment.succeeded",
        "payment_id": "pay-api-1",
        "customer_id": "c-00001",
        "amount_cents": 32000,
        "paid_at": "2026-09-02T00:00:00Z",
    }).encode()
    signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    with TestClient(app) as client:
        assert client.post("/webhooks/payments", content=body).status_code == 401
        first = client.post("/webhooks/payments", content=body, headers={"X-GrowthOps-Signature": signature})
        assert first.status_code == 202
        assert first.json()["status"] == "completed"
        repeat = client.post("/webhooks/payments", content=body, headers={"X-GrowthOps-Signature": signature})
        assert repeat.json()["duplicate"] is True
        customer = client.get("/ops/customers/c-00001").json()
        assert customer["crm"]["current_stage"] == "customer"
        assert customer["access"]["status"] == "active"
        assert len([p for p in customer["payments"] if p["payment_id"] == "pay-api-1"]) == 1
        assert customer["workflows"][0]["event_id"] == "evt-api-1"
        reuse = json.dumps({
            "event_id": "evt-api-2", "event_type": "payment.succeeded",
            "payment_id": "pay-api-1", "customer_id": "c-00001",
            "amount_cents": 32000, "paid_at": "2026-09-02T00:00:00Z",
        }).encode()
        reuse_sig = hmac.new(b"test-secret", reuse, hashlib.sha256).hexdigest()
        assert client.post("/webhooks/payments", content=reuse,
                           headers={"X-GrowthOps-Signature": reuse_sig}).status_code == 409
        link = client.post("/campaign-links", json={
            "campaign_id": "meta-founder",
            "destination_url": "https://scalelab.test/guide?ref=home&utm_source=bad#form",
            "content": "video_hook_03",
        })
        assert link.status_code == 200
        assert "utm_source=meta" in link.json()["url"]
        assert "utm_campaign=meta_founder" in link.json()["url"]
        assert "ref=home" in link.json()["url"]
        assert link.json()["url"].endswith("#form")
        invalid = client.post("/campaign-links", json={
            "campaign_id": "FB-Broad",
            "destination_url": "https://scalelab.test/guide",
            "content": "video_hook_03",
        })
        assert invalid.status_code == 422
        executive = client.get("/metrics/executive").json()
        assert executive["period"] == "all_time_synthetic"
        assert executive["metrics"]["leads"] == 8
        assert client.get("/metrics/funnel").json()[0]["stage"] == "lead"
        assert client.get("/metrics/attribution/lead_creation").status_code == 200
        assert client.get("/metrics/attribution/unsupported").status_code == 422
        page = client.get("/dashboard")
        assert page.status_code == 200
        assert "Synthetic scenario" in page.text
