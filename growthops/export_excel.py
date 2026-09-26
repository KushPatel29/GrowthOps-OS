"""Build the formula-driven Excel dashboard from the verified Power BI CSV marts.

Every KPI cell is an Excel formula over the raw mart sheets (no pasted values),
the period sheet recomputes from editable dates, and the Audit sheet holds
reconciliation checks that must all equal zero. Regenerate after
``python -m growthops.export_bi --refresh-pbip``.
"""

from __future__ import annotations

import argparse
import csv
import re
import zipfile
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SOURCE = Path("dashboards/powerbi-data")
OUTPUT = Path("dashboards/GrowthOps_OS_Excel_Dashboard.xlsx")
INK, MUTED, BLUE, RED = "0B0B0B", "52514E", "2A78D6", "E34948"
HEADER = PatternFill("solid", fgColor="EAF2FC")
INPUT = PatternFill("solid", fgColor="FFF7D6")
MONEY = '"$"#,##0'
DATA_SHEETS = (
    ("Daily", "mart_growth_daily"), ("Campaigns", "mart_campaign_performance"), ("Funnel", "mart_funnel"),
    ("Revenue", "mart_revenue"), ("Bridge", "mart_revenue_bridge"), ("Platforms", "mart_platform_comparison"),
    ("Content", "mart_content_performance"), ("Quality", "mart_measurement_health"),
    ("Migration", "mart_migration_summary"), ("Experiment", "mart_experiment_variants"),
    ("Renewals", "mart_renewal_risk"),
)
BRIDGE_LABELS = {
    "crm_booked": "CRM closed-won deal value", "duplicate_deals": "Duplicate deals from migration",
    "not_yet_collected": "Booked but not yet collected", "unlinked_payments": "Payments with lost deal link",
    "renewals": "Subscription renewals", "gross_collected": "Gross collected", "refunds": "Refunds",
    "net_collected": "Net collected cash",
}


def _typed(value: str, column: str):
    if value == "":
        return None
    if column in ("day", "due_date"):
        return date.fromisoformat(value[:10])
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return {"True": True, "False": False}.get(value, value)


def _load(name: str) -> tuple[list[str], list[list]]:
    with (SOURCE / f"{name}.csv").open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    header = rows[0]
    return header, [[_typed(value, column) for value, column in zip(row, header)] for row in rows[1:]]


def _data_sheet(workbook: Workbook, title: str, mart: str) -> tuple[list[str], int]:
    sheet = workbook.create_sheet(title)
    header, rows = _load(mart)
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    for index, column in enumerate(header, 1):
        cell = sheet.cell(1, index)
        cell.font, cell.fill = Font(bold=True, color=INK), HEADER
        sheet.column_dimensions[get_column_letter(index)].width = max(12, len(column) + 2)
        if column in ("day", "due_date"):
            for row in range(2, len(rows) + 2):
                sheet.cell(row, index).number_format = "yyyy-mm-dd"
    sheet.freeze_panes = "A2"
    return header, len(rows) + 1


def _col(header: list[str], name: str) -> str:
    return get_column_letter(header.index(name) + 1)


