from growthops.db import connect
from growthops.funnel import funnel
from growthops.report import campaign_performance, measurement_health, metrics
from growthops.seed import seed
from growthops.warehouse import build
from growthops.brief import daily_series, period_brief


def test_sql_marts_match_reference_metrics(tmp_path):
    database = tmp_path / "warehouse.db"
    seed(str(database))
    build(str(database))
    connection = connect(database)
    reference = metrics(connection)
    revenue = dict(connection.execute("SELECT * FROM mart_revenue").fetchone())
    assert revenue["gross_collected_cents"] == reference["gross_collected_cents"]
    assert revenue["refunds_cents"] == reference["refunds_cents"]
    assert revenue["net_collected_cents"] == reference["net_collected_cents"]
    assert sum(row["net_cash_cents"] for row in connection.execute(
        "SELECT net_cash_cents FROM mart_attribution_lead_creation"
    )) == reference["net_collected_cents"]

    sql_campaigns = {row["campaign_id"]: dict(row) for row in connection.execute(
        "SELECT * FROM mart_campaign_performance"
    )}
    for row in campaign_performance(connection):
        assert sql_campaigns[row["campaign_id"]] == row

    sql_funnel = {row["stage"]: dict(row) for row in connection.execute("SELECT * FROM mart_funnel")}
    for row in funnel(connection):
        assert sql_funnel[row["stage"]]["people"] == row["people"]
        assert sql_funnel[row["stage"]]["from_previous_rate"] == row["from_previous_rate"]

    health = measurement_health(connection)
    sql_health = dict(connection.execute("SELECT * FROM mart_measurement_health").fetchone())
    assert sql_health["duplicate_contact_rows"] == health["duplicate_contact_rows"]
    assert sql_health["unmatched_payments"] == health["unmatched_payment_count"]
    assert round(sql_health["touches_with_utm"] / sql_health["eligible_touches"], 4) == health["utm_completeness"]
    assert round(sql_health["registered_touches"] / sql_health["eligible_touches"], 4) == health["campaign_registry_match"]
    assert sql_health["valid_paid_journeys"] == health["valid_paid_journeys"]
    daily = daily_series(connection, 365)
    assert sum(day["spend_cents"] for day in daily) == reference["spend_cents"]
    assert sum(day["gross_collected_cents"] for day in daily) == reference["gross_collected_cents"]
    assert sum(day["refunds_cents"] for day in daily) == reference["refunds_cents"]
    assert sum(day["net_cash_cents"] for day in daily) == reference["net_collected_cents"]
    brief = period_brief(connection)
    assert brief["current"]["spend_cents"] > 0
    assert brief["current"]["leads"] == 0
    assert brief["findings"][0]["finding"] == "Spend continued without recorded leads."
    content = [dict(row) for row in connection.execute("SELECT * FROM mart_content_performance")]
    assert len(content) == 8
    assert sum(item["influenced_net_cash_cents"] for item in content) <= reference["net_collected_cents"]
    assert any(item["views"] > 5000 and item["influenced_net_cash_cents"] == 0 for item in content)
    connection.close()
