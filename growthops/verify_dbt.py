"""Compare DuckDB/dbt marts with the local governed metric reference."""

from __future__ import annotations

import argparse
from pathlib import Path

from growthops.db import connect
from growthops.funnel import funnel
from growthops.report import campaign_performance, measurement_health, metrics
from growthops.migration import audit as migration_audit
from growthops.experiments import analyze as experiment_analysis


def verify(sqlite_database: str, duckdb_database: str) -> None:
    import duckdb  # Installed with the optional warehouse dependency.

    if not Path(duckdb_database).exists():
        raise FileNotFoundError(duckdb_database)
    source = connect(sqlite_database)
    warehouse = duckdb.connect(duckdb_database, read_only=True)
    try:
        reference = metrics(source)
        revenue_query = warehouse.execute("select * from mart_revenue")
        revenue = dict(zip([item[0] for item in revenue_query.description], revenue_query.fetchone()))
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
        quality = dict(zip(quality_columns, warehouse.execute(
            "select * from mart_measurement_health"
        ).fetchone()))
        expected_quality = measurement_health(source)
        assert quality["duplicate_contact_rows"] == expected_quality["duplicate_contact_rows"]
        assert quality["unmatched_payments"] == expected_quality["unmatched_payment_count"]
        assert quality["valid_paid_journeys"] == expected_quality["valid_paid_journeys"]
        assert round(quality["touches_with_utm"] / quality["eligible_touches"], 4) == expected_quality["utm_completeness"]
        assert round(quality["registered_touches"] / quality["eligible_touches"], 4) == expected_quality["campaign_registry_match"]

        migration_query = warehouse.execute("select * from mart_migration_summary")
        migration = dict(zip([item[0] for item in migration_query.description], migration_query.fetchone()))
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
