from growthops.db import connect
from growthops.report import campaign_performance, executive_brief, measurement_health, metrics
from growthops.seed import seed
from growthops.attribution import MODELS, allocations, summary
from growthops.funnel import funnel


def test_seeded_metrics_reconcile_to_cash(tmp_path):
    database = tmp_path / "scenario.db"
    seed(str(database))
    connection = connect(database)
    result = metrics(connection)
    quality = measurement_health(connection)
    campaigns = campaign_performance(connection)
    assert result["leads"] == 240
    assert result["mqls"] == 100
    assert result["closed_won_deals"] == 30
    assert result["net_collected_cents"] == result["gross_collected_cents"] - result["refunds_cents"]
    assert sum(row["net_cash_cents"] for row in campaigns) == result["net_collected_cents"]
    assert quality["duplicate_contact_rows"] > 0
    assert quality["unmatched_payment_count"] == 1
    assert quality["utm_completeness"] < 1
    assert quality["lifecycle_integrity"] == 0.9
    assert result["spend_cents"] == 1340000  # Includes invalid but real paid campaign spend.
    assert result["net_cash_roas"] == round(1050000 / 1340000, 4)
    assert len(executive_brief(connection)["observations"]) >= 1
    stages = {row["stage"]: row["people"] for row in funnel(connection)}
    assert stages["lead"] == 240
    assert stages["call_booked"] == 60
    assert stages["opportunity"] == 37
    assert stages["paid"] == 30
    for model in MODELS:
        assert sum(row["net_cash_cents"] for row in summary(connection, model)) == result["net_collected_cents"]
    assert summary(connection, "lead_creation") != summary(connection, "last_non_direct")
    first = next(row for row in allocations(connection, "first_touch") if row["payment_id"] == "p-00000")
    last = next(row for row in allocations(connection, "last_non_direct") if row["payment_id"] == "p-00000")
    assert first["campaign_id"] != last["campaign_id"]
    assert last["campaign_id"] != "direct"
    u_rows = [row for row in allocations(connection, "u_shaped") if row["payment_id"] == "p-00000"]
    assert sorted(row["credited_cents"] for row in u_rows) == [17500, 17500]
    connection.close()
