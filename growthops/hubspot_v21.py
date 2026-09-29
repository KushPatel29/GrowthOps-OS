"""Reviewed, schema-only HubSpot v2.1 extension for the synthetic test portal.

This module never populates record values or changes associations, lifecycle,
marketing status, lists, or workflows. The write path is gated by an explicit
CLI flag and checks the portal and every existing definition before posting.
"""

from __future__ import annotations

import argparse
import json
import sqlite3

from growthops.db import connect, initialize
from growthops.hubspot_portal import (
    GROUP,
    Portal,
    account,
    load_token,
    portal_properties,
)

EXPECTED_PORTAL_ID = "247549241"


def _definition_issues(object_type: str, expected: dict, current: dict) -> list[dict]:
    issues = []
    for field in ("label", "groupName", "type", "fieldType", "hasUniqueValue"):
        if field in expected and current.get(field) != expected[field]:
            issues.append({"object": object_type, "property": expected["name"],
                           "issue": f"{field}_mismatch", "expected": expected[field],
                           "observed": current.get(field)})
    if "options" in expected:
        wanted = [item["value"] for item in expected["options"]]
        observed = [item["value"] for item in current.get("options", []) if not item.get("hidden")]
        if observed != wanted:
            issues.append({"object": object_type, "property": expected["name"],
                           "issue": "options_mismatch", "expected": wanted, "observed": observed})
    return issues


def definitions() -> dict[str, list[dict]]:
    group = GROUP
    return {
        "contacts": [
            {"name": "growthops_person_key", "label": "GrowthOps person key",
             "type": "string", "fieldType": "text", "groupName": group,
             "description": "Verified GrowthOps identity key; empty until identity review."},
            {"name": "growthops_sql_date", "label": "Became SQL",
             "type": "date", "fieldType": "date", "groupName": group,
             "description": "Accepted sales-qualified transition date; historical unknown stays empty."},
        ],
        "deals": [
            {"name": "growthops_qualification_status", "label": "GrowthOps qualification status",
             "type": "enumeration", "fieldType": "select", "groupName": group,
             "description": "Explicit rep qualification decision, not inferred from a meeting.",
             "options": [{"label": label, "value": value, "displayOrder": order}
                         for order, (label, value) in enumerate((
                             ("Qualified", "qualified"), ("Unqualified", "unqualified"),
                             ("Unknown", "unknown"))) ]},
            {"name": "growthops_qualified_at", "label": "GrowthOps qualified at",
             "type": "datetime", "fieldType": "date", "groupName": group,
             "description": "Source timestamp of an explicit rep qualification decision."},
            {"name": "growthops_qualification_reason", "label": "GrowthOps qualification reason",
             "type": "string", "fieldType": "text", "groupName": group,
             "description": "Reason supplied with an explicit rep qualification decision."},
        ],
    }


def plan() -> dict:
    return {"mode": "schema_only", "target_portal_id": EXPECTED_PORTAL_ID,
            "properties": definitions(), "record_values_changed": 0,
            "requires_approval": True}


def audit_schema(portal: Portal, connection: sqlite3.Connection) -> dict:
    """Read-only comparison of the baseline registry and proposed additions."""
    found = account(portal)
    if found["portal_id"] != EXPECTED_PORTAL_ID:
        raise ValueError(f"refusing portal {found['portal_id']}; expected {EXPECTED_PORTAL_ID}")
    baseline = portal_properties(connection)
    proposed = definitions()
    issues: list[dict] = []
    proposed_issues: list[dict] = []
    additions: list[dict] = []
    present = 0
    for object_type in baseline:
        actual = {item["name"]: item for item in portal.get(
            f"/crm/v3/properties/{object_type}"
        ).get("results", [])}
        for expected in baseline[object_type]:
            current = actual.get(expected["name"])
            if current is None:
                issues.append({"object": object_type, "property": expected["name"],
                               "issue": "missing"})
                continue
            present += 1
            issues.extend(_definition_issues(object_type, expected, current))
        for expected in proposed[object_type]:
            current = actual.get(expected["name"])
            additions.append({"object": object_type, "property": expected["name"],
                              "state": "present" if current else "not_created"})
            if current:
                proposed_issues.extend(_definition_issues(object_type, expected, current))
    return {"portal_id": found["portal_id"], "mode": "read_only",
            "baseline_expected": sum(map(len, baseline.values())),
            "baseline_present": present, "baseline_issues": issues,
            "proposed_additions": additions, "proposed_issues": proposed_issues, "writes": 0}


def apply_schema(portal: Portal, *, approved: bool = False) -> dict:
    if not approved:
        raise PermissionError("explicit schema approval required")
    found = account(portal)
    if found["portal_id"] != EXPECTED_PORTAL_ID:
        raise ValueError(f"refusing portal {found['portal_id']}; expected {EXPECTED_PORTAL_ID}")
    wanted = definitions()
    present: dict[str, dict[str, dict]] = {}
    for object_type in wanted:
        present[object_type] = {
            prop["name"]: prop for prop in portal.get(
                f"/crm/v3/properties/{object_type}"
            ).get("results", [])
        }
    conflicts = []
    for object_type, properties in wanted.items():
        for prop in properties:
            old = present[object_type].get(prop["name"])
            if old and (old.get("label") != prop["label"] or
                        old.get("groupName") != prop["groupName"] or
                        old.get("type") != prop["type"] or
                        old.get("fieldType") != prop["fieldType"] or
                        ("options" in prop and
                         [x["value"] for x in old.get("options", []) if not x.get("hidden")]
                         != [x["value"] for x in prop["options"]])):
                conflicts.append(f"{object_type}.{prop['name']}")
    if conflicts:
        raise ValueError(f"existing property definitions conflict: {', '.join(conflicts)}")
    created = []
    for object_type, properties in wanted.items():
        for prop in properties:
            if prop["name"] not in present[object_type]:
                portal.post(f"/crm/v3/properties/{object_type}", prop)
                created.append(f"{object_type}.{prop['name']}")
    for object_type, properties in wanted.items():
        readback = {prop["name"]: prop for prop in portal.get(
            f"/crm/v3/properties/{object_type}"
        ).get("results", [])}
        readback_issues = []
        for prop in properties:
            current = readback.get(prop["name"])
            if current is None:
                readback_issues.append({"object": object_type, "property": prop["name"],
                                        "issue": "missing"})
            else:
                readback_issues.extend(_definition_issues(object_type, prop, current))
        if readback_issues:
            raise RuntimeError(f"HubSpot schema read-back mismatch: {readback_issues}")
    return {"portal_id": found["portal_id"], "created": created,
            "verified_properties": sum(map(len, wanted.values())), "record_values_changed": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "audit", "apply"))
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--approved-schema-only", action="store_true",
                        help="use only after the exact five-property plan was approved in chat")
    args = parser.parse_args()
    if args.command == "plan":
        print(json.dumps(plan(), indent=2))
        return
    if args.command == "audit":
        connection = connect(args.database)
        try:
            initialize(connection)
            result = audit_schema(Portal(load_token()), connection)
        finally:
            connection.close()
        print(json.dumps(result, indent=2))
        return
    if not args.approved_schema_only:
        raise SystemExit("schema approval required; inspect `plan` and get explicit approval first")
    print(json.dumps(apply_schema(Portal(load_token()), approved=True), indent=2))


if __name__ == "__main__":
    main()
