from growthops.reconciliation import (
    crm_bridge,
    duplicate_contacts,
    four_numbers,
    platform_bridge,
    platform_comparison,
)
from growthops.report import metrics


def _check_waterfall(steps):
    running = None
    for step in steps:
        if step["kind"] == "total":
            if running is not None:
                assert running == step["cents"], step["step"]
            running = step["cents"]
        else:
            running += step["cents"]


def test_crm_bridge_ties_exactly_from_bookings_to_cash(connection):
    bridge = crm_bridge(connection)
    assert bridge["residual_cents"] == 0
    _check_waterfall(bridge["steps"])
    values = {step["step"]: step["cents"] for step in bridge["steps"]}
    kpis = metrics(connection)
    assert values["crm_booked"] == kpis["booked_revenue_cents"]
    assert values["net_collected"] == kpis["net_collected_cents"]
    assert values["duplicate_deals"] <= 0 and values["not_yet_collected"] < 0 and values["renewals"] > 0
    assert len(duplicate_contacts(connection)) == metrics(connection)["leads"] - metrics(connection)["unique_people"]


def test_platform_bridge_ties_exactly_from_claims_to_warehouse(connection):
    bridge = platform_bridge(connection)
    assert bridge["residual_cents"] == 0
    _check_waterfall(bridge["steps"])
    values = {step["step"]: step["cents"] for step in bridge["steps"]}
    assert values["warehouse_paid_cash"] == metrics(connection)["paid_attributed_net_cash_cents"]
    assert values["cross_platform_duplicates"] < 0 and values["view_through"] < 0


def test_platforms_overstate_and_net_cash_is_the_answer(connection):
    numbers = four_numbers(connection)
    assert numbers["platform_reported_total_cents"] > numbers["gross_collected_cents"] > numbers["net_collected_cents"]
    assert numbers["paid_media_net_cash_cents"] < numbers["platform_reported_total_cents"]
    for row in platform_comparison(connection):
        assert row["platform_roas"] > row["warehouse_roas"], row["platform"]
