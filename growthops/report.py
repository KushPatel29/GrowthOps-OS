"""Governed metrics, measurement health and deterministic executive observations."""

from __future__ import annotations

import argparse
import json
import sqlite3

from growthops.attribution import MODELS
from growthops.attribution import summary as attribution_summary
from growthops.db import connect, initialize
from growthops.funnel import funnel, lifecycle_integrity

PAID_MEDIA = ("paid_social", "paid_search")
TARGETS = {
    "utm_completeness": 0.95,
    "campaign_registry_match": 0.98,
    "crm_owner_completeness": 0.99,
    "lifecycle_integrity": 0.99,
    "deal_payment_reconciliation": 0.99,
}


def _one(connection: sqlite3.Connection, query: str) -> int:
    return int(connection.execute(query).fetchone()[0] or 0)


def _ratio(numerator: float, denominator: float, digits: int = 4) -> float | None:
    return round(numerator / denominator, digits) if denominator else None


def paid_campaigns(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute(
        "SELECT campaign_id FROM campaigns WHERE medium IN ('paid_social','paid_search')"
    )}


def metrics(connection: sqlite3.Connection) -> dict:
    leads = _one(connection, "SELECT COUNT(*) FROM contacts")
    unique_people = _one(connection, "SELECT COUNT(DISTINCT LOWER(TRIM(email))) FROM contacts")
    mqls = _one(connection, "SELECT COUNT(DISTINCT contact_id) FROM lifecycle_events WHERE stage='mql'")
    closed_won = _one(connection, "SELECT COUNT(*) FROM deals WHERE stage='closed_won'")
    closed_lost = _one(connection, "SELECT COUNT(*) FROM deals WHERE stage='closed_lost'")
    # Sales win rate excludes self-serve add-on deals, which are only created when won.
    sales_won = _one(connection, "SELECT COUNT(*) FROM deals WHERE stage='closed_won' AND COALESCE(product_id,'')<>'community'")
    booked = _one(connection, "SELECT SUM(amount_cents) FROM deals WHERE stage='closed_won'")
    pipeline = _one(connection, "SELECT SUM(amount_cents) FROM deals WHERE stage='open'")
    spend = _one(connection, "SELECT SUM(spend_cents) FROM ad_spend_daily")
    gross = _one(connection, "SELECT SUM(amount_cents) FROM payments WHERE status='succeeded'")
    refunds = _one(connection, "SELECT SUM(amount_cents) FROM refunds")
    customers = _one(connection, "SELECT COUNT(DISTINCT customer_id) FROM payments WHERE status='succeeded'")
    net = gross - refunds
    paid = paid_campaigns(connection)
    paid_performance = [row for row in campaign_performance(connection) if row["campaign_id"] in paid]
    paid_leads = sum(row["leads"] for row in paid_performance)
    paid_mqls = sum(row["mqls"] for row in paid_performance)
    paid_cash = sum(row["net_cash_cents"] for row in paid_performance)
    return {
        "leads": leads,
        "unique_people": unique_people,
        "mqls": mqls,
        "closed_won_deals": closed_won,
        "closed_lost_deals": closed_lost,
        "win_rate": _ratio(sales_won, sales_won + closed_lost),
        "customers": customers,
        "booked_revenue_cents": booked,
        "open_pipeline_cents": pipeline,
        "spend_cents": spend,
        "paid_leads": paid_leads,
        "paid_mqls": paid_mqls,
        "paid_attributed_net_cash_cents": paid_cash,
        "gross_collected_cents": gross,
        "refunds_cents": refunds,
        "net_collected_cents": net,
        "lead_to_mql_rate": _ratio(mqls, leads),
        "cost_per_lead_cents": round(spend / paid_leads) if paid_leads else None,
        "cost_per_mql_cents": round(spend / paid_mqls) if paid_mqls else None,
        "net_cash_roas": _ratio(paid_cash, spend),
    }


def measurement_health(connection: sqlite3.Connection) -> dict:
    eligible = "(t.campaign_id IS NULL OR t.campaign_id!='direct')"
    total_contacts = _one(connection, "SELECT COUNT(*) FROM contacts")
    total_touches = _one(connection, f"SELECT COUNT(*) FROM touches t WHERE {eligible}")
    missing_utm = _one(connection, f"SELECT COUNT(*) FROM touches t WHERE {eligible} AND (utm_source IS NULL OR TRIM(utm_source)='')")
    invalid_campaign = _one(connection, f"SELECT COUNT(*) FROM touches t LEFT JOIN campaigns c ON c.campaign_id=t.campaign_id WHERE {eligible} AND (c.campaign_id IS NULL OR c.registry_valid=0)")
    missing_owner = _one(connection, "SELECT COUNT(*) FROM contacts WHERE owner_id IS NULL")
    duplicate_emails = _one(connection, "SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM contacts GROUP BY LOWER(TRIM(email)) HAVING COUNT(*)>1)")
    # Renewals legitimately have no new deal; every other successful payment must map to one.
    matchable = "p.status='succeeded' AND p.payment_type<>'renewal'"
    total_payments = _one(connection, f"SELECT COUNT(*) FROM payments p WHERE {matchable}")
    unmatched = connection.execute(
        f"""SELECT COUNT(*), COALESCE(SUM(p.amount_cents),0) FROM payments p
            LEFT JOIN deals d ON d.deal_id=p.deal_id WHERE {matchable} AND d.deal_id IS NULL"""
    ).fetchone()
    unassigned = next((row["net_cash_cents"] for row in attribution_summary(connection, "lead_creation")
                       if row["campaign_id"] is None), 0)
    return {
        "utm_completeness": _ratio(total_touches - missing_utm, total_touches),
        "campaign_registry_match": _ratio(total_touches - invalid_campaign, total_touches),
        "crm_owner_completeness": _ratio(total_contacts - missing_owner, total_contacts),
        "duplicate_contact_rows": duplicate_emails,
        "deal_payment_reconciliation": _ratio(total_payments - unmatched[0], total_payments),
        "unmatched_payment_count": unmatched[0],
        "unmatched_payment_cents": unmatched[1],
        "unassigned_net_cash_cents": unassigned,
        **lifecycle_integrity(connection),
    }


