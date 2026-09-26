from growthops.brief import daily_series
from growthops.funnel import funnel
from growthops.reconciliation import crm_bridge, platform_comparison
from growthops.report import campaign_performance, measurement_health, metrics


def test_sql_marts_match_python_reference(connection):
    reference = metrics(connection)
    revenue = dict(connection.execute("SELECT * FROM mart_revenue").fetchone())
    for column in ("gross_collected_cents", "refunds_cents", "net_collected_cents"):
        assert revenue[column] == reference[column]
    assert revenue["booked_cents"] == reference["booked_revenue_cents"]
    sql_campaigns = {row["campaign_id"]: dict(row) for row in connection.execute("SELECT * FROM mart_campaign_performance")}
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
    daily = daily_series(connection, 500)
    for column, total in (("spend_cents", "spend_cents"), ("gross_collected_cents", "gross_collected_cents"),
                          ("refunds_cents", "refunds_cents"), ("net_cash_cents", "net_collected_cents")):
        assert sum(day[column] for day in daily) == reference[total]
    assert sum(day["closed_won_deals"] for day in daily) == reference["closed_won_deals"]
    bridge = {row["step"]: row["cents"] for row in connection.execute("SELECT * FROM mart_revenue_bridge")}
    assert bridge == {step["step"]: step["cents"] for step in crm_bridge(connection)["steps"]}
    platforms = {row["platform"]: dict(row) for row in connection.execute("SELECT * FROM mart_platform_comparison")}
    for row in platform_comparison(connection):
        assert platforms[row["platform"]]["warehouse_net_cash_cents"] == row["warehouse_net_cash_cents"]
        assert platforms[row["platform"]]["reported_value_cents"] == row["reported_value_cents"]
    content = [dict(row) for row in connection.execute("SELECT * FROM mart_content_performance")]
    assert len(content) == 30 and sum(item["influenced_net_cash_cents"] for item in content) <= reference["net_collected_cents"]
