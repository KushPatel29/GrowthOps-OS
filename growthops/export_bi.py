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
    "mart_paid_efficiency_daily", "mart_email_performance", "mart_link_hygiene",
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
    "mart_paid_efficiency_daily": "day, campaign_id",
    "mart_email_performance": "sent_date, email_id",
    "mart_link_hygiene": "link_id",
}
# Date-keyed tables joined to the date dimension (many-to-one, single direction).
DATE_KEYS = {"mart_growth_daily": "day", "mart_paid_efficiency_daily": "day", "mart_email_performance": "sent_date"}
PAID, EMAIL, LINKS = "'mart_paid_efficiency_daily'", "'mart_email_performance'", "'mart_link_hygiene'"
# table -> (name, DAX, format string, display folder, description). Added when missing; never overwritten.
MEASURES = {
    "mart_paid_efficiency_daily": (
        ("Daily paid spend USD", f"DIVIDE(SUM({PAID}[spend_cents]), 100)", '"$"#,0', "Paid efficiency",
         "Paid media spend in the filter context, in dollars."),
        ("Daily paid leads", f"SUM({PAID}[leads])", "#,0", "Paid efficiency",
         "Leads created by paid campaigns (activity basis)."),
        ("CPL USD", "DIVIDE([Daily paid spend USD], [Daily paid leads])", '"$"#,0.00', "Paid efficiency",
         "Cost per lead: paid spend / paid leads in the same window."),
        ("Cost per MQL USD", f"DIVIDE([Daily paid spend USD], SUM({PAID}[mqls]))", '"$"#,0.00', "Paid efficiency",
         "Paid spend / MQLs reached in the window by paid-created leads."),
        ("Cost per booked call USD", f"DIVIDE([Daily paid spend USD], SUM({PAID}[calls_booked]))", '"$"#,0.00',
         "Paid efficiency", "Paid spend / discovery calls booked in the window by paid-created leads (CPDM)."),
        ("CPM USD", f"DIVIDE([Daily paid spend USD] * 1000, SUM({PAID}[impressions]))", '"$"#,0.00', "Paid efficiency",
         "Cost per thousand impressions."),
        ("CTR", f"DIVIDE(SUM({PAID}[clicks]), SUM({PAID}[impressions]))", "0.00%", "Paid efficiency",
         "Ad clicks / impressions."),
        ("CPC USD", f"DIVIDE([Daily paid spend USD], SUM({PAID}[clicks]))", '"$"#,0.00', "Paid efficiency",
         "Paid spend / ad clicks."),
    ),
    "mart_email_performance": (
        ("Emails delivered", f"SUM({EMAIL}[delivered])", "#,0", "Email", "Delivered sends."),
        ("Human open rate", f"DIVIDE(SUM({EMAIL}[human_opens]), [Emails delivered])", "0.0%", "Email",
         "Opens excluding privacy-proxy machine opens / delivered."),
        ("Reported open rate", f"DIVIDE(SUM({EMAIL}[opens]), [Emails delivered])", "0.0%", "Email",
         "All opens / delivered; inflated by machine opens, shown only for comparison."),
        ("Email click rate", f"DIVIDE(SUM({EMAIL}[clicks]), [Emails delivered])", "0.00%", "Email",
         "Clicks / delivered (email CTR)."),
        ("Click-to-open rate", f"DIVIDE(SUM({EMAIL}[clicks]), SUM({EMAIL}[human_opens]))", "0.0%", "Email",
         "Clicks / human opens."),
        ("Bounce rate", f"DIVIDE(SUM({EMAIL}[bounces]), SUM({EMAIL}[sends]))", "0.00%", "Email",
         "Bounces / sends; above 2% flags the sending domain."),
        ("Complaint rate", f"DIVIDE(SUM({EMAIL}[spam_complaints]), [Emails delivered])", "0.000%", "Email",
         "Spam complaints / delivered; limit 0.1%."),
    ),
    "mart_link_hygiene": (
        ("Links with defects", f"COUNTROWS(FILTER({LINKS}, {LINKS}[missing_utm] || {LINKS}[unregistered_campaign] "
         f"|| {LINKS}[off_taxonomy]))", "#,0", "Tracking", "Short links with missing, unregistered or off-taxonomy UTMs."),
        ("Recent clicks on defective links share",
         f"DIVIDE(CALCULATE(SUM({LINKS}[recent_clicks]), FILTER({LINKS}, {LINKS}[missing_utm] || "
         f"{LINKS}[unregistered_campaign] || {LINKS}[off_taxonomy])), SUM({LINKS}[recent_clicks]))", "0%", "Tracking",
         "Share of last-30-day short-link clicks that land without a valid campaign."),
    ),
}
PROJECT = Path("dashboards/powerbi-project/GrowthOpsOS.SemanticModel/definition")
REPORT = Path("dashboards/powerbi-project/GrowthOpsOS.Report/definition")
VISUAL_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json"
PAGE_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json"
MARKETING_PAGE = "Paid, email and tracking - Synthetic"
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


