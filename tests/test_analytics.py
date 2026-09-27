from fractions import Fraction

from growthops.attribution import MODELS, _split_cents, allocations, summary
from growthops.funnel import funnel, funnel_by_campaign
from growthops.report import (
    campaign_performance,
    executive_brief,
    measurement_health,
    metrics,
)


def test_split_cents_is_exact_and_stable():
    parts = _split_cents(1001, {"a": Fraction(1, 3), "b": Fraction(1, 3), "c": Fraction(1, 3)})
    assert sum(parts.values()) == 1001
    assert parts == {"a": 334, "b": 334, "c": 333}  # two leftover cents, ties broken by key


def test_every_attribution_model_conserves_net_cash(connection):
    net = metrics(connection)["net_collected_cents"]
    for model in MODELS:
        rows = summary(connection, model)
        assert sum(row["net_cash_cents"] for row in rows) == net, model
    lead = {row["campaign_id"]: row["net_cash_cents"] for row in summary(connection, "lead_creation")}
    last = {row["campaign_id"]: row["net_cash_cents"] for row in summary(connection, "last_non_direct")}
    assert lead != last
    assert all(row["credited_cents"] >= 0 for row in allocations(connection, "u_shaped"))


def test_campaign_cash_plus_unassigned_equals_net_cash(connection):
    result = metrics(connection)
    health = measurement_health(connection)
    campaigns = campaign_performance(connection)
    assert health["unassigned_net_cash_cents"] > 0  # the UTM break leaves cash uncreditable
    assert sum(row["net_cash_cents"] for row in campaigns) + health["unassigned_net_cash_cents"] == \
        result["net_collected_cents"]
    assert result["net_collected_cents"] == result["gross_collected_cents"] - result["refunds_cents"]
    assert result["cost_per_lead_cents"] == round(result["spend_cents"] / result["paid_leads"])
    assert result["net_cash_roas"] == round(result["paid_attributed_net_cash_cents"] / result["spend_cents"], 4)
    assert result["unique_people"] < result["leads"]  # duplicates inflate raw lead counts


def test_funnel_is_monotonic_and_quality_checks_fire(connection):
    stages = funnel(connection)
    counts = [row["people"] for row in stages[:8]]
    assert counts == sorted(counts, reverse=True)
    assert all(row["median_days_from_previous"] is None or row["median_days_from_previous"] >= 0 for row in stages)
    brief = executive_brief(connection)
    health = brief["measurement_health"]
    assert health["duplicate_contact_rows"] > 0
    assert health["unmatched_payment_count"] > 0
    assert 0.9 < health["lifecycle_integrity"] < 1
    assert health["campaign_registry_match"] < 0.98  # legacy FB-Broad naming
    assert len(brief["observations"]) >= 4
    by_campaign = {row["campaign_id"]: row for row in funnel_by_campaign(connection)}
    assert by_campaign["meta_broad_v17"]["lead_to_mql"] < by_campaign["google_brand_search"]["lead_to_mql"]
    assert "(unattributed)" in by_campaign
