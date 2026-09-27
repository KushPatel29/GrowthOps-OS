"""Ask your data: keyless, retrieval-grounded answers from governed metrics only.

The same design as Ask Your Data, sized to this project. A question goes through:

1. **Guard**: SQL or prompt-injection patterns, requests for personal data and
   forecasts are refused before anything else runs. Nothing typed is executed.
2. **Certified phrases**: an exact, known question maps straight to its metric.
3. **Retrieval**: hybrid BM25 + local MiniLM search over a curated corpus: one
   entry per governed answer (with example phrasings) and one passage per
   documented metric definition or policy section.
4. **Understand the details** (:mod:`growthops.ask_slots`): the time window
   ("last week", "in August"), ad platform, campaign and measure the question
   names, so "what does a lead cost on Google last month" answers exactly that.
   A clear quantity question over a period ("how many leads last week") goes
   straight to the windowed totals; an ad platform the business does not buy,
   or a period the data does not cover, is refused rather than answered wrongly.
5. **Answer or refuse**: a definition question returns the documented passage
   with its citation; a metric question runs that metric's fixed, tested
   function with those details and renders its values, with follow-up questions.
   Below the confidence threshold it refuses and suggests the closest questions
   it can answer.

There is no language model and no API key: every number comes from a governed
function, every definition from a committed document. Each question is logged
to ``ask_log`` with its route, target and score. ``python -m growthops.ask_data
--eval`` scores the contract in ``evals/ask_questions.json`` (right, wrong,
refused) in keyword and hybrid mode.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from growthops.ask_slots import Slots, day_label, parse, resolve_window
from growthops.retrieval import Entry, HybridIndex, resolve_mode, tokens

ROOT = Path(__file__).resolve().parent.parent
EVAL_PATH = ROOT / "evals" / "ask_questions.json"
THRESHOLD = {"hybrid": 0.40, "keyword": 0.45}
AMBIGUITY_MARGIN = {"hybrid": 0.02, "keyword": 0.04}  # two different answers this close: ask, don't guess
PASSAGE_THRESHOLD = {"hybrid": 0.40, "keyword": 0.30}
BLOCKED = re.compile(r"(;|--|/\*|\b(drop|delete|insert|update|alter|truncate)\s+(table|from|into)\b|"
                     r"\bselect\s+.+\s+from\b|ignore (all |your |previous )|system prompt|jailbreak)", re.IGNORECASE)
PRIVATE = re.compile(r"\b(e-?mail address(es)?|phone numbers?|home address(es)?|personal (data|details|information)|"
                     r"contact details|customer (list|names|emails)|who (bought|paid|are the customers))\b",
                     re.IGNORECASE)
FORECAST = re.compile(r"\b(forecast|predict(ion)?|projection|project(ed)? (revenue|cash|sales)|next (month|quarter|year)|"
                      r"will (we|revenue|cash|sales|it) (be|hit|reach|grow))\b", re.IGNORECASE)
DEFINITION = re.compile(r"\b(define|definition|meaning of|what does .+ mean|how (is|are|do you|do we) .*"
                        r"(calculated|computed|defined|measured|calculate|compute|define|measure)|"
                        r"what counts as|formula for)\b", re.IGNORECASE)


def _usd(cents: float) -> str:
    return f"{'-' if cents < 0 else ''}${abs(cents) / 100:,.0f}"


@dataclass(frozen=True)
class Context:
    """What the question asked for, resolved against the data: slots plus the resolved window."""
    slots: Slots = field(default_factory=Slots)
    window: dict | None = None
    as_of: date | None = None
    first: date | None = None


def data_range(connection: sqlite3.Connection) -> tuple[date, date]:
    """The first and last complete day of the daily growth mart: every window is cut from this."""
    first, last = connection.execute("SELECT MIN(day), MAX(day) FROM mart_growth_daily").fetchone()
    return date.fromisoformat(first[:10]), date.fromisoformat(last[:10])


def _window_or(ctx: Context | None, days: int) -> tuple[date, date, str]:
    """The asked-for window, else the last `days` days of data, with how to say it."""
    if ctx and ctx.window:
        return ctx.window["start"], ctx.window["end"], ctx.window["label"]
    end = ctx.as_of if ctx and ctx.as_of else None
    if end is None:
        raise ValueError("a window needs the data's as-of date")
    start = end - timedelta(days=days - 1)
    return start, end, f"{day_label(start)} to {day_label(end)} (the last {days} days)"


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}%}"


def _change(now: float | None, before: float | None, rate: bool = False) -> str:
    if now is None or before is None:
        return ""
    if rate:
        return f" ({(now - before) * 100:+.1f} pts on the period before)"
    if not before:
        return ""
    return f" ({(now - before) / abs(before):+.0%} on the period before)"


# --- governed answers ------------------------------------------------------
# Each takes (connection, context) and returns (answer, source). Only these
# functions ever touch the database. A function that has no use for a detail
# (a platform, a window) ignores it, and the answer says what period it covers.

def _revenue_truth(connection, ctx=None):
    from growthops.reconciliation import four_numbers, platform_comparison

    numbers = four_numbers(connection)
    meta = next(row for row in platform_comparison(connection) if row["platform"] == "meta")
    return ((f"Ad platforms claim {_usd(numbers['platform_reported_total_cents'])}, the CRM books "
            f"{_usd(numbers['crm_booked_cents'])}, and {_usd(numbers['net_collected_cents'])} of net cash was actually "
            f"collected. The warehouse credits {_usd(numbers['paid_media_net_cash_cents'])} of that to paid media. Meta "
            f"reports {meta['platform_roas']}x ROAS; on net cash it is {meta['warehouse_roas']}x. Net collected cash is "
            "the number to run the business on."), "reconciliation bridges")


def _what_changed(connection, ctx=None):
    from growthops.brief import findings

    top = [item for item in findings(connection) if item["category"] in ("growth", "tracking", "automation", "email")][:3]
    if not top:
        return "No material changes were detected in the last 45 days.", "diagnostics"
    return " ".join(f"{item['finding']} {item['why']}".rstrip(".") + "." for item in top), "diagnostics + workflow log"


def _automation(connection, ctx=None):
    from growthops.workflow import health

    ops = health(connection)
    return ((f"Of {ops['events']} recent payment events, {ops['completed']} completed and {ops['dead_letter']} are in the "
            f"dead-letter queue; {ops['duplicate_deliveries_absorbed']} duplicate deliveries were absorbed with no double "
            f"side effects. {ops['within_5_minutes']:.0%} of events reached access within five minutes."),
            "processed_events, workflow_step_attempts")


def _experiment(connection, ctx=None):
    from growthops.experiments import analyze

    comparison = analyze(connection, "cta_growth_plan")["comparison"]
    lo, hi = comparison["cash_per_visitor_bootstrap_95_ci_cents"]
    return ((f"CTA B changed lead rate by {comparison['variant_b_minus_a_lead_rate'] * 100:+.1f} points (p = "
            f"{comparison['lead_rate_p_value']}) and MQLs per lead by {comparison['variant_b_minus_a_mql_per_lead'] * 100:+.1f} "
            f"points. Cash per visitor moved ${comparison['variant_b_minus_a_cash_per_visitor_cents'] / 100:+.2f} (95% "
            f"interval ${lo / 100:.2f} to ${hi / 100:.2f}). Decision: {comparison['decision']}"),
            "experiment exposures, lifecycle, payments, refunds")


def _renewals(connection, ctx=None):
    from growthops.renewals import monitor

    risk = monitor(connection)
    return ((f"As of {risk['as_of']}, {risk['high_risk']} of {risk['active_subscriptions']} active subscriptions are overdue "
            f"or failed, {risk['due_soon']} more renew within {risk['due_soon_days']} days, and {risk['canceled']} have "
            "canceled."), "subscriptions, renewal attempts")


def _funnel(connection, ctx=None):
    from growthops.funnel import funnel

    stages = funnel(connection)
    rates = [row for row in stages[:7] if row["from_previous_rate"] is not None]
    weakest = min(rates, key=lambda row: row["from_previous_rate"])
    steps = ", ".join(f"{row['stage'].replace('_', ' ')} {row['from_previous_rate']:.0%}" for row in rates)
    return ((f"Stage-to-stage conversion: {steps}. The weakest step before payment is into "
            f"{weakest['stage'].replace('_', ' ')} ({weakest['from_previous_rate']:.1%}, median "
            f"{weakest['median_days_from_previous']} days)."), "lifecycle events")


def _paid_campaigns(connection, ctx=None):
    from growthops.report import campaign_performance

    rows = [row for row in campaign_performance(connection) if row["medium"].startswith("paid_") and row["spend_cents"]]
    best = max(rows, key=lambda row: row["net_cash_cents"] / row["spend_cents"])
    worst = min(rows, key=lambda row: row["net_cash_cents"] / row["spend_cents"])
    return ((f"Best paid campaign by net cash per dollar: {best['campaign_id']} "
            f"({best['net_cash_cents'] / best['spend_cents']:.1f}x on {_usd(best['spend_cents'])}). Weakest: "
            f"{worst['campaign_id']} ({worst['net_cash_cents'] / worst['spend_cents']:.1f}x). Lead-creation attribution "
            "is descriptive, not incremental."), "campaign performance mart")


KPI_COLUMNS = {  # measure -> (label, mart column or None for a ratio, money?)
    "spend": ("ad spend", "spend_cents", True),
    "leads": ("leads", "leads", False),
    "mqls": ("MQLs", "mqls", False),
    "calls_booked": ("discovery calls booked", "calls_booked", False),
    "deals_won": ("deals won", "closed_won_deals", False),
    "gross_collected": ("gross cash collected", "gross_collected_cents", True),
    "refunds": ("refunds", "refunds_cents", True),
    "net_cash": ("net cash collected", "net_cash_cents", True),
    "crm_booked": ("CRM bookings (closed-won value)", "booked_cents", True),
    "mql_rate": ("MQL rate (MQLs per lead)", None, False),
}
KPI_MEASURES = frozenset(KPI_COLUMNS)


def _period_totals(connection, start: date, end: date) -> dict:
    row = connection.execute(
        """SELECT COUNT(*) days, SUM(spend_cents) spend_cents, SUM(leads) leads, SUM(mqls) mqls,
                  SUM(calls_booked) calls_booked, SUM(closed_won_deals) closed_won_deals,
                  SUM(gross_collected_cents) gross_collected_cents, SUM(refunds_cents) refunds_cents,
                  SUM(net_cash_cents) net_cash_cents, SUM(booked_cents) booked_cents
           FROM mart_growth_daily WHERE day BETWEEN ? AND ?""", (start.isoformat(), end.isoformat())).fetchone()
    totals = {key: value or 0 for key, value in dict(row).items()}
    totals["mql_rate"] = totals["mqls"] / totals["leads"] if totals["leads"] else None
    return totals


def _kpi_value(totals: dict, measure: str) -> float | None:
    column = KPI_COLUMNS[measure][1]
    return totals["mql_rate"] if column is None else totals[column]


def _kpi_text(measure: str, value: float | None, before: float | None) -> str:
    label, _column, money = KPI_COLUMNS[measure]
    if measure == "mql_rate":
        return f"{label} {_pct(value)}{_change(value, before, rate=True)}"
    shown = _usd(value) if money else f"{value:,}"
    return f"{label} {shown}{_change(value, before)}"


def _kpi_totals(connection, ctx=None):
    """Totals for any period the data covers, against the same number of days before it."""
    ctx = ctx or Context()
    if not ctx.as_of:
        first, as_of = data_range(connection)
        ctx = Context(ctx.slots, ctx.window, as_of, first)
    if ctx.window:
        start, end, label = ctx.window["start"], ctx.window["end"], ctx.window["label"]
    else:
        start, end, label = ctx.first, ctx.as_of, f"all time ({day_label(ctx.first)} to {day_label(ctx.as_of)})"
    days = (end - start).days + 1
    now = _period_totals(connection, start, end)
    prior_end, prior_start = start - timedelta(days=1), start - timedelta(days=days)
    before = _period_totals(connection, prior_start, prior_end) if prior_start >= ctx.first else None
    measure = ctx.slots.measure if ctx.slots.measure in KPI_MEASURES else None
    order = [measure] if measure else []
    order += [m for m in ("net_cash", "spend", "leads", "mqls", "mql_rate", "calls_booked", "deals_won")
              if m not in order]
    first_line = _kpi_text(order[0], _kpi_value(now, order[0]), before and _kpi_value(before, order[0]))
    rest = "; ".join(_kpi_text(m, _kpi_value(now, m), before and _kpi_value(before, m)) for m in order[1:4])
    note = "" if before else " There is no earlier period of the same length in the data to compare with."
    clipped = " The data starts on 1 Jul 2025, so the period is cut to what exists." if (
        ctx.window and ctx.window.get("clipped")) else ""
    basis = (" Cash is dated by payment and refund, leads and MQLs by the day they happened, so the MQL rate is "
             "an event-basis read of lead quality.") if measure in (None, "mql_rate", "mqls", "leads") else ""
    return (f"{label[0].upper() + label[1:]}: {first_line}. Also {rest}.{note}{clipped}{basis}",
            "mart_growth_daily (event dates)")


def _paid_efficiency(connection, ctx=None):
    from growthops.performance import PLATFORM_LABELS, paid_efficiency

    ctx = ctx or Context()
    if not ctx.as_of:
        first, as_of = data_range(connection)
        ctx = Context(ctx.slots, ctx.window, as_of, first)
    start, end, label = _window_or(ctx, 30)
    campaign, platform, measure = ctx.slots.campaign, ctx.slots.platform, ctx.slots.measure
    rows = paid_efficiency(connection, start, end, by="campaign" if campaign else "platform")
    total = rows[-1]
    metrics = {
        "cpl": ("cost per lead", "cost_per_lead_cents", _usd), "cost_per_mql": ("cost per MQL", "cost_per_mql_cents", _usd),
        "cost_per_booked_call": ("cost per booked call", "cost_per_booked_call_cents", _usd),
        "cpm": ("CPM", "cpm_cents", _usd), "cpc": ("CPC", "cpc_cents", _usd),
        "ctr": ("CTR", "ctr", lambda v: _pct(v, 2)), "spend": ("spend", "spend_cents", _usd),
        "leads": ("leads", "leads", lambda v: f"{v:,}"), "mqls": ("MQLs", "mqls", lambda v: f"{v:,}"),
        "calls_booked": ("booked calls", "calls_booked", lambda v: f"{v:,}"),
        "roas": ("net-cash ROAS", "net_cash_roas", lambda v: f"{v}x"),
    }

    def describe(row: dict, lead: str | None) -> str:
        keys = ([lead] if lead in metrics else []) + [k for k in ("cpl", "cost_per_mql", "cost_per_booked_call",
                                                                  "ctr", "spend", "roas") if k != lead]
        parts = []
        for key in keys:
            name, field, fmt = metrics[key]
            value = row.get(field)
            parts.append(f"{name} {fmt(value) if value is not None else 'n/a'}")
        return ", ".join(parts)

    focus = None
    if campaign:
        focus = next((row for row in rows[:-1] if row["segment"] == campaign), None)
        if focus is None:
            return (f"{campaign} had no paid spend or paid-created leads from {label}.",
                    "ad_spend_daily, lifecycle events (activity basis)")
        name = campaign
    elif platform:
        focus = next((row for row in rows[:-1] if row["segment"] == platform), None)
        name = PLATFORM_LABELS[platform]
        if focus is None:
            return f"{name} had no paid spend or paid-created leads from {label}.", "ad_spend_daily"
    lag = " Cash lags leads by weeks, so a short-window ROAS understates." if (
        measure == "roas" or not measure) else ""
    if focus:
        return ((f"{name}, {label}: {describe(focus, measure)}. All paid media for comparison: "
                 f"{describe(total, measure)}.{lag}"),
                "ad_spend_daily, lifecycle events (activity basis)")
    parts = [f"{PLATFORM_LABELS.get(row['segment'], row['segment'])}: {describe(row, measure)}" for row in rows[:-1]]
    return (f"{label[0].upper() + label[1:]}: " + "; ".join(parts) + f". All paid: {describe(total, measure)}.{lag}",
            "ad_spend_daily, lifecycle events (activity basis)")


def _tracking(connection, ctx=None):
    from growthops.report import measurement_health

    health = measurement_health(connection)
    return ((f"UTM completeness is {health['utm_completeness']:.1%}, campaign registry match "
            f"{health['campaign_registry_match']:.1%}, owner coverage {health['crm_owner_completeness']:.1%}, with "
            f"{health['duplicate_contact_rows']} duplicate contact rows and {_usd(health['unassigned_net_cash_cents'])} "
            "of net cash that cannot be credited to a campaign."), "measurement health")


def _cash(connection, ctx=None):
    from growthops.report import metrics

    if ctx and ctx.window:
        return _kpi_totals(connection, Context(Slots(measure="net_cash"), ctx.window, ctx.as_of, ctx.first))
    values = metrics(connection)
    return ((f"Gross collected was {_usd(values['gross_collected_cents'])}; refunds were {_usd(values['refunds_cents'])}; "
            f"net collected cash was {_usd(values['net_collected_cents'])}. Closed-won deal value is a separate "
            f"{_usd(values['booked_revenue_cents'])} booking measure."), "payments, refunds, closed-won deals")


def _email(connection, ctx=None):
    from growthops.email_analytics import type_summary

    last = datetime.fromisoformat(connection.execute("SELECT MAX(sent_at) FROM email_campaigns").fetchone()[0]).date()
    if ctx and ctx.window:
        since, label = ctx.window["start"], ctx.window["label"]
    else:
        since, label = last - timedelta(days=89), "Last 90 days"
    rows = [row for row in type_summary(connection, since) if row.get("delivered")]
    if not rows:
        return f"No emails were sent from {label}.", "email_campaigns"
    parts = [f"{row['email_type'].replace('_', ' ')}: human open {row['human_open_rate']:.1%}, click "
             f"{row['click_rate']:.2%}, click-to-open {row['click_to_open_rate']:.1%}" for row in rows]
    return (f"{label[0].upper() + label[1:]}, on human opens (privacy-proxy machine opens excluded): "
            + "; ".join(parts) + ".", "email_campaigns")


def _deliverability(connection, ctx=None):
    from growthops.email_analytics import deliverability_finding

    finding = deliverability_finding(connection)
    if finding is None:
        return "Every sending domain is under the 2% bounce and 0.1% complaint limits.", "email_campaigns"
    return f"{finding['finding']} {finding['evidence']} Next: {finding['investigation']}", "email_campaigns"


def _links(connection, ctx=None):
    from growthops.campaign_links import audit_short_links

    audit = audit_short_links(connection)
    broken = "; ".join(f"{link['link_id']} ({link['issues'][0]})" for link in audit["links"] if link["issues"])
    return ((f"{audit['links_with_issues']} of {len(audit['links'])} short links fail the registry check: {broken}. They "
            f"carried {audit['share_of_recent_clicks_broken']:.0%} of short-link clicks in the last "
            f"{audit['recent_days']} days."), "short_links, short_link_clicks, campaigns")


def _crm(connection, ctx=None):
    from growthops.hubspot import audit

    crm = audit(connection)
    return ((f"A HubSpot import would merge {crm['rows_merged_on_email']} rows on email; "
            f"{crm['paying_contacts_not_customer']} paying contacts and {crm['closed_won_contacts_not_customer']} "
            f"closed-won contacts are not at the Customer stage; owner fill rate is "
            f"{crm['property_fill_rate']['hubspot_owner_id']:.1%}; {crm['stale_leads_non_marketing_candidates']:,} "
            "untouched leads could be set to non-marketing."), "contacts, deals, payments (HubSpot mapping)")


def _daily(connection, ctx=None):
    from growthops.performance import daily_update

    return daily_update(connection)["text"], "mart_growth_daily, paid efficiency, brief"


def _content(connection, ctx=None):
    rows = connection.execute(
        """SELECT topic, SUM(views) views, SUM(customers) customers, SUM(influenced_net_cash_cents) cash
           FROM mart_content_performance GROUP BY topic ORDER BY cash DESC""").fetchall()
    top, bottom = rows[0], rows[-1]
    return ((f"By topic, {top['topic']} content influenced the most cash ({_usd(top['cash'])} from {top['customers']} "
            f"customers on {top['views']:,} views); {bottom['topic']} influenced the least ({_usd(bottom['cash'])} on "
            f"{bottom['views']:,} views). First identified content touch: descriptive, not incremental."),
            "mart_content_performance")


def _attribution(connection, ctx=None):
    from growthops.attribution import MODELS, summary

    parts = []
    for model in MODELS:
        rows = [row for row in summary(connection, model) if row["campaign_id"]]
        best = max(rows, key=lambda row: row["net_cash_cents"])
        parts.append(f"{model.replace('_', ' ')} credits {best['campaign_id']} most ({_usd(best['net_cash_cents'])})")
    return ("; ".join(parts) + ". Every model sums to the same net cash; the governed model is lead creation.",
            "attribution models")


def _freshness(connection, ctx=None):
    from growthops.freshness import check

    items = check(connection)
    stale = [item["source"] for item in items if item["status"] != "fresh"]
    latest = ", ".join(f"{item['source']} {item['latest']}" for item in items)
    return (("All sources are within their freshness SLA. " if not stale else f"Stale: {', '.join(stale)}. ")
            + f"Latest records: {latest}.", "freshness check")


def _list_mix(connection, ctx=None):
    from growthops.email_analytics import list_source_mix

    mix = list_source_mix(connection)
    return ("New contacts in the last three months by original source: "
            + ", ".join(f"{row['source']} {row['share']:.0%}" for row in mix[:6]) + ".", "contacts")


@dataclass(frozen=True)
class Intent:
    id: str
    title: str
    description: str
    phrasings: tuple[str, ...]
    run: Callable[..., tuple[str, str]]


INTENTS = (
    Intent("revenue_truth", "Which revenue number is right",
           "Meta, Google, LinkedIn, the CRM and Stripe payments each say a different revenue number: reconcile ad "
           "platform claims, CRM bookings and net collected cash; platform ROAS versus cash ROAS.",
           ("Which revenue number is right?", "Meta and Google report more revenue than we collected",
            "Why don't the ad platforms match the CRM?", "reconcile platform claims to cash"), _revenue_truth),
    Intent("anomaly_episodes", "What changed and why",
           "Anomalies, spikes, drops, red flags and what moved in the numbers: unusual changes in leads, MQL rate, spend, "
           "UTM tracking and cash over "
           "recent weeks, with the campaign or page that drove each change.",
           ("What changed this week?", "Why did lead quality drop?", "anything unusual lately",
            "biggest changes this month",
            "what should I look at this morning"), _what_changed),
    Intent("automation_health", "Payment to access automation",
           "Payment to access automation: buyers who paid but did not get into the community, access grants, payment "
           "webhooks, CRM update, onboarding, retries and the dead-letter queue.",
           ("Did every buyer get community access?", "Are payment webhooks failing?",
            "did an access outage stop people getting in",
            "how many events are in the dead letter queue", "customers who paid but have no access"), _automation),
    Intent("experiment_cash_per_visitor", "CTA experiment result",
           "The call to action (CTA) A/B split test: which variant is winning on lead rate, MQL per lead, cash per visitor, "
           "and the ship decision.",
           ("How did the CTA test do?", "Should we ship variant B?", "A/B test result for the landing page"),
           _experiment),
    Intent("renewal_risk", "Renewal risk", "Subscribers and members who may not renew: overdue or failed card renewals, renewals due soon, cancellations.",
           ("Which renewals are at risk?", "how many subscriptions are overdue", "subscription churn"), _renewals),
    Intent("funnel_adjacent_conversion", "Funnel conversion",
           "Stage-to-stage conversion and drop-off: lead to MQL, booked call, show rate, opportunity, close rate "
           "from calls to wins, and payment. Where people are lost.",
           ("Where does the funnel drop off?", "lead to MQL conversion rate", "conversion by funnel stage",
            "what percent of MQLs book a call",
            "close rate from booked call to won deal"), _funnel),
    Intent("paid_campaign_net_cash", "Best and worst paid campaigns",
           "Paid campaigns ranked by net cash returned per dollar of ad spend (ROAS by campaign), to decide where to "
           "cut, scale or reallocate budget.",
           ("Which paid campaigns are best?", "worst campaign by return", "which campaign should get more budget"),
           _paid_campaigns),
    Intent("paid_efficiency", "Paid media efficiency",
           "Ad efficiency by platform for the last 30 days: ad CPM, ad click-through rate (CTR), CPC, cost per lead "
           "(CPL), cost per MQL and cost per booked discovery call on Meta, Google and LinkedIn ads.",
           ("What is our cost per lead?", "cost per booked call on Meta", "CPL and CPM by platform",
            "compare LinkedIn spend with Meta campaigns",
            "how much does an MQL cost us"), _paid_efficiency),
    Intent("measurement_health", "Tracking and data quality",
           "Measurement health: whether UTM tagging can be trusted, UTM completeness, campaign registry match, CRM owner "
           "coverage, duplicate contacts, and cash with no campaign attached (untracked).",
           ("How clean is our tracking?", "are UTMs missing", "data quality score", "untracked revenue",
            "can we trust UTM tagging", "how many contacts are missing an owner",
            "cash with no campaign attached"), _tracking),
    Intent("net_collected_cash", "Cash and refunds", "Money actually collected: gross cash, refunds given to customers, net collected cash after refunds, versus "
           "CRM bookings.",
           ("What is net cash after refunds?", "how much money did we collect", "total refunds",
            "booked revenue versus collected cash across systems"), _cash),
    Intent("email_performance", "Email performance",
           "Email campaign engagement: newsletter, webinar invite, promo and nurture open rate on human opens, email "
           "click rate and click-to-open.",
           ("How is the newsletter performing?", "email open and click rates", "what's our email CTR"), _email),
    Intent("email_deliverability", "Email deliverability",
           "Email deliverability: bouncing emails, bounce rate, spam or junk folder placement, complaint rate and the "
           "sending domain.",
           ("Are our emails landing in spam?", "email bounce rate", "sending domain problems"), _deliverability),
    Intent("link_hygiene", "Short-link and UTM tagging",
           "Bitly-style short links checked against the campaign registry: missing, unregistered or off-taxonomy UTMs.",
           ("Are our short links tagged correctly?", "bitly links missing UTMs", "which links are broken",
            "is the Instagram bio link tracked"), _links),
    Intent("crm_hubspot_audit", "CRM (HubSpot) hygiene",
           "HubSpot-mapped CRM audit: duplicates merged on email, lifecycle stage errors, owners, marketing contacts.",
           ("How clean is HubSpot?", "contacts with the wrong lifecycle stage", "CRM duplicates and owner gaps"), _crm),
    Intent("daily_update", "Daily performance update",
           "Yesterday's spend, leads, MQLs, calls booked and cash against the trailing week, with what needs attention.",
           ("Give me the daily update", "how did yesterday go", "morning summary for the team"), _daily),
    Intent("content_pipeline", "Content to pipeline",
           "Which YouTube content topics influence customers and cash, not just views.",
           ("Which videos drive buyers?", "best content topic by revenue", "does YouTube content produce customers",
            "do mindset videos make money"),
           _content),
    Intent("attribution_models", "Attribution model comparison",
           "Net cash credit by campaign under first touch, lead creation, last non-direct, U-shaped and linear models.",
           ("Compare attribution models", "first touch versus linear credit", "how do the attribution models differ"),
           _attribution),
    Intent("data_freshness", "Data freshness", "Data freshness: whether the numbers are current and up to date, when each source (ads, payments, CRM, email) "
           "last synced or refreshed, and which are stale.",
           ("Is the data up to date?", "when was the data last refreshed", "are any sources stale"), _freshness),
    Intent("kpi_totals", "Totals for a period",
           # Quantities, not dates: the period is read by ask_slots, and date words here would pull in any
           # question that mentions a week ("red flags in the last few weeks").
           "Totals and counts: how many leads, MQLs, booked calls and deals were won; how much was spent on ads; "
           "gross cash, refunds and net cash collected; the MQL rate; each against the period before.",
           ("How many leads did we get last week?", "how much did we spend on ads", "total net cash collected",
            "how many deals did we win", "number of MQLs and the MQL rate"),
           _kpi_totals),
    Intent("list_source_mix", "List growth by source",
           "Email list and contact growth: new signups and subscribers in the last three months by acquisition source "
           "or channel (YouTube, Meta, Google, newsletter).",
           ("Where do new contacts come from?", "list growth by source", "acquisition source mix"), _list_mix),
)
INTENT_BY_ID = {intent.id: intent for intent in INTENTS}

# What someone usually asks next. Each answer offers these as one-click follow-ups.
FOLLOW_UPS: dict[str, tuple[str, ...]] = {
    "revenue_truth": ("How is warehouse ROAS calculated?", "Which paid campaigns are best?",
                      "What is net cash after refunds?"),
    "anomaly_episodes": ("Why did lead quality drop?", "What was the MQL rate last month?",
                         "Are our emails landing in spam?"),
    "automation_health": ("Which renewals are at risk?", "Is the data up to date?"),
    "experiment_cash_per_visitor": ("How is cash per visitor calculated?", "Where does the funnel drop off?"),
    "renewal_risk": ("Did every buyer get community access?", "What is net cash after refunds?"),
    "funnel_adjacent_conversion": ("What was the MQL rate last month?", "What does a booked call cost on Meta?",
                                   "How did the CTA test do?"),
    "paid_campaign_net_cash": ("What is the cost per MQL for meta_broad_v17?", "Compare attribution models",
                               "What does a lead cost on Google?"),
    "paid_efficiency": ("Which paid campaigns are best?", "How much did we spend on ads this month?",
                        "How is cost per MQL calculated?"),
    "measurement_health": ("Are our short links tagged correctly?", "How clean is HubSpot?"),
    "net_collected_cash": ("Which revenue number is right?", "Net cash last month", "What changed this week?"),
    "email_performance": ("Are our emails landing in spam?", "What does human open rate mean?"),
    "email_deliverability": ("How is the newsletter performing?", "What changed this week?"),
    "link_hygiene": ("How clean is our tracking?", "Where do new contacts come from?"),
    "crm_hubspot_audit": ("How clean is our tracking?", "Which revenue number is right?"),
    "daily_update": ("What changed this week?", "How many leads did we get last week?"),
    "content_pipeline": ("Compare attribution models", "Where do new contacts come from?"),
    "attribution_models": ("Which paid campaigns are best?", "Which revenue number is right?"),
    "data_freshness": ("Give me the daily update", "What changed this week?"),
    "kpi_totals": ("What changed this week?", "What does a lead cost on Google?", "Which revenue number is right?"),
    "list_source_mix": ("Which videos drive buyers?", "How many leads did we get last week?"),
}

# Starter questions by theme, shown as one-click prompts. Every one is held by a test to reach a governed
# answer with numbers in it, in both retrieval modes, so a suggestion can never lead to a refusal.
SUGGESTIONS: dict[str, tuple[str, ...]] = {
    "Revenue truth": ("Which revenue number is right?", "What is net cash after refunds?",
                      "Net cash in August", "Compare attribution models"),
    "Paid media": ("What does a lead cost on Google?", "What does a booked call cost on Meta?",
                   "How much did we spend on ads this month?", "Which paid campaigns are best?"),
    "Funnel and leads": ("How many leads did we get last week?", "What was the MQL rate last month?",
                         "Where does the funnel drop off?", "How did the CTA test do?"),
    "Email and tracking": ("Are our emails landing in spam?", "How is the newsletter performing?",
                           "Are our short links tagged correctly?", "How clean is our tracking?"),
    "Operations": ("What changed this week?", "Did every buyer get community access?",
                   "Which renewals are at risk?", "Is the data up to date?"),
    "Definitions": ("How is cost per MQL calculated?", "What does human open rate mean?",
                    "How is warehouse ROAS calculated?"),
}
CERTIFIED = {" ".join(phrase.lower().rstrip("?").split()): intent.id for intent in INTENTS
             for phrase in intent.phrasings[:1]}
TOPICS = tuple(intent.title for intent in INTENTS)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def load_passages(docs: Path | None = None) -> list[Entry]:
    """Metric definitions and policy sections from the committed docs, each with its citation."""
    docs = docs or Path(os.getenv("GROWTHOPS_DOCS_DIR", ROOT / "docs"))
    entries = []
    catalog = docs / "metric-catalog.md"
    if catalog.exists():
        for line in catalog.read_text(encoding="utf-8").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if line.startswith("| ") and len(cells) == 3 and cells[0] != "Metric" and not cells[0].startswith("---"):
                entries.append(Entry(f"metric:{_slug(cells[0])}", "passage", cells[0], f"{cells[1]}. {cells[2]}",
                                     meta={"source": "docs/metric-catalog.md"}))
    for name in ("metric-catalog.md", "tracking-plan.md", "hubspot-mapping.md"):
        path = docs / name
        if not path.exists():
            continue
        for section in re.split(r"^## ", path.read_text(encoding="utf-8"), flags=re.MULTILINE)[1:]:
            heading, _, body = section.partition("\n")
            paragraph = next((block.strip() for block in body.split("\n\n")
                              if block.strip() and not block.lstrip().startswith(("|", "```"))), "")
            if paragraph:
                entries.append(Entry(f"doc:{name.removesuffix('.md')}:{_slug(heading)}", "passage", heading.strip(),
                                     " ".join(paragraph.split()), meta={"source": f"docs/{name}"}))
    return entries


def corpus() -> list[Entry]:
    intents = [Entry(intent.id, "intent", intent.title, intent.description, intent.phrasings) for intent in INTENTS]
    return intents + load_passages()


_indexes: dict[str, HybridIndex] = {}
_index_lock = threading.Lock()


def index(mode: str | None = None) -> HybridIndex:
    resolved = resolve_mode(mode)
    with _index_lock:
        if resolved not in _indexes:
            _indexes[resolved] = HybridIndex(corpus(), resolved)
        return _indexes[resolved]


def _suggest(hits) -> str:
    return "; ".join(f"“{INTENT_BY_ID[hit.entry.id].phrasings[0]}”" for hit in hits[:3] if hit.entry.id in INTENT_BY_ID)


def _refuse(reason: str, hits=(), score=None, mode=None) -> dict:
    return {"route": "refused", "reason": reason, "target": None, "score": score, "hits": list(hits), "mode": mode}


# Paid-media measures that name an efficiency figure, and the quantity words that mark a "how many / how much" question.
# Pairs that give the same figure for the same question, so a near-tie between them is not a reason to refuse:
# an undated cash question gets all-time net cash from either.
EQUIVALENT = frozenset({frozenset({"net_collected_cash", "kpi_totals"})})

PLATFORM_NAMES = {"tiktok": "TikTok", "tik tok": "TikTok", "snapchat": "Snapchat", "snap": "Snapchat",
                  "x ads": "X", "bing": "Microsoft (Bing)", "microsoft ads": "Microsoft", "amazon ads": "Amazon"}

# Answers that read the question's details (window, platform, campaign, measure); the rest are fixed views.
SLOT_AWARE = frozenset({"kpi_totals", "paid_efficiency", "email_performance", "net_collected_cash"})
EFFICIENCY_MEASURES = frozenset({"cpl", "cost_per_mql", "cost_per_booked_call", "cpm", "cpc", "ctr"})
PAID_VOLUMES = frozenset({"spend", "leads", "mqls", "calls_booked"})  # counts a paid platform or campaign also has
# "Why did leads drop last week" is a diagnosis, and "which campaign had the most leads" a ranking: neither is a total.
DIAGNOSTIC = re.compile(r"\b(why|drop(ped|s)?|fell|fall(ing)?|spiked?|chang(e|ed|es)|unusual|anomal\w*|red flags?|"
                        r"what happened|explain|going on)\b", re.IGNORECASE)
BREAKDOWN = re.compile(r"\b(which|best|worst|top|most|least|rank\w*|by (campaign|platform|channel|source|topic))\b",
                       re.IGNORECASE)
RECONCILE = re.compile(r"\b(right|correct|true|match|matches|claim|claims|disagree|differ|reconcil\w*|versus|vs)\b",
                       re.IGNORECASE)


def _slot_route(text: str, slots: Slots) -> str | None:
    """The governed answer a question's details decide on their own, when they are unambiguous.

    Two patterns only. A quantity question over a named period ("how many leads last week") is the windowed
    totals. An efficiency figure for a named platform or campaign ("CPL on Google", "cost per MQL for
    meta_broad_v17") is paid efficiency. Anything else is left to retrieval, which knows the other answers.
    """
    if slots.window and slots.measure in KPI_MEASURES and not (slots.platform or slots.campaign) \
            and not (RECONCILE.search(text) or DIAGNOSTIC.search(text) or BREAKDOWN.search(text)):
        return "kpi_totals"
    if (slots.platform or slots.campaign) and slots.measure in EFFICIENCY_MEASURES | PAID_VOLUMES \
            and not RECONCILE.search(text):
        return "paid_efficiency"
    return None


def route(question: str, mode: str | None = None) -> dict:
    """Decide how to answer, without touching the database."""
    text = " ".join(question.split())[:300]
    if not text:
        return _refuse("Ask a question about the growth metrics.")
    if BLOCKED.search(text):
        return _refuse("This interface answers metric questions only; it never runs SQL or instructions.")
    if PRIVATE.search(text):
        return _refuse("Personal data is not available here; answers are aggregate metrics only.")
    if FORECAST.search(text):
        return _refuse("GrowthOps reports measured results; it does not forecast.")
    slots = parse(text)
    if slots.untracked_platform:
        return {**_refuse(f"{PLATFORM_NAMES.get(slots.untracked_platform, slots.untracked_platform.title())} ads are not bought or tracked here; paid media is "
                          "Meta, Google and LinkedIn. Try “What does a lead cost on Google?”"), "slots": slots}
    certified = CERTIFIED.get(text.lower().rstrip("?").strip())
    if certified:
        return {"route": "certified", "target": certified, "score": 1.0, "hits": [], "mode": "certified",
                "slots": slots}
    decided = _slot_route(text, slots)
    if decided and not DEFINITION.search(text):
        return {"route": "metric", "target": decided, "score": 1.0, "hits": [], "mode": "slots", "slots": slots}
    idx = index(mode)
    if DEFINITION.search(text):
        passages = idx.search(text, k=3, kind="passage")
        if passages and passages[0].score >= PASSAGE_THRESHOLD[idx.mode]:
            return {"route": "definition", "target": passages[0].entry.id, "score": passages[0].score,
                    "hits": passages, "mode": idx.mode, "slots": slots}
    hits = idx.search(text, k=3, kind="intent")
    ambiguous = len(hits) > 1 and hits[0].score - hits[1].score < AMBIGUITY_MARGIN[idx.mode] \
        and frozenset((hits[0].entry.id, hits[1].entry.id)) not in EQUIVALENT
    # Keyword-only mode has no sense of paraphrase, so one shared word ("marketing") is not enough to answer.
    thin = idx.mode == "keyword" and hits and hits[0].matched_terms < min(2, len(set(tokens(text))))
    if hits and hits[0].score >= THRESHOLD[idx.mode] and not ambiguous and not thin:
        return {"route": "metric", "target": hits[0].entry.id, "score": hits[0].score, "hits": hits, "mode": idx.mode,
                "slots": slots}
    suggestion = f" Closest questions I can answer: {_suggest(hits)}." if hits else ""
    return {**_refuse("I can't answer that from the governed metrics." + suggestion, hits,
                      hits[0].score if hits else None, idx.mode), "slots": slots}


def _log(connection: sqlite3.Connection, question: str, decision: dict, latency_ms: int) -> None:
    try:
        connection.execute("INSERT INTO ask_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (uuid.uuid4().hex, datetime.now(timezone.utc).isoformat(), question[:300],
                            decision["route"], decision.get("target"), decision.get("score"),
                            decision.get("mode") or "none", latency_ms))
    except sqlite3.OperationalError:
        pass  # a read-only or pre-migration database still answers


def answer(connection: sqlite3.Connection, question: str, mode: str | None = None) -> dict:
    started = time.perf_counter()
    decision = route(question, mode)
    retrieved = [{"id": hit.entry.id, "title": hit.entry.title, "score": hit.score} for hit in decision["hits"]]
    slots = decision.get("slots") or Slots()
    window = None
    if decision["route"] in ("certified", "metric") and slots.window:
        first, as_of = data_range(connection)
        window = resolve_window(slots.window, as_of, first)
        if window is None:
            decision = {**_refuse(f"The data covers {day_label(first)} to {day_label(as_of)}; "
                                  f"{slots.window['phrase']} is outside it.", mode=decision.get("mode")),
                        "slots": slots}
    follow_ups: list[str] = []
    if decision["route"] in ("certified", "metric"):
        intent = INTENT_BY_ID[decision["target"]]
        first, as_of = data_range(connection)
        text, source = intent.run(connection, Context(slots, window, as_of, first))
        result = {"answer": text, "source": source, "metric_id": intent.id, "citations": []}
        follow_ups = [q for q in FOLLOW_UPS.get(intent.id, ()) if q.lower().rstrip("?") != question.lower().rstrip("?")]
    elif decision["route"] == "definition":
        entry = decision["hits"][0].entry
        result = {"answer": f"{entry.title}: {entry.text}", "source": entry.meta["source"], "metric_id": None,
                  "citations": [{"id": entry.id, "source": entry.meta["source"], "title": entry.title}]}
    else:
        result = {"answer": decision["reason"], "source": "governed metric catalog", "metric_id": None,
                  "citations": []}
    latency = round((time.perf_counter() - started) * 1000)
    _log(connection, question, decision, latency)
    if decision["route"] == "refused":
        # A refusal still leaves somewhere to go: the closest answerable questions, else the starters.
        follow_ups = [INTENT_BY_ID[hit.entry.id].phrasings[0] for hit in decision["hits"]
                      if hit.entry.id in INTENT_BY_ID][:3] or [SUGGESTIONS[theme][0] for theme in list(SUGGESTIONS)[:3]]
    # Say what was understood only where it shaped the answer; elsewhere it would imply a filter never applied.
    shaped = decision.get("target") in SLOT_AWARE and decision["route"] in ("certified", "metric")
    understood = slots.describe() if shaped else ""
    if window and shaped:
        understood = f"{understood} ({window['label']})" if understood else window["label"]
    return {**result, "route": decision["route"], "target": decision.get("target"), "confidence": decision.get("score"),
            "retrieval_mode": decision.get("mode"), "retrieved": retrieved, "latency_ms": latency,
            "understood": understood, "follow_ups": follow_ups}


def suggestions() -> dict[str, list[str]]:
    """The starter questions, by theme."""
    return {theme: list(questions) for theme, questions in SUGGESTIONS.items()}


def run_eval(mode: str | None = None, path: Path = EVAL_PATH) -> dict:
    """Score routing against the question contract: right, wrong, or refused."""
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    results = []
    for case in cases:
        decision = route(case["question"], mode)
        got = decision["target"] if decision["route"] != "refused" else "refuse"
        accepted = case["expect"] if isinstance(case["expect"], list) else [case["expect"]]
        verdict = "right" if got in accepted else "refused" if got == "refuse" else "wrong"
        # The right answer read for the wrong platform, measure or period is a wrong answer.
        slots = decision.get("slots") or Slots()
        read = {"platform": slots.platform, "campaign": slots.campaign, "measure": slots.measure,
                "window": slots.window["kind"] if slots.window else None}
        mismatched = {key: read[key] for key, value in case.get("expect_slots", {}).items() if read[key] != value}
        if verdict == "right" and mismatched:
            verdict = "wrong"
        results.append({**case, "got": got, "verdict": verdict, "score": decision.get("score"),
                        **({"slots_read": mismatched} if mismatched else {})})
    tally = {key: sum(item["verdict"] == key for item in results) for key in ("right", "wrong", "refused")}
    splits = {split: {key: sum(item["verdict"] == key for item in results if item.get("split") == split)
                      for key in ("right", "wrong", "refused")} for split in ("dev", "holdout")}
    return {"mode": resolve_mode(mode), "cases": len(results), **tally, "splits": splits, "results": results}


def usage(connection: sqlite3.Connection, days: int = 7) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = connection.execute("SELECT route, COUNT(*) questions, ROUND(AVG(latency_ms)) avg_ms FROM ask_log "
                              "WHERE asked_at>=? GROUP BY route ORDER BY route", (since,)).fetchall()
    return {"days": days, "retrieval_mode": resolve_mode(), "by_route": [dict(row) for row in rows]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the governed metrics a question, or score the contract.")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--mode", choices=("auto", "keyword", "hybrid"), default="auto")
    parser.add_argument("--eval", action="store_true")
    args = parser.parse_args()
    if args.eval:
        failed = False
        for mode in (("keyword", "hybrid") if args.mode == "auto" else (args.mode,)):
            if mode == "hybrid" and resolve_mode("auto") != "hybrid":
                print("hybrid: skipped (onnxruntime/tokenizers not installed)")
                continue
            report = run_eval(mode)
            for item in report["results"]:
                if item["verdict"] != "right":
                    print(f"  {item['verdict'].upper():<8} {item['question']!r}: expected {item['expect']}, "
                          f"got {item['got']} (score {item['score']})")
            splits = "; ".join(f"{name} {v['right']}/{v['wrong']}/{v['refused']}" for name, v in report["splits"].items())
            print(f"{mode}: {report['right']} right, {report['wrong']} wrong, {report['refused']} refused "
                  f"of {report['cases']} (right/wrong/refused by split: {splits})")
            failed = failed or report["wrong"] > 0
        raise SystemExit(1 if failed else 0)
    from growthops.db import connect, initialize

    connection = connect(args.database)
    try:
        initialize(connection)
        print(json.dumps(answer(connection, args.question or "", None if args.mode == "auto" else args.mode), indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
