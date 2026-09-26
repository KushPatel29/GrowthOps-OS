"""HubSpot-shaped CRM layer: property mapping, import files, API payloads and a CRM audit.

Nothing here connects to HubSpot. The module maps the internal CRM model onto
HubSpot's standard objects and property values (``lifecyclestage``,
``dealstage``, ``hubspot_owner_id``, ``amount``, ``closedate``), defines the
custom properties an import would need, writes import-ready CSVs, and parses a
CRM v3 search response back into the internal model, so the mapping can be
tested in both directions. Owner IDs are placeholders.

``hs_analytics_source`` (Original Traffic Source) is calculated by HubSpot and
cannot be imported, so the registry source travels in a custom property and the
audit compares the two when a live export is available.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from growthops.scenario import AS_OF

LIFECYCLE = {"lead": "lead", "mql": "marketingqualifiedlead", "opportunity": "opportunity", "customer": "customer"}
LIFECYCLE_ORDER = ("subscriber", "lead", "marketingqualifiedlead", "salesqualifiedlead", "opportunity",
                   "customer", "evangelist", "other")
DEAL_STAGE = {"open": "presentationscheduled", "closed_won": "closedwon", "closed_lost": "closedlost"}
MEDIUM_TO_HS_SOURCE = {  # registry medium -> the Original Traffic Source HubSpot should compute
    "paid_social": "PAID_SOCIAL", "paid_search": "PAID_SEARCH", "organic_video": "SOCIAL_MEDIA",
    "owned_email": "EMAIL_MARKETING", "referral": "REFERRALS", "event": "OTHER_CAMPAIGNS",
    "none": "DIRECT_TRAFFIC",
}
OWNER_IDS = {  # internal owner -> hubspot_owner_id (placeholders; a real portal's Owners API supplies these)
    "owner-ava": "71000001", "owner-ben": "71000002", "owner-chloe": "71000003",
    "owner-dev": "71000004", "owner-emma": "71000005", "owner-farid": "71000006",
}
SEARCH_PROPERTIES = ("email", "lifecyclestage", "hubspot_owner_id", "createdate", "lastmodifieddate",
                     "hs_analytics_source", "growthops_contact_id", "growthops_original_source",
                     "growthops_lead_campaign")
STALE_DAYS = 365


def _options(values: list[str]) -> list[dict]:
    return [{"label": value, "value": value, "displayOrder": order} for order, value in enumerate(values)]


def property_definitions(connection: sqlite3.Connection) -> dict[str, list[dict]]:
    """Custom properties as CRM v3 Properties API bodies (POST /crm/v3/properties/{object})."""
    sources = [row[0] for row in connection.execute(
        "SELECT DISTINCT source FROM campaigns WHERE registry_valid=1 ORDER BY source")]
    products = [row[0] for row in connection.execute("SELECT product_id FROM products ORDER BY product_id")]
    group = "growthops"
    return {
        "contacts": [
            {"name": "growthops_contact_id", "label": "GrowthOps contact ID", "type": "string",
             "fieldType": "text", "groupName": group, "hasUniqueValue": True},
            {"name": "growthops_original_source", "label": "Original source (registry)", "type": "enumeration",
             "fieldType": "select", "groupName": group, "options": _options(sources)},
            {"name": "growthops_lead_campaign", "label": "Lead-creation campaign", "type": "string",
             "fieldType": "text", "groupName": group},
            {"name": "growthops_legacy_id", "label": "Legacy CRM ID", "type": "string",
             "fieldType": "text", "groupName": group},
        ],
        "deals": [
            {"name": "growthops_deal_id", "label": "GrowthOps deal ID", "type": "string",
             "fieldType": "text", "groupName": group, "hasUniqueValue": True},
            {"name": "growthops_product", "label": "Product", "type": "enumeration",
             "fieldType": "select", "groupName": group, "options": _options(products)},
        ],
    }


def _survivors(connection: sqlite3.Connection) -> sqlite3.Cursor:
    """One row per normalized email, as a HubSpot import would dedupe: keep the earliest record."""
    return connection.execute(
        """
        WITH ranked AS (
          SELECT c.*, LOWER(TRIM(c.email)) email_key,
                 ROW_NUMBER() OVER (PARTITION BY LOWER(TRIM(c.email))
                                    ORDER BY c.created_at, c.contact_id) rn
          FROM contacts c
        ), lead_touch AS (
          SELECT contact_id, campaign_id,
                 ROW_NUMBER() OVER (PARTITION BY contact_id ORDER BY occurred_at DESC, touch_id DESC) rn
          FROM touches WHERE touch_type='lead_creation'
        )
        SELECT r.contact_id, r.email_key, r.legacy_id, r.owner_id, r.original_source, r.current_stage,
               r.created_at, t.campaign_id
        FROM ranked r LEFT JOIN lead_touch t ON t.contact_id=r.contact_id AND t.rn=1
        WHERE r.rn=1 ORDER BY r.contact_id
        """
    )


def contact_records(connection: sqlite3.Connection) -> list[dict]:
    valid = {row[0] for row in connection.execute("SELECT DISTINCT source FROM campaigns WHERE registry_valid=1")}
    return [{
        "email": row["email_key"],
        "lifecyclestage": LIFECYCLE[row["current_stage"]],
        "hubspot_owner_id": OWNER_IDS.get(row["owner_id"] or "", ""),
        "createdate": (row["created_at"] or "")[:10],
        "growthops_contact_id": row["contact_id"],
        # Only registry values import cleanly into an enumeration; the rest stay blank for review.
        "growthops_original_source": row["original_source"] if row["original_source"] in valid else "",
        "growthops_lead_campaign": row["campaign_id"] or "",
        "growthops_legacy_id": row["legacy_id"] or "",
    } for row in _survivors(connection)]


def deal_records(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """SELECT d.deal_id, d.stage, d.amount_cents, d.closed_at, d.created_at, d.product_id,
                  LOWER(TRIM(c.email)) email, COALESCE(p.product_name, 'Unmapped product') product_name
           FROM deals d JOIN contacts c ON c.contact_id=d.contact_id
           LEFT JOIN products p ON p.product_id=d.product_id ORDER BY d.deal_id"""
    ).fetchall()
    return [{
        "dealname": f"{row['product_name']} - {row['deal_id']}",
        "pipeline": "default",
        "dealstage": DEAL_STAGE[row["stage"]],
        "amount": f"{row['amount_cents'] / 100:.2f}",
        "closedate": (row["closed_at"] or "")[:10],
        "createdate": (row["created_at"] or "")[:10],
        "growthops_deal_id": row["deal_id"],
        "growthops_product": row["product_id"] or "",
        "contact_email": row["email"],  # association key in a two-object import
    } for row in rows]


def export(connection: sqlite3.Connection, output: str | Path) -> dict[str, int]:
    """Write import-ready contacts and deals CSVs plus the property definitions."""
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, records in (("contacts", contact_records(connection)), ("deals", deal_records(connection))):
        with (destination / f"hubspot_{name}.csv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
        counts[name] = len(records)
    (destination / "hubspot_properties.json").write_text(
        json.dumps(property_definitions(connection), indent=2) + "\n", encoding="utf-8")
    return counts


def search_request(modified_since: datetime, after: str | None = None) -> dict:
    """Body for POST /crm/v3/objects/contacts/search: incremental pull ordered by last modified.

    Search returns at most 10,000 results per query, so a large backfill moves the
    ``modified_since`` watermark forward instead of paging past that limit.
    """
    body = {
        "filterGroups": [{"filters": [{
            "propertyName": "lastmodifieddate", "operator": "GTE",
            "value": str(int(modified_since.astimezone(timezone.utc).timestamp() * 1000)),
        }]}],
        "sorts": [{"propertyName": "lastmodifieddate", "direction": "ASCENDING"}],
        "properties": list(SEARCH_PROPERTIES),
        "limit": 100,
    }
    if after:
        body["after"] = after
    return body


def parse_search_response(payload: dict) -> dict:
    """Map a CRM v3 contacts search response back to the internal model."""
    stage_back = {value: key for key, value in LIFECYCLE.items()}
    owner_back = {value: key for key, value in OWNER_IDS.items()}
    contacts, problems = [], []
    for result in payload.get("results", []):
        props = result.get("properties", {})
        stage = props.get("lifecyclestage")
        if stage and stage not in stage_back:
            problems.append({"hubspot_id": result["id"], "issue": f"lifecycle stage '{stage}' has no internal mapping"})
        owner = props.get("hubspot_owner_id")
        if owner and owner not in owner_back:
            problems.append({"hubspot_id": result["id"], "issue": f"owner {owner} is not in the owner map"})
        contacts.append({
            "hubspot_id": result["id"],
            "contact_id": props.get("growthops_contact_id"),
            "email": (props.get("email") or "").strip().lower() or None,
            "current_stage": stage_back.get(stage or ""),
            "owner_id": owner_back.get(owner or ""),
            "original_source": props.get("growthops_original_source") or None,
            "lead_campaign": props.get("growthops_lead_campaign") or None,
            "hs_analytics_source": props.get("hs_analytics_source"),
            "last_modified": props.get("lastmodifieddate") or result.get("updatedAt"),
        })
    return {"contacts": contacts, "problems": problems,
            "next_after": payload.get("paging", {}).get("next", {}).get("after")}


def source_mismatches(connection: sqlite3.Connection, contacts: list[dict]) -> list[dict]:
    """Contacts whose HubSpot Original Traffic Source disagrees with their registry campaign."""
    medium = {row[0]: row[1] for row in connection.execute("SELECT campaign_id, medium FROM campaigns")}
    issues = []
    for contact in contacts:
        expected = MEDIUM_TO_HS_SOURCE.get(medium.get(contact["lead_campaign"] or "", ""))
        if expected and contact["hs_analytics_source"] and contact["hs_analytics_source"] != expected:
            issues.append({"hubspot_id": contact["hubspot_id"], "contact_id": contact["contact_id"],
                           "lead_campaign": contact["lead_campaign"], "expected": expected,
                           "hubspot_says": contact["hs_analytics_source"]})
    return issues


def audit(connection: sqlite3.Connection, as_of: date = AS_OF) -> dict:
    """CRM hygiene as a HubSpot admin would check it before an import or a reporting rebuild."""
    records = contact_records(connection)
    total_rows = connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    one = lambda sql, *args: connection.execute(sql, args).fetchone()[0]  # noqa: E731
    customers_without_won = one(
        """SELECT COUNT(*) FROM contacts c WHERE c.current_stage='customer' AND NOT EXISTS
           (SELECT 1 FROM deals d WHERE d.contact_id=c.contact_id AND d.stage='closed_won')""")
    won_not_customer = one(
        """SELECT COUNT(DISTINCT d.contact_id) FROM deals d JOIN contacts c ON c.contact_id=d.contact_id
           WHERE d.stage='closed_won' AND c.current_stage<>'customer'""")
    paid_not_customer = one(
        """SELECT COUNT(DISTINCT p.customer_id) FROM payments p JOIN contacts c ON c.contact_id=p.customer_id
           WHERE p.status='succeeded' AND c.current_stage<>'customer'""")
    cutoff = (as_of - timedelta(days=STALE_DAYS)).isoformat()
    stale = one(
        """SELECT COUNT(*) FROM contacts c WHERE c.current_stage='lead' AND COALESCE(c.created_at,'') < ?
           AND NOT EXISTS (SELECT 1 FROM touches t WHERE t.contact_id=c.contact_id AND t.occurred_at >= ?)
           AND NOT EXISTS (SELECT 1 FROM lifecycle_events e WHERE e.contact_id=c.contact_id AND e.occurred_at >= ?)""",
        cutoff, cutoff, cutoff)
    fill = {field: round(sum(1 for record in records if record[field]) / len(records), 4)
            for field in ("hubspot_owner_id", "growthops_original_source", "growthops_lead_campaign")}
    return {
        "contact_rows": total_rows,
        "contacts_after_email_dedupe": len(records),
        "rows_merged_on_email": total_rows - len(records),
        "property_fill_rate": fill,
        "customers_without_closed_won_deal": customers_without_won,
        "closed_won_contacts_not_customer": won_not_customer,
        "paying_contacts_not_customer": paid_not_customer,
        "stale_leads_non_marketing_candidates": stale,
        "stale_rule": f"lead stage, created and untouched for {STALE_DAYS}+ days before {as_of.isoformat()}",
        "lifecycle_values_valid": all(record["lifecyclestage"] in LIFECYCLE_ORDER for record in records),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Write HubSpot import files and print the CRM audit.")
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--output", default="build/hubspot")
    args = parser.parse_args()
    from growthops.db import connect

    connection = connect(args.database)
    try:
        print(json.dumps({"files": export(connection, args.output), "audit": audit(connection)}, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
