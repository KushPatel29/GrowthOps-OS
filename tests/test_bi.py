"""The governed BI snapshot and the Excel workbook built on it.

The workbook is checked three ways: its structure (formulas, named ranges, controls,
protection), that the committed copy carries the same formulas as a fresh build, and
that the values Excel calculated for the committed copy equal the same figures
computed here in Python from the CSVs. The last is what makes "every figure is a
formula" worth saying: the formulas are right, not just present.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import openpyxl
import pytest

from growthops.export_bi import DATE_KEYS, TABLES, date_dimension
from growthops.export_excel import DATA_SHEETS, REPORT_SHEETS, WINDOWS, build

ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = ROOT / "dashboards" / "powerbi-data"
COMMITTED = ROOT / "dashboards" / "GrowthOps_OS_Excel_Dashboard.xlsx"


def _rows(name: str) -> list[dict]:
    with (CSV_DIR / f"{name}.csv").open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return openpyxl.load_workbook(build(tmp_path_factory.mktemp("xl") / "workbook.xlsx"))


# --------------------------------------------------------------------------- the snapshot


def test_every_bi_table_is_exported():
    assert {path.stem for path in CSV_DIR.glob("*.csv")} == set(TABLES)


def test_attribution_conserves_net_cash_to_the_cent():
    attributed = sum(int(r["net_cash_cents"]) for r in _rows("fact_cash_attribution"))
    assert attributed == int(_rows("mart_revenue")[0]["net_collected_cents"])
    assert attributed == sum(int(r["net_cash_cents"]) for r in _rows("mart_growth_daily"))


def test_the_calendar_covers_every_dated_fact():
    days = {r["date"] for r in _rows("dim_date")}
    for table, column in DATE_KEYS.items():
        assert {r[column] for r in _rows(table)} <= days, table


def test_date_dimension_windows():
    header, rows = date_dimension(date(2026, 1, 1), date(2026, 3, 31))
    last = [r for r in rows if r[header.index("is_last_28_days")]]
    prior = [r for r in rows if r[header.index("is_prior_28_days")]]
    assert len(last) == len(prior) == 28 and last[-1][0] == "2026-03-31" and prior[-1][0] == "2026-03-03"
    assert rows[0][header.index("month_index")] == 0 and rows[-1][header.index("month_index")] == 2


# --------------------------------------------------------------------------- structure


def test_sheets_controls_and_names(built):
    assert built.sheetnames == [*REPORT_SHEETS, *(title for title, _, _ in DATA_SHEETS)]
    names = set(built.defined_names)
    assert {"AsOf", "WinStart", "WinEnd", "PriorStart", "PriorEnd", "WindowList", "AllChecksPass"} <= names
    assert {"pd_spend_cents", "em_bounces", "att_net_cash_cents", "dly_day"} <= names
    dashboard = built["Dashboard"]
    assert dashboard["E5"].value == "Last 28 days"
    choice = next(v for v in dashboard.data_validations.dataValidation if str(v.sqref) == "E5")
    assert choice.type == "list" and choice.formula1 == "=WindowList"
    assert [built["Calc"].cell(r, 5).value for r in range(6, 6 + len(WINDOWS))] == list(WINDOWS)
    for cell in ("E5", "I5", "L5"):
        assert not dashboard[cell].protection.locked
    assert dashboard["B9"].protection.locked
    assert all(sheet.protection.sheet for sheet in built.worksheets)


def test_report_figures_are_formulas_over_named_ranges(built):
    dashboard = built["Dashboard"]
    for col in ("B", "E", "H", "K", "N", "Q"):
        assert str(dashboard[f"{col}9"].value).startswith("=") and "WinStart" in dashboard[f"{col}9"].value
    scorecard = built["Campaign scorecard"]
    assert "pd_spend_cents" in scorecard["D7"].value and "$B7" in scorecard["D7"].value
    audit = built["Audit"]
    checks = [audit.cell(r, 3).value for r in range(7, 21)]
    assert all(str(c).startswith("=") and c != "=0" for c in checks)


def test_the_committed_workbook_has_the_formulas_of_a_fresh_build(built):
    delivered = openpyxl.load_workbook(COMMITTED)
    assert delivered.sheetnames == built.sheetnames
    assert set(delivered.defined_names) == set(built.defined_names)
    for fresh, committed in zip(built.worksheets, delivered.worksheets):
        assert len(committed._charts) == len(fresh._charts), fresh.title
        for fresh_row, committed_row in zip(fresh.iter_rows(), committed.iter_rows()):
            assert [c.value for c in committed_row] == [c.value for c in fresh_row], fresh.title


# --------------------------------------------------------------------------- values


@pytest.fixture(scope="module")
def cached():
    return openpyxl.load_workbook(COMMITTED, data_only=True)


def _window(rows, column, start, end, key="day", where=None):
    return sum(float(r[column]) for r in rows
               if start <= r[key] <= end and (where is None or where(r)))


def test_the_committed_workbook_was_calculated_and_reconciles(cached):
    assert cached["Audit"]["C22"].value == "Yes"
    assert [cached["Audit"].cell(r, 3).value for r in range(7, 21)] == [0] * 14


def test_dashboard_values_equal_python_on_the_same_csvs(cached):
    daily, paid, email = _rows("mart_growth_daily"), _rows("mart_paid_efficiency_daily"), _rows(
        "mart_email_performance")
    as_of = date.fromisoformat(max(r["day"] for r in daily))
    start, end = (as_of - timedelta(days=27)).isoformat(), as_of.isoformat()
    prior_start, prior_end = (as_of - timedelta(days=55)).isoformat(), (as_of - timedelta(days=28)).isoformat()
    sheet = cached["Dashboard"]
    assert sheet["B9"].value == pytest.approx(_window(daily, "net_cash_cents", start, end) / 100)
    spend = _window(paid, "spend_cents", start, end) / 100
    leads = _window(paid, "leads", start, end)
    assert sheet["E9"].value == pytest.approx(spend)
    assert sheet["H9"].value == pytest.approx(spend / leads)
    assert sheet["K9"].value == pytest.approx(_window(paid, "mqls", start, end) / leads)
    bounce = _window(email, "bounces", start, end, "sent_date") / _window(email, "sends", start, end, "sent_date")
    assert sheet["Q9"].value == pytest.approx(bounce)
    prior = _window(daily, "net_cash_cents", prior_start, prior_end) / 100
    assert sheet["B11"].value == pytest.approx(sheet["B9"].value / prior - 1)
    # The written summary names the planted lead-quality incident without being told about it.
    assert "meta_broad_v17" in cached["Dashboard"]["B16"].value


def test_scorecard_rows_equal_python(cached):
    paid = _rows("mart_paid_efficiency_daily")
    as_of = date.fromisoformat(max(r["day"] for r in _rows("mart_growth_daily")))
    start, end = (as_of - timedelta(days=27)).isoformat(), as_of.isoformat()
    sheet = cached["Campaign scorecard"]
    for row in range(7, 14):
        campaign = sheet.cell(row, 2).value
        spend = _window(paid, "spend_cents", start, end, where=lambda r: r["campaign_id"] == campaign) / 100
        assert sheet.cell(row, 4).value == pytest.approx(spend), campaign
