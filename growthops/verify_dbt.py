"""Compare DuckDB/dbt marts with the local governed metric reference."""

from __future__ import annotations

import argparse
from pathlib import Path

from growthops.campaign_links import audit_short_links
from growthops.control_plane import crm_health, qualified_pipeline
from growthops.db import connect
from growthops.email_analytics import email_performance
from growthops.experiments import analyze as experiment_analysis
from growthops.funnel import funnel
from growthops.migration import audit as migration_audit
from growthops.performance import paid_efficiency
from growthops.reconciliation import crm_bridge, platform_comparison
from growthops.renewals import monitor as renewal_monitor
from growthops.report import campaign_performance, measurement_health, metrics
from growthops.scenario import AS_OF, START


def _one(cursor) -> tuple:
    """The one row a mart query must return; an empty mart fails here, by name, rather than as a None."""
    row = cursor.fetchone()
    if row is None:
        raise AssertionError("the mart returned no row")
    return row


def verify(sqlite_database: str, duckdb_database: str) -> None:
    import duckdb  # Installed with the optional warehouse dependency.

    if not Path(duckdb_database).exists():
        raise FileNotFoundError(duckdb_database)
    source = connect(sqlite_database)
    warehouse = duckdb.connect(duckdb_database, read_only=True)
    warehouse.execute("SET TimeZone = 'UTC'")  # same day boundaries on every machine
    try:
        reference = metrics(source)
        revenue_query = warehouse.execute("select * from mart_revenue")
        revenue = dict(zip([item[0] for item in revenue_query.description], _one(revenue_query)))
        for column in ("gross_collected_cents", "refunds_cents", "net_collected_cents"):
            assert int(revenue[column]) == reference[column], column

        campaign_columns = [item[0] for item in warehouse.execute(
            "select * from mart_campaign_performance limit 0"
        ).description]
        campaign_rows = [dict(zip(campaign_columns, row)) for row in warehouse.execute(
            "select * from mart_campaign_performance order by campaign_id"
        ).fetchall()]
        assert campaign_rows == campaign_performance(source), "campaign mart differs from reference"

        funnel_rows = {stage: (int(people), rate) for stage, people, rate in warehouse.execute(
            "select stage, people, from_previous_rate from mart_funnel"
        ).fetchall()}
        for row in funnel(source):
            actual_count, actual_rate = funnel_rows[row["stage"]]
            assert actual_count == row["people"], row["stage"]
            assert actual_rate == row["from_previous_rate"], row["stage"]

        daily_columns = [item[0] for item in warehouse.execute(
            "select * from mart_growth_daily limit 0"
        ).description]
        duckdb_daily = [dict(zip(daily_columns, row)) for row in warehouse.execute(
            "select * from mart_growth_daily order by day"
        ).fetchall()]
        sqlite_daily = [dict(row) for row in source.execute(
            "select * from mart_growth_daily order by day"
        ).fetchall()]
        assert len(duckdb_daily) == len(sqlite_daily), "daily mart has a different date spine"
        for warehouse_row, source_row in zip(duckdb_daily, sqlite_daily):
            warehouse_row["day"] = str(warehouse_row["day"])
            assert warehouse_row == source_row, f"daily mart differs on {source_row['day']}"

        content_columns = [item[0] for item in warehouse.execute(
            "select * from mart_content_performance limit 0"
        ).description]
        duckdb_content = [dict(zip(content_columns, row)) for row in warehouse.execute(
            "select * from mart_content_performance order by content_id"
        ).fetchall()]
        sqlite_content = [dict(row) for row in source.execute(
            "select * from mart_content_performance order by content_id"
        ).fetchall()]
        assert duckdb_content == sqlite_content, "content mart differs from reference"

        quality_columns = [item[0] for item in warehouse.execute(
            "select * from mart_measurement_health limit 0"
        ).description]
        quality = dict(zip(quality_columns, _one(warehouse.execute("select * from mart_measurement_health"))))
        expected_quality = measurement_health(source)
        assert quality["duplicate_contact_rows"] == expected_quality["duplicate_contact_rows"]
        assert quality["unmatched_payments"] == expected_quality["unmatched_payment_count"]
        assert quality["valid_paid_journeys"] == expected_quality["valid_paid_journeys"]
        assert round(quality["touches_with_utm"] / quality["eligible_touches"], 4) == expected_quality["utm_completeness"]
        assert round(quality["registered_touches"] / quality["eligible_touches"], 4) == expected_quality["campaign_registry_match"]

        migration_query = warehouse.execute("select * from mart_migration_summary")
        migration = dict(zip([item[0] for item in migration_query.description], _one(migration_query)))
        expected_migration = migration_audit(source)
        for field in ("legacy_contacts", "migrated_contacts", "missing_contacts",
                      "owner_match_rate", "source_match_rate", "stage_match_rate", "duplicate_crm_rows"):
            assert migration[field] == expected_migration[field], field

        experiment_columns = [item[0] for item in warehouse.execute(
            "select * from mart_experiment_variants limit 0"
        ).description]
        experiment_rows = [dict(zip(experiment_columns, row)) for row in warehouse.execute(
            "select * from mart_experiment_variants order by variant_id"
        ).fetchall()]
        expected_experiment = experiment_analysis(source, "cta_growth_plan", bootstrap_draws=100)
        for actual, expected in zip(experiment_rows, expected_experiment["variants"]):
            for field in expected:
                assert actual[field] == expected[field], f"experiment {actual['variant_id']} {field}"

        renewal_counts = dict(warehouse.execute(
            "select risk_level, count(*) from mart_renewal_risk group by risk_level"
        ).fetchall())
        expected_renewals = renewal_monitor(source)
        assert sum(renewal_counts.values()) == expected_renewals["active_subscriptions"]
        assert renewal_counts.get("high", 0) == expected_renewals["high_risk"]
        assert renewal_counts.get("medium", 0) == expected_renewals["due_soon"]

        bridge = {step: int(cents) for step, cents in warehouse.execute(
            "select step, cents from mart_revenue_bridge").fetchall()}
        for step in crm_bridge(source)["steps"]:
            assert bridge[step["step"]] == step["cents"], f"revenue bridge {step['step']}"

        platforms = {row[0]: row[1:] for row in warehouse.execute(
            """select platform, spend_cents, reported_conversions, reported_value_cents, warehouse_net_cash_cents
               from mart_platform_comparison""").fetchall()}
        for row in platform_comparison(source):
            expected = (row["spend_cents"], row["reported_conversions"], row["reported_value_cents"],
                        row["warehouse_net_cash_cents"])
            assert tuple(int(value) for value in platforms[row["platform"]]) == expected, row["platform"]

        email_columns = [item[0] for item in warehouse.execute("select * from mart_email_performance limit 0").description]
        email_rows = {row[0]: dict(zip(email_columns, row)) for row in warehouse.execute(
            "select * from mart_email_performance").fetchall()}
        expected_emails = email_performance(source)
        assert len(email_rows) == len(expected_emails), "email mart has a different number of sends"
        for expected in expected_emails:
            actual = email_rows[expected["email_id"]]
            for field in ("sends", "delivered", "bounces", "opens", "machine_opens", "human_opens", "clicks",
                          "unsubscribes", "spam_complaints", "sending_domain", "sent_date"):
                assert actual[field] == expected[field], f"email {expected['email_id']} {field}"
            for field in ("bounce_rate", "human_open_rate", "click_rate", "click_to_open_rate", "complaint_rate"):
                assert abs(actual[field] - expected[field]) <= 0.0001, f"email {expected['email_id']} {field}"

        links = audit_short_links(source)
        hygiene = {row[0]: row[1:] for row in warehouse.execute(
            """select link_id, missing_utm or unregistered_campaign or off_taxonomy, recent_clicks, clicks
               from mart_link_hygiene""").fetchall()}
        for link in links["links"]:
            assert hygiene[link["link_id"]] == (bool(link["issues"]), link["recent_clicks"], link["clicks"]), \
                f"link hygiene {link['link_id']}"

        fields = ("spend_cents", "impressions", "clicks", "leads", "mqls", "calls_booked", "closed_won_deals")
        daily = {row[0]: row[1:] for row in warehouse.execute(
            f"select campaign_id, {', '.join(f'sum({f})' for f in fields)} from mart_paid_efficiency_daily "
            "group by campaign_id").fetchall()}
        for row in paid_efficiency(source, START, AS_OF)[:-1]:
            totals = tuple(int(value) for value in daily[row["segment"]])
            assert totals == tuple(row[f] for f in fields), f"paid efficiency daily {row['segment']}"

        pipeline = qualified_pipeline(source)
        dbt_pipeline = {
            campaign: (int(deals), int(created), int(open_value), int(won))
            for campaign, deals, created, open_value, won in warehouse.execute(
                """select campaign_id, qualified_deals, created_minor, open_minor, won_minor
                   from mart_qualified_pipeline where currency='USD'"""
            ).fetchall()
        }
        for row in pipeline["by_campaign"]:
            assert dbt_pipeline[row["campaign_id"]] == (
                row["qualified_deals"], row["created_minor"],
                row["open_minor"], row["won_minor"],
            ), f"qualified pipeline {row['campaign_id']}"
        assert len(dbt_pipeline) == len(pipeline["by_campaign"])

        expected_health = {row["name"]: (row["passing"], row["eligible"])
                           for row in crm_health(source)["components"]}
        actual_health = {
            name: (int(passing), int(eligible))
            for name, passing, eligible in warehouse.execute(
                "select component, passed_records, eligible from mart_crm_health_v21"
            ).fetchall()
        }
        assert actual_health == expected_health, "CRM health component denominators differ"
    finally:
        warehouse.close()
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default="data/growthops-sample.db")
    parser.add_argument("--duckdb", default="data/growthops-warehouse.duckdb")
    args = parser.parse_args()
    verify(args.sqlite, args.duckdb)
    print("DuckDB/dbt marts match the local reference")


if __name__ == "__main__":
    main()