def _measure_block(table: str, name: str, expression: str, fmt: str, folder: str, description: str) -> str:
    return (f"\t/// {description}\n\tmeasure '{name}' = {expression}\n\t\tformatString: {fmt}\n"
            f"\t\tdisplayFolder: {folder}\n\t\tlineageTag: {_lineage(table, 'measure', name)}\n\n")


def _drop_generated_measures(table: str, text: str) -> str:
    """Remove measures this module wrote (their lineage tag is derived from the name), so renamed
    or retired ones do not linger; measures written by hand in Desktop keep random tags and stay."""
    def keep(match: re.Match) -> str:
        return "" if match["tag"] == _lineage(table, "measure", match["name"]) else match[0]
    pattern = r"(?:\t/// [^\n]*\n)?\tmeasure '(?P<name>[^']+)' = (?:(?!\n\tmeasure ).)*?\n\t\tlineageTag: (?P<tag>[0-9a-f-]+)\n\n"
    return re.sub(pattern, keep, text, flags=re.DOTALL)


def _date_table(first: str, last: str) -> str:
    start, end = first[:7] + "-01", last[:4] + "-12-31"
    y1, m1, _ = (int(part) for part in start.split("-"))
    y2 = int(end[:4])
    return (
        f"table dim_date\n\tlineageTag: {_lineage('dim_date')}\n\tdataCategory: Time\n\n"
        f"\tcolumn Date\n\t\tdataType: dateTime\n\t\tisKey\n\t\tformatString: yyyy-mm-dd\n"
        f"\t\tlineageTag: {_lineage('dim_date', 'Date')}\n\t\tsummarizeBy: none\n\t\tisNameInferred\n"
        f"\t\tsourceColumn: [Date]\n\n"
        f"\tcolumn Month = FORMAT('dim_date'[Date], \"yyyy-mm\")\n\t\tdataType: string\n"
        f"\t\tlineageTag: {_lineage('dim_date', 'Month')}\n\t\tsummarizeBy: none\n\n"
        f"\tcolumn 'Week start' = 'dim_date'[Date] - WEEKDAY('dim_date'[Date], 3)\n\t\tdataType: dateTime\n"
        f"\t\tformatString: yyyy-mm-dd\n\t\tlineageTag: {_lineage('dim_date', 'Week start')}\n"
        f"\t\tsummarizeBy: none\n\n"
        f"\tpartition dim_date = calculated\n\t\tmode: import\n"
        f"\t\tsource = CALENDAR(DATE({y1}, {m1}, 1), DATE({y2}, 12, 31))\n"
    )


def _relationships() -> str:
    return "".join(f"relationship {_lineage('relationship', table)}\n\tfromColumn: {table}.{column}\n"
                   f"\ttoColumn: dim_date.Date\n\n" for table, column in DATE_KEYS.items())


def _id(*parts: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, "growthops-pbir:" + ":".join(parts)).hex[:20]


def _field(kind: str, table: str, name: str) -> dict:
    return {"field": {kind: {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}},
            "queryRef": f"{table}.{name}", "nativeQueryRef": name}


def _visual(page: str, key: str, visual_type: str, position: tuple[int, int, int, int, int], title: str,
            roles: dict[str, list[dict]]) -> dict:
    x, y, width, height, z = position
    literal = lambda value: {"expr": {"Literal": {"Value": value}}}  # noqa: E731
    return {
        "$schema": VISUAL_SCHEMA,
        "name": _id(page, key),
        "position": {"x": x, "y": y, "z": z, "width": width, "height": height, "tabOrder": z},
        "visual": {
            "visualType": visual_type,
            "query": {"queryState": {role: {"projections": fields} for role, fields in roles.items()}},
            "visualContainerObjects": {"title": [{"properties": {"text": literal(f"'{title}'"),
                                                                 "show": literal("true")}}]},
        },
    }


