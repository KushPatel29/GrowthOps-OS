"""Governed local metrics and deterministic executive observations."""

from __future__ import annotations

import argparse
import json
import sqlite3

from growthops.db import connect, initialize
from growthops.attribution import summary as attribution_summary
from growthops.funnel import funnel, lifecycle_integrity


def _one(connection: sqlite3.Connection, query: str) -> int:
    return int(connection.execute(query).fetchone()[0] or 0)


def metrics(connection: sqlite3.Connection) -> dict:
    leads = _one(connection, "SELECT COUNT(*) FROM contacts")
    mqls = _one(connection, "SELECT COUNT(DISTINCT contact_id) FROM lifecycle_events WHERE stage='mql'")
    closed_won = _one(connection, "SELECT COUNT(*) FROM deals WHERE stage='closed_won'")
    spend = _one(connection, "SELECT SUM(spend_cents) FROM campaigns WHERE medium IN ('paid_social','paid_search')")
    gross = _one(connection, "SELECT SUM(amount_cents) FROM payments WHERE status='succeeded'")
    refunds = _one(connection, "SELECT SUM(amount_cents) FROM refunds")
    net = gross - refunds
    paid_campaigns = {row[0] for row in connection.execute(
        "SELECT campaign_id FROM campaigns WHERE medium IN ('paid_social','paid_search')"
    )}
    paid_performance = [row for row in campaign_performance(connection)
                        if row["campaign_id"] in paid_campaigns]
    paid_leads = sum(row["leads"] for row in paid_performance)
    paid_mqls = sum(row["mqls"] for row in paid_performance)
    paid_cash = sum(row["net_cash_cents"] for row in attribution_summary(connection, "lead_creation")
                    if row["campaign_id"] in paid_campaigns)
    return {
        "leads": leads,
        "mqls": mqls,
        "closed_won_deals": closed_won,
        "spend_cents": spend,
        "paid_leads": paid_leads,
        "paid_mqls": paid_mqls,
        "gross_collected_cents": gross,
        "refunds_cents": refunds,
        "net_collected_cents": net,
        "lead_to_mql_rate": round(mqls / leads, 4) if leads else None,
        "cost_per_lead_cents": round(spend / paid_leads) if paid_leads else None,
        "cost_per_mql_cents": round(spend / paid_mqls) if paid_mqls else None,
        "net_cash_roas": round(paid_cash / spend, 4) if spend else None,
    }


def measurement_health(connection: sqlite3.Connection) -> dict:
    total_contacts = _one(connection, "SELECT COUNT(*) FROM contacts")
    total_touches = _one(connection, "SELECT COUNT(*) FROM touches WHERE campaign_id IS NULL OR campaign_id!='direct'")
    total_payments = _one(connection, "SELECT COUNT(*) FROM payments")
    missing_utm = _one(connection, "SELECT COUNT(*) FROM touches WHERE (campaign_id IS NULL OR campaign_id!='direct') AND (utm_source IS NULL OR TRIM(utm_source)='')")
    invalid_campaign = _one(connection, "SELECT COUNT(*) FROM touches t LEFT JOIN campaigns c ON c.campaign_id=t.campaign_id WHERE (t.campaign_id IS NULL OR t.campaign_id!='direct') AND (c.campaign_id IS NULL OR c.registry_valid=0)")
    missing_owner = _one(connection, "SELECT COUNT(*) FROM contacts WHERE owner_id IS NULL")
    duplicate_emails = _one(connection, "SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM contacts GROUP BY LOWER(email) HAVING COUNT(*)>1)")
    unmatched_payments = _one(connection, "SELECT COUNT(*) FROM payments p LEFT JOIN deals d ON d.deal_id=p.deal_id WHERE d.deal_id IS NULL")
    return {
        "utm_completeness": round(1 - missing_utm / total_touches, 4) if total_touches else None,
        "campaign_registry_match": round(1 - invalid_campaign / total_touches, 4) if total_touches else None,
        "crm_owner_completeness": round(1 - missing_owner / total_contacts, 4) if total_contacts else None,
        "duplicate_contact_rows": duplicate_emails,
        "deal_payment_reconciliation": round(1 - unmatched_payments / total_payments, 4) if total_payments else None,
        "unmatched_payment_count": unmatched_payments,
        **lifecycle_integrity(connection),
    }


def campaign_performance(connection: sqlite3.Connection) -> list[dict]:
    # Cash is allocated by the governed lead-creation model, separate from CRM deal value.
    rows = connection.execute(
        """
        WITH lead_touch AS (
          SELECT contact_id, campaign_id,
                 ROW_NUMBER() OVER (PARTITION BY contact_id ORDER BY occurred_at DESC, touch_id DESC) rn
          FROM touches WHERE touch_type='lead_creation'
        ), mql_people AS (
          SELECT DISTINCT contact_id FROM lifecycle_events WHERE stage='mql'
        )
        SELECT c.campaign_id, c.source, c.medium, c.spend_cents,
               COUNT(t.contact_id) leads,
               COUNT(m.contact_id) mqls
        FROM campaigns c
        LEFT JOIN lead_touch t ON t.campaign_id=c.campaign_id AND t.rn=1
        LEFT JOIN mql_people m ON m.contact_id=t.contact_id
        GROUP BY c.campaign_id, c.source, c.medium, c.spend_cents
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
    if quality["utm_completeness"] is not None and quality["utm_completeness"] < 0.95:
        observations.append({
            "finding": "UTM completeness is below the 95% target.",
            "evidence": f"{quality['utm_completeness']:.1%} of acquisition touches have a source.",
            "action": "Inspect forms and campaign links with missing source values.",
        })
    if quality["unmatched_payment_count"]:
        observations.append({
            "finding": "Some collected payments cannot be joined to closed-won deals.",
            "evidence": f"{quality['unmatched_payment_count']} payment(s) lack a matching deal.",
            "action": "Review customer identity and migration mappings before using deal-based attribution.",
        })
    if quality["campaign_registry_match"] is not None and quality["campaign_registry_match"] < 0.98:
        observations.append({
            "finding": "Campaign naming does not meet the registry target.",
            "evidence": f"{quality['campaign_registry_match']:.1%} of touches map to a valid campaign.",
            "action": "Repair source and campaign aliases before comparing channel performance.",
        })
    if quality["lifecycle_integrity"] is not None and quality["lifecycle_integrity"] < 0.99:
        observations.append({
            "finding": "Some paid journeys have missing or out-of-order CRM stages.",
            "evidence": f"{quality['valid_paid_journeys']} of {quality['paid_contact_count']} paid journeys pass the stage check.",
            "action": "Audit migration mappings and deal-stage automation for affected contacts.",
        })
    return {"metrics": kpis, "measurement_health": quality, "observations": observations}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/growthops-sample.db")
    args = parser.parse_args()
    connection = connect(args.database)
    initialize(connection)
    print(json.dumps({**executive_brief(connection), "campaigns": campaign_performance(connection),
                      "funnel": funnel(connection),
                      "attribution": {model: attribution_summary(connection, model)
                                      for model in ("first_touch", "lead_creation", "last_non_direct", "u_shaped")}}, indent=2))
    connection.close()


if __name__ == "__main__":
    main()
