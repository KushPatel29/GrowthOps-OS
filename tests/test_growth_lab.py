"""Conservation and boundary tests for the synthetic growth planning tools."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from growthops.growth_lab import (
    ScenarioInputs,
    customer_economics,
    funnel_cohorts,
    scenario_plan,
    trust_center,
)
from growthops.report import metrics


def test_cohorts_preserve_person_and_cash_totals(connection):
    totals = metrics(connection)
    for dimension in ("acquisition_month", "source", "campaign", "owner"):
        rows = funnel_cohorts(connection, dimension)["rows"]
        assert sum(row["leads"] for row in rows) == totals["leads"]
        assert sum(row["customers"] for row in rows) == totals["customers"]
        assert sum(row["net_cash_minor"] for row in rows) == totals["net_collected_cents"]
    with pytest.raises(ValueError, match="unsupported cohort"):
        funnel_cohorts(connection, "1; DROP TABLE contacts")


def test_economics_marks_observed_vs_unavailable_values(connection):
    result = customer_economics(connection)
    assert result["paid_acquired_buyers"] <= result["customers"]
    assert result["contracted_arr_minor"] == result["contracted_mrr_minor"] * 12
    assert 0 <= result["observed_renewal_rate"] <= 1
    assert "projected_ltv" in result["unsupported_metrics"]
    assert "nrr" in result["unsupported_metrics"]


def test_scenario_is_transparent_arithmetic_and_rejects_invalid_rates():
    plan = scenario_plan(ScenarioInputs(
        spend_minor=1_000_000, cost_per_lead_minor=10_000, mql_rate=.3,
        qualification_rate=.5, win_rate=.2, average_deal_minor=500_000,
        collection_rate=.8, refund_rate=.05,
    ))
    assert plan["expected_leads"] == 100
    assert plan["expected_qualified_opportunities"] == 15
    assert plan["pipeline_created_minor"] == 7_500_000
    assert plan["net_collected_minor"] == 1_125_000
    assert plan["mode"] == "user_assumption_scenario"
    with pytest.raises(ValidationError):
        ScenarioInputs(spend_minor=1, cost_per_lead_minor=1, mql_rate=.5,
                       qualification_rate=.5, win_rate=.5, average_deal_minor=1,
                       collection_rate=.9, refund_rate=.5)


def test_trust_center_states_unavailable_sources(connection):
    trust = trust_center(connection)
    assert trust["schema_version"] == trust["expected_schema_version"]
    assert trust["foreign_key_violations"] == 0
    assert trust["materialized_marts"] >= 1
    assert "dashboard_usage" in trust["unavailable_signals"]
