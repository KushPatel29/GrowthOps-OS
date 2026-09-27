"""Executive Morning Brief: what changed, why, and what to do next.

Every sentence is assembled from computed evidence (anomaly episodes, exact
decompositions, operations health, renewal risk and data-quality checks). The
brief never states a cause as fact: drivers are arithmetic contributions, and
the recommended action is phrased as an investigation.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from growthops.diagnostics import BASELINE, detect, share_phrase
from growthops.email_analytics import deliverability_finding
from growthops.renewals import monitor as renewal_monitor
from growthops.report import campaign_performance, executive_brief
from growthops.scenario import AS_OF
from growthops.workflow import MAX_ATTEMPTS
from growthops.workflow import health as workflow_health

METRICS = (
    "spend_cents", "leads", "mqls", "calls_booked", "closed_won_deals", "booked_cents",
    "gross_collected_cents", "refunds_cents", "net_cash_cents",
)
SEGMENT_LABELS = {"(unattributed)": "untracked (no UTM)"}
# When several metrics share a driver, the most decision-relevant one leads the finding.
ACTION_ORDER = ("mql_rate", "utm_completeness", "leads", "spend", "net_cash")


def daily_series(connection: sqlite3.Connection, days: int = 90) -> list[dict]:
    if not 1 <= days <= 500:
        raise ValueError("days must be between 1 and 500")
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


def _driver_sentence(episode: dict) -> str:
    parts = []
    for driver in episode["top_drivers"][:2]:
        segment = SEGMENT_LABELS.get(driver["segment"], driver["segment"])
        new = " (new in this period)" if driver["new_segment"] else ""
        parts.append(f"{segment}{new} explains {share_phrase(driver['share_of_change'], driver['contribution_text'])}")
    return "; ".join(parts)


def _action(episode: dict, campaigns: dict[str, dict], unassigned_cents: int) -> str:
    top = episode["top_drivers"][0]["segment"] if episode["top_drivers"] else None
    metric, direction = episode["metric_id"], episode["direction"]
    if metric == "mql_rate" and direction == "down" and top in campaigns:
        row = campaigns[top]
        account = [r for r in campaigns.values() if r["mqls"] and r["spend_cents"]]
        account_cpm = sum(r["spend_cents"] for r in account) / sum(r["mqls"] for r in account)
        own = f"${row['spend_cents'] / row['mqls'] / 100:,.0f}" if row["mqls"] else "no MQLs yet"
        return (f"Review {top}'s audience and landing-page match before adding budget: its cost per MQL is "
                f"{own} against ${account_cpm / 100:,.0f} for the rest of paid media.")
    if metric == "utm_completeness" and direction == "down":
        return (f"Check the latest release of {top}: its form must pass UTM parameters to the CRM. "
                f"${unassigned_cents / 100:,.0f} of net cash currently cannot be credited to any campaign.")
    if metric == "leads" and direction == "up":
        return f"Judge the extra volume from {top} on MQLs and cash, not lead count."
    if metric == "spend" and direction == "up":
        return f"Confirm the spend increase on {top} was planned and is paying back in qualified leads."
    if metric == "net_cash" and direction == "up":
        return "Check whether a launch or deadline explains the spike before extrapolating it into the forecast."
    return f"Decompose {episode['label'].lower()} by {episode['dimension']} and check tracking health first."


def _confidence(episode: dict) -> str:
    share = episode["top_drivers"][0]["share_of_change"] if episode["top_drivers"] else 0
    if share >= 0.6 and episode["peak_z"] >= 4:
        return "high that the change is real and concentrated; cause not yet confirmed"
    return "medium: the change is unusual but spread across segments"


def findings(connection: sqlite3.Connection, as_of: date = AS_OF, recent_days: int = 45) -> list[dict]:
    """Prioritized, evidence-linked findings for the brief."""
    result = []
    ops = workflow_health(connection)
    if ops["dead_letter"]:
        stuck = connection.execute(
            """SELECT COUNT(*), MIN(e.received_at), COALESCE(SUM(p.amount_cents),0),
                      COALESCE(SUM(a.customer_id IS NULL),0)
               FROM processed_events e JOIN payments p ON p.payment_id=e.payment_id
               LEFT JOIN access_entitlements a ON a.customer_id=p.customer_id
               WHERE e.status='dead_letter'"""
        ).fetchone()
        events, first, cents, without_access = stuck
        others = events - without_access
        headline = (f"{without_access} paying customers have no community access."
                    if without_access else f"{events} payment events are stuck in the dead-letter queue.")
        if without_access and others:
            headline = headline[:-1] + f"; {others} more stuck events belong to customers a later event gave access."
        result.append({
            "id": "ops_dead_letter", "category": "automation", "priority": 100,
            "finding": headline,
            "evidence": f"{events} payment events (${cents / 100:,.0f}) exhausted {MAX_ATTEMPTS} attempts after "
                        f"downstream errors; the first failed at {first[:16].replace('T', ' ')} UTC.",
            "why": "Top error: " + next(iter(ops["error_types"]), "unknown") + ".",
            "investigation": "Replay the dead-lettered events now that the provider is healthy, then confirm access "
                             "and send an apology to affected customers.",
            "confidence": "high: observed in the workflow log",
            "source": "processed_events + workflow_step_attempts",
            "customers_without_access": without_access,
        })
    quality = executive_brief(connection)
    unassigned = quality["measurement_health"]["unassigned_net_cash_cents"]
    campaigns = {row["campaign_id"]: row for row in campaign_performance(connection)}
    cutoff = as_of - timedelta(days=recent_days)
    recent = [e for e in detect(connection, as_of) if date.fromisoformat(e["window_end"]) >= cutoff]
    # One story per dominant driver: a campaign that moved spend, leads and quality is one finding.
    groups: dict[str, list[dict]] = {}
    for episode in recent:
        top = episode["top_drivers"][0] if episode["top_drivers"] else None
        key = top["segment"] if top and top["share_of_change"] >= 0.5 else episode["episode_id"]
        groups.setdefault(key, []).append(episode)
    for key, episodes in groups.items():
        episodes.sort(key=lambda e: ACTION_ORDER.index(e["metric_id"]))
        lead = episodes[0]
        changes = []
        for episode in episodes:
            verb = "rose" if episode["direction"] == "up" else "fell"
            text = f"{episode['label']} {verb} from {episode['baseline_text']} to {episode['current_text']}"
            if text.split(" from ")[0] not in [c.split(" from ")[0] for c in changes]:
                changes.append(text)
        segment = SEGMENT_LABELS.get(key, key)
        grouped = len(episodes) > 1 and not key.startswith(lead["metric_id"])
        windows = f"{min(e['window_start'] for e in episodes)} to {max(e['window_end'] for e in episodes)}"
        result.append({
            "id": lead["episode_id"],
            "category": "tracking" if lead["metric_id"] == "utm_completeness" else "growth",
            "priority": 50 + min(max(e["peak_z"] for e in episodes), 40) + (10 if any(e["ongoing"] for e in episodes) else 0),
            "finding": (f"{segment} moved {len(changes)} metrics: " if grouped else "") + "; ".join(changes) + ".",
            "evidence": "; ".join(f"{e['label']} {e['change_text']} (z {e['peak_z']})" for e in episodes)
                        + f"; window {windows}, compared with the prior {BASELINE} days.",
            "why": _driver_sentence(lead),
            "investigation": _action(lead, campaigns, unassigned),
            "confidence": _confidence(lead),
            "source": "diagnostics:" + ",".join(sorted({e["metric_id"] for e in episodes})),
            "episode_ids": [e["episode_id"] for e in episodes],
        })
    renewals = renewal_monitor(connection, as_of)
    if renewals["high_risk"]:
        result.append({
            "id": "renewal_risk", "category": "retention", "priority": 45,
            "finding": (f"{renewals['high_risk']} community renewal is overdue or failed." if renewals["high_risk"] == 1
                        else f"{renewals['high_risk']} community renewals are overdue or failed."),
            "evidence": f"{renewals['high_risk']} of {renewals['active_subscriptions']} active subscriptions; "
                        f"{renewals['due_soon']} more renew in the next {renewals['due_soon_days']} days.",
            "why": "Card failures and missed renewal dates; see the renewal queue.",
            "investigation": "Ask customers to update payment details before the next retry.",
            "confidence": "high: observed in subscription records",
            "source": "subscriptions + renewal_attempts",
        })
    email = deliverability_finding(connection, as_of)
    if email:
        result.append(email)
    for index, observation in enumerate(quality["observations"], 1):
        result.append({
            "id": f"quality_{index}", "category": "measurement", "priority": 30 - index,
            "finding": observation["finding"], "evidence": observation["evidence"],
            "why": "Data-quality check below target.", "investigation": observation["action"],
            "confidence": "high: deterministic check", "source": "measurement_health",
        })
    result.sort(key=lambda item: (-item["priority"], item["id"]))
    return result


def period_brief(connection: sqlite3.Connection, days: int = 7, end: date | None = None) -> dict:
    if not 1 <= days <= 30:
        raise ValueError("days must be between 1 and 30")
    end = end or date.fromisoformat(connection.execute("SELECT MAX(day) FROM mart_growth_daily").fetchone()[0])
    start = end - timedelta(days=days - 1)
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=days - 1)
    current = _window(connection, start, end)
    previous = _window(connection, prior_start, prior_end)
    change_pct = {
        metric: round((current[metric] - previous[metric]) / previous[metric], 4)
        if previous[metric] else None for metric in METRICS
    }
    return {
        "basis": "synthetic event dates; cash on payment and refund dates",
        "as_of": end.isoformat(),
        "current": {"start": start.isoformat(), "end": end.isoformat(), **current},
        "previous": {"start": prior_start.isoformat(), "end": prior_end.isoformat(), **previous},
        "change_pct": change_pct,
        "findings": findings(connection, end),
    }
