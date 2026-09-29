"""Synthetic identity, CRM health and qualified-pipeline control plane.

The overlay is derived from the existing scenario with an independent,
deterministic qualification rule. It never writes to a provider account.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from growthops.scenario import AS_OF

VERSION = "2.1"
STAGES = ("subscriber", "lead", "mql", "sql", "opportunity", "customer")
DEMO_HASH_KEY = b"growthops-synthetic-identity-v1"


class EventContract(TypedDict):
    trigger: str
    required: list[str]
    conversion: bool


EVENT_CONTRACTS: dict[str, EventContract] = {
    "page_view": {"trigger": "web", "required": ["page_path", "anonymous_id"], "conversion": False},
    "video_start": {"trigger": "web", "required": ["content_id", "anonymous_id"], "conversion": False},
    "lead_form_submit": {"trigger": "form", "required": ["form_id", "submission_id", "anonymous_id"],
                         "conversion": True},
    "meeting_booked": {"trigger": "backend", "required": ["meeting_id", "person_key"],
                       "conversion": True},
    "checkout_started": {"trigger": "web", "required": ["product_id", "person_key"],
                         "conversion": False},
    "purchase": {"trigger": "payment_bridge",
                 "required": ["transaction_id", "person_key", "currency", "value_minor", "product_id"],
                 "conversion": True},
}


def _digest(value: str) -> str:
    return hashlib.sha256(DEMO_HASH_KEY + value.encode("utf-8")).hexdigest()


def _issue_id(rule: str, entity_type: str, entity_id: str) -> str:
    return "qi_" + _digest(f"{rule}:{entity_type}:{entity_id}")[:20]


def _issue(
    connection: sqlite3.Connection,
    rule: str,
    entity_type: str,
    entity_id: str,
    severity: str,
    evidence: dict,
) -> None:
    now = datetime.combine(AS_OF, datetime.min.time(), UTC).isoformat()
    connection.execute(
        """INSERT OR IGNORE INTO quality_issues
           (issue_id, rule_id, entity_type, entity_id, severity, first_seen_at,
            last_seen_at, state, evidence_ref)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (_issue_id(rule, entity_type, entity_id), rule, entity_type, entity_id,
         severity, now, now, json.dumps(evidence, sort_keys=True)),
    )


