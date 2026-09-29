"""Alert delivery: brief findings and stale sources to a Slack-compatible webhook, deduplicated.

An alert's key is its finding ID plus a hash of its text, so a finding whose
numbers change is sent again, while an unchanged one is repeated at most once per
``RENOTIFY_HOURS``. Without ``GROWTHOPS_ALERT_WEBHOOK_URL`` alerts are recorded as
``dry_run`` and logged, which is the safe default for development.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from growthops.adapters import ProviderError, Transport, urllib_transport
from growthops.config import Settings
from growthops.observability import log

RENOTIFY_HOURS = 24
logger = logging.getLogger("growthops.alerts")


def candidates(connection: sqlite3.Connection, settings: Settings, findings: list[dict] | None = None) -> list[dict]:
    from growthops.brief import findings as brief_findings
    from growthops.freshness import stale_sources

    items = [{"key": item["id"], "title": item["finding"], "detail": item["evidence"],
              "action": item["investigation"], "priority": item["priority"]}
             for item in (findings if findings is not None else brief_findings(connection))
             if item["priority"] >= settings.alert_min_priority]
    for source in stale_sources(connection, settings):
        items.append({"key": f"stale_{source['source']}", "priority": 90,
                      "title": f"Source {source['source']} is {source['status']}.",
                      "detail": f"Latest record {source['latest']} ({source['age_hours']} h old; SLA "
                                f"{source['sla_hours']} h).",
                      "action": "Check the ingestion job and the provider's API status before trusting today's numbers."})
    return sorted(items, key=lambda item: -item["priority"])


def _text(alert: dict) -> str:
    return f"*GrowthOps alert:* {alert['title']}\n{alert['detail']}\n*Next:* {alert['action']}"


def post_message(settings: Settings, text: str, transport: Transport = urllib_transport) -> str:
    """Send one message; returns 'sent', 'dry_run' or 'failed'."""
    if not settings.alert_webhook_url:
        log(logger, logging.INFO, "alert (dry run)", text_sha256=hashlib.sha256(text.encode()).hexdigest())
        return "dry_run"
    try:
        status, _body = transport("POST", settings.alert_webhook_url, {"Content-Type": "application/json"},
                                 json.dumps({"text": text}).encode(), settings.adapter_timeout_seconds)
    except ProviderError as exc:
        log(logger, logging.ERROR, "alert delivery failed", error=str(exc))
        return "failed"
    if 200 <= status < 300:
        return "sent"
    log(logger, logging.ERROR, "alert delivery failed", status=status)
    return "failed"


def deliver(connection: sqlite3.Connection, settings: Settings, alerts: list[dict], *,
            now: datetime | None = None, transport: Transport = urllib_transport) -> list[dict]:
    """Send new or changed alerts; returns what was attempted."""
    now = now or datetime.now(timezone.utc)
    attempted = []
    for alert in alerts:
        digest = hashlib.sha256(_text(alert).encode()).hexdigest()[:12]
        key = f"{alert['key']}:{digest}"
        previous = connection.execute("SELECT last_sent_at, status FROM alert_log WHERE alert_key=?", (key,)).fetchone()
        if previous and previous["status"] != "failed" and \
                now - datetime.fromisoformat(previous["last_sent_at"]) < timedelta(hours=RENOTIFY_HOURS):
            continue
        status = post_message(settings, _text(alert), transport)
        connection.execute(
            """INSERT INTO alert_log VALUES (?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(alert_key) DO UPDATE SET last_sent_at=excluded.last_sent_at, sends=sends+1,
                 status=excluded.status""",
            (key, now.isoformat(), now.isoformat(), "webhook" if settings.alert_webhook_url else "log",
             status, json.dumps(alert)))
        attempted.append({"key": key, "status": status})
    return attempted
