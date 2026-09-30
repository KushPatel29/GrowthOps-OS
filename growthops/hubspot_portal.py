"""Build and operate a real HubSpot portal from GrowthOps, through HubSpot's public APIs.

``hubspot.py`` maps the internal CRM model onto HubSpot's objects offline. This module applies that mapping to a
live portal as a sequence of idempotent steps; running a step twice writes nothing the second time.

1. ``properties``  a ``growthops`` property group on contacts and deals: original and latest UTM, first and latest
                   content, funnel dates, tracking status, owner, net cash, renewal and attribution fields. Enumerations are
                   limited to the campaign registry, so an off-taxonomy value cannot be written.
2. ``pipeline``    a "GrowthOps sales" deal pipeline whose stages follow the funnel.
3. ``prune``       GrowthOps contacts outside the portal sample archived (a soft delete HubSpot can restore).
4. ``import``      contacts loaded through the Imports API, the way a CRM migration lands.
5. ``sync``        contacts and deals upserted on their unique GrowthOps IDs, and deal-to-contact associations; only
                   records whose values differ are written. Lifecycle stage and owner are set on create only, so a
                   later sync never undoes a cleanup.
6. ``cleanup``     the portal audited through the CRM search API and repaired: open leads without an owner routed,
                   closed-won and paying contacts moved to Customer, stale leads flagged as non-marketing candidates.
7. ``lists``       segments for tracking gaps, unowned leads, lifecycle breaks and stale leads.
8. ``workflows``   automation that keeps those repairs in place for new records. No workflow sends email.
9. ``verify``      the portal read back through the API and reconciled to the warehouse, written as evidence.

Safety: ``apply`` refuses any portal that the Account Information API does not report as a developer test account
or sandbox, unless that portal's ID is passed explicitly. HubSpot rejects the warehouse's reserved ``.test`` domain
as an invalid email, so the portal copy moves every address to ``scalelab.example.com``, which RFC 2606 reserves
and which cannot receive mail: nothing can be delivered. The access token comes from ``HUBSPOT_ACCESS_TOKEN`` or
the git-ignored ``.env`` and is never printed or written to evidence.

Scale: a developer test account keeps the free CRM's cap of 1,000 contacts even with Enterprise hub trials. The
portal therefore holds a deterministic sample, every contact whose SHA-256 bucket falls under
``PORTAL_SAMPLE_BASIS_POINTS`` plus the 19 lifecycle breaks the migration left, together with all of those
contacts' deals; the warehouse is reconciled on the same sample. The first full import proved the cap: 11,113 of 14,555 rows landed before HubSpot's count caught up, and the
rest failed ``LIMIT_EXCEEDED``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sqlite3
import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from growthops.adapters import ProviderError
from growthops.hubspot import DEAL_STAGE, LIFECYCLE, STALE_DAYS
from growthops.hubspot_client import HubSpotClient, account, load_token
from growthops.scenario import AS_OF

GROUP = "growthops"
PIPELINE_LABEL = "GrowthOps sales"
# (internal key, label, probability, closed)
PIPELINE_STAGES = (
    ("call_booked", "Discovery call booked", "0.2", False),
    ("call_attended", "Discovery call attended", "0.4", False),
    ("offer_made", "Offer made", "0.6", False),
    ("closedwon", "Closed won", "1.0", True),
    ("closedlost", "Closed lost", "0.0", True),
)
OPEN_LIFECYCLE = ("lead", "marketingqualifiedlead", "opportunity")
TRACKING_STATUSES = ("complete", "missing_utm", "off_taxonomy", "direct", "no_lead_touch")
RENEWAL_RISKS = ("high", "medium", "not_due")  # renewals.monitor severities, plus active and not yet due
CREATE_ONLY = frozenset({"lifecyclestage", "hubspot_owner_id", "growthops_owner"})  # cleanup or a rep owns these after create
BATCH = 100
LIST_PREFIX = "GrowthOps: "
WORKFLOW_PREFIX = "GrowthOps: "
EVIDENCE_DOC = Path("docs/hubspot-portal.md")
PORTAL_EMAIL_DOMAIN = "scalelab.example.com"  # RFC 2606 reserved; HubSpot rejects .test as INVALID_EMAIL


PORTAL_SAMPLE_BASIS_POINTS = 640  # 6.4% of contacts: 940 of 14,555, under the 1,000-contact cap with headroom


def in_portal_sample(contact_id: str, basis_points: int | None = PORTAL_SAMPLE_BASIS_POINTS) -> bool:
    """Deterministic, platform-independent membership: the same contacts on every machine and every run."""
    if basis_points is None:
        return True
    return int(hashlib.sha256(contact_id.encode()).hexdigest(), 16) % 10_000 < basis_points


def portal_email(email: str) -> str:
    """The address as the portal stores it: same mailbox name on a reserved, undeliverable domain."""
    return email.strip().lower().rsplit("@", 1)[0] + "@" + PORTAL_EMAIL_DOMAIN


# --------------------------------------------------------------------------- desired state (pure, offline)

def _group_ids(connection: sqlite3.Connection) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Survivor contact per normalized email (earliest record wins, as a HubSpot import dedupes) and its merged IDs."""
    survivor: dict[str, str] = {}
    members: dict[str, list[str]] = defaultdict(list)
    for row in connection.execute(
            "SELECT contact_id, LOWER(TRIM(email)) email_key FROM contacts ORDER BY created_at, contact_id"):
        survivor.setdefault(row["email_key"], row["contact_id"])
        members[survivor[row["email_key"]]].append(row["contact_id"])
    return survivor, dict(members)


def _day(value: str | None) -> str:
    return (value or "")[:10]


