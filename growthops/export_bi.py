"""Export verified dbt marts for Power BI and re-embed them in the editable PBIP project.

``export`` writes one CSV per governed mart. ``refresh_pbip`` rewrites each TMDL
table's embedded import partition from those CSVs, appends any new columns (with
deterministic lineage tags) and registers new tables, while leaving every
hand-authored measure and existing column untouched. Run both after a dbt build
so the Power BI model always carries the same numbers as the warehouse.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import uuid
import zlib
from pathlib import Path

MARTS = (
    "mart_growth_daily", "mart_campaign_performance", "mart_funnel", "mart_revenue",
    "mart_content_performance", "mart_measurement_health", "mart_migration_summary",
    "mart_experiment_variants", "mart_renewal_risk", "mart_revenue_bridge", "mart_platform_comparison",
)
ORDER_BY = {
    "mart_growth_daily": "day",
    "mart_campaign_performance": "net_cash_cents DESC, campaign_id",
    "mart_funnel": "ordinal",
    "mart_content_performance": "influenced_net_cash_cents DESC, content_id",
    "mart_experiment_variants": "variant_id",
    "mart_renewal_risk": "due_date, subscription_id",
    "mart_revenue_bridge": "ordinal",
    "mart_platform_comparison": "platform",
}
PROJECT = Path("dashboards/powerbi-project/GrowthOpsOS.SemanticModel/definition")
M_TYPES = {"int64": "Int64.Type", "double": "type number", "dateTime": "type date", "string": "type text",
           "boolean": "type logical"}


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


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    return rows[0], rows[1:]


def _infer_type(name: str, values: list[str]) -> str:
    present = [value for value in values if value != ""]
    if name in ("day", "due_date") or name.endswith("_date"):
        return "dateTime"
    if present and all(re.fullmatch(r"-?\d+", value) for value in present):
        return "int64"
    if present and all(re.fullmatch(r"-?\d+(\.\d+)?", value) for value in present):
        return "double"
    if present and all(value in ("True", "False", "true", "false") for value in present):
        return "boolean"
    return "string"


def _lineage(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "growthops-pbip:" + ":".join(parts)))


def _column_block(table: str, column: str, data_type: str) -> str:
    return (f"\tcolumn {column}\n\t\tdataType: {data_type}\n\t\tlineageTag: {_lineage(table, column)}\n"
            f"\t\tsummarizeBy: none\n\t\tsourceColumn: {column}\n\n")


def _partition(table: str, columns: list[str], rows: list[list[str]], types: dict[str, str]) -> str:
    payload = json.dumps(rows, separators=(",", ":")).encode()
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    encoded = base64.b64encode(compressor.compress(payload) + compressor.flush()).decode()
    schema = ", ".join(f"{column} = nullable text" for column in columns)
    conversions = ", ".join(f'{{"{column}", {M_TYPES[types[column]]}}}' for column in columns)
    return (
        f"\tpartition {table} = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet\n"
        f'\t\t\t\t  Source = Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText("{encoded}", '
        f"BinaryEncoding.Base64), Compression.Deflate)), let _t = ((type nullable text) meta "
        f"[Serialized.Text = true]) in type table [{schema}]),\n"
        f'\t\t\t\t  #"Changed column type" = Table.TransformColumnTypes(Source, {{{conversions}}})\n'
        f'\t\t\t\tin\n\t\t\t\t  #"Changed column type"\n'
    )


def refresh_pbip(csv_dir: str = "dashboards/powerbi-data", project: Path = PROJECT) -> dict[str, int]:
    tables_dir = project / "tables"
    refreshed = {}
    for mart in MARTS:
        columns, rows = _read_csv(Path(csv_dir) / f"{mart}.csv")
        path = tables_dir / f"{mart}.tmdl"
        text = path.read_text(encoding="utf-8") if path.exists() else \
            f"table {mart}\n\tlineageTag: {_lineage(mart)}\n\n"
        existing = dict(re.findall(r"\n\tcolumn (\w+)\n\t\tdataType: (\w+)", text))
        types = {column: existing.get(column) or _infer_type(column, [row[i] for row in rows])
                 for i, column in enumerate(columns)}
        text = text.split("\tpartition ", 1)[0].rstrip("\n") + "\n\n"
        missing = [column for column in columns if column not in existing]
        if missing:
            blocks = "".join(_column_block(mart, column, types[column]) for column in missing)
            anchor = text.find("\n\tmeasure ")
            text = text + blocks if anchor == -1 else text[:anchor + 1] + blocks + text[anchor + 1:]
        text = text.rstrip("\n") + "\n\n" + _partition(mart, columns, rows, types)
        path.write_text(text, encoding="utf-8")
        refreshed[mart] = len(rows)
    model = project / "model.tmdl"
    text = model.read_text(encoding="utf-8")
    names = sorted(set(re.findall(r"^ref table (\w+)$", text, re.MULTILINE)) | set(MARTS))
    text = re.sub(r"annotation PBI_QueryOrder = \[.*?\]", "annotation PBI_QueryOrder = " + json.dumps(names), text)
    text = re.sub(r"(ref table \w+\n?)+", "".join(f"ref table {name}\n" for name in names), text)
    model.write_text(text, encoding="utf-8")
    return refreshed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="data/growthops-warehouse.duckdb")
    parser.add_argument("--output", default="data/powerbi")
    parser.add_argument("--refresh-pbip", action="store_true",
                        help="write the versioned CSVs and re-embed them in the PBIP project")
    args = parser.parse_args()
    output = "dashboards/powerbi-data" if args.refresh_pbip else args.output
    print(export(args.warehouse, output))
    if args.refresh_pbip:
        print(refresh_pbip(output))


if __name__ == "__main__":
    main()