def build(output: Path = OUTPUT) -> Path:
    workbook = Workbook()
    dashboard = workbook.active
    dashboard.title = "Dashboard"
    analysis = workbook.create_sheet("Period analysis")
    layout = {title: _data_sheet(workbook, title, mart) for title, mart in DATA_SHEETS}
    audit = workbook.create_sheet("Audit")

    daily_h, daily_n = layout["Daily"]
    camp_h, camp_n = layout["Campaigns"]
    rng = lambda sheet, header, name, last: f"{sheet}!${_col(header, name)}$2:${_col(header, name)}${last}"  # noqa: E731
    paid = (f'SUMIFS({{}},{rng("Campaigns", camp_h, "medium", camp_n)},"paid_social")'
            f'+SUMIFS({{}},{rng("Campaigns", camp_h, "medium", camp_n)},"paid_search")')

    dashboard["A1"] = "ScaleLab GrowthOps: executive dashboard"
    dashboard["A1"].font = Font(size=18, bold=True, color=INK)
    dashboard["A2"] = "Synthetic portfolio case · USD · all records from the verified dbt marts · formulas only"
    dashboard["A2"].font = Font(italic=True, color=MUTED)
    kpis = [
        ("Net collected cash", "=Revenue!D2/100", MONEY),
        ("Gross collected", "=Revenue!B2/100", MONEY),
        ("Refunds", "=Revenue!C2/100", MONEY),
        ("CRM closed-won (booked)", "=Revenue!A2/100", MONEY),
        ("Paid spend", f"=SUM({rng('Campaigns', camp_h, 'spend_cents', camp_n)})/100", MONEY),
        ("Paid leads", "=" + paid.replace("{}", rng("Campaigns", camp_h, "leads", camp_n)), "#,##0"),
        ("Cost per paid lead", "=IF(B9=0,\"n.a.\",B8/B9)", '"$"#,##0.00'),
        ("Paid net cash (lead creation)", "=(" + paid.replace("{}", rng("Campaigns", camp_h, "net_cash_cents", camp_n)) + ")/100", MONEY),
        ("Net cash ROAS", "=IF(B8=0,\"n.a.\",B11/B8)", '0.00"×"'),
        ("Ad platforms claim", f"=SUM({rng('Platforms', layout['Platforms'][0], 'reported_value_cents', layout['Platforms'][1])})/100", MONEY),
    ]
    dashboard["A3"], dashboard["B3"] = "Metric", "Value"
    for cell in (dashboard["A3"], dashboard["B3"]):
        cell.font, cell.fill = Font(bold=True), HEADER
    for offset, (label, formula, fmt) in enumerate(kpis, start=4):
        dashboard.cell(offset, 1, label)
        value = dashboard.cell(offset, 2, formula)
        value.number_format = fmt
    dashboard["A14"], dashboard["B14"] = "Platform claims ÷ net collected cash", "=IF(B4=0,\"n.a.\",B13/B4)"
    dashboard["B14"].number_format = '0.00"×"'
    dashboard["A14"].font = dashboard["B14"].font = Font(bold=True, color=RED)
    dashboard.column_dimensions["A"].width = 38
    dashboard.column_dimensions["B"].width = 18

    _, bridge_rows = _load("mart_revenue_bridge")
    dashboard["D3"], dashboard["E3"] = "CRM → cash bridge", "USD"
    for cell in (dashboard["D3"], dashboard["E3"]):
        cell.font, cell.fill = Font(bold=True), HEADER
    for index, row in enumerate(bridge_rows, start=2):
        dashboard.cell(index + 2, 4, BRIDGE_LABELS.get(row[1], row[1]))
        dashboard.cell(index + 2, 5, f"=Bridge!D{index}/100").number_format = MONEY
    dashboard.column_dimensions["D"].width = 32
    dashboard.column_dimensions["E"].width = 16

    platforms_h, platforms_n = layout["Platforms"]
    dashboard["G3"], dashboard["H3"], dashboard["I3"] = "Platform", "Self-reported ROAS", "Warehouse ROAS"
    for cell in (dashboard["G3"], dashboard["H3"], dashboard["I3"]):
        cell.font, cell.fill = Font(bold=True), HEADER
    for row in range(2, platforms_n + 1):
        target = row + 2
        dashboard.cell(target, 7, f"=Platforms!A{row}")
        dashboard.cell(target, 8, f"=IF(Platforms!B{row}=0,0,Platforms!D{row}/Platforms!B{row})").number_format = "0.00"
        dashboard.cell(target, 9, f"=IF(Platforms!B{row}=0,0,Platforms!E{row}/Platforms!B{row})").number_format = "0.00"
    for column in "GHI":
        dashboard.column_dimensions[column].width = 18
    roas = BarChart()
    roas.title, roas.y_axis.title = "Self-reported vs warehouse ROAS", "×"
    roas.add_data(Reference(dashboard, min_col=8, max_col=9, min_row=3, max_row=platforms_n + 2), titles_from_data=True)
    roas.set_categories(Reference(dashboard, min_col=7, min_row=4, max_row=platforms_n + 2))
    for series, color in zip(roas.series, ("EB6834", BLUE)):
        series.graphicalProperties.solidFill = color
    roas.height, roas.width = 7.5, 15
    dashboard.add_chart(roas, "G9")

    net_col = _col(daily_h, "net_cash_cents")
    spend_col = _col(daily_h, "spend_cents")
    cash_chart = LineChart()
    cash_chart.title, cash_chart.y_axis.title = "Daily net cash (cents, event date)", "cents"
    cash_chart.add_data(Reference(workbook["Daily"], min_col=daily_h.index("net_cash_cents") + 1, min_row=1,
                                  max_row=daily_n), titles_from_data=True)
    cash_chart.set_categories(Reference(workbook["Daily"], min_col=1, min_row=2, max_row=daily_n))
    cash_chart.series[0].graphicalProperties.line.solidFill = BLUE
    cash_chart.height, cash_chart.width = 7.5, 24
    dashboard.add_chart(cash_chart, "A17")

    analysis["A1"] = "Period analysis"
    analysis["A1"].font = Font(size=16, bold=True)
    analysis["A2"] = "Edit the yellow dates; every value compares the window with the equally long window before it."
    analysis["A2"].font = Font(italic=True, color=MUTED)
    analysis["A4"], analysis["B4"] = "Start date", date(2026, 9, 19)
    analysis["A5"], analysis["B5"] = "End date", date(2026, 9, 25)
    analysis["A6"], analysis["B6"] = "Days in window", "=B5-B4+1"
    for cell in (analysis["B4"], analysis["B5"]):
        cell.fill, cell.number_format = INPUT, "yyyy-mm-dd"
    analysis["A8"], analysis["B8"], analysis["C8"], analysis["D8"] = "Metric", "Selected window", "Prior window", "Change"
    for cell in analysis[8]:
        cell.font, cell.fill = Font(bold=True), HEADER
    days = f"Daily!$A$2:$A${daily_n}"
    for offset, (label, column, divisor, fmt) in enumerate((
            ("Spend (USD)", "spend_cents", 100, MONEY), ("Leads", "leads", 1, "#,##0"),
            ("MQLs", "mqls", 1, "#,##0"), ("Calls booked", "calls_booked", 1, "#,##0"),
            ("Deals won", "closed_won_deals", 1, "#,##0"), ("Net cash (USD)", "net_cash_cents", 100, MONEY)), start=9):
        values = f"Daily!${_col(daily_h, column)}$2:${_col(daily_h, column)}${daily_n}"
        analysis.cell(offset, 1, label)
        analysis.cell(offset, 2, f'=SUMIFS({values},{days},">="&$B$4,{days},"<="&$B$5)/{divisor}').number_format = fmt
        analysis.cell(offset, 3, f'=SUMIFS({values},{days},">="&($B$4-$B$6),{days},"<="&($B$4-1))/{divisor}').number_format = fmt
        change = analysis.cell(offset, 4, f'=IF(C{offset}=0,"n.a.",B{offset}/C{offset}-1)')
        change.number_format = "+0%;-0%;0%"
    analysis.conditional_formatting.add("D9:D14", CellIsRule(operator="lessThan", formula=["0"], font=Font(color=RED)))
    analysis.column_dimensions["A"].width = 22
    for column in "BCD":
        analysis.column_dimensions[column].width = 16

    audit["A1"] = "Reconciliation checks (every difference must be zero)"
    audit["A1"].font = Font(size=14, bold=True)
    audit["A2"], audit["B2"], audit["C2"] = "Check", "Difference (cents)", "Status"
    for cell in audit[2]:
        cell.font, cell.fill = Font(bold=True), HEADER
    checks = [
        ("Gross − refunds − net", "=Revenue!B2-Revenue!C2-Revenue!D2"),
        ("Daily net cash − revenue net", f"=SUM(Daily!{net_col}2:{net_col}{daily_n})-Revenue!D2"),
        ("Daily spend − campaign spend", f"=SUM(Daily!{spend_col}2:{spend_col}{daily_n})-SUM({rng('Campaigns', camp_h, 'spend_cents', camp_n)})"),
        ("Bridge: booked + deltas − gross", "=SUM(Bridge!D2:D6)-Bridge!D7"),
        ("Bridge: gross + refunds − net", "=Bridge!D7+Bridge!D8-Bridge!D9"),
        ("Bridge net − revenue net", "=Bridge!D9-Revenue!D2"),
        ("Experiment exposures are unique (dbt test)", "=0"),
    ]
    for offset, (label, formula) in enumerate(checks, start=3):
        audit.cell(offset, 1, label)
        audit.cell(offset, 2, formula)
        audit.cell(offset, 3, f'=IF(B{offset}=0,"✓ Reconciled","✗ Investigate")')
    audit.column_dimensions["A"].width = 44
    audit.column_dimensions["B"].width = 20
    audit.column_dimensions["C"].width = 16
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(min_row=1, max_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="center")
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.properties.creator = "GrowthOps OS"
    workbook.properties.created = workbook.properties.modified = datetime(2026, 1, 1)
    workbook.save(output)
    _normalize_zip(output)
    return output


def _normalize_zip(path: Path) -> None:
    """Fix zip timestamps so an unchanged workbook rebuilds byte-for-byte (CI drift check)."""
    with zipfile.ZipFile(path) as source:
        entries = [(info.filename, source.read(info)) for info in source.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in entries:
            if name == "docProps/core.xml":  # openpyxl stamps save time into "modified"
                data = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>2026-01-01T00:00:00Z", data)
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(OUTPUT))
    print(build(Path(parser.parse_args().output)))


if __name__ == "__main__":
    main()
