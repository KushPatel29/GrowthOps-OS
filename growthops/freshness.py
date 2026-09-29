"""Source freshness: how old is the newest record from each feed, against its SLA."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from growthops.config import Settings, get_settings

# source name -> (table, timestamp column, SLA in hours). Email is weekly, so its SLA is a week plus a day.
SOURCES = {
    "ad_spend": ("ad_spend_daily", "spend_date", None),
    "web_touches": ("touches", "occurred_at", None),
    "crm_lifecycle": ("lifecycle_events", "occurred_at", None),
    "payments": ("payments", "paid_at", None),
    "email_sends": ("email_campaigns", "sent_at", 8 * 24),
    "short_link_clicks": ("short_link_clicks", "click_date", None),
}


def _parse(value: str) -> datetime:
    if len(value) == 10:  # a date: the day is complete at its end
        return datetime.fromisoformat(value).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def check(connection: sqlite3.Connection, settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    now = settings.reference_time()
    results: list[dict[str, object]] = []
    for source, (table, column, sla) in SOURCES.items():
        sla = sla or settings.freshness_sla_hours
        try:
            latest = connection.execute(f"SELECT MAX({column}) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            latest = None
        if latest is None:
            results.append({"source": source, "latest": None, "age_hours": None, "sla_hours": sla, "status": "missing"})
            continue
        try:
            age = round((now - _parse(latest)).total_seconds() / 3600, 1)
        except (TypeError, ValueError):
            results.append({"source": source, "latest": latest, "age_hours": None,
                            "sla_hours": sla, "status": "invalid"})
            continue
        status = "future" if age < -1 else "fresh" if age <= sla else "stale"
        results.append({"source": source, "latest": latest, "age_hours": age, "sla_hours": sla,
                        "status": status})
    return results


def stale_sources(connection: sqlite3.Connection, settings: Settings | None = None) -> list[dict]:
    return [item for item in check(connection, settings) if item["status"] != "fresh"]
