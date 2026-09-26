"""Paid-media efficiency and the written daily performance update.

Efficiency uses an activity basis: everything that happened inside the window
(spend, leads created, MQLs reached, calls booked, deals won, cash collected
less refunds issued) is credited to the campaign that created the lead. Cash
uses the governed lead-creation model (the latest lead-creation touch at or
before each payment), so it ties to the attribution views.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, timedelta

PAID = "('paid_social','paid_search')"
PLATFORM_LABELS = {"meta": "Meta", "google": "Google", "linkedin": "LinkedIn"}


def _ratio(numerator: float, denominator: float, digits: int = 4) -> float | None:
    return round(numerator / denominator, digits) if denominator else None


def _cents(numerator: float, denominator: float) -> int | None:
    return round(numerator / denominator) if denominator else None


def _derive(row: dict) -> dict:
    spend = row["spend_cents"]
    return {
        **row,
        "cpm_cents": _cents(spend * 1000, row["impressions"]),
        "ctr": _ratio(row["clicks"], row["impressions"]),
        "cpc_cents": _cents(spend, row["clicks"]),
        "cost_per_lead_cents": _cents(spend, row["leads"]),
        "cost_per_mql_cents": _cents(spend, row["mqls"]),
        "cost_per_booked_call_cents": _cents(spend, row["calls_booked"]),
        "lead_to_mql_rate": _ratio(row["mqls"], row["leads"]),
        "net_cash_roas": _ratio(row["net_cash_cents"], spend, 2),
    }


def paid_efficiency(connection: sqlite3.Connection, start: date, end: date, by: str = "campaign") -> list[dict]:
    """CPM, CTR, CPC, CPL, cost per MQL, cost per booked call and net-cash ROAS per paid campaign or platform.

    The last row is the paid-media total.
    """
    if by not in ("campaign", "platform"):
        raise ValueError("by must be 'campaign' or 'platform'")
    key = "c.campaign_id" if by == "campaign" else "c.platform"
    lo, hi = start.isoformat(), (end + timedelta(days=1)).isoformat()
    rows = connection.execute(
        f"""
        WITH lead_touch AS MATERIALIZED (
          SELECT contact_id, campaign_id, occurred_at,
                 ROW_NUMBER() OVER (PARTITION BY contact_id ORDER BY occurred_at DESC, touch_id DESC) rn
          FROM touches WHERE touch_type='lead_creation'
        ), owner AS (
          SELECT contact_id, campaign_id FROM lead_touch WHERE rn=1 AND campaign_id IS NOT NULL
        ), spend AS (
          SELECT campaign_id, SUM(spend_cents) spend_cents, SUM(impressions) impressions, SUM(clicks) clicks
          FROM ad_spend_daily WHERE spend_date >= ? AND spend_date < ? GROUP BY campaign_id
        ), stages AS (
          SELECT o.campaign_id,
                 SUM(e.stage='lead') leads, SUM(e.stage='mql') mqls, SUM(e.stage='call_booked') calls_booked
          FROM lifecycle_events e JOIN owner o ON o.contact_id=e.contact_id
          WHERE e.occurred_at >= ? AND e.occurred_at < ? GROUP BY o.campaign_id
        ), won AS (
          SELECT o.campaign_id, COUNT(*) closed_won_deals FROM deals d JOIN owner o ON o.contact_id=d.contact_id
          WHERE d.stage='closed_won' AND COALESCE(d.product_id,'')<>'community'
            AND d.closed_at >= ? AND d.closed_at < ? GROUP BY o.campaign_id
        ), credited AS (
          SELECT p.payment_id, p.paid_at, p.amount_cents,
                 (SELECT t.campaign_id FROM touches t WHERE t.contact_id=p.customer_id
                    AND t.touch_type='lead_creation' AND t.occurred_at<=p.paid_at
                  ORDER BY t.occurred_at DESC, t.touch_id DESC LIMIT 1) campaign_id
          FROM payments p WHERE p.status='succeeded'
        ), cash AS (
          SELECT campaign_id, SUM(cents) net_cash_cents FROM (
            SELECT campaign_id, amount_cents cents FROM credited WHERE paid_at >= ? AND paid_at < ?
            UNION ALL
            SELECT cr.campaign_id, -r.amount_cents FROM refunds r JOIN credited cr ON cr.payment_id=r.payment_id
            WHERE r.refunded_at >= ? AND r.refunded_at < ?
          ) GROUP BY campaign_id
        )
        SELECT {key} segment,
               COALESCE(SUM(s.spend_cents),0) spend_cents, COALESCE(SUM(s.impressions),0) impressions,
               COALESCE(SUM(s.clicks),0) clicks, COALESCE(SUM(st.leads),0) leads, COALESCE(SUM(st.mqls),0) mqls,
               COALESCE(SUM(st.calls_booked),0) calls_booked, COALESCE(SUM(w.closed_won_deals),0) closed_won_deals,
               COALESCE(SUM(ca.net_cash_cents),0) net_cash_cents
        FROM campaigns c
        LEFT JOIN spend s ON s.campaign_id=c.campaign_id
        LEFT JOIN stages st ON st.campaign_id=c.campaign_id
        LEFT JOIN won w ON w.campaign_id=c.campaign_id
        LEFT JOIN cash ca ON ca.campaign_id=c.campaign_id
        WHERE c.medium IN {PAID}
        GROUP BY {key} HAVING SUM(s.spend_cents) > 0 OR SUM(st.leads) > 0 ORDER BY spend_cents DESC, segment
        """,
        (lo, hi, lo, hi, lo, hi, lo, hi, lo, hi),
    ).fetchall()
    result = [_derive(dict(row)) for row in rows]
    fields = ("spend_cents", "impressions", "clicks", "leads", "mqls", "calls_booked", "closed_won_deals",
              "net_cash_cents")
    total = {"segment": "Total paid", **{field: sum(row[field] for row in result) for field in fields}}
    return [*result, _derive(total)]


def _usd(cents: float | None) -> str:
    if cents is None:
        return "n/a"
    return f"{'-' if cents < 0 else ''}${abs(cents) / 100:,.0f}"


def _vs(current: float, baseline: float) -> str:
    if not baseline:
        return "no prior-week baseline"
    return f"{(current - baseline) / baseline:+.0%} vs 7-day avg"


def daily_update(connection: sqlite3.Connection, day: date | None = None, findings: list[dict] | None = None) -> dict:
    """Yesterday against the trailing seven-day daily average, plus what needs attention today."""
    from growthops.brief import findings as brief_findings
    from growthops.campaign_links import audit_short_links
    from growthops.email_analytics import email_performance

    day = day or date.fromisoformat(connection.execute("SELECT MAX(day) FROM mart_growth_daily").fetchone()[0])
    prior_start, prior_end = day - timedelta(days=7), day - timedelta(days=1)
    columns = ("spend_cents", "leads", "mqls", "calls_booked", "closed_won_deals", "net_cash_cents")
    select = ", ".join(f"COALESCE(SUM({column}),0) {column}" for column in columns)
    today = dict(connection.execute(f"SELECT {select} FROM mart_growth_daily WHERE day=?",
                                    (day.isoformat(),)).fetchone())
    week = dict(connection.execute(f"SELECT {select} FROM mart_growth_daily WHERE day BETWEEN ? AND ?",
                                   (prior_start.isoformat(), prior_end.isoformat())).fetchone())
    average = {column: week[column] / 7 for column in columns}
    paid_day = paid_efficiency(connection, day, day, by="platform")
    paid_week = paid_efficiency(connection, day - timedelta(days=6), day, by="platform")
    emails = [row for row in email_performance(connection) if row["sent_date"] <= day.isoformat()]
    last_bulk = next((row for row in reversed(emails) if row["email_type"] != "nurture"), None)
    links = audit_short_links(connection)
    items = (findings if findings is not None else brief_findings(connection, day))[:3]

    total_day, total_week = paid_day[-1], paid_week[-1]
    lines = [
        f"Daily performance update: {day:%a %d %b %Y} (synthetic ScaleLab data)",
        "",
        "Yesterday",
        f"- Paid spend {_usd(today['spend_cents'])} ({_vs(today['spend_cents'], average['spend_cents'])}); "
        f"{total_day['leads']} paid leads at {_usd(total_day['cost_per_lead_cents'])} CPL.",
        f"- All channels: {today['leads']} leads ({_vs(today['leads'], average['leads'])}), {today['mqls']} MQLs, "
        f"{today['calls_booked']} calls booked, {today['closed_won_deals']} deals won, "
        f"{_usd(today['net_cash_cents'])} net cash.",
    ]
    lines.append("")
    lines.append("Paid media, last 7 days (activity basis; cash lags leads by weeks, so 7-day ROAS understates)")
    for row in paid_week[:-1]:
        lines.append(f"- {PLATFORM_LABELS.get(row['segment'], row['segment'])}: {_usd(row['spend_cents'])} spend, {row['leads']} leads, "
                     f"CPL {_usd(row['cost_per_lead_cents'])}, cost/MQL {_usd(row['cost_per_mql_cents'])}, "
                     f"cost/booked call {_usd(row['cost_per_booked_call_cents'])}, ROAS "
                     f"{row['net_cash_roas'] if row['net_cash_roas'] is not None else 'n/a'}x.")
    lines.append(f"- Total: {_usd(total_week['spend_cents'])} spend, CPL {_usd(total_week['cost_per_lead_cents'])}, "
                 f"cost/MQL {_usd(total_week['cost_per_mql_cents'])}, ROAS {total_week['net_cash_roas']}x.")
    if last_bulk:
        lines += ["", "Email",
                  f"- Last bulk send {last_bulk['sent_date']} ({last_bulk['email_type'].replace('_', ' ')}, "
                  f"{last_bulk['sending_domain']}): {last_bulk['delivered']:,} delivered, human open rate "
                  f"{last_bulk['human_open_rate']:.1%}, click rate {last_bulk['click_rate']:.2%}, bounce rate "
                  f"{last_bulk['bounce_rate']:.1%}."]
    lines += ["", "Needs attention"]
    for item in items:
        lines.append(f"- {item['finding']} Next: {item['investigation']}")
    if links["links_with_issues"]:
        lines.append(f"- {links['links_with_issues']} short links have UTM defects; they carried "
                     f"{links['share_of_recent_clicks_broken']:.0%} of short-link clicks in the last "
                     f"{links['recent_days']} days.")
    return {
        "day": day.isoformat(),
        "yesterday": today,
        "trailing_7_day_average": {column: round(value, 2) for column, value in average.items()},
        "paid_yesterday": paid_day,
        "paid_last_7_days": paid_week,
        "last_bulk_email": last_bulk,
        "attention": items,
        "text": "\n".join(lines),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the written daily performance update.")
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--day", type=date.fromisoformat)
    args = parser.parse_args()
    from growthops.db import connect

    connection = connect(args.database)
    try:
        print(daily_update(connection, args.day)["text"])
    finally:
        connection.close()


if __name__ == "__main__":
    main()
