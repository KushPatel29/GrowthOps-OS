"""Safe natural-language access to a small governed metric catalog."""

from __future__ import annotations

import sqlite3
import re

from growthops.experiments import analyze as experiment_analysis
from growthops.funnel import funnel
from growthops.renewals import monitor as renewal_monitor
from growthops.report import campaign_performance, metrics


def answer(connection: sqlite3.Connection, question: str) -> dict:
    text = " ".join(question.lower().split())[:300]
    if ";" in text or "--" in text:
        return {"answer": "This interface accepts a metric question only. Try asking about cash, campaigns, funnel, experiments, or renewals.",
                "source": "governed metric catalog", "metric_id": None}

    def mentions(*phrases: str) -> bool:
        return any(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) for phrase in phrases)

    if mentions("experiment", "variant", "cta", "a/b"):
        comparison = experiment_analysis(connection, "cta_growth_plan", bootstrap_draws=300)["comparison"]
        return {
            "answer": f"CTA B gained {comparison['variant_b_minus_a_lead_rate']:.1%} in lead rate but changed net cash per visitor by ${comparison['variant_b_minus_a_cash_per_visitor_cents']/100:.2f} versus A. The cash interval includes zero, so keep A pending a larger revenue sample.",
            "source": "experiment exposures, payments, refunds", "metric_id": "experiment_cash_per_visitor",
        }
    if mentions("renewal", "subscription", "past due", "overdue"):
        risk = renewal_monitor(connection)
        return {
            "answer": f"As of {risk['as_of']}, {risk['high_risk']} of {risk['active_subscriptions']} active subscriptions are high risk and {risk['due_soon']} more are due within seven days.",
            "source": "subscriptions, renewal attempts", "metric_id": "renewal_risk",
        }
    if mentions("funnel", "drop off", "drop-off", "conversion", "mql"):
        stages = funnel(connection)
        rates = [row for row in stages if row["from_previous_rate"] is not None]
        weakest = min(rates, key=lambda row: row["from_previous_rate"])
        return {
            "answer": f"The lowest adjacent-stage conversion is into {weakest['stage']}: {weakest['from_previous_rate']:.1%} of the prior stage, with {weakest['people']} people reaching it.",
            "source": "lifecycle events", "metric_id": "funnel_adjacent_conversion",
        }
    if mentions("campaign", "channel", "paid media", "ad spend"):
        campaigns = [row for row in campaign_performance(connection) if row["medium"].startswith("paid_")]
        best = max(campaigns, key=lambda row: row["net_cash_cents"])
        return {
            "answer": f"Among paid campaigns, {best['campaign_id']} has the highest lead-creation attributed net cash at ${best['net_cash_cents']/100:,.0f} on ${best['spend_cents']/100:,.0f} of spend. This is descriptive attribution, not incremental return.",
            "source": "campaign performance mart", "metric_id": "paid_campaign_net_cash",
        }
    if mentions("cash", "revenue", "refund", "refunds", "collected"):
        values = metrics(connection)
        return {
            "answer": f"Gross collected was ${values['gross_collected_cents']/100:,.0f}; refunds were ${values['refunds_cents']/100:,.0f}; net collected cash was ${values['net_collected_cents']/100:,.0f}. Booked deal value is a separate ${values['booked_revenue_cents']/100:,.0f} measure.",
            "source": "payments, refunds, closed-won deals", "metric_id": "net_collected_cash",
        }
    return {
        "answer": "I can answer questions about cash, paid campaigns, funnel conversion, the CTA experiment, and renewal risk. Choose one of those topics.",
        "source": "governed metric catalog", "metric_id": None,
    }
