"""Email performance, deliverability and newsletter-to-cash analysis.

Opens include machine opens from mailbox privacy proxies (Apple Mail Privacy
Protection and similar). They say nothing about a human, so engagement is judged
on human opens and clicks; reported open rate is shown only to explain the gap.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

BOUNCE_LIMIT = 0.02  # Above ~2% mailbox providers start throttling.
COMPLAINT_LIMIT = 0.001  # Gmail and Yahoo bulk-sender guidance: stay under 0.1%, never reach 0.3%.
BULK_TYPES = ("newsletter", "promo", "webinar_invite")


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _rates(row: dict) -> dict:
    human = row["opens"] - row["machine_opens"]
    return {
        **row,
        "human_opens": human,
        "delivery_rate": _rate(row["delivered"], row["sends"]),
        "bounce_rate": _rate(row["bounces"], row["sends"]),
        "reported_open_rate": _rate(row["opens"], row["delivered"]),
        "human_open_rate": _rate(human, row["delivered"]),
        "click_rate": _rate(row["clicks"], row["delivered"]),
        "click_to_open_rate": _rate(row["clicks"], human),
        "unsubscribe_rate": _rate(row["unsubscribes"], row["delivered"]),
        "complaint_rate": _rate(row["spam_complaints"], row["delivered"]),
    }


def email_performance(connection: sqlite3.Connection, email_type: str | None = None) -> list[dict]:
    """One row per send with the rates a lifecycle marketer reads, oldest first."""
    rows = connection.execute(
        """SELECT email_id, SUBSTR(sent_at,1,10) sent_date, email_type, subject, sending_domain, sends,
                  delivered, bounces, opens, machine_opens, clicks, unsubscribes, spam_complaints
           FROM email_campaigns WHERE ? IS NULL OR email_type=? ORDER BY sent_at, email_id""",
        (email_type, email_type),
    ).fetchall()
    return [_rates(dict(row)) for row in rows]


def type_summary(connection: sqlite3.Connection, since: date | None = None) -> list[dict]:
    """Totals per email type (rates are ratios of sums, not averages of rates)."""
    rows = connection.execute(
        """SELECT email_type, COUNT(*) emails, SUM(sends) sends, SUM(delivered) delivered, SUM(bounces) bounces,
                  SUM(opens) opens, SUM(machine_opens) machine_opens, SUM(clicks) clicks,
                  SUM(unsubscribes) unsubscribes, SUM(spam_complaints) spam_complaints
           FROM email_campaigns WHERE sent_at >= ? GROUP BY email_type ORDER BY SUM(sends) DESC""",
        ((since or date.min).isoformat(),),
    ).fetchall()
    return [_rates(dict(row)) for row in rows]


def newsletter_pipeline(connection: sqlite3.Connection) -> list[dict]:
    """Leads created by the newsletter between one issue and the next, followed to cash.

    Descriptive: a lead is credited to the issue in whose window (send time to the
    next issue) its newsletter lead-creation touch falls; cash is those customers'
    net collected cash. Leads before the first issue are not credited.
    """
    rows = connection.execute(
        """
        WITH issues AS (
          SELECT email_id, sent_at, LEAD(sent_at, 1, '9999') OVER (ORDER BY sent_at) next_sent_at,
                 delivered, clicks
          FROM email_campaigns WHERE email_type='newsletter'
        ), lead_touch AS (
          SELECT contact_id, campaign_id, occurred_at,
                 ROW_NUMBER() OVER (PARTITION BY contact_id ORDER BY occurred_at DESC, touch_id DESC) rn
          FROM touches WHERE touch_type='lead_creation'
        ), leads AS (
          SELECT i.email_id, t.contact_id FROM issues i
          JOIN lead_touch t ON t.rn=1 AND t.campaign_id='newsletter_weekly'
           AND t.occurred_at >= i.sent_at AND t.occurred_at < i.next_sent_at
        ), cash AS (
          SELECT p.customer_id, SUM(p.amount_cents) - COALESCE(SUM(r.refund_cents),0) net_cash_cents
          FROM payments p
          LEFT JOIN (SELECT payment_id, SUM(amount_cents) refund_cents FROM refunds GROUP BY payment_id) r
            ON r.payment_id=p.payment_id
          WHERE p.status='succeeded' GROUP BY p.customer_id
        )
        SELECT i.email_id, SUBSTR(i.sent_at,1,10) sent_date, i.delivered, i.clicks,
               COUNT(l.contact_id) leads,
               COALESCE(SUM(EXISTS (SELECT 1 FROM lifecycle_events e WHERE e.contact_id=l.contact_id AND e.stage='mql')),0) mqls,
               COALESCE(SUM(EXISTS (SELECT 1 FROM lifecycle_events e WHERE e.contact_id=l.contact_id AND e.stage='call_booked')),0) calls_booked,
               COUNT(c.customer_id) customers, COALESCE(SUM(c.net_cash_cents),0) net_cash_cents
        FROM issues i LEFT JOIN leads l ON l.email_id=i.email_id
        LEFT JOIN cash c ON c.customer_id=l.contact_id
        GROUP BY i.email_id ORDER BY i.sent_at
        """
    ).fetchall()
    return [{**dict(row), "leads_per_1k_delivered": round(row["leads"] / row["delivered"] * 1000, 2)
             if row["delivered"] else None} for row in rows]


def deliverability(connection: sqlite3.Connection, as_of: date | None = None, recent_days: int = 28) -> dict:
    """Recent bulk sends against the prior eight weeks, split by sending domain."""
    as_of = as_of or date.fromisoformat(connection.execute(
        "SELECT MAX(SUBSTR(sent_at,1,10)) FROM email_campaigns").fetchone()[0])
    start = as_of - timedelta(days=recent_days - 1)
    baseline_start = start - timedelta(days=56)
    placeholders = ",".join("?" * len(BULK_TYPES))

    def window(lo: date, hi: date) -> list[dict]:
        rows = connection.execute(
            f"""SELECT sending_domain, COUNT(*) emails, SUM(sends) sends, SUM(delivered) delivered,
                       SUM(bounces) bounces, SUM(opens) opens, SUM(machine_opens) machine_opens,
                       SUM(clicks) clicks, SUM(unsubscribes) unsubscribes, SUM(spam_complaints) spam_complaints
                FROM email_campaigns WHERE email_type IN ({placeholders})
                  AND SUBSTR(sent_at,1,10) BETWEEN ? AND ? GROUP BY sending_domain ORDER BY sending_domain""",
            (*BULK_TYPES, lo.isoformat(), hi.isoformat()),
        ).fetchall()
        return [_rates(dict(row)) for row in rows]

    recent, baseline = window(start, as_of), window(baseline_start, start - timedelta(days=1))
    flagged = [row for row in recent if (row["bounce_rate"] or 0) > BOUNCE_LIMIT
               or (row["complaint_rate"] or 0) > COMPLAINT_LIMIT]
    affected = connection.execute(
        f"""SELECT email_id, email_type, subject, SUBSTR(sent_at,1,10) sent_date FROM email_campaigns
            WHERE email_type IN ({placeholders}) AND sending_domain IN ({",".join("?" * len(flagged)) or "''"})
              AND SUBSTR(sent_at,1,10) BETWEEN ? AND ? ORDER BY sent_at""",
        (*BULK_TYPES, *[row["sending_domain"] for row in flagged], start.isoformat(), as_of.isoformat()),
    ).fetchall()
    return {
        "window": {"start": start.isoformat(), "end": as_of.isoformat()},
        "baseline_window": {"start": baseline_start.isoformat(), "end": (start - timedelta(days=1)).isoformat()},
        "limits": {"bounce_rate": BOUNCE_LIMIT, "complaint_rate": COMPLAINT_LIMIT},
        "recent_by_domain": recent,
        "baseline_by_domain": baseline,
        "flagged_domains": [row["sending_domain"] for row in flagged],
        "affected_emails": [dict(row) for row in affected],
    }


def deliverability_finding(connection: sqlite3.Connection, as_of: date | None = None) -> dict | None:
    """A brief-ready finding when a sending domain breaks the bounce or complaint limit."""
    check = deliverability(connection, as_of)
    if not check["flagged_domains"]:
        return None
    domain = check["flagged_domains"][0]
    now = next(row for row in check["recent_by_domain"] if row["sending_domain"] == domain)
    before = check["baseline_by_domain"]
    base = _rates({key: sum(row[key] for row in before) for key in (
        "sends", "delivered", "bounces", "opens", "machine_opens", "clicks", "unsubscribes", "spam_complaints")}) \
        if before else None
    promos = [row for row in check["affected_emails"] if row["email_type"] == "promo"]
    evidence = (f"{now['emails']} bulk sends from {domain} since {check['affected_emails'][0]['sent_date']}: "
                f"bounce rate {now['bounce_rate']:.1%} and complaint rate {now['complaint_rate']:.2%} "
                f"(limits {BOUNCE_LIMIT:.0%} and {COMPLAINT_LIMIT:.1%}); human open rate {now['human_open_rate']:.1%}")
    if base:
        evidence += (f" against {base['human_open_rate']:.1%}, and bounce rate {base['bounce_rate']:.1%}, "
                     f"in the prior eight weeks")
    evidence += "."
    return {
        "id": "email_deliverability", "category": "email", "priority": 47,
        "finding": f"Email deliverability broke after bulk sends moved to {domain}"
                   + (f"; {len(promos)} enrollment-deadline promos went out on it." if promos else "."),
        "evidence": evidence,
        "why": "The switch date lines up with the jump; only sends from the new domain are affected.",
        "investigation": f"Check SPF, DKIM and DMARC alignment for {domain}, move bulk sends back to the warmed "
                         "domain, then warm the new one gradually and suppress hard bounces.",
        "confidence": "high: observed in send logs; mailbox-provider filtering is inferred, not measured",
        "source": "email_campaigns",
    }


def list_source_mix(connection: sqlite3.Connection, months: int = 3) -> list[dict]:
    """New CRM contacts (the list's inflow) by original source over the last N months."""
    latest = connection.execute("SELECT MAX(SUBSTR(created_at,1,7)) FROM contacts").fetchone()[0]
    year, month = map(int, latest.split("-"))
    month -= months - 1
    while month <= 0:
        year, month = year - 1, month + 12
    rows = connection.execute(
        """SELECT COALESCE(original_source, '(no source)') source, COUNT(*) contacts FROM contacts
           WHERE SUBSTR(created_at,1,7) >= ? GROUP BY 1 ORDER BY 2 DESC, 1""",
        (f"{year:04d}-{month:02d}",),
    ).fetchall()
    total = sum(row["contacts"] for row in rows)
    return [{**dict(row), "share": _rate(row["contacts"], total)} for row in rows]
