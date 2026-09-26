from growthops.brief import findings, period_brief
from growthops.narrator import deterministic, validate


def test_brief_leads_with_customer_impact_and_groups_by_driver(connection):
    brief = period_brief(connection)
    items = brief["findings"]
    assert items[0]["id"] == "ops_dead_letter"
    broad = [item for item in items if "meta_broad_v17" in item["finding"]]
    assert len(broad) == 1 and "moved" in broad[0]["finding"]
    assert "cost per MQL" in broad[0]["investigation"]
    assert any(item["id"].startswith("utm_completeness") and "/webinar" in item["why"] for item in items)
    for item in items:
        assert item["evidence"] and item["investigation"] and item["source"] and item["confidence"]
    assert brief["current"]["end"] == "2026-09-25"
    assert set(brief["change_pct"]) == set(brief["current"]) - {"start", "end"}


def test_brief_never_states_a_cause_and_passes_its_own_guardrail(connection):
    items = findings(connection)
    assert validate(deterministic(items), items) == []
