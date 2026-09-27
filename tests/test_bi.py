import base64
import csv
import json
import re
import zlib
from pathlib import Path

import openpyxl

from growthops.export_bi import MARTS, PROJECT
from growthops.export_excel import build

ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = ROOT / "dashboards" / "powerbi-data"


def test_every_power_bi_partition_embeds_the_committed_mart_exactly():
    model = (ROOT / PROJECT / "model.tmdl").read_text(encoding="utf-8")
    for mart in MARTS:
        assert f"ref table {mart}" in model, mart
        tmdl = (ROOT / PROJECT / "tables" / f"{mart}.tmdl").read_text(encoding="utf-8")
        encoded = re.search(r'Binary\.FromText\("([^"]+)"', tmdl).group(1)
        embedded = json.loads(zlib.decompress(base64.b64decode(encoded), -15))
        with (CSV_DIR / f"{mart}.csv").open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.reader(file))
        assert embedded == rows[1:], mart
        for column in rows[0]:
            assert f"\tcolumn {column}\n" in tmdl, f"{mart}.{column}"


def test_excel_dashboard_is_formula_driven_and_audited(tmp_path):
    workbook = openpyxl.load_workbook(build(tmp_path / "dashboard.xlsx"))
    dashboard = workbook["Dashboard"]
    kpis = [dashboard.cell(row, 2).value for row in range(4, 15)]
    assert all(isinstance(value, str) and value.startswith("=") for value in kpis)
    audit = workbook["Audit"]
    checks = [audit.cell(row, 2).value for row in range(3, audit.max_row + 1) if audit.cell(row, 1).value]
    assert len(checks) >= 6 and all(str(value).startswith("=") for value in checks)
    assert workbook["Period analysis"]["B9"].value.startswith("=SUMIFS(")
    committed = ROOT / "dashboards" / "GrowthOps_OS_Excel_Dashboard.xlsx"
    assert (tmp_path / "dashboard.xlsx").read_bytes() == committed.read_bytes(), \
        "Excel workbook is stale: run python -m growthops.export_excel"


def test_power_bi_model_has_date_dimension_relationships_and_documented_measures():
    from growthops.export_bi import DATE_KEYS, MEASURES

    definition = ROOT / PROJECT
    assert "ref table dim_date" in (definition / "model.tmdl").read_text(encoding="utf-8")
    date_table = (definition / "tables" / "dim_date.tmdl").read_text(encoding="utf-8")
    assert "partition dim_date = calculated" in date_table and "\t\tisKey\n" in date_table
    relationships = (definition / "relationships.tmdl").read_text(encoding="utf-8")
    for table, column in DATE_KEYS.items():
        assert f"fromColumn: {table}.{column}\n\ttoColumn: dim_date.Date" in relationships
    for table, measures in MEASURES.items():
        tmdl = (definition / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
        for name, _, fmt, folder, description in measures:
            block = tmdl.split(f"measure '{name}' = ", 1)
            assert len(block) == 2, f"{table}: {name}"
            assert f"/// {description}\n\t" in block[0][-len(description) - 12:], name
            assert f"formatString: {fmt}" in block[1][:400] and f"displayFolder: {folder}" in block[1][:400]


def test_excel_workbook_has_controls_and_marketing_kpis(tmp_path):
    workbook = openpyxl.load_workbook(build(tmp_path / "dashboard.xlsx"))
    assert {"Marketing KPIs", "Paid daily", "Email", "Links", "Definitions"} <= set(workbook.sheetnames)
    assert set(workbook.defined_names) >= {"StartDate", "EndDate"}
    analysis = workbook["Period analysis"]
    assert str(analysis.data_validations.dataValidation[0].sqref) == "B4:B5"
    assert not analysis["B4"].protection.locked and analysis["B9"].protection.locked
    assert all(sheet.protection.sheet for sheet in workbook.worksheets)
    kpis = workbook["Marketing KPIs"]
    assert kpis["B7"].value.startswith("=IFERROR(B5/B6") and "StartDate" in kpis["B5"].value
    assert workbook["Dashboard"]["H1"].value.startswith("=MAX(Daily!")
    definitions = [row[0] for row in workbook["Definitions"].iter_rows(min_row=4, values_only=True)]
    assert "Human open rate" in definitions and "Cost per booked call (CPDM)" in definitions


def _shape(node):
    """Key structure of a JSON document (lists collapse to their first element), for comparing visuals."""
    if isinstance(node, dict):
        return {key: _shape(value) for key, value in node.items() if key not in ("projections",)}
    if isinstance(node, list):
        return [_shape(node[0])] if node else []
    return type(node).__name__


def test_marketing_report_page_matches_existing_visual_shapes_and_model_fields():
    from growthops.export_bi import MARKETING_PAGE, REPORT

    pages = ROOT / REPORT / "pages"
    order = json.loads((pages / "pages.json").read_text())["pageOrder"]
    page = next(p for p in order if json.loads((pages / p / "page.json").read_text())["displayName"] == MARKETING_PAGE)
    existing = {}
    for path in pages.glob("*/visuals/*/visual.json"):
        if path.parts[-4] != page:
            visual = json.loads(path.read_text())
            existing.setdefault(visual["visual"]["visualType"], _shape(visual))
    tables = {path.stem: path.read_text(encoding="utf-8") for path in (ROOT / PROJECT / "tables").glob("*.tmdl")}
    new = [json.loads(path.read_text()) for path in (pages / page / "visuals").glob("*/visual.json")]
    assert {v["visual"]["visualType"] for v in new} == {"cardVisual", "clusteredBarChart", "lineChart", "tableEx"}
    for visual in new:
        assert _shape(visual) == existing[visual["visual"]["visualType"]], visual["name"]
        for role in visual["visual"]["query"]["queryState"].values():
            for projection in role["projections"]:
                kind, spec = next(iter(projection["field"].items()))
                table, name = spec["Expression"]["SourceRef"]["Entity"], spec["Property"]
                pattern = f"measure '{name}' = " if kind == "Measure" else (
                    f"\tcolumn '{name}'" if " " in name else f"\tcolumn {name}")
                assert pattern in tables[table], f"{table}.{name}"
