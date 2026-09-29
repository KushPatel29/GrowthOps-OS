"""Read-only renewal-risk monitor for the synthetic community subscription."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta

from growthops.communications import latest_consent
from growthops.db import connect
from growthops.scenario import AS_OF

DUE_SOON_DAYS = 14


def monitor(connection: sqlite3.Connection, as_of: date = AS_OF, due_soon_days: int = DUE_SOON_DAYS) -> dict:
    rows = connection.execute(
        """SELECT s.subscription_id, s.customer_id, s.plan_id, s.renewal_due_at,
                  COUNT(a.attempt_id) AS attempts,
                  COALESCE(SUM(CASE WHEN a.outcome='failed' AND a.attempted_at>=DATE(s.renewal_due_at,'-7 days') THEN 1 ELSE 0 END),0) AS failed_attempts,
                  (SELECT failure_code FROM renewal_attempts f WHERE f.subscription_id=s.subscription_id
                     AND f.outcome='failed' ORDER BY f.attempted_at DESC LIMIT 1) AS last_failure
           FROM subscriptions s LEFT JOIN renewal_attempts a
             ON a.subscription_id=s.subscription_id
           WHERE s.status='active'
           GROUP BY s.subscription_id, s.customer_id, s.plan_id, s.renewal_due_at
           ORDER BY s.renewal_due_at, s.subscription_id"""
    ).fetchall()
    due_soon = as_of + timedelta(days=due_soon_days)
    issues = []
    for row in rows:
        due = datetime.fromisoformat(row["renewal_due_at"]).date()
        severity = "high" if due <= as_of or row["failed_attempts"] else "medium" if due <= due_soon else None
        if severity is None:
            continue
        issues.append({
            "subscription_id": row["subscription_id"],
            "customer_id": row["customer_id"],
            "due_date": due.isoformat(),
            "days_to_due": (due - as_of).days,
            "failed_attempts": row["failed_attempts"],
            "last_failure": row["last_failure"],
            "severity": severity,
            "investigation": "Payment failed or renewal overdue: update the card and have customer success call."
                             if severity == "high" else "Confirm the upcoming renewal and payment method.",
        })
    outcomes = connection.execute(
        """SELECT SUM(outcome='succeeded') succeeded,
                  COUNT(DISTINCT CASE WHEN outcome='failed' THEN subscription_id END) failed_subscriptions
           FROM renewal_attempts"""
    ).fetchone()
    canceled = connection.execute("SELECT COUNT(*) FROM subscriptions WHERE status='canceled'").fetchone()[0]
    renewed = outcomes["succeeded"] or 0
    return {
        "as_of": as_of.isoformat(),
        "active_subscriptions": len(rows),
        "high_risk": sum(item["severity"] == "high" for item in issues),
        "due_soon": sum(item["severity"] == "medium" for item in issues),
        "due_soon_days": due_soon_days,
        "renewals_collected": renewed,
        "canceled": canceled,
        "issues": issues,
        "synthetic": True,
    }


def action_proposals(connection: sqlite3.Connection, as_of: date = AS_OF) -> dict:
    """Explain next CRM tasks; never send a message or mutate a subscription."""
    issues = monitor(connection, as_of)["issues"]
    proposals = []
    for issue in issues:
        consent = latest_consent(connection, issue["customer_id"], "email")
        channel = "email" if consent and consent["status"] == "granted" else "manual_call"
        reason = ("failed_payment" if issue["failed_attempts"] else
                  "renewal_overdue" if issue["days_to_due"] <= 0 else "renewal_due_soon")
        proposals.append({
            "subscription_id": issue["subscription_id"],
            "person_key": issue["customer_id"], "severity": issue["severity"],
            "reason": reason, "due_date": issue["due_date"],
            "suggested_task": "Review payment method and account state" if issue["severity"] == "high"
            else "Confirm renewal readiness", "suggested_channel": channel,
            "email_consent_status": consent["status"] if consent else "unknown",
            "action_status": "proposal_only", "provider_dispatched": False,
        })
    return {"scope": "synthetic_renewal_proposals", "as_of": as_of.isoformat(),
            "count": len(proposals), "proposals": proposals,
            "caveat": "Engagement, satisfaction and support signals are unavailable; no CRM task or message was sent."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--as-of", type=date.fromisoformat, default=AS_OF)
    args = parser.parse_args()
    connection = connect(args.database)
    try:
        print(json.dumps(monitor(connection, args.as_of), indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