def _money(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _options(values: Iterable[str]) -> list[dict]:
    return [{"label": value, "value": value, "displayOrder": order} for order, value in enumerate(values)]


def _prop(name: str, label: str, kind: str, options: Iterable[str] | None = None, unique: bool = False) -> dict:
    field_type = {"string": "text", "enumeration": "select", "date": "date", "number": "number",
                  "bool": "booleancheckbox"}[kind]
    body: dict = {"name": name, "label": label, "type": kind, "fieldType": field_type, "groupName": GROUP}
    if kind == "bool":
        body["options"] = [{"label": "Yes", "value": "true", "displayOrder": 0},
                           {"label": "No", "value": "false", "displayOrder": 1}]
    elif options is not None:
        body["options"] = _options(options)
    if unique:
        body["hasUniqueValue"] = True
    return body


def portal_properties(connection: sqlite3.Connection) -> dict[str, list[dict]]:
    """Every custom property the portal needs, as CRM v3 Properties API bodies."""
    valid = connection.execute("SELECT source, medium, campaign_id FROM campaigns WHERE registry_valid=1").fetchall()
    sources = sorted({row["source"] for row in valid})
    mediums = sorted({row["medium"] for row in valid})
    campaigns = sorted(row["campaign_id"] for row in valid)
    owners = sorted(row[0] for row in connection.execute(
        "SELECT DISTINCT owner_id FROM contacts WHERE owner_id IS NOT NULL"))
    products = [row[0] for row in connection.execute("SELECT product_id FROM products ORDER BY product_id")]
    content = [row[0] for row in connection.execute("SELECT content_id FROM content_items ORDER BY content_id")]
    attribution = [
        _prop("growthops_first_touch_campaign", "First-touch campaign", "enumeration", campaigns),
        _prop("growthops_lead_creation_campaign", "Lead-creation campaign", "enumeration", campaigns),
        _prop("growthops_last_non_direct_campaign", "Last non-direct campaign", "enumeration", campaigns),
        _prop("growthops_first_touch_medium", "First-touch channel", "enumeration", mediums),
        _prop("growthops_net_cash", "Net cash collected", "number"),
    ]
    return {
        "contacts": [
            _prop("growthops_contact_id", "GrowthOps contact ID", "string", unique=True),
            _prop("growthops_legacy_id", "Legacy CRM ID", "string"),
            _prop("growthops_merged_contact_ids", "Merged duplicate contact IDs", "string"),
            _prop("growthops_owner", "Assigned rep (GrowthOps)", "enumeration", owners),
            _prop("growthops_original_source", "Original source (registry)", "enumeration", sources),
            _prop("growthops_original_utm_source", "Original UTM source", "enumeration", sources),
            _prop("growthops_original_utm_medium", "Original UTM medium", "enumeration", mediums),
            _prop("growthops_original_utm_campaign", "Original UTM campaign", "enumeration", campaigns),
            _prop("growthops_latest_utm_source", "Latest UTM source", "enumeration", sources),
            _prop("growthops_latest_utm_campaign", "Latest UTM campaign", "enumeration", campaigns),
            _prop("growthops_first_content_id", "First content engaged", "enumeration", content),
            _prop("growthops_latest_content_id", "Latest content engaged", "enumeration", content),
            _prop("growthops_mql_date", "Became MQL", "date"),
            _prop("growthops_call_booked_date", "Discovery call booked", "date"),
            _prop("growthops_call_attended_date", "Discovery call attended", "date"),
            _prop("growthops_last_activity_date", "Last marketing activity", "date"),
            _prop("growthops_tracking_status", "Tracking status", "enumeration", TRACKING_STATUSES),
            _prop("growthops_has_closed_won", "Has a closed-won deal", "bool"),
            _prop("growthops_stale_lead", "Stale lead (non-marketing candidate)", "bool"),
            _prop("growthops_renewal_due_date", "Community renewal due", "date"),
            _prop("growthops_renewal_risk", "Renewal risk", "enumeration", RENEWAL_RISKS),
            *attribution,
        ],
        "deals": [
            _prop("growthops_deal_id", "GrowthOps deal ID", "string", unique=True),
            _prop("growthops_contact_id", "GrowthOps contact ID", "string"),
            _prop("growthops_product", "Product", "enumeration", products),
            _prop("growthops_first_content_id", "First content engaged", "enumeration", content),
            *attribution,
        ],
    }


def _touch_rows(connection: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    rows: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in connection.execute(
        """SELECT t.contact_id, t.touch_id, t.campaign_id, t.touch_type, t.occurred_at, t.utm_source,
                  c.source, c.medium, COALESCE(c.registry_valid, 0) registry_valid
           FROM touches t LEFT JOIN campaigns c ON c.campaign_id=t.campaign_id
           ORDER BY t.occurred_at, t.touch_id"""
    ):
        rows[row["contact_id"]].append(row)
    return rows


def _tracked(touch: sqlite3.Row) -> bool:
    """A touch whose UTM arrived and names a registry campaign other than direct."""
    return bool(touch["utm_source"] and touch["campaign_id"] and touch["registry_valid"]
                and touch["campaign_id"] != "direct")


def _tracking_status(lead: sqlite3.Row | None) -> str:
    if lead is None:
        return "no_lead_touch"
    if lead["campaign_id"] == "direct":
        return "direct"
    if not (lead["utm_source"] or "").strip():
        return "missing_utm"
    if not lead["campaign_id"] or not lead["registry_valid"]:
        return "off_taxonomy"
    return "complete"


def _attribution(touches: list[sqlite3.Row], cutoff: str) -> dict[str, str]:
    """First touch, lead creation and last non-direct campaigns among touches up to the cutoff (registry only)."""
    eligible = [t for t in touches if t["occurred_at"] <= cutoff]
    valid = [t for t in eligible if t["campaign_id"] and t["registry_valid"]]
    lead = next((t for t in reversed(eligible) if t["touch_type"] == "lead_creation"), None)
    non_direct = [t for t in valid if t["campaign_id"] != "direct"]
    first = valid[0] if valid else None
    return {
        "growthops_first_touch_campaign": first["campaign_id"] if first else "",
        "growthops_first_touch_medium": first["medium"] if first else "",
        "growthops_lead_creation_campaign": (lead["campaign_id"] if lead and lead["registry_valid"] else "") or "",
        "growthops_last_non_direct_campaign": non_direct[-1]["campaign_id"] if non_direct else "",
    }


def _net_cash_by(connection: sqlite3.Connection, key: str) -> dict[str, int]:
    return {row[0]: row[1] for row in connection.execute(
        f"""SELECT p.{key}, SUM(p.amount_cents - COALESCE(r.refunded, 0))
            FROM payments p LEFT JOIN (SELECT payment_id, SUM(amount_cents) refunded FROM refunds GROUP BY payment_id) r
              ON r.payment_id=p.payment_id
            WHERE p.status='succeeded' AND p.{key} IS NOT NULL GROUP BY p.{key}""")}


def records(connection: sqlite3.Connection, as_of: date = AS_OF,
            sample: int | None = PORTAL_SAMPLE_BASIS_POINTS) -> dict[str, list[dict]]:
    """Desired contacts (one per email) and deals, with every GrowthOps property filled from the warehouse.

    Properties are computed over the full scenario first, so a sampled contact carries exactly the values it has in
    the warehouse; ``sample`` then keeps the contacts in the portal sample and all of their deals (None keeps all).
    """
    survivor, members = _group_ids(connection)
    to_survivor = {member: head for head, ids in members.items() for member in ids}
    touches = _touch_rows(connection)
    events: dict[str, dict[str, str]] = defaultdict(dict)
    last_event: dict[str, str] = {}
    for row in connection.execute("SELECT contact_id, stage, occurred_at FROM lifecycle_events ORDER BY occurred_at"):
        head = to_survivor[row["contact_id"]]
        events[head].setdefault(row["stage"], row["occurred_at"])
        events[head]["latest:" + row["stage"]] = row["occurred_at"]
        last_event[head] = max(last_event.get(head, ""), row["occurred_at"])
    content: dict[str, list[str]] = defaultdict(list)
    for row in connection.execute("SELECT contact_id, content_id FROM content_engagements ORDER BY occurred_at, engagement_id"):
        content[to_survivor[row["contact_id"]]].append(row["content_id"])
    won = {to_survivor[row[0]] for row in connection.execute("SELECT contact_id FROM deals WHERE stage='closed_won'")}
    cash_by_customer: dict[str, int] = defaultdict(int)
    for customer, cents in _net_cash_by(connection, "customer_id").items():
        if customer in to_survivor:
            cash_by_customer[to_survivor[customer]] += cents
    cutoff = f"{as_of.isoformat()}T23:59:59+00:00"
    # Renewal state for customer success: the next due date of an active subscription and the monitor's risk.
    from growthops.renewals import monitor

    risk = {item["subscription_id"]: item["severity"] for item in monitor(connection, as_of)["issues"]}
    renewal: dict[str, tuple[str, str]] = {}
    for row in connection.execute("""SELECT subscription_id, customer_id, renewal_due_at FROM subscriptions
                                     WHERE status='active' ORDER BY renewal_due_at, subscription_id"""):
        survivor_id = to_survivor.get(row["customer_id"])
        if survivor_id and survivor_id not in renewal:
            renewal[survivor_id] = (_day(row["renewal_due_at"]), risk.get(row["subscription_id"], "not_due"))
    registry_sources = {row[0] for row in connection.execute("SELECT source FROM campaigns WHERE registry_valid=1")}

    contacts = []
    by_head: dict[str, list[sqlite3.Row]] = {}
    for row in connection.execute("SELECT * FROM contacts ORDER BY contact_id"):
        if survivor[row["email"].strip().lower()] != row["contact_id"]:
            continue
        head = row["contact_id"]
        group = sorted((t for member in members[head] for t in touches.get(member, [])),
                       key=lambda t: (t["occurred_at"], t["touch_id"]))
        by_head[head] = group
        tracked = [t for t in group if _tracked(t)]
        lead = next((t for t in reversed(group) if t["touch_type"] == "lead_creation"), None)
        last_touch = group[-1]["occurred_at"] if group else ""
        contacts.append({
            "email": portal_email(row["email"]),
            "firstname": "Synthetic",
            "lastname": head,
            "lifecyclestage": LIFECYCLE[row["current_stage"]],
            "growthops_contact_id": head,
            "growthops_legacy_id": row["legacy_id"] or "",
            "growthops_merged_contact_ids": ";".join(m for m in members[head] if m != head),
            "growthops_owner": row["owner_id"] or "",
            # Only registry values fit the enumeration; anything else stays blank and shows as a tracking gap.
            "growthops_original_source": row["original_source"] if row["original_source"] in registry_sources else "",
            "growthops_original_utm_source": tracked[0]["source"] if tracked else "",
            "growthops_original_utm_medium": tracked[0]["medium"] if tracked else "",
            "growthops_original_utm_campaign": tracked[0]["campaign_id"] if tracked else "",
            "growthops_latest_utm_source": tracked[-1]["source"] if tracked else "",
            "growthops_latest_utm_campaign": tracked[-1]["campaign_id"] if tracked else "",
            "growthops_first_content_id": content[head][0] if content[head] else "",
            "growthops_latest_content_id": content[head][-1] if content[head] else "",
            "growthops_mql_date": _day(events[head].get("mql")),
            "growthops_call_booked_date": _day(events[head].get("call_booked")),
            "growthops_call_attended_date": _day(events[head].get("call_attended")),
            "growthops_last_activity_date": _day(max(last_touch, last_event.get(head, ""))),
            "growthops_tracking_status": _tracking_status(lead),
            "growthops_has_closed_won": "true" if head in won else "false",
            "growthops_renewal_due_date": renewal.get(head, ("", ""))[0],
            "growthops_renewal_risk": renewal.get(head, ("", ""))[1],
            "growthops_net_cash": _money(cash_by_customer.get(head, 0)),
            **_attribution(group, cutoff),
        })

    cash_by_deal = _net_cash_by(connection, "deal_id")
    deals = []
    for row in connection.execute(
        """SELECT d.*, COALESCE(p.product_name, 'Unmapped product') product_name
           FROM deals d LEFT JOIN products p ON p.product_id=d.product_id ORDER BY d.deal_id"""
    ):
        head = to_survivor[row["contact_id"]]
        if row["stage"] == "open":
            reached = [key for key in ("call_booked", "call_attended", "opportunity") if key in events[head]]
            latest = max(reached, key=lambda key: events[head]["latest:" + key]) if reached else "opportunity"
            stage = {"opportunity": "offer_made"}.get(latest, latest)
        else:
            stage = DEAL_STAGE[row["stage"]]
        deal_cutoff = row["closed_at"] or cutoff
        deals.append({
            "dealname": f"{row['product_name']} - {row['deal_id']}",
            "amount": _money(row["amount_cents"]),
            "closedate": _day(row["closed_at"]),
            "pipeline_stage": stage,  # resolved to a HubSpot stage ID once the pipeline exists
            "growthops_deal_id": row["deal_id"],
            "growthops_contact_id": head,
            "growthops_product": row["product_id"] or "",
            "growthops_first_content_id": content[head][0] if content[head] else "",
            "growthops_net_cash": _money(cash_by_deal.get(row["deal_id"], 0)),
            **_attribution(by_head.get(head, []), deal_cutoff),
        })
    # The hash sample, plus every lifecycle break the migration left (a closed-won deal on a contact not yet at
    # Customer): only 19 exist, so a 6.4% sample would carry about one and the cleanup would have nothing to prove.
    kept = {c["growthops_contact_id"] for c in contacts if in_portal_sample(c["growthops_contact_id"], sample)
            or (c["growthops_has_closed_won"] == "true" and c["lifecyclestage"] != "customer")}
    return {"contacts": [c for c in contacts if c["growthops_contact_id"] in kept],
            "deals": [d for d in deals if d["growthops_contact_id"] in kept]}


def expected_after_cleanup(desired: dict[str, list[dict]], as_of: date = AS_OF) -> dict:
    """What the portal should hold once cleanup has run, computed from the same rules without a portal.

    HubSpot's own deal lifecycle sync also acts on the portal: associating a deal lifts its contacts to at least
    Opportunity, and a closed-won deal lifts them to Customer. Lifecycle stages only move forward, so the expected
    stage is the highest of the imported stage, the deal sync and the cleanup rules.
    """
    cutoff = (as_of - timedelta(days=STALE_DAYS)).isoformat()
    lifecycle: dict[str, int] = defaultdict(int)
    stale = 0
    with_deal = {deal["growthops_contact_id"] for deal in desired["deals"]}
    rank = {stage: order for order, stage in enumerate(("lead", "marketingqualifiedlead", "opportunity", "customer"))}
    for contact in desired["contacts"]:
        stage = contact["lifecyclestage"]
        if contact["growthops_contact_id"] in with_deal and rank[stage] < rank["opportunity"]:
            stage = "opportunity"
        if contact["growthops_has_closed_won"] == "true" or float(contact["growthops_net_cash"]) > 0:
            stage = "customer"
        lifecycle[stage] += 1
        if stage == "lead" and "" < contact["growthops_last_activity_date"] < cutoff:
            stale += 1
    won = [deal for deal in desired["deals"] if deal["pipeline_stage"] == "closedwon"]
    return {
        "contacts": len(desired["contacts"]),
        "lifecycle": dict(sorted(lifecycle.items())),
        "stale_leads": stale,
        "deals": len(desired["deals"]),
        "closed_won_deals": len(won),
        "closed_won_amount": round(sum(float(deal["amount"]) for deal in won), 2),
        "deal_net_cash": round(sum(float(deal["growthops_net_cash"]) for deal in desired["deals"]), 2),
    }


def plan(connection: sqlite3.Connection) -> dict:
    """Everything apply would create in an empty portal, with no token and no network."""
    desired = records(connection)
    properties = portal_properties(connection)
    status: dict[str, int] = defaultdict(int)
    for contact in desired["contacts"]:
        status[contact["growthops_tracking_status"]] += 1
    full = records(connection, sample=None)
    return {
        "properties": {obj: [p["name"] for p in props] for obj, props in properties.items()},
        "pipeline": {"label": PIPELINE_LABEL, "stages": [label for _, label, _, _ in PIPELINE_STAGES]},
        "sample_basis_points": PORTAL_SAMPLE_BASIS_POINTS,
        "warehouse_contacts": len(full["contacts"]),
        "warehouse_deals": len(full["deals"]),
        "contacts": len(desired["contacts"]),
        "merged_duplicate_rows": sum(1 for c in desired["contacts"] for m in c["growthops_merged_contact_ids"].split(";") if m),
        "deals": len(desired["deals"]),
        "tracking_status": dict(sorted(status.items())),
        "lists": [spec["name"] for spec in list_specs()],
        "workflows": [spec["name"] for spec in workflow_specs("<owner>")],
        "expected_after_cleanup": expected_after_cleanup(desired),
    }


# --------------------------------------------------------------------------- the API client

# The client lives in hubspot_client; `Portal` stays the name the portal build and its tests use.
Portal = HubSpotClient


# --------------------------------------------------------------------------- steps

def _same_options(current: list[dict], desired: list[dict]) -> bool:
    return [o["value"] for o in current if not o.get("hidden")] == [o["value"] for o in desired]


def ensure_properties(portal: Portal, connection: sqlite3.Connection) -> dict:
    changes: dict[str, list[str]] = {"created": [], "updated": [], "groups": []}
    for object_type, desired in portal_properties(connection).items():
        groups = {g["name"] for g in portal.get(f"/crm/v3/properties/{object_type}/groups").get("results", [])}
        if GROUP not in groups:
            portal.post(f"/crm/v3/properties/{object_type}/groups", {"name": GROUP, "label": "GrowthOps",
                                                                      "displayOrder": -1})
            changes["groups"].append(object_type)
        existing = {p["name"]: p for p in portal.get(f"/crm/v3/properties/{object_type}").get("results", [])}
        for prop in desired:
            current = existing.get(prop["name"])
            if current is None:
                portal.post(f"/crm/v3/properties/{object_type}", prop)
                changes["created"].append(f"{object_type}.{prop['name']}")
            elif prop["type"] == "enumeration" and not _same_options(current.get("options", []), prop["options"]):
                portal.request("PATCH", f"/crm/v3/properties/{object_type}/{prop['name']}",
                               {"options": prop["options"], "label": prop["label"]})
                changes["updated"].append(f"{object_type}.{prop['name']}")
    return changes


def ensure_pipeline(portal: Portal) -> dict:
    """The deal pipeline, created if absent; returns internal stage key -> HubSpot stage ID."""
    pipelines = portal.get("/crm/v3/pipelines/deals").get("results", [])
    pipeline = next((p for p in pipelines if p["label"] == PIPELINE_LABEL), None)
    created = pipeline is None
    if created:
        pipeline = portal.post("/crm/v3/pipelines/deals", {
            "label": PIPELINE_LABEL, "displayOrder": 1,
            "stages": [{"label": label, "displayOrder": order,
                        "metadata": {"probability": prob, **({"isClosed": "true"} if closed else {})}}
                       for order, (_, label, prob, closed) in enumerate(PIPELINE_STAGES)]})
    assert pipeline is not None
    by_label = {stage["label"]: stage["id"] for stage in pipeline.get("stages", [])}
    missing = [label for _, label, _, _ in PIPELINE_STAGES if label not in by_label]
    if missing:
        raise ProviderError(f"pipeline '{PIPELINE_LABEL}' exists without stages {missing}; fix it in HubSpot")
    return {"pipeline_id": pipeline["id"], "created": created,
            "stages": {key: by_label[label] for key, label, _, _ in PIPELINE_STAGES}}


def owners(portal: Portal, connection: sqlite3.Connection) -> dict:
    """Internal reps mapped round-robin onto the portal's active owners (a test portal usually has one user)."""
    active = sorted((o for o in portal.paged("/crm/v3/owners?limit=100") if not o.get("archived")),
                    key=lambda o: int(o["id"]))
    if not active:
        raise ProviderError("the portal has no owners; add a user in HubSpot before loading contacts")
    reps = sorted(row[0] for row in connection.execute(
        "SELECT DISTINCT owner_id FROM contacts WHERE owner_id IS NOT NULL"))
    mapping = {rep: str(active[i % len(active)]["id"]) for i, rep in enumerate(reps)}
    return {"portal_owners": len(active), "map": mapping, "default": str(active[0]["id"])}


def _normalize(name: str, value) -> str:
    text = "" if value is None else str(value)
    if name.endswith("_date") or name == "closedate":
        return text[:10]
    if name in {"amount", "growthops_net_cash"}:
        return f"{float(text):.2f}" if text else ""
    return text


def _batch_read(portal: Portal, object_type: str, id_property: str, ids: list[str],
                properties: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for start in range(0, len(ids), BATCH):
        page = portal.post(f"/crm/v3/objects/{object_type}/batch/read", {
            "idProperty": id_property, "properties": properties,
            "inputs": [{"id": value} for value in ids[start:start + BATCH]]})
        for result in page.get("results", []):
            found[result["properties"][id_property]] = result
    return found


def _upsert(portal: Portal, object_type: str, id_property: str, rows: list[dict]) -> dict[str, str]:
    """Batch upsert on a unique property; returns that property's value -> HubSpot record ID."""
    ids: dict[str, str] = {}
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        page = portal.post(f"/crm/v3/objects/{object_type}/batch/upsert", {"inputs": [
            {"idProperty": id_property, "id": row[id_property], "properties": row} for row in chunk]})
        for result in page.get("results", []):
            key = result.get("properties", {}).get(id_property)
            if key:
                ids[key] = result["id"]
    missing = [row[id_property] for row in rows if row[id_property] not in ids]
    if missing:
        ids.update({key: result["id"] for key, result in
                    _batch_read(portal, object_type, id_property, missing, [id_property]).items()})
    return ids


def _contact_row(contact: dict, owner_map: dict) -> dict:
    row = {k: v for k, v in contact.items() if v != "" or k.startswith("growthops_")}
    owner = owner_map["map"].get(contact["growthops_owner"])
    if owner:
        row["hubspot_owner_id"] = owner
    return row


def _deal_row(deal: dict, pipeline: dict, contact_owner: dict[str, str]) -> dict:
    row = {k: v for k, v in deal.items() if k != "pipeline_stage" and (v != "" or k.startswith("growthops_"))}
    row["pipeline"] = pipeline["pipeline_id"]
    row["dealstage"] = pipeline["stages"][deal["pipeline_stage"]]
    owner = contact_owner.get(deal["growthops_contact_id"])
    if owner:
        row["hubspot_owner_id"] = owner
    return row


def _diff(desired: list[dict], current: dict[str, dict], id_property: str) -> tuple[list[dict], list[dict]]:
    """Records to create (all fields) and to update (changed fields only, never the create-only ones)."""
    creates, updates = [], []
    for row in desired:
        existing = current.get(row[id_property])
        if existing is None:
            creates.append(row)
            continue
        props = existing.get("properties", {})
        changed = {k: v for k, v in row.items()
                   if k not in CREATE_ONLY and _normalize(k, props.get(k)) != _normalize(k, v)}
        if changed:
            updates.append({id_property: row[id_property], **changed})
    return creates, updates


def import_contacts(portal: Portal, contacts: list[dict], owner_map: dict, output: Path,
                    poll_seconds: float = 5, max_polls: int = 180) -> dict:
    """Load contacts through the Imports API (upsert on email), then report HubSpot's own counters and errors."""
    rows = [_contact_row(contact, owner_map) for contact in contacts]
    columns = sorted({key for row in rows for key in row}, key=lambda k: (k != "email", k))
    output.mkdir(parents=True, exist_ok=True)
    path = output / "import_contacts.csv"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    request_body = {
        "name": f"GrowthOps contacts {datetime.now(timezone.utc):%Y-%m-%d %H:%M}",
        "importOperations": {"0-1": "UPSERT"},
        "dateFormat": "YEAR_MONTH_DAY",
        "files": [{"fileName": path.name, "fileFormat": "CSV", "fileImportPage": {"hasHeader": True, "columnMappings": [
            {"columnObjectTypeId": "0-1", "columnName": column, "propertyName": column,
             **({"idColumnType": "HUBSPOT_ALTERNATE_ID"} if column == "email" else {})} for column in columns]}}],
    }
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"importRequest\"\r\n\r\n"
            f"{json.dumps(request_body)}\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: text/csv\r\n\r\n").encode() + path.read_bytes() + \
        f"\r\n--{boundary}--\r\n".encode()
    started = portal.request("POST", "/crm/v3/imports", raw=body,
                             content_type=f"multipart/form-data; boundary={boundary}") or {}
    import_id = started["id"]
    state: dict = started
    for _ in range(max_polls):
        state = portal.get(f"/crm/v3/imports/{import_id}")
        if state.get("state") in {"DONE", "FAILED", "CANCELED"}:
            break
        portal.sleep(poll_seconds)
    errors = portal.get(f"/crm/v3/imports/{import_id}/errors?limit=50").get("results", [])
    return {"import_id": import_id, "state": state.get("state"), "rows": len(rows),
            "counters": state.get("metadata", {}).get("counters", {}),
            "error_types": _error_types(portal, import_id),
            "error_samples": [{"type": e.get("errorType"), "line": e.get("sourceData", {}).get("lineNumber"),
                               "value": e.get("invalidValue"), "column": e.get("knownColumnNumber")}
                              for e in errors[:10]]}


def _error_types(portal: Portal, import_id: str) -> dict[str, int]:
    """Every import error by type (the errors endpoint pages at most 100 at a time)."""
    counts: dict[str, int] = defaultdict(int)
    after = None
    while True:
        page = portal.get(f"/crm/v3/imports/{import_id}/errors?limit=100" + (f"&after={after}" if after else ""))
        for error in page.get("results", []):
            counts[error.get("errorType", "UNKNOWN")] += 1
        after = page.get("paging", {}).get("next", {}).get("after")
        if not after:
            return dict(counts)


def sync(portal: Portal, desired: dict[str, list[dict]], pipeline: dict, owner_map: dict) -> dict:
    """Upsert contacts and deals that differ, then add any missing deal-to-contact associations."""
    contact_rows = [_contact_row(c, owner_map) for c in desired["contacts"]]
    contact_props = sorted({k for row in contact_rows for k in row})
    current = _batch_read(portal, "contacts", "growthops_contact_id",
                          [r["growthops_contact_id"] for r in contact_rows], contact_props)
    creates, updates = _diff(contact_rows, current, "growthops_contact_id")
    contact_ids = {key: result["id"] for key, result in current.items()}
    contact_ids.update(_upsert(portal, "contacts", "growthops_contact_id", creates + updates))
    contact_owner = {row["growthops_contact_id"]: row["hubspot_owner_id"] for row in contact_rows
                     if row.get("hubspot_owner_id")}

    deal_rows = [_deal_row(d, pipeline, contact_owner) for d in desired["deals"]]
    deal_props = sorted({k for row in deal_rows for k in row})
    current_deals = _batch_read(portal, "deals", "growthops_deal_id",
                                [r["growthops_deal_id"] for r in deal_rows], deal_props)
    deal_creates, deal_updates = _diff(deal_rows, current_deals, "growthops_deal_id")
    # A deal's stage belongs to the pipeline, so a stage change is a real update, not create-only.
    deal_ids = {key: result["id"] for key, result in current_deals.items()}
    deal_ids.update(_upsert(portal, "deals", "growthops_deal_id", deal_creates + deal_updates))

    wanted = {deal_ids[d["growthops_deal_id"]]: contact_ids[d["growthops_contact_id"]] for d in deal_rows}
    have: dict[str, set[str]] = defaultdict(set)
    deal_list = list(wanted)
    for start in range(0, len(deal_list), BATCH):
        page = portal.post("/crm/v4/associations/deals/contacts/batch/read",
                           {"inputs": [{"id": d} for d in deal_list[start:start + BATCH]]})
        for result in page.get("results", []):
            have[str(result["from"]["id"])] |= {str(t["toObjectId"]) for t in result.get("to", [])}
    links = [(d, c) for d, c in wanted.items() if c not in have[d]]
    for start in range(0, len(links), BATCH):
        portal.post("/crm/v4/associations/deals/contacts/batch/associate/default",
                    {"inputs": [{"from": {"id": d}, "to": {"id": c}} for d, c in links[start:start + BATCH]]})
    return {"contacts_created": len(creates), "contacts_updated": len(updates),
            "deals_created": len(deal_creates), "deals_updated": len(deal_updates),
            "associations_added": len(links)}


def _f(prop: str, operator: str, value=None, values=None) -> dict:
    item: dict = {"propertyName": prop, "operator": operator}
    if value is not None:
        item["value"] = value
    if values is not None:
        item["values"] = values
    return item


GOPS = _f("growthops_contact_id", "HAS_PROPERTY")
CLEANUP_RULES = {
    "open_leads_without_owner": [GOPS, _f("hubspot_owner_id", "NOT_HAS_PROPERTY"),
                                 _f("lifecyclestage", "IN", values=list(OPEN_LIFECYCLE))],
    "closed_won_not_customer": [GOPS, _f("growthops_has_closed_won", "EQ", "true"),
                                _f("lifecyclestage", "NEQ", "customer")],
    "paying_not_customer": [GOPS, _f("growthops_net_cash", "GT", "0"), _f("lifecyclestage", "NEQ", "customer")],
}


def _settled_count(portal: Portal, filters: list[dict], expected: int, attempts: int = 12) -> int:
    """Search is indexed asynchronously; poll until the count reaches what the writes imply."""
    count = portal.count("contacts", filters)
    for _ in range(attempts):
        if count == expected:
            break
        portal.sleep(5)
        count = portal.count("contacts", filters)
    return count


def cleanup(portal: Portal, connection: sqlite3.Connection, owner_map: dict, as_of: date = AS_OF) -> dict:
    """Find CRM hygiene problems in the portal itself, fix them in bulk, and report before and after counts."""
    reps = sorted(owner_map["map"])
    # Search compares date properties as epoch milliseconds at midnight UTC.
    stale_cutoff = datetime.combine(as_of - timedelta(days=STALE_DAYS), datetime.min.time(), timezone.utc)
    rules = {**CLEANUP_RULES, "stale_leads_unflagged": [
        GOPS, _f("lifecyclestage", "EQ", "lead"),
        _f("growthops_last_activity_date", "LT", str(int(stale_cutoff.timestamp() * 1000))),
        _f("growthops_stale_lead", "NOT_HAS_PROPERTY")]}
    report = {}
    for name, filters in rules.items():
        found = portal.search("contacts", filters, ["growthops_contact_id", "lifecyclestage"])
        updates = []
        for i, record in enumerate(sorted(found, key=lambda r: r["properties"]["growthops_contact_id"])):
            if name == "open_leads_without_owner":
                rep = reps[i % len(reps)]  # rotate across the rep roster, as a lead-rotation rule would
                props = {"growthops_owner": rep, "hubspot_owner_id": owner_map["map"][rep]}
            elif name == "stale_leads_unflagged":
                props = {"growthops_stale_lead": "true"}
            else:
                props = {"lifecyclestage": "customer"}
            updates.append({"id": record["id"], "properties": props})
        for start in range(0, len(updates), BATCH):
            portal.post("/crm/v3/objects/contacts/batch/update", {"inputs": updates[start:start + BATCH]})
        after = _settled_count(portal, filters, 0) if updates else 0
        report[name] = {"found": len(found), "fixed": len(updates), "remaining": after}
    return report


def list_specs() -> list[dict]:
    def prop(name: str, operation: dict) -> dict:
        return {"filterType": "PROPERTY", "property": name, "operation": operation}

    def enum(name: str, operator: str, values: list[str]) -> dict:
        return prop(name, {"operationType": "ENUMERATION", "operator": operator, "values": values})

    def boolean(name: str) -> dict:
        return prop(name, {"operationType": "BOOL", "operator": "IS_EQUAL_TO", "value": True})

    unknown = {"operationType": "ALL_PROPERTY", "operator": "IS_UNKNOWN"}
    return [
        {"name": LIST_PREFIX + "Tracking gaps (missing or off-taxonomy UTM)",
         "filters": [enum("growthops_tracking_status", "IS_ANY_OF", ["missing_utm", "off_taxonomy"])]},
        {"name": LIST_PREFIX + "Open leads without an owner",
         "filters": [prop("hubspot_owner_id", unknown), enum("lifecyclestage", "IS_ANY_OF", list(OPEN_LIFECYCLE))]},
        {"name": LIST_PREFIX + "Closed won but not Customer",
         "filters": [boolean("growthops_has_closed_won"), enum("lifecyclestage", "IS_NONE_OF", ["customer"])]},
        {"name": LIST_PREFIX + "Stale leads (non-marketing candidates)", "filters": [boolean("growthops_stale_lead")]},
        {"name": LIST_PREFIX + "MQLs from meta_broad_v17",
         "filters": [enum("growthops_lead_creation_campaign", "IS_ANY_OF", ["meta_broad_v17"]),
                     enum("lifecyclestage", "IS_ANY_OF", ["marketingqualifiedlead"])]},
        {"name": LIST_PREFIX + "Booked a discovery call",
         "filters": [prop("growthops_call_booked_date", {"operationType": "ALL_PROPERTY", "operator": "IS_KNOWN"})]},
    ]


def _branch(filters: list[dict], operator_key: bool = False) -> dict:
    inner: dict = {"filterBranchType": "AND", "filterBranches": [], "filters": filters}
    outer: dict = {"filterBranchType": "OR", "filterBranches": [inner], "filters": []}
    if operator_key:  # the automation API names the operator explicitly
        inner["filterBranchOperator"], outer["filterBranchOperator"] = "AND", "OR"
    return outer


def ensure_lists(portal: Portal) -> dict:
    found = portal.post("/crm/v3/lists/search", {"query": LIST_PREFIX.strip(), "count": 100}).get("lists", [])
    existing = {item["name"]: item for item in found}
    created, sizes = [], {}
    for spec in list_specs():
        item = existing.get(spec["name"])
        if item is None:
            item = portal.post("/crm/v3/lists", {"name": spec["name"], "objectTypeId": "0-1",
                                                 "processingType": "DYNAMIC",
                                                 "filterBranch": _branch(spec["filters"])}).get("list", {})
            created.append(spec["name"])
        sizes[spec["name"]] = item.get("additionalProperties", {}).get("hs_list_size")
    return {"created": created, "lists": sorted(existing) + created, "sizes": sizes}


def workflow_specs(default_owner: str) -> list[dict]:
    def prop(name: str, operation: dict) -> dict:
        return {"filterType": "PROPERTY", "property": name, "operation": operation}

    def enum(name: str, operator: str, values: list[str]) -> dict:
        return prop(name, {"operationType": "ENUMERATION", "operator": operator, "values": values,
                           "includeObjectsWithNoValueSet": operator == "IS_NONE_OF"})

    unknown = {"operationType": "ALL_PROPERTY", "operator": "IS_UNKNOWN"}
    return [
        {"name": WORKFLOW_PREFIX + "Closed won sets lifecycle to Customer",
         "filters": [prop("growthops_has_closed_won", {"operationType": "BOOL", "operator": "IS_EQUAL_TO",
                                                       "value": True}),
                     enum("lifecyclestage", "IS_NONE_OF", ["customer"])],
         "set": ("lifecyclestage", "customer")},
        {"name": WORKFLOW_PREFIX + "Route open leads without an owner",
         "filters": [prop("hubspot_owner_id", unknown), enum("lifecyclestage", "IS_ANY_OF", list(OPEN_LIFECYCLE))],
         "set": ("hubspot_owner_id", default_owner)},
        {"name": WORKFLOW_PREFIX + "Flag leads that arrive without UTMs",
         "filters": [prop("growthops_original_utm_source", unknown), prop("growthops_tracking_status", unknown),
                     enum("lifecyclestage", "IS_ANY_OF", list(OPEN_LIFECYCLE))],
         "set": ("growthops_tracking_status", "missing_utm")},
    ]


def ensure_workflows(portal: Portal, owner_map: dict, known: dict[str, str] | None = None,
                     enable: bool = True) -> dict:
    """Create the workflows through the Automation v4 API, once.

    The API answers HTTP 403 MISSING_SCOPES, even to a key that holds ``automation``, when the portal itself has no
    workflows entitlement. A new developer test account can start on the free tier until its Enterprise trials are
    applied (Development > Test accounts > Actions > Renew trials); the error below says so rather than
    blaming the key.

    The list endpoint cannot be trusted to find them again: flows created through the API come back as an empty
    list, and a GET by ID answers "Flow must be accessible via external APIs". A re-run that relied on the list
    created every workflow a second time, so ``known`` (name -> flow ID, from the local state file) is checked too.
    """
    known = known if known is not None else {}
    try:
        listed = {flow["name"]: str(flow.get("id", "")) for flow in portal.paged("/automation/v4/flows")}
    except ProviderError as error:
        if "HTTP 403" in str(error):
            raise ProviderError("workflows are not enabled in this portal; if it is a developer test account, "
                                "renew its trials (Development > Test accounts > Actions) and retry") from error
        raise
    existing = {**known, **listed}
    created = []
    for spec in workflow_specs(owner_map["default"]):
        if spec["name"] in existing:
            continue
        name, value = spec["set"]
        flow = portal.post("/automation/v4/flows", {
            "type": "CONTACT_FLOW", "objectTypeId": "0-1", "flowType": "WORKFLOW", "name": spec["name"],
            "isEnabled": enable, "startActionId": "1", "nextAvailableActionId": "2",
            "actions": [{"type": "SINGLE_CONNECTION", "actionId": "1", "actionTypeVersion": 0,
                         "actionTypeId": "0-5",
                         "fields": {"property_name": name, "value": {"type": "STATIC_VALUE", "staticValue": value}}}],
            "enrollmentCriteria": {"type": "LIST_BASED", "shouldReEnroll": True,
                                   "unEnrollObjectsNotMeetingCriteria": False,
                                   "listFilterBranch": _branch(spec["filters"], operator_key=True),
                                   "reEnrollmentTriggersFilterBranches": []},
            "timeWindows": [], "blockedDates": [], "customProperties": {}, "suppressionListIds": [],
            "canEnrollFromSalesforce": False,
        })
        existing[spec["name"]] = str(flow.get("id", ""))
        created.append(spec["name"])
    return {"created": created, "workflows": sorted(existing), "ids": dict(sorted(existing.items()))}


def verify(portal: Portal, connection: sqlite3.Connection, pipeline: dict) -> dict:
    """Read the portal back through the CRM API and compare it with what the warehouse says it should hold."""
    desired = records(connection)
    expected = expected_after_cleanup(desired)
    lifecycle = {stage: portal.count("contacts", [GOPS, _f("lifecyclestage", "EQ", stage)])
                 for stage in sorted(expected["lifecycle"])}
    deals = portal.search("deals", [_f("growthops_deal_id", "HAS_PROPERTY")],
                          ["amount", "dealstage", "growthops_net_cash"])
    won_id = pipeline["stages"]["closedwon"]
    won = [d for d in deals if d["properties"].get("dealstage") == won_id]
    actual = {
        "contacts": portal.count("contacts", [GOPS]),
        "lifecycle": lifecycle,
        "stale_leads": portal.count("contacts", [GOPS, _f("growthops_stale_lead", "EQ", "true")]),
        "deals": len(deals),
        "closed_won_deals": len(won),
        "closed_won_amount": round(sum(float(d["properties"].get("amount") or 0) for d in won), 2),
        "deal_net_cash": round(sum(float(d["properties"].get("growthops_net_cash") or 0) for d in deals), 2),
    }
    status = {s: portal.count("contacts", [GOPS, _f("growthops_tracking_status", "EQ", s)]) for s in TRACKING_STATUSES}
    differences = {key: {"expected": expected[key], "portal": actual[key]}
                   for key in expected if expected[key] != actual[key]}
    return {"expected": expected, "portal": actual, "tracking_status": status, "differences": differences,
            "lifecycle_set_by": lifecycle_sources(portal, [c["growthops_contact_id"] for c in desired["contacts"]]),
            "reconciled": not differences}


def lifecycle_sources(portal: Portal, contact_ids: list[str]) -> dict[str, int]:
    """Which system set each contact's current lifecycle stage, from HubSpot's own property history.

    This is how the portal shows that HubSpot's deal lifecycle sync, not the cleanup, promoted contacts once their
    deals were associated. Reads with history are limited to 50 records a request.
    """
    counts: dict[str, int] = defaultdict(int)
    for start in range(0, len(contact_ids), 50):
        page = portal.post("/crm/v3/objects/contacts/batch/read", {
            "idProperty": "growthops_contact_id", "properties": ["growthops_contact_id"],
            "propertiesWithHistory": ["lifecyclestage"],
            "inputs": [{"id": value} for value in contact_ids[start:start + 50]]})
        for result in page.get("results", []):
            history = result.get("propertiesWithHistory", {}).get("lifecyclestage") or [{}]
            latest = history[0]
            counts[f"{latest.get('value', 'none')} via {latest.get('sourceType', 'unknown')}"] += 1
    return dict(sorted(counts.items()))


def prune(portal: Portal, connection: sqlite3.Connection, desired: dict[str, list[dict]]) -> dict:
    """Archive GrowthOps contacts and deals that are in the portal but outside the sample.

    Archiving is HubSpot's soft delete: records go to the recycle bin and can be restored for 90 days, and this code
    never deletes permanently. Archived contacts stop counting toward the contact cap: after the first prune, the
    sync created the sample's 244 missing contacts without a limit error.
    """
    _, members = _group_ids(connection)
    keep = {c["growthops_contact_id"] for c in desired["contacts"]}
    keep_deals = {d["growthops_deal_id"] for d in desired["deals"]}
    all_deals = [row[0] for row in connection.execute("SELECT deal_id FROM deals ORDER BY deal_id")]
    archived = {}
    for object_type, id_property, outside in (
            ("contacts", "growthops_contact_id", [head for head in sorted(members) if head not in keep]),
            ("deals", "growthops_deal_id", [deal for deal in all_deals if deal not in keep_deals])):
        found = [result["id"] for result in _batch_read(portal, object_type, id_property, outside,
                                                        [id_property]).values()]
        for start in range(0, len(found), BATCH):
            portal.post(f"/crm/v3/objects/{object_type}/batch/archive",
                        {"inputs": [{"id": hs_id} for hs_id in found[start:start + BATCH]]})
        archived[object_type] = len(found)
    return {"sample_basis_points": PORTAL_SAMPLE_BASIS_POINTS, "contacts_kept": len(keep),
            "deals_kept": len(keep_deals), "contacts_archived": archived["contacts"],
            "deals_archived": archived["deals"]}


# --------------------------------------------------------------------------- orchestration and evidence

STEPS = ("properties", "pipeline", "prune", "import", "sync", "cleanup", "lists", "workflows", "verify")


def apply(portal: Portal, connection: sqlite3.Connection, steps: Iterable[str] = STEPS,
          allow_portal: str | None = None, output: Path = Path("build/hubspot")) -> dict:
    steps = list(steps)
    evidence: dict = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "account": account(portal, allow_portal), "steps": {}}
    desired = records(connection)
    if "properties" in steps:
        evidence["steps"]["properties"] = ensure_properties(portal, connection)
    pipeline = ensure_pipeline(portal)
    evidence["steps"]["pipeline"] = {"label": PIPELINE_LABEL, "created": pipeline["created"],
                                     "stages": [label for _, label, _, _ in PIPELINE_STAGES]}
    owner_map = owners(portal, connection)
    evidence["steps"]["owners"] = {"portal_owners": owner_map["portal_owners"], "reps_mapped": len(owner_map["map"])}
    if "prune" in steps:
        evidence["steps"]["prune"] = prune(portal, connection, desired)
    if "import" in steps:
        loaded = portal.count("contacts", [GOPS])
        # A migration import runs once; after that, sync owns the records and writes only differences.
        evidence["steps"]["import"] = (import_contacts(portal, desired["contacts"], owner_map, output) if not loaded
                                       else {"skipped": f"{loaded} GrowthOps contacts are already in the portal"})
    if "sync" in steps:
        evidence["steps"]["sync"] = sync(portal, desired, pipeline, owner_map)
    if "cleanup" in steps:
        evidence["steps"]["cleanup"] = cleanup(portal, connection, owner_map)
    if "lists" in steps:
        evidence["steps"]["lists"] = ensure_lists(portal)
    if "workflows" in steps:
        # Local state (git-ignored, per portal) remembers the flow IDs the API will not list back.
        state_path = output / "portal_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        portal_state = state.setdefault(evidence["account"]["portal_id"], {})
        result = ensure_workflows(portal, owner_map, portal_state.get("workflows", {}))
        portal_state["workflows"] = result["ids"]
        output.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        evidence["steps"]["workflows"] = result
    if "verify" in steps:
        evidence["steps"]["verify"] = verify(portal, connection, pipeline)
    evidence["api_calls"] = len(portal.calls)
    evidence["api_writes"] = portal.writes
    return evidence


# Steps whose work is spread over runs: a re-run that finds nothing to do must not erase what an earlier run did.
ACCUMULATING_STEPS = frozenset({"properties", "prune", "sync", "cleanup", "lists", "workflows"})
POINT_IN_TIME = frozenset({"remaining", "contacts_kept", "deals_kept", "sample_basis_points"})


def _accumulate(previous: dict, new: dict) -> dict:
    """Counts add up, ``created`` lists union, and point-in-time values (what remains, what is kept) are the latest."""
    merged = dict(new)
    for key, value in new.items():
        before = previous.get(key)
        if key in POINT_IN_TIME or isinstance(value, bool):
            continue
        if isinstance(value, int) and isinstance(before, int):
            merged[key] = before + value
        elif isinstance(value, list) and isinstance(before, list) and key == "created":
            merged[key] = before + [item for item in value if item not in before]
        elif isinstance(value, dict) and isinstance(before, dict):
            merged[key] = _accumulate(before, value)
    return merged


def write_evidence(evidence: dict, output: Path = Path("build/hubspot")) -> Path:
    """Merge a run into the evidence file: each step keeps its latest real result.

    Steps run in separate invocations, so a run that skips a step (or only re-verifies) must not erase what an
    earlier run recorded, such as the one-time import's own counters.
    """
    output.mkdir(parents=True, exist_ok=True)
    path = output / "portal_evidence.json"
    merged = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"steps": {}}
    history = merged.setdefault("history", [])
    history.append({"run_at": evidence["run_at"], "steps": sorted(evidence["steps"]),
                    "api_calls": evidence["api_calls"], "api_writes": evidence["api_writes"]})
    for name, result in evidence["steps"].items():
        previous = merged["steps"].get(name)
        if name == "import" and "skipped" in result and previous:
            continue
        if name in ACCUMULATING_STEPS and previous:
            result = _accumulate(previous, result)
        merged["steps"][name] = {**result, "run_at": evidence["run_at"]}
    merged.update({k: v for k, v in evidence.items() if k not in {"steps", "api_calls", "api_writes"}})
    merged["api_calls"] = sum(run["api_calls"] for run in history)
    merged["api_writes"] = sum(run["api_writes"] for run in history)
    path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# HubSpot has no public API for custom reports or dashboards, so these are built in the report builder by hand to
