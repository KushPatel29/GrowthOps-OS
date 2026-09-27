"""The question contract, graded on the answers themselves rather than on routing alone.

`python -m growthops.ask_data --eval` scores which governed answer each question reaches. This runs every
contract question through `answer()` against the scenario and checks what the reader actually gets: that a
refusal is a refusal (a period outside the data is only known once the data is read), that every period
question states the period it was asked for, and that the headline figure equals the mart, recomputed here
with independent SQL.
"""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from growthops.ask_data import KPI_COLUMNS, RATIOS, _usd, answer
from growthops.ask_slots import parse, resolve_window
from growthops.db import connect
from growthops.performance import PLATFORM_LABELS, paid_efficiency

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "evals/ask_questions.json").read_text(encoding="utf-8"))["cases"]
PAID = {  # measure -> (label in the answer, paid_efficiency field, format)
    "cpl": ("cost per lead", "cost_per_lead_cents", _usd),
    "cost_per_mql": ("cost per MQL", "cost_per_mql_cents", _usd),
    "cost_per_booked_call": ("cost per booked call", "cost_per_booked_call_cents", _usd),
    "ctr": ("CTR", "ctr", lambda v: f"{v:.2%}"),
    "spend": ("spend", "spend_cents", _usd),
    "leads": ("leads", "leads", lambda v: f"{v:,}"),
}


@pytest.fixture(scope="module")
def connection(sample_path, tmp_path_factory):
    path = tmp_path_factory.mktemp("contract") / "contract.db"
    shutil.copy(sample_path, path)  # answer() writes to ask_log, so it gets its own copy
    handle = connect(path)
    yield handle
    handle.close()


@pytest.fixture(scope="module")
def span(connection):
    first, last = connection.execute("SELECT MIN(day), MAX(day) FROM mart_growth_daily").fetchone()
    return date.fromisoformat(first[:10]), date.fromisoformat(last[:10])


def _mart_value(connection, measure: str, start: date, end: date):
    def total(column):
        return connection.execute(f"SELECT COALESCE(SUM({column}), 0) FROM mart_growth_daily WHERE day BETWEEN ? AND ?",
                                  (start.isoformat(), end.isoformat())).fetchone()[0]
    if measure in RATIOS:
        num, den = (total(column) for column in RATIOS[measure])
        return num / den if den else None
    return total(KPI_COLUMNS[measure][1])


def _shown(measure: str, value) -> str:
    if measure in RATIOS:
        return "n/a" if value is None else f"{value:.1%}"
    return _usd(value) if KPI_COLUMNS[measure][2] else f"{value:,}"


def _capital(label: str) -> str:
    return label[0].upper() + label[1:]


@pytest.mark.parametrize("case", CASES, ids=[case["question"][:60] for case in CASES])
def test_every_contract_answer_says_what_was_asked(connection, span, case):
    result = answer(connection, case["question"])
    expected = case.get("answer_expect") or case["expect"]
    accepted = expected if isinstance(expected, list) else [expected]
    if accepted == ["refuse"] or accepted == ["refused"]:
        assert result["route"] == "refused", result["answer"]
        return
    got = result["metric_id"] or (result["citations"][0]["id"] if result["citations"] else None)
    assert result["route"] != "refused" and got in accepted, (got, result["answer"])
    assert "None" not in result["answer"] and "nan" not in result["answer"].split()

    slots = parse(case["question"])
    window = resolve_window(slots.window, span[1], span[0])
    if result["metric_id"] == "kpi_totals" and window and slots.measure in KPI_COLUMNS:
        value = _mart_value(connection, slots.measure, window["start"], window["end"])
        lead = f"{_capital(window['label'])}: {KPI_COLUMNS[slots.measure][0]} {_shown(slots.measure, value)}"
        assert result["answer"].startswith(lead), (lead, result["answer"][:160])
    if result["metric_id"] == "paid_efficiency" and window and slots.platform and slots.measure in PAID:
        label, field, fmt = PAID[slots.measure]
        row = next(r for r in paid_efficiency(connection, window["start"], window["end"], by="platform")
                   if r["segment"] == slots.platform)
        lead = f"{PLATFORM_LABELS[slots.platform]}, {window['label']}: {label} {fmt(row[field])}"
        assert result["answer"].startswith(lead), (lead, result["answer"][:160])
    if result["metric_id"] in ("kpi_totals", "paid_efficiency") and window:
        assert window["label"] in result["understood"], (window["label"], result["understood"])