def campaign_performance(connection: sqlite3.Connection) -> list[dict]:
    """Leads, MQLs, customers and lead-creation net cash per registered campaign."""
    rows = connection.execute(
        """
        WITH lead_touch AS MATERIALIZED (
          SELECT contact_id, campaign_id,
                 ROW_NUMBER() OVER (PARTITION BY contact_id ORDER BY occurred_at DESC, touch_id DESC) rn
          FROM touches WHERE touch_type='lead_creation'
        ), lead_counts AS MATERIALIZED (
          SELECT t.campaign_id, COUNT(*) leads,
                 SUM(EXISTS (SELECT 1 FROM lifecycle_events e
                             WHERE e.stage='mql' AND e.contact_id=t.contact_id)) mqls,
                 SUM(EXISTS (SELECT 1 FROM payments p
                             WHERE p.customer_id=t.contact_id AND p.status='succeeded')) customers
          FROM lead_touch t WHERE t.rn=1 AND t.campaign_id IS NOT NULL GROUP BY t.campaign_id
        ), spend AS MATERIALIZED (
          SELECT campaign_id, SUM(spend_cents) spend_cents FROM ad_spend_daily GROUP BY campaign_id
        )
        SELECT c.campaign_id, c.source, c.medium, COALESCE(s.spend_cents,0) spend_cents,
               COALESCE(l.leads,0) leads, COALESCE(l.mqls,0) mqls, COALESCE(l.customers,0) customers
        FROM campaigns c
        LEFT JOIN spend s ON s.campaign_id=c.campaign_id
        LEFT JOIN lead_counts l ON l.campaign_id=c.campaign_id
        ORDER BY c.campaign_id
        """
    ).fetchall()
    cash = {row["campaign_id"]: row["net_cash_cents"]
            for row in attribution_summary(connection, "lead_creation")}
    return [{**dict(row), "net_cash_cents": cash.get(row["campaign_id"], 0)} for row in rows]


def executive_brief(connection: sqlite3.Connection) -> dict:
    kpis = metrics(connection)
    quality = measurement_health(connection)
    observations = []
    if quality["utm_completeness"] is not None and quality["utm_completeness"] < TARGETS["utm_completeness"]:
        observations.append({
            "finding": "UTM completeness is below the 95% target.",
            "evidence": f"{quality['utm_completeness']:.1%} of acquisition touches carry a source; "
                        f"${quality['unassigned_net_cash_cents'] / 100:,.0f} of net cash cannot be assigned to a campaign.",
            "action": "Inspect forms and campaign links with missing source values.",
        })
    if quality["unmatched_payment_count"]:
        observations.append({
            "finding": "Some collected payments cannot be joined to a closed-won deal.",
            "evidence": f"{quality['unmatched_payment_count']} payment(s) worth ${quality['unmatched_payment_cents'] / 100:,.0f} lack a matching deal.",
            "action": "Review customer identity and migration mappings before using deal-based attribution.",
        })
    if quality["campaign_registry_match"] is not None and quality["campaign_registry_match"] < TARGETS["campaign_registry_match"]:
        observations.append({
            "finding": "Campaign naming does not meet the registry target.",
            "evidence": f"{quality['campaign_registry_match']:.1%} of touches map to a valid registered campaign.",
            "action": "Repair source and campaign aliases before comparing channel performance.",
        })
    if quality["crm_owner_completeness"] is not None and quality["crm_owner_completeness"] < TARGETS["crm_owner_completeness"]:
        observations.append({
            "finding": "Some CRM contacts have no owner.",
            "evidence": f"{quality['crm_owner_completeness']:.1%} of contacts have an owner against a 99% target.",
            "action": "Backfill owners from the legacy CRM and fix the routing workflow for new leads.",
        })
    if quality["lifecycle_integrity"] is not None and quality["lifecycle_integrity"] < TARGETS["lifecycle_integrity"]:
        observations.append({
            "finding": "Some paid journeys have missing or out-of-order CRM stages.",
            "evidence": f"{quality['valid_paid_journeys']} of {quality['paid_contact_count']} paid journeys pass the stage check.",
            "action": "Audit migration mappings and deal-stage automation for affected contacts.",
        })
    if quality["duplicate_contact_rows"]:
        observations.append({
            "finding": "Duplicate CRM contacts inflate lead counts.",
            "evidence": f"{quality['duplicate_contact_rows']} extra contact rows share a normalized email with another contact.",
            "action": "Merge duplicates after confirming the surviving record; do not auto-merge customers.",
        })
    return {"metrics": kpis, "measurement_health": quality, "targets": TARGETS, "observations": observations}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/growthops-sample.db")
    args = parser.parse_args()
    connection = connect(args.database)
    initialize(connection)
    print(json.dumps({**executive_brief(connection), "campaigns": campaign_performance(connection),
                      "funnel": funnel(connection),
                      "attribution": {model: attribution_summary(connection, model) for model in MODELS}},
                     indent=2))
    connection.close()


if __name__ == "__main__":
    main()
