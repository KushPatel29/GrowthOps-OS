"""Safe natural-language access to a small governed metric catalog.

Questions are matched to allowlisted topics; each topic runs a fixed, tested
function. Nothing the user types is ever executed as SQL.
"""

from __future__ import annotations

import re
import sqlite3

from growthops.brief import findings
from growthops.experiments import analyze as experiment_analysis
from growthops.funnel import funnel
from growthops.reconciliation import four_numbers, platform_comparison
from growthops.renewals import monitor as renewal_monitor
from growthops.report import campaign_performance, measurement_health, metrics
from growthops.workflow import health as workflow_health

TOPICS = ("which revenue number is right", "what changed", "cash and refunds", "paid campaigns",
          "funnel conversion", "the CTA experiment", "renewal risk", "data quality", "automation health")


def _usd(cents: float) -> str:
    return f"${cents / 100:,.0f}"


def answer(connection: sqlite3.Connection, question: str) -> dict:
    text = " ".join(question.lower().split())[:300]
    if ";" in text or "--" in text:
        return {"answer": "This interface accepts a metric question only. Try one of: " + ", ".join(TOPICS) + ".",
                "source": "governed metric catalog", "metric_id": None}

    def mentions(*phrases: str) -> bool:
        return any(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) for phrase in phrases)

    if mentions("which number", "revenue number", "number is right", "right number", "platform", "meta says", "google says", "reconcile", "reconciliation", "roas", "truth"):
        numbers = four_numbers(connection)
        meta = next(row for row in platform_comparison(connection) if row["platform"] == "meta")
        return {
            "answer": (f"Ad platforms claim {_usd(numbers['platform_reported_total_cents'])}, the CRM books "
                       f"{_usd(numbers['crm_booked_cents'])}, and {_usd(numbers['net_collected_cents'])} of net cash was "
                       f"actually collected. The warehouse credits {_usd(numbers['paid_media_net_cash_cents'])} of that to "
                       f"paid media. Meta reports {meta['platform_roas']}x ROAS; the warehouse measures "
                       f"{meta['warehouse_roas']}x. Net collected cash is the number to run the business on."),
            "source": "reconciliation bridges", "metric_id": "revenue_truth",
        }
    if mentions("what changed", "why", "anomaly", "anomalies", "morning", "brief", "happening"):
        top = [item for item in findings(connection) if item["category"] in ("growth", "tracking", "automation")][:2]
        if not top:
            return {"answer": "No material changes were detected in the last 45 days.",
                    "source": "diagnostics", "metric_id": "anomaly_episodes"}
        return {"answer": " ".join(f"{item['finding']} {item['why']}." for item in top).replace("..", "."),
                "source": "diagnostics + workflow log", "metric_id": "anomaly_episodes"}
    if mentions("access", "webhook", "automation", "workflow", "dead letter", "dead-letter", "onboarding"):
        ops = workflow_health(connection)
        return {
            "answer": (f"Of {ops['events']} recent payment events, {ops['completed']} completed and {ops['dead_letter']} "
                       f"are in the dead-letter queue; {ops['duplicate_deliveries_absorbed']} duplicate deliveries were "
                       f"absorbed with no double side effects. {ops['within_5_minutes']:.0%} of events reached access "
                       "within five minutes."),
            "source": "processed_events, workflow_step_attempts", "metric_id": "automation_health",
        }
    if mentions("experiment", "variant", "cta", "a/b", "test"):
        result = experiment_analysis(connection, "cta_growth_plan")
        comparison = result["comparison"]
        lo, hi = comparison["cash_per_visitor_bootstrap_95_ci_cents"]
        return {
            "answer": (f"CTA B changed lead rate by {comparison['variant_b_minus_a_lead_rate'] * 100:+.1f} points "
                       f"(p = {comparison['lead_rate_p_value']}) and MQLs per lead by "
                       f"{comparison['variant_b_minus_a_mql_per_lead'] * 100:+.1f} points. Cash per visitor moved "
                       f"${comparison['variant_b_minus_a_cash_per_visitor_cents'] / 100:+.2f} (95% interval "
                       f"${lo / 100:.2f} to ${hi / 100:.2f}). Decision: {comparison['decision']}"),
            "source": "experiment exposures, lifecycle, payments, refunds", "metric_id": "experiment_cash_per_visitor",
        }
    if mentions("renewal", "renewals", "subscription", "past due", "overdue", "churn"):
        risk = renewal_monitor(connection)
        return {
            "answer": (f"As of {risk['as_of']}, {risk['high_risk']} of {risk['active_subscriptions']} active subscriptions "
                       f"are overdue or failed, {risk['due_soon']} more renew within {risk['due_soon_days']} days, and "
                       f"{risk['canceled']} have canceled."),
            "source": "subscriptions, renewal attempts", "metric_id": "renewal_risk",
        }
    if mentions("funnel", "drop off", "drop-off", "conversion", "mql", "pipeline"):
        stages = funnel(connection)
        rates = [row for row in stages[:7] if row["from_previous_rate"] is not None]
        weakest = min(rates, key=lambda row: row["from_previous_rate"])
        return {
            "answer": (f"The weakest step before payment is into {weakest['stage'].replace('_', ' ')}: "
                       f"{weakest['from_previous_rate']:.1%} of the prior stage converts, with a median of "
                       f"{weakest['median_days_from_previous']} days."),
            "source": "lifecycle events", "metric_id": "funnel_adjacent_conversion",
        }
    if mentions("campaign", "campaigns", "channel", "paid media", "ad spend", "cpl", "cost per"):
        campaigns = [row for row in campaign_performance(connection) if row["medium"].startswith("paid_") and row["spend_cents"]]
        best = max(campaigns, key=lambda row: row["net_cash_cents"] / row["spend_cents"])
        worst = min(campaigns, key=lambda row: row["net_cash_cents"] / row["spend_cents"])
        return {
            "answer": (f"Best paid campaign by net cash per dollar: {best['campaign_id']} "
                       f"({best['net_cash_cents'] / best['spend_cents']:.1f}x on {_usd(best['spend_cents'])}). Weakest: "
                       f"{worst['campaign_id']} ({worst['net_cash_cents'] / worst['spend_cents']:.1f}x). "
                       "Lead-creation attribution is descriptive, not incremental."),
            "source": "campaign performance mart", "metric_id": "paid_campaign_net_cash",
        }
    if mentions("quality", "utm", "tracking", "duplicate", "duplicates", "owner", "hygiene"):
        health = measurement_health(connection)
        return {
            "answer": (f"UTM completeness is {health['utm_completeness']:.1%}, campaign registry match "
                       f"{health['campaign_registry_match']:.1%}, owner coverage {health['crm_owner_completeness']:.1%}, "
                       f"with {health['duplicate_contact_rows']} duplicate contact rows and "
                       f"{_usd(health['unassigned_net_cash_cents'])} of net cash that cannot be credited to a campaign."),
            "source": "measurement health", "metric_id": "measurement_health",
        }
    if mentions("cash", "revenue", "refund", "refunds", "collected", "sales"):
        values = metrics(connection)
        return {
            "answer": (f"Gross collected was {_usd(values['gross_collected_cents'])}; refunds were "
                       f"{_usd(values['refunds_cents'])}; net collected cash was {_usd(values['net_collected_cents'])}. "
                       f"Closed-won deal value is a separate {_usd(values['booked_revenue_cents'])} booking measure."),
            "source": "payments, refunds, closed-won deals", "metric_id": "net_collected_cash",
        }
    return {"answer": "I can answer questions about " + ", ".join(TOPICS) + ".",
            "source": "governed metric catalog", "metric_id": None}