# this spec; the dashboard is named in the evidence doc and its figures are the ones verify reads back.
DASHBOARD = "GrowthOps: Marketing measurement"
REPORT_SPECS = (
    ("GrowthOps: Contacts by tracking status", "contacts", "Tracking status", "count of contacts", "all time"),
    ("GrowthOps: Contacts by original UTM source", "contacts", "Original UTM source", "count of contacts", "all time"),
    ("GrowthOps: Contacts by lifecycle stage", "contacts", "Lifecycle stage", "count of contacts", "all time"),
    ("GrowthOps: Closed-won revenue by first-touch campaign", "deals", "First-touch campaign", "sum of amount",
     "all time, deal stage = Closed won"),
)


def render_doc(evidence: dict) -> str:
    """The committed write-up of the live build: what exists in the portal, what broke, and how it reconciles."""
    steps = evidence["steps"]
    verify_step = steps.get("verify", {})

    def rows(result: dict) -> dict:
        return {k: v for k, v in result.items() if isinstance(v, dict)}

    lines = [
        "# HubSpot portal build (live)",
        "",
        "Generated by `python -m growthops.hubspot_portal apply` from `build/hubspot/portal_evidence.json`; do not",
        "edit. The portal is a HubSpot **developer test account** with Enterprise trials, holding the synthetic",
        "ScaleLab scenario. Addresses use `scalelab.example.com` (RFC 2606, undeliverable) and no workflow sends email.",
        "",
        (f"Last run {evidence['run_at']} · account type `{evidence['account']['account_type']}` · "
         f"{evidence['api_calls']:,} API calls and {evidence['api_writes']:,} writes across "
         f"{len(evidence.get('history', []))} runs."),
        "",
        "## What was built",
        "",
        "| Step | Result |",
        "|---|---|",
    ]
    if "properties" in steps:
        lines.append(f"| Custom properties | {len(steps['properties']['created'])} in a `growthops` group on contacts "
                     "and deals: original/latest UTM, first/latest content, funnel dates, tracking status, rep, "
                     "net cash, renewal due date and risk, first-touch/lead-creation/last-non-direct attribution |")
    lines.append(f"| Deal pipeline | {PIPELINE_LABEL}: " + " → ".join(steps["pipeline"]["stages"]) + " |")
    lines.append(f"| Owners | {steps['owners']['reps_mapped']} reps mapped onto {steps['owners']['portal_owners']} "
                 "portal user(s) through the Owners API; the rep stays in `growthops_owner` |")
    if "prune" in steps:
        pr = steps["prune"]
        lines.append(f"| Sample | {pr['contacts_kept']:,} contacts and {pr['deals_kept']:,} deals kept "
                     f"({pr['sample_basis_points'] / 100:.1f}% hash sample plus every lifecycle break); "
                     f"{pr['contacts_archived']:,} contacts archived to fit the 1,000-contact cap |")
    if "import" in steps:
        imp = steps["import"]
        lines.append("| Imports API | " + (imp["skipped"] if "skipped" in imp else
                     f"{imp['rows']:,} rows (the full 14,555 before the cap was known), state {imp['state']}: "
                     + ", ".join(f"{k.lower().replace('_', ' ')} {v:,}" for k, v in sorted(imp["counters"].items()))
                     + "; errors " + ", ".join(f"{k} {v:,}" for k, v in imp.get("error_types", {}).items())) + " |")
    if "sync" in steps:
        sy = steps["sync"]
        lines.append(f"| CRM API sync | contacts {sy['contacts_created']:,} created / {sy['contacts_updated']:,} "
                     f"updated; deals {sy['deals_created']:,} created / {sy['deals_updated']:,} updated; "
                     f"{sy['associations_added']:,} deal-to-contact associations (only differences are written) |")
    if "lists" in steps:
        sizes = steps["lists"].get("sizes", {})
        lines.append("| Lists API | " + "; ".join(f"{name.removeprefix(LIST_PREFIX)} ({sizes.get(name, '?')})"
                                                   for name in steps["lists"]["lists"]) + " |")
    if "workflows" in steps:
        lines.append("| Automation API | " + "; ".join(name.removeprefix(WORKFLOW_PREFIX)
                                                     for name in steps["workflows"]["workflows"])
                     + " (contact workflows, published, property actions only) |")
    lines.append(f"| Report builder (UI) | Dashboard \"{DASHBOARD}\": "
                 + "; ".join(name.removeprefix("GrowthOps: ") for name, *_ in REPORT_SPECS)
                 + " (HubSpot has no public API for reports) |")
    if "cleanup" in steps:
        lines += ["", "## CRM cleanup: found through the search API, fixed in bulk", "",
                  "| Rule | Found | Fixed | Remaining |", "|---|---:|---:|---:|"]
        for rule, result in rows(steps["cleanup"]).items():
            lines.append(f"| {rule.replace('_', ' ')} | {result['found']:,} | {result['fixed']:,} | "
                         f"{result['remaining']:,} |")
    if verify_step:
        lines += ["", "## Read back and reconciled to the warehouse", "",
                  "| Measure | Warehouse | Portal |", "|---|---:|---:|"]
        for key, expected in verify_step["expected"].items():
            portal_value = verify_step["portal"][key]
            if isinstance(expected, dict):
                for sub, value in expected.items():
                    lines.append(f"| {key} · {sub} | {value:,} | {portal_value.get(sub, 0):,} |")
            else:
                lines.append(f"| {key.replace('_', ' ')} | {expected:,} | {portal_value:,} |")
        lines += ["", "Tracking status in the portal: " + ", ".join(
            f"{k.replace('_', ' ')} {v:,}" for k, v in verify_step["tracking_status"].items()) + ".", ""]
        if verify_step.get("lifecycle_set_by"):
            lines += ["Who set each contact's current lifecycle stage (HubSpot property history): " + ", ".join(
                f"{k} {v:,}" for k, v in verify_step["lifecycle_set_by"].items()) + ".", ""]
        lines += ["**Reconciled: " + ("yes, zero differences." if verify_step["reconciled"] else
                                      f"no: {json.dumps(verify_step['differences'])}") + "**"]
    lines += [
        "", "## What the live portal taught",
        "",
        "- **The test account keeps the free CRM's 1,000-contact cap** despite its Enterprise trials. The first import",
        "  of all 14,555 contacts landed 11,113 before HubSpot's count caught up, then failed the rest",
        "  `LIMIT_EXCEEDED`; the API then refused every create with HTTP 402. The build now holds a deterministic",
        "  sample, and archived contacts stop counting toward the cap.",
        "- **HubSpot rejects `.test` addresses** (`INVALID_EMAIL` on every row), so the portal copy moves each",
        "  mailbox to an RFC 2606 domain instead.",
        "- **HubSpot's deal lifecycle sync did the lifecycle cleanup first.** The 19 contacts the migration left with",
        "  a closed-won deal short of Customer were promoted by source `DEALS` the moment the sync associated their",
        "  deal, so the cleanup rule found none, and a contact with any deal is lifted to Opportunity. The expected",
        "  state models that sync, which is why the reconciliation holds.",
        "- **A 403 `MISSING_SCOPES` from the workflows API meant the portal, not the key.** The test account started",
        "  on the free tier until its trials were renewed; the same key then created all three workflows.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision and verify a HubSpot portal from GrowthOps.")
    parser.add_argument("command", choices=("plan", "apply", "verify"))
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--steps", default=",".join(STEPS), help="comma-separated subset of: " + ", ".join(STEPS))
    parser.add_argument("--allow-portal", help="portal ID to allow when it is not a developer test account")
    parser.add_argument("--output", default="build/hubspot")
    args = parser.parse_args()
    from growthops.db import connect

    connection = connect(args.database)
    try:
        if args.command == "plan":
            print(json.dumps(plan(connection), indent=2))
            return
        portal = Portal(load_token())
        steps = ["verify"] if args.command == "verify" else [s.strip() for s in args.steps.split(",") if s.strip()]
        unknown = set(steps) - set(STEPS)
        if unknown:
            raise SystemExit(f"unknown steps: {sorted(unknown)}")
        evidence = apply(portal, connection, steps, args.allow_portal, Path(args.output))
        print(json.dumps(evidence, indent=2))
        print(f"evidence written to {write_evidence(evidence, Path(args.output))}")
        if "verify" in steps and len(steps) > 1:
            merged = json.loads((Path(args.output) / "portal_evidence.json").read_text(encoding="utf-8"))
            EVIDENCE_DOC.write_text(render_doc(merged), encoding="utf-8")
            print(f"write-up regenerated at {EVIDENCE_DOC}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
