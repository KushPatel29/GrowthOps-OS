"""Read-only renewal-risk monitor for the synthetic community plan."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta

from growthops.db import connect


def monitor(connection: sqlite3.Connection, as_of: date = date(2026, 9, 26)) -> dict:
    rows = connection.execute(
        """SELECT s.subscription_id, s.customer_id, s.plan_id, s.renewal_due_at,
                  COUNT(a.attempt_id) AS attempts,
                  SUM(CASE WHEN a.outcome='failed' THEN 1 ELSE 0 END) AS failed_attempts,
                  SUM(CASE WHEN a.outcome='succeeded' THEN 1 ELSE 0 END) AS successful_attempts
           FROM subscriptions s LEFT JOIN renewal_attempts a
             ON a.subscription_id=s.subscription_id
           WHERE s.status='active'
           GROUP BY s.subscription_id, s.customer_id, s.plan_id, s.renewal_due_at
           ORDER BY s.renewal_due_at, s.subscription_id"""
    ).fetchall()
    due_soon = as_of + timedelta(days=7)
    issues = []
    for row in rows:
        due = datetime.fromisoformat(row["renewal_due_at"]).date()
        if row["successful_attempts"]:
            continue
        severity = "high" if due < as_of or row["failed_attempts"] else "medium" if due <= due_soon else None
        if severity is None:
            continue
        issues.append({
            "subscription_id": row["subscription_id"],
            "customer_id": row["customer_id"],
            "due_date": due.isoformat(),
            "days_to_due": (due - as_of).days,
            "failed_attempts": row["failed_attempts"],
            "severity": severity,
            "investigation": "Review payment method and contact customer success." if severity == "high"
                             else "Confirm upcoming renewal and payment method.",
        })
    return {
        "as_of": as_of.isoformat(),
        "active_subscriptions": len(rows),
        "high_risk": sum(item["severity"] == "high" for item in issues),
        "due_soon": sum(item["severity"] == "medium" for item in issues),
        "issues": issues,
        "synthetic": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 26))
    args = parser.parse_args()
    connection = connect(args.database)
    try:
        print(json.dumps(monitor(connection, args.as_of), indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
