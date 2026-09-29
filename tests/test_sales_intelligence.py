"""Behavior and evidence gates for the synthetic sales intelligence slice."""

from __future__ import annotations

from fastapi.testclient import TestClient

from growthops.api import app
from growthops.sales_intelligence import (
    MODEL_VERSION,
    batch_classify,
    classification_summary,
    classify,
    sales_copilot,
)
from growthops.sales_intelligence_eval import run_eval


def test_classifier_uses_explicit_spans_and_abstains_on_conflict():
    text = "Everything depends on me. We have no time to implement. Please send the agreement."
    result = classify(text)
    assert result["labels"]["pain"] == "founder_bottleneck"
    assert result["labels"]["objection"] == "capacity"
    assert result["labels"]["intent"] == "high"
    assert result["labels"]["timeline"] == "unknown"
    for field in ("pain", "objection", "intent"):
        span = result["evidence"][field]
        assert text[span["start"]:span["end"]] == span["quote"]
    assert classify("Budget and implementation capacity both matter.")["labels"]["objection"] == "unknown"


def test_batch_is_versioned_idempotent_and_copilot_only_cites_won_cases(connection):
    assert batch_classify(connection)["classified"] == 0
    summary = classification_summary(connection)
    assert summary["conversations"] == 180 and summary["model_version"] == MODEL_VERSION
    answer = sales_copilot(connection, "c-000789")
    assert answer and answer["conversation_id"]
    assert answer["raw_transcript_in_response"] is False
    assert "transcript" not in answer
    assert answer["similar_won_cases"]
    for case in answer["similar_won_cases"]:
        row = connection.execute("SELECT stage FROM deals WHERE deal_id=?", (case["deal_id"],)).fetchone()
        assert row["stage"] == "closed_won"


def test_held_out_synthetic_sales_eval_and_api(db_path, connection, monkeypatch):
    result = run_eval(connection)
    assert result["classification_cases"] >= 100
    assert result["copilot_checks"] >= 20
    assert result["macro_f1"] >= 0.85 and not result["failures"]
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    with TestClient(app) as client:
        summary = client.get("/v2/ai/classifications")
        assert summary.status_code == 200 and summary.json()["conversations"] == 180
        prospect = client.get("/v2/ai/sales-copilot/c-000789")
        assert prospect.status_code == 200 and not prospect.json()["raw_transcript_in_response"]
        assert client.get("/v2/ai/sales-copilot/not-real").status_code == 404
