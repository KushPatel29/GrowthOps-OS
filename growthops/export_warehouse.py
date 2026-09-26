"""Export deterministic synthetic source tables as dbt seed inputs."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from growthops.db import connect, initialize

TABLES = (
    "campaigns", "ad_spend_daily", "content_items", "contacts", "legacy_contacts",
    "touches", "content_engagements", "lifecycle_events", "deals", "payments", "refunds",
    "experiments", "experiment_variants", "experiment_exposures"
)


def export(database: str, output: str = "warehouse/dbt/seeds") -> dict[str, int]:
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    connection = connect(database)
    try:
        initialize(connection)
        counts = {}
        for table in TABLES:
            columns = [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]
            rows = connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            with (destination / f"raw_{table}.csv").open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(columns)
                writer.writerows(tuple(row) for row in rows)
            counts[table] = len(rows)
        return counts
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--output", default="warehouse/dbt/seeds")
    args = parser.parse_args()
    print(export(args.database, args.output))


if __name__ == "__main__":
    main()