def seed_overlay(connection: sqlite3.Connection) -> None:
    """Add explicit synthetic evidence without changing the existing scenario."""
    contacts = connection.execute(
        "SELECT contact_id, email, owner_id, original_source, current_stage, created_at "
        "FROM contacts ORDER BY contact_id"
    ).fetchall()
    email_members: dict[str, list[str]] = defaultdict(list)
    for contact in contacts:
        email_members[contact["email"].strip().lower()].append(contact["contact_id"])
    valid_sources = {
        row[0] for row in connection.execute(
            "SELECT DISTINCT source FROM campaigns WHERE registry_valid=1"
        )
    }
    first_touch = {
        row["contact_id"]: row
        for row in connection.execute(
            """SELECT contact_id, touch_id, occurred_at FROM (
                 SELECT contact_id, touch_id, occurred_at,
                        ROW_NUMBER() OVER (PARTITION BY contact_id
                                           ORDER BY occurred_at, touch_id) rn
                 FROM touches
               ) WHERE rn=1"""
        )
    }
    connection.execute("BEGIN")
    try:
        for contact in contacts:
            person = contact["contact_id"]
            created = contact["created_at"] or datetime.combine(
                AS_OF, datetime.min.time(), UTC
            ).isoformat()
            connection.execute(
                "INSERT OR IGNORE INTO persons VALUES (?, ?, ?, 'resolved')",
                (person, created, VERSION),
            )
            connection.execute(
                """INSERT OR IGNORE INTO identity_links VALUES
                   ('growthops', 'contact_id', ?, ?, ?, ?, ?, 'verified', ?, 'active')""",
                (_digest(person), person, created, created, f"contact:{person}", VERSION),
            )
            email = contact["email"].strip().lower()
            if len(email_members[email]) == 1:
                connection.execute(
                    """INSERT OR IGNORE INTO identity_links VALUES
                       ('synthetic_crm', 'email', ?, ?, ?, ?, ?, 'observed', ?, 'active')""",
                    (_digest(email), person, created, created, f"contact:{person}", VERSION),
                )
            else:
                _issue(connection, "duplicate_email_candidate", "contact", person, "warning",
                       {"email_hash": _digest(email), "matching_records": len(email_members[email])})
            touch = first_touch.get(person)
            if touch:
                connection.execute(
                    """INSERT OR IGNORE INTO identity_links VALUES
                       ('synthetic_web', 'anonymous_id', ?, ?, ?, ?, ?, 'observed', ?, 'active')""",
                    (_digest(f"anon:{touch['touch_id']}"), person, touch["occurred_at"],
                     touch["occurred_at"], touch["touch_id"], VERSION),
                )
            if not contact["owner_id"] and contact["current_stage"] in (
                "lead", "mql", "opportunity"
            ):
                _issue(connection, "actionable_contact_missing_owner", "contact", person, "warning",
                       {"stage": contact["current_stage"]})
            if not contact["original_source"]:
                _issue(connection, "contact_missing_source", "contact", person, "warning",
                       {"stage": contact["current_stage"]})
            elif contact["original_source"] not in valid_sources:
                _issue(connection, "contact_off_taxonomy_source", "contact", person, "warning",
                       {"source": contact["original_source"]})

        for item in connection.execute(
            """SELECT e.engagement_id, e.contact_id, e.content_id, e.touch_id,
                      e.occurred_at, e.watched_seconds, i.platform
               FROM content_engagements e
               JOIN content_items i ON i.content_id=e.content_id"""
        ):
            connection.execute(
                """INSERT OR IGNORE INTO engagement_events
                   (event_id, person_key, anonymous_id_hash, platform, content_id,
                    event_type, occurred_at, duration_seconds, source_event_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"eng:{item['engagement_id']}", item["contact_id"],
                 _digest(f"anon:{item['touch_id']}") if item["touch_id"] else None,
                 item["platform"], item["content_id"], "content_engaged",
                 item["occurred_at"], item["watched_seconds"], item["engagement_id"]),
            )

        # Existing history remains the source of the observed transitions.
        for row in connection.execute(
            """SELECT lifecycle_event_id, contact_id, stage, occurred_at
               FROM lifecycle_events WHERE stage IN ('lead','mql','opportunity','paid')
               ORDER BY contact_id, occurred_at, lifecycle_event_id"""
        ):
            stage = "customer" if row["stage"] == "paid" else row["stage"]
            connection.execute(
                """INSERT OR IGNORE INTO lifecycle_transitions
                   (transition_id, person_key, from_stage, to_stage, occurred_at,
                    source_event_id, policy_version, owner_id, campaign_id)
                   VALUES (?, ?, NULL, ?, ?, ?, ?, NULL, NULL)""",
                (f"lt:{row['lifecycle_event_id']}", row["contact_id"], stage,
                 row["occurred_at"], row["lifecycle_event_id"], VERSION),
            )
        history = connection.execute(
            """SELECT transition_id, person_key, to_stage, occurred_at
               FROM lifecycle_transitions ORDER BY person_key, occurred_at, transition_id"""
        ).fetchall()
        last: dict[str, str] = {}
        for row in history:
            previous = last.get(row["person_key"])
            connection.execute(
                "UPDATE lifecycle_transitions SET from_stage=? WHERE transition_id=?",
                (previous, row["transition_id"]),
            )
            if previous and STAGES.index(row["to_stage"]) < STAGES.index(previous):
                _issue(connection, "backward_lifecycle_transition", "contact",
                       row["person_key"], "warning",
                       {"from_stage": previous, "to_stage": row["to_stage"],
                        "source_event_id": row["transition_id"]})
            last[row["person_key"]] = row["to_stage"]

        # Rep decisions are explicit synthetic fixtures, not a rule that
        # attending a call automatically makes someone qualified.
        for deal in connection.execute(
            "SELECT deal_id, contact_id, amount_cents, stage, created_at, closed_at "
            "FROM deals ORDER BY deal_id"
        ):
            bucket = int(_digest(deal["deal_id"])[:8], 16) % 100
            qualified = deal["stage"] == "closed_won" or bucket < 65
            when = deal["created_at"] or deal["closed_at"] or datetime.combine(
                AS_OF, datetime.min.time(), UTC
            ).isoformat()
            connection.execute(
                """INSERT OR IGNORE INTO deal_qualification
                   (decision_id, deal_id, qualified_at, status, reason, amount_minor,
                    currency, source_event_id, policy_version)
                   VALUES (?, ?, ?, ?, 'synthetic_rep_assessment', ?, 'USD', ?, ?)""",
                (f"qd:{deal['deal_id']}", deal["deal_id"], when,
                 "qualified" if qualified else "unqualified",
                 deal["amount_cents"], f"synthetic_qualification:{deal['deal_id']}", VERSION),
            )

        for deal in connection.execute("SELECT deal_id, contact_id FROM deals"):
            has_campaign = connection.execute(
                """SELECT 1 FROM touches WHERE contact_id=?
                   AND touch_type='lead_creation' AND campaign_id IS NOT NULL LIMIT 1""",
                (deal["contact_id"],),
            ).fetchone()
            if not has_campaign:
                _issue(connection, "deal_missing_lead_campaign", "deal",
                       deal["deal_id"], "warning", {"contact_id": deal["contact_id"]})

        registries = {
            "lifecycle": {"stages": STAGES, "policy_version": VERSION,
                          "backward_transition": "review"},
            "campaign": {"campaign_ids": [
                row[0] for row in connection.execute(
                    "SELECT campaign_id FROM campaigns WHERE registry_valid=1 ORDER BY campaign_id"
                )
            ]},
            "instrumentation": {"events": EVENT_CONTRACTS,
                                "purchase_truth": "signed_payment_bridge"},
        }
        from growthops.hubspot_portal import portal_properties, workflow_specs
        from growthops.hubspot_v21 import definitions as v21_property_definitions

        registries["property"] = {
            "scope": "expected_synthetic_portal_schema",
            "objects": portal_properties(connection),
            "proposed_v21_additions": v21_property_definitions(),
        }
        registries["workflow"] = {
            "scope": "expected_synthetic_portal_workflows",
            "workflows": workflow_specs("<portal_default_owner>"),
        }
        now = datetime.combine(AS_OF, datetime.min.time(), UTC).isoformat()
        for kind, definition in registries.items():
            connection.execute(
                "INSERT OR IGNORE INTO registry_versions VALUES (?, ?, ?, 'growthops_demo', ?, ?)",
                (kind, VERSION, json.dumps(definition, sort_keys=True), now, now),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def qualified_pipeline(connection: sqlite3.Connection) -> dict:
    """Deal-grain pipeline by lead-creation campaign; no cash is implied."""
    rows = connection.execute(
        """WITH lead_touch AS (
             SELECT contact_id, campaign_id,
                    ROW_NUMBER() OVER (PARTITION BY contact_id
                                       ORDER BY occurred_at DESC, touch_id DESC) rn
             FROM touches WHERE touch_type='lead_creation'
           )
           SELECT d.deal_id, d.contact_id, d.stage, q.status, q.amount_minor,
                  q.qualified_at, COALESCE(t.campaign_id, '(unattributed)') campaign_id
           FROM deals d JOIN deal_qualification q ON q.deal_id=d.deal_id
           LEFT JOIN lead_touch t ON t.contact_id=d.contact_id AND t.rn=1
           ORDER BY d.deal_id"""
    ).fetchall()
    qualified = [r for r in rows if r["status"] == "qualified"]
    by_campaign: dict[str, dict] = {}
    for row in qualified:
        item = by_campaign.setdefault(
            row["campaign_id"],
            {"campaign_id": row["campaign_id"], "qualified_deals": 0,
             "created_minor": 0, "open_minor": 0, "won_minor": 0},
        )
        item["qualified_deals"] += 1
        item["created_minor"] += row["amount_minor"]
        if row["stage"] == "open":
            item["open_minor"] += row["amount_minor"]
        elif row["stage"] == "closed_won":
            item["won_minor"] += row["amount_minor"]
    return {
        "scope": "full_synthetic_scenario",
        "as_of": AS_OF.isoformat(),
        "currency": "USD",
        "qualification_policy": VERSION,
        "qualified_deals": len(qualified),
        "qualified_people": len({r["contact_id"] for r in qualified}),
        "created_minor": sum(r["amount_minor"] for r in qualified),
        "open_minor": sum(r["amount_minor"] for r in qualified if r["stage"] == "open"),
        "won_minor": sum(r["amount_minor"] for r in qualified if r["stage"] == "closed_won"),
        "unqualified_deals": len(rows) - len(qualified),
        "by_campaign": sorted(by_campaign.values(), key=lambda x: (-x["created_minor"],
                                                                    x["campaign_id"])),
    }


def crm_health(connection: sqlite3.Connection) -> dict:
    """Transparent component rates with explicit denominators."""
    total = connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    actionable = connection.execute(
        "SELECT COUNT(*) FROM contacts WHERE current_stage IN ('lead','mql','opportunity')"
    ).fetchone()[0]
    owned = connection.execute(
        """SELECT COUNT(*) FROM contacts
           WHERE current_stage IN ('lead','mql','opportunity') AND owner_id IS NOT NULL"""
    ).fetchone()[0]
    source = connection.execute(
        "SELECT COUNT(*) FROM contacts WHERE original_source IS NOT NULL"
    ).fetchone()[0]
    dup = connection.execute(
        "SELECT COUNT(*) FROM quality_issues WHERE rule_id='duplicate_email_candidate' AND state='open'"
    ).fetchone()[0]
    deals = connection.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    with_campaign = deals - connection.execute(
        "SELECT COUNT(*) FROM quality_issues WHERE rule_id='deal_missing_lead_campaign' AND state='open'"
    ).fetchone()[0]
    parts = [
        {"name": "actionable_owner", "passing": owned, "eligible": actionable, "weight": 0.25},
        {"name": "source_present", "passing": source, "eligible": total, "weight": 0.25},
        {"name": "unique_email", "passing": total - dup, "eligible": total, "weight": 0.25},
        {"name": "deal_campaign", "passing": with_campaign, "eligible": deals, "weight": 0.25},
    ]
    for part in parts:
        part["rate"] = round(part["passing"] / part["eligible"], 4) if part["eligible"] else None
    score = sum((part["rate"] or 0) * part["weight"] for part in parts)
    return {
        "scope": "full_synthetic_scenario",
        "as_of": AS_OF.isoformat(),
        "contacts": total,
        "deals": deals,
        "components": parts,
        "score": round(score * 100, 1),
        "open_issues": connection.execute(
            "SELECT COUNT(*) FROM quality_issues WHERE state='open'"
        ).fetchone()[0],
        "marketing_eligibility": "only explicit synthetic consent evidence is eligible; the connected HubSpot test portal is separate",
    }


def quality_queue(
    connection: sqlite3.Connection, *, limit: int = 100, offset: int = 0,
    rule: str | None = None, severity: str | None = None,
    entity_type: str | None = None, state: str = "open",
) -> dict:
    clauses = ["state=?"]
    params: list[str] = [state]
    for column, value in (("rule_id", rule), ("severity", severity),
                          ("entity_type", entity_type)):
        if value is not None:
            clauses.append(f"{column}=?")
            params.append(value)
    where = " AND ".join(clauses)
    total = connection.execute(
        f"SELECT COUNT(*) FROM quality_issues WHERE {where}", params
    ).fetchone()[0]
    rows = connection.execute(
        """SELECT issue_id, rule_id, entity_type, entity_id, severity,
                  first_seen_at, last_seen_at, evidence_ref
           FROM quality_issues WHERE """ + where + """
           ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                    rule_id, entity_id LIMIT ? OFFSET ?""",
        (*params, limit, offset),
    ).fetchall()
    rule_counts = connection.execute(
        "SELECT rule_id, COUNT(*) count FROM quality_issues WHERE state=? "
        "GROUP BY rule_id ORDER BY count DESC, rule_id", (state,),
    ).fetchall()
    return {"total": total, "limit": limit, "offset": offset,
            "filters": {"rule": rule, "severity": severity,
                        "entity_type": entity_type, "state": state},
            "rule_counts": [dict(row) for row in rule_counts],
            "results": [{**dict(row), "evidence": json.loads(row["evidence_ref"])}
                        for row in rows]}


def repair_proposal(connection: sqlite3.Connection, issue_id: str) -> dict | None:
    """A reviewable diagnosis only; no missing CRM value is guessed or written."""
    row = connection.execute(
        """SELECT issue_id, rule_id, entity_type, entity_id, severity, state,
                  evidence_ref FROM quality_issues WHERE issue_id=?""", (issue_id,),
    ).fetchone()
    if row is None:
        return None
    issue = dict(row)
    evidence = json.loads(issue.pop("evidence_ref"))
    remedies = {
        "duplicate_email_candidate": ("review_identity", "Review both records and verified identifiers before merging."),
        "actionable_contact_missing_owner": ("assign_owner", "Choose an owner from current CRM assignment rules."),
        "contact_missing_source": ("restore_source", "Find an authenticated form or tracked touch before filling source."),
        "contact_off_taxonomy_source": ("map_source", "Approve a registry mapping before changing the source."),
        "deal_missing_lead_campaign": ("link_campaign", "Verify a lead-creation touch before associating a campaign."),
        "backward_lifecycle_transition": ("review_transition", "Check source history and correction evidence."),
    }
    action, instruction = remedies.get(issue["rule_id"], ("manual_review", "Review source evidence."))
    return {**issue, "evidence": evidence, "mode": "dry_run", "proposed_action": action,
            "proposed_value": None, "instruction": instruction,
            "can_apply_automatically": False}


def marketing_contact_audit(connection: sqlite3.Connection) -> dict:
    """Evidence-based segmentation candidates; never a marketing-status write."""
    cutoff = (AS_OF - timedelta(days=180)).isoformat()
    row = connection.execute(
        """WITH last_touch AS (
             SELECT contact_id, MAX(occurred_at) last_at FROM touches GROUP BY contact_id
           ), open_deals AS (
             SELECT DISTINCT contact_id FROM deals WHERE stage='open'
           ), buyers AS (
             SELECT DISTINCT customer_id FROM payments WHERE status='succeeded'
           ), consent AS (
             SELECT person_key, status FROM (
               SELECT person_key, status,
                 ROW_NUMBER() OVER (PARTITION BY person_key ORDER BY recorded_at DESC, consent_id DESC) rn
               FROM consent_ledger WHERE channel='email'
             ) WHERE rn=1
           ), duplicates AS (
             SELECT entity_id contact_id FROM quality_issues
             WHERE rule_id='duplicate_email_candidate' AND state='open'
           )
           SELECT COUNT(*) contacts,
             COUNT(CASE WHEN o.contact_id IS NOT NULL THEN 1 END) open_opportunity_people,
             COUNT(CASE WHEN b.customer_id IS NOT NULL THEN 1 END) customers,
             COUNT(CASE WHEN d.contact_id IS NOT NULL THEN 1 END) duplicate_candidates,
             COUNT(CASE WHEN s.status='granted' THEN 1 END) consent_granted,
             COUNT(CASE WHEN s.status IN ('denied','revoked') THEN 1 END) suppressed,
             COUNT(CASE WHEN s.status IS NULL OR s.status='unknown' THEN 1 END) eligibility_unknown,
             COUNT(CASE WHEN o.contact_id IS NULL AND b.customer_id IS NULL
                        AND (t.last_at IS NULL OR t.last_at < ?) THEN 1 END) dormant_candidates
           FROM contacts c
           LEFT JOIN last_touch t ON t.contact_id=c.contact_id
           LEFT JOIN open_deals o ON o.contact_id=c.contact_id
           LEFT JOIN buyers b ON b.customer_id=c.contact_id
           LEFT JOIN consent s ON s.person_key=c.contact_id
           LEFT JOIN duplicates d ON d.contact_id=c.contact_id""",
        (cutoff,),
    ).fetchone()
    return {
        "scope": "full_synthetic_scenario", "data_as_of": AS_OF.isoformat(),
        "dormancy_cutoff": cutoff, "counts": dict(row),
        "marketing_eligibility_policy": "Only an explicit granted email-consent ledger entry is eligible; the planted evidence is synthetic and not synced to HubSpot.",
        "cleanup_policy": "Dormant and duplicate counts are review candidates, never automatic deletions or marketing-status changes.",
    }


def campaign_qa(connection: sqlite3.Connection) -> dict:
    from growthops.campaign_links import audit_short_links

    campaigns = connection.execute(
        "SELECT COUNT(*) total, SUM(registry_valid) valid FROM campaigns"
    ).fetchone()
    links = audit_short_links(connection)
    return {
        "scope": "full_synthetic_scenario", "data_as_of": AS_OF.isoformat(),
        "registry_version": VERSION, "registered_campaigns": campaigns["total"],
        "valid_campaigns": campaigns["valid"],
        "invalid_campaigns": campaigns["total"] - campaigns["valid"],
        "short_links": len(links["links"]), "short_links_with_issues": links["links_with_issues"],
        "recent_clicks_on_broken_links": links["recent_clicks_on_broken_links"],
        "share_of_recent_clicks_broken": links["share_of_recent_clicks_broken"],
        "link_issues": [{"link_id": item["link_id"], "issues": item["issues"],
                         "recent_clicks": item["recent_clicks"]}
                        for item in links["links"] if item["issues"]],
    }


def validate_instrumentation_event(connection: sqlite3.Connection, name: str,
                                   source: str, parameters: dict) -> dict:
    """Check a proposed event against the versioned local contract; no ingestion or conversion export."""
    contract = EVENT_CONTRACTS.get(name)
    issues = []
    if contract is None:
        issues.append("event_name_unregistered")
    else:
        if source != contract["trigger"]:
            issues.append(f"source_must_be_{contract['trigger']}")
        for key in contract["required"]:
            value = parameters.get(key)
            if (value is None or value == "" or
                (key != "value_minor" and
                 (not isinstance(value, str) or not value.strip()))):
                issues.append(f"missing_{key}")
        if name == "purchase":
            if parameters.get("currency") != "USD":
                issues.append("currency_must_be_USD_for_synthetic_contract")
            value = parameters.get("value_minor")
            if type(value) is not int or value <= 0:
                issues.append("value_minor_must_be_positive_integer")
        if campaign_id := parameters.get("campaign_id"):
            if not isinstance(campaign_id, str):
                issues.append("campaign_id_must_be_string")
            else:
                campaign = connection.execute(
                    "SELECT registry_valid, source, medium FROM campaigns WHERE campaign_id=?",
                    (campaign_id,),
                ).fetchone()
                if campaign is None or not campaign["registry_valid"]:
                    issues.append("campaign_id_not_in_valid_registry")
                elif ((parameters.get("utm_source") and parameters["utm_source"] != campaign["source"])
                      or (parameters.get("utm_medium") and parameters["utm_medium"] != campaign["medium"])):
                    issues.append("campaign_utm_taxonomy_mismatch")
    return {"mode": "dry_run", "registry_version": VERSION, "event_name": name,
            "valid": not issues, "issues": issues,
            "conversion": contract["conversion"] if contract else None,
            "emits_to_ad_platform": False}


def registry_versions(connection: sqlite3.Connection, kind: str) -> list[dict]:
    rows = connection.execute(
        """SELECT registry_type, version, definition_json, owner, approved_at, effective_at
           FROM registry_versions WHERE registry_type=? ORDER BY effective_at DESC, version DESC""",
        (kind,),
    ).fetchall()
    return [{**{key: row[key] for key in ("registry_type", "version", "owner",
                                             "approved_at", "effective_at")},
             "definition": json.loads(row["definition_json"])} for row in rows]


def decision_center(connection: sqlite3.Connection) -> dict:
    """One consistent snapshot for the analyst and operator landing view."""
    from growthops.ai_brief import generate as evidence_brief
    from growthops.reconciliation import crm_bridge, four_numbers, platform_bridge
    from growthops.workflow import health as operations_health

    numbers = four_numbers(connection)
    pipeline = qualified_pipeline(connection)
    health = crm_health(connection)
    return {
        "scope": "full_synthetic_scenario", "data_as_of": AS_OF.isoformat(),
        "metric_version": VERSION, "currency": "USD",
        "revenue_truth": {
            "platform_reported_minor": numbers["platform_reported_total_cents"],
            "qualified_pipeline_created_minor": pipeline["created_minor"],
            "crm_booked_minor": numbers["crm_booked_cents"],
            "net_collected_minor": numbers["net_collected_cents"],
            "platform_bridge_residual_minor": platform_bridge(connection)["residual_cents"],
            "crm_bridge_residual_minor": crm_bridge(connection)["residual_cents"],
        },
        "pipeline": pipeline, "crm_health": health,
        "marketing_contacts": marketing_contact_audit(connection),
        "campaign_qa": campaign_qa(connection),
        "operations": operations_health(connection),
        "brief": evidence_brief(connection, limit=3),
        "quality_preview": quality_queue(connection, limit=5),
    }


def person_journey(connection: sqlite3.Connection, person_key: str) -> dict | None:
    contact = connection.execute(
        "SELECT contact_id, current_stage, original_source, owner_id FROM contacts WHERE contact_id=?",
        (person_key,),
    ).fetchone()
    if contact is None:
        return None
    identities = connection.execute(
        """SELECT source_system, id_type, confidence, state, first_seen_at
           FROM identity_links WHERE person_key=? ORDER BY source_system, id_type""",
        (person_key,),
    ).fetchall()
    touches = connection.execute(
        """SELECT touch_id, campaign_id, touch_type, occurred_at
           FROM touches WHERE contact_id=? ORDER BY occurred_at, touch_id LIMIT 100""",
        (person_key,),
    ).fetchall()
    stages = connection.execute(
        """SELECT from_stage, to_stage, occurred_at, source_event_id
           FROM lifecycle_transitions WHERE person_key=?
           ORDER BY occurred_at, transition_id""",
        (person_key,),
    ).fetchall()
    deals = connection.execute(
        """SELECT d.deal_id, d.stage, d.amount_cents, q.status qualification_status,
                  q.qualified_at
           FROM deals d LEFT JOIN deal_qualification q ON q.deal_id=d.deal_id
           WHERE d.contact_id=? ORDER BY d.deal_id""",
        (person_key,),
    ).fetchall()
    payments = connection.execute(
        """SELECT payment_id, deal_id, amount_cents, payment_type, paid_at
           FROM payments WHERE customer_id=? AND status='succeeded'
           ORDER BY paid_at, payment_id""",
        (person_key,),
    ).fetchall()
    return {
        "person_key": person_key,
        "identity_version": VERSION,
        "crm": dict(contact),
        "identity_evidence": [dict(row) for row in identities],
        "touches": [dict(row) for row in touches],
        "lifecycle": [dict(row) for row in stages],
        "deals": [dict(row) for row in deals],
        "payments": [dict(row) for row in payments],
        "touches_truncated": len(touches) == 100,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the synthetic v2.1 control-plane overlay")
    parser.add_argument("--database", default="data/growthops-sample.db")
    args = parser.parse_args()
    from growthops.db import connect, initialize

    connection = connect(args.database)
    try:
        initialize(connection)
        seed_overlay(connection)
        print(json.dumps({"crm_health": crm_health(connection),
                          "qualified_pipeline": qualified_pipeline(connection)}, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
