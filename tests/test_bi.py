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