def write_marketing_page(report: Path = REPORT) -> str:
    """A report page over the paid, email and link tables, generated in the same PBIR shape as the other pages."""
    page = _id("page", "marketing")
    paid, email, links = "mart_paid_efficiency_daily", "mart_email_performance", "mart_link_hygiene"
    cards = [(paid, "CPL USD", "Cost per lead"), (paid, "Cost per MQL USD", "Cost per MQL"),
             (paid, "Cost per booked call USD", "Cost per booked call"), (paid, "CTR", "Ad CTR"),
             (email, "Human open rate", "Email human open rate"), (email, "Bounce rate", "Email bounce rate")]
    visuals = [_visual(page, f"card-{index}", "cardVisual", (20 + index * 207, 20, 195, 120, 5000 + index), title,
                       {"Data": [_field("Measure", table, measure)]})
               for index, (table, measure, title) in enumerate(cards)]
    visuals.append(_visual(page, "cost-per-call", "clusteredBarChart", (20, 160, 610, 270, 1000),
                           "Cost per booked call by paid campaign (USD)",
                           {"Category": [_field("Column", paid, "campaign_id")],
                            "Y": [_field("Measure", paid, "Cost per booked call USD")]}))
    visuals.append(_visual(page, "email-trend", "lineChart", (650, 160, 610, 270, 2000),
                           "Email human open rate and bounce rate by week",
                           {"Category": [_field("Column", "dim_date", "Week start")],
                            "Y": [_field("Measure", email, "Human open rate"), _field("Measure", email, "Bounce rate")]}))
    visuals.append(_visual(page, "links", "tableEx", (20, 450, 1240, 250, 3000), "Short links against the registry",
                           {"Values": [_field("Column", links, name) for name in (
                               "link_id", "channel", "utm_source", "utm_medium", "utm_campaign", "missing_utm",
                               "unregistered_campaign", "off_taxonomy", "recent_clicks")]}))
    folder = report / "pages" / page
    if folder.exists():
        for old in (folder / "visuals").glob("*/visual.json"):
            old.unlink()
    for visual in visuals:
        target = folder / "visuals" / visual["name"] / "visual.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(visual, indent=2) + "\n", encoding="utf-8")
    (folder / "page.json").write_text(json.dumps({"$schema": PAGE_SCHEMA, "name": page, "displayName": MARKETING_PAGE,
                                                  "displayOption": "FitToPage", "width": 1280, "height": 720},
                                                 indent=2) + "\n", encoding="utf-8")
    pages_file = report / "pages" / "pages.json"
    pages = json.loads(pages_file.read_text(encoding="utf-8"))
    if page not in pages["pageOrder"]:
        pages["pageOrder"].append(page)
        pages_file.write_text(json.dumps(pages, indent=2) + "\n", encoding="utf-8")
    return page


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
        text = _drop_generated_measures(mart, text)
        present = {name.lower() for name in re.findall(r"\n\tmeasure '([^']+)'", text)}
        additions = "".join(_measure_block(mart, *spec) for spec in MEASURES.get(mart, ())
                            if spec[0].lower() not in present)
        text = text.rstrip("\n") + "\n\n" + additions + _partition(mart, columns, rows, types)
        path.write_text(text, encoding="utf-8")
        refreshed[mart] = len(rows)
    _, daily = _read_csv(Path(csv_dir) / "mart_growth_daily.csv")
    (tables_dir / "dim_date.tmdl").write_text(_date_table(daily[0][0], daily[-1][0]), encoding="utf-8")
    (project / "relationships.tmdl").write_text(_relationships(), encoding="utf-8")
    model = project / "model.tmdl"
    text = model.read_text(encoding="utf-8")
    names = sorted(set(re.findall(r"^ref table (\w+)$", text, re.MULTILINE)) | set(MARTS) | {"dim_date"})
    text = re.sub(r"annotation PBI_QueryOrder = \[.*?\]", "annotation PBI_QueryOrder = " + json.dumps(names), text)
    text = re.sub(r"(ref table \w+\n?)+", "".join(f"ref table {name}\n" for name in names), text)
    model.write_text(text, encoding="utf-8")
    if project == PROJECT:
        write_marketing_page()
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
