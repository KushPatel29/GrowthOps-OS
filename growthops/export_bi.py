"""Export verified dbt marts as Power BI import tables."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

MARTS = (
    "mart_growth_daily", "mart_campaign_performance", "mart_funnel", "mart_revenue",
    "mart_content_performance", "mart_measurement_health", "mart_migration_summary",
    "mart_experiment_variants", "mart_renewal_risk",
)
ORDER_BY = {
    "mart_growth_daily": "day",
    "mart_campaign_performance": "net_cash_cents DESC, campaign_id",
    "mart_funnel": "ordinal",
    "mart_content_performance": "influenced_net_cash_cents DESC, content_id",
    "mart_experiment_variants": "variant_id",
    "mart_renewal_risk": "due_date, subscription_id",
}


def export(warehouse_database: str, output: str = "data/powerbi") -> dict[str, int]:
    import duckdb  # Installed with the optional warehouse dependency.

    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(warehouse_database, read_only=True)
    try:
        counts = {}
        for mart in MARTS:
            ordering = f" ORDER BY {ORDER_BY[mart]}" if mart in ORDER_BY else ""
            result = connection.execute(f"SELECT * FROM {mart}{ordering}")
            columns = [item[0] for item in result.description]
            rows = result.fetchall()
            with (destination / f"{mart}.csv").open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(columns)
                writer.writerows(rows)
            counts[mart] = len(rows)
        return counts
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="data/growthops-warehouse.duckdb")
    parser.add_argument("--output", default="data/powerbi")
    args = parser.parse_args()
    print(export(args.warehouse, args.output))


if __name__ == "__main__":
    main()
