"""Period-based executive brief from the daily growth mart."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

METRICS = (
    "spend_cents", "leads", "mqls", "calls_booked", "booked_cents",
    "gross_collected_cents", "refunds_cents", "net_cash_cents",
)


def daily_series(connection: sqlite3.Connection, days: int = 90) -> list[dict]:
    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")
    rows = connection.execute(
        "SELECT * FROM mart_growth_daily ORDER BY day DESC LIMIT ?", (days,)
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


def _window(connection: sqlite3.Connection, start: date, end: date) -> dict[str, int]:
    columns = ", ".join(f"COALESCE(SUM({metric}),0) {metric}" for metric in METRICS)
    row = connection.execute(
        f"SELECT {columns} FROM mart_growth_daily WHERE day BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    return {metric: int(row[metric]) for metric in METRICS}


def period_brief(connection: sqlite3.Connection, days: int = 7, end: date | None = None) -> dict:
    if not 1 <= days <= 30:
        raise ValueError("days must be between 1 and 30")
    if end is None:
        latest = connection.execute(
            "SELECT COALESCE(MAX(CASE WHEN spend_cents>0 THEN day END),MAX(day)) FROM mart_growth_daily"
        ).fetchone()[0]
        if latest is None:
            raise ValueError("daily mart is empty")
        end = date.fromisoformat(latest)
    start = end - timedelta(days=days - 1)
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=days - 1)
    current = _window(connection, start, end)
    previous = _window(connection, prior_start, prior_end)
    latest_event_date = connection.execute("SELECT MAX(day) FROM mart_growth_daily").fetchone()[0]
    change_pct = {
        metric: round((current[metric] - previous[metric]) / previous[metric], 4)
        if previous[metric] else None for metric in METRICS
    }
    findings = []
    if current["spend_cents"] > 0 and current["leads"] == 0:
        findings.append({
            "finding": "Spend continued without recorded leads.",
            "evidence": f"{days} days recorded ${current['spend_cents']/100:,.2f} of paid spend and zero leads; the prior period recorded {previous['leads']} leads.",
            "investigation": "Check form delivery, tagging and CRM ingestion before changing bids.",
            "confidence": "high for the measurement gap; cause unconfirmed",
        })
    elif previous["leads"] and current["leads"] < previous["leads"] * 0.75:
        findings.append({
            "finding": "Lead volume fell by more than 25%.",
            "evidence": f"Leads changed from {previous['leads']} to {current['leads']} across equal {days}-day windows.",
            "investigation": "Decompose by campaign and landing page, then check tracking health.",
            "confidence": "high for the change; cause unconfirmed",
        })
    return {
        "basis": "synthetic event date; cash refunds booked on refund date",
        "anchor": "latest day with paid spend",
        "latest_event_date": latest_event_date,
        "current": {"start": start.isoformat(), "end": end.isoformat(), **current},
        "previous": {"start": prior_start.isoformat(), "end": prior_end.isoformat(), **previous},
        "change_pct": change_pct,
        "findings": findings,
    }
