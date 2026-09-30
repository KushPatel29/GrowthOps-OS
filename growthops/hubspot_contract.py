"""Who owns each HubSpot field: the contract every GrowthOps write to HubSpot is checked against.

Two systems hold the same customer. HubSpot is where reps and customer success work, so it is the **system of
record for the CRM record itself**: identity (email, name), owner, and every deal field (name, amount, stage,
pipeline, close date). GrowthOps is the system of record for the **analytics it computes** from the warehouse:
attribution, net cash, tracking status, funnel dates, renewal risk, all in the ``growthops`` property group.

One field is shared. ``lifecyclestage`` is moved by reps, by HubSpot's own deal lifecycle sync, and by GrowthOps
when a payment clears, so GrowthOps may only move it **forward** (a paying contact to Customer) and never back.

The rules:

* ``push``: GrowthOps may write the field on an existing record. Only GrowthOps-owned fields and the shared
  lifecycle stage are pushable; a push of a HubSpot-owned field is refused before any request is made.
* ``on_create``: GrowthOps sets the field when it creates the record (the initial migration), and after that the
  field belongs to HubSpot. Identifiers are create-only and immutable.
* ``pii``: contact data. The sync lands it in the warehouse as a keyed hash, never in clear text, so the landing
  tables can prove identity matches without holding email addresses or names.

The contract is data, so it is rendered into docs/hubspot-production.md and checked against the live schema.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from growthops.hubspot_portal import portal_properties

LIFECYCLE_RANK = {stage: rank for rank, stage in enumerate(
    ("subscriber", "lead", "marketingqualifiedlead", "salesqualifiedlead", "opportunity", "customer", "evangelist"))}


@dataclass(frozen=True)
class FieldRule:
    object_type: str
    name: str
    owner: str  # "growthops" | "hubspot" | "shared"
    push: bool
    on_create: bool
    pii: bool
    note: str


# Standard HubSpot fields GrowthOps reads, and the few it sets.
STANDARD: tuple[FieldRule, ...] = (
    FieldRule("contacts", "email", "hubspot", False, True, True, "Identity; HubSpot dedupes contacts on it"),
    FieldRule("contacts", "firstname", "hubspot", False, True, True, "Set on create; reps correct it after"),
    FieldRule("contacts", "lastname", "hubspot", False, True, True, "Set on create; reps correct it after"),
    FieldRule("contacts", "hubspot_owner_id", "hubspot", False, True, False,
              "Routed on create; lead rotation and reps reassign it"),
    FieldRule("contacts", "lifecyclestage", "shared", True, True, False,
              "Forward only: a cleared payment moves a contact to Customer; nothing moves it back"),
    FieldRule("contacts", "lastmodifieddate", "hubspot", False, False, False, "Sync watermark"),
    FieldRule("contacts", "hs_merged_object_ids", "hubspot", False, False, False, "Records merged into this one"),
    FieldRule("deals", "dealname", "hubspot", False, True, False, "Set on create"),
    FieldRule("deals", "amount", "hubspot", False, True, False, "Reps and quotes change it after create"),
    FieldRule("deals", "closedate", "hubspot", False, True, False, "Set by the rep who closes the deal"),
    FieldRule("deals", "pipeline", "hubspot", False, True, False, "Set on create"),
    FieldRule("deals", "dealstage", "hubspot", False, True, False, "Reps move deals through the pipeline"),
    FieldRule("deals", "hubspot_owner_id", "hubspot", False, True, False, "The contact's owner on create"),
    FieldRule("deals", "hs_lastmodifieddate", "hubspot", False, False, False, "Sync watermark"),
)
# GrowthOps fields that are not recomputed from the warehouse on every run.
IDENTIFIERS = frozenset({"growthops_contact_id", "growthops_deal_id"})
CREATE_ONLY_GROWTHOPS = {
    "growthops_owner": "The rep GrowthOps routed on create; lead rotation reassigns it in HubSpot",
    "growthops_stale_lead": "Set by the stale-lead cleanup rule, not recomputed per record",
}


def contract(connection: sqlite3.Connection) -> tuple[FieldRule, ...]:
    """Every field GrowthOps reads from or writes to HubSpot, with its owner and write rules."""
    rules = list(STANDARD)
    for object_type, props in portal_properties(connection).items():
        for prop in props:
            name = prop["name"]
            if name in IDENTIFIERS:
                rules.append(FieldRule(object_type, name, "growthops", False, True, False,
                                       "Unique GrowthOps key; immutable, the upsert and reconciliation key"))
            elif name in CREATE_ONLY_GROWTHOPS:
                rules.append(FieldRule(object_type, name, "growthops", False, True, False,
                                       CREATE_ONLY_GROWTHOPS[name]))
            else:
                rules.append(FieldRule(object_type, name, "growthops", True, True, False,
                                       f"{prop['label']}; recomputed from the warehouse"))
    return tuple(rules)


def rules_by_field(connection: sqlite3.Connection) -> dict[tuple[str, str], FieldRule]:
    return {(rule.object_type, rule.name): rule for rule in contract(connection)}


def pull_properties(connection: sqlite3.Connection, object_type: str) -> list[str]:
    """What the sync reads for an object: every field in the contract."""
    return sorted(rule.name for rule in contract(connection) if rule.object_type == object_type)


class ContractViolation(ValueError):
    """A write that the contract does not allow."""


def check_push(rules: dict[tuple[str, str], FieldRule], object_type: str, name: str,
               before: str | None, after: str) -> None:
    """Refuse a write GrowthOps does not own, or a lifecycle stage that would move backwards."""
    rule = rules.get((object_type, name))
    if rule is None:
        raise ContractViolation(f"{object_type}.{name} is not in the HubSpot contract")
    if not rule.push:
        raise ContractViolation(f"{object_type}.{name} is owned by {rule.owner}; GrowthOps does not write it "
                                "after create")
    if name == "lifecyclestage" and (before or "") in LIFECYCLE_RANK and \
            LIFECYCLE_RANK.get(after, -1) <= LIFECYCLE_RANK[before or ""]:
        raise ContractViolation(f"lifecyclestage may only move forward; {before} -> {after} refused")


def as_rows(connection: sqlite3.Connection) -> list[dict]:
    return [asdict(rule) for rule in contract(connection)]


CONTRACT_DOC = Path("docs/hubspot-contract.md")


def render(connection: sqlite3.Connection) -> str:
    """docs/hubspot-contract.md, generated from the contract so the document cannot drift from the code."""
    rules = contract(connection)
    lines = [
        "# HubSpot field contract",
        "",
        ("Generated from `growthops/hubspot_contract.py` by `python -m growthops.hubspot_contract`; do not edit by "
         "hand. A test regenerates it and fails on any difference."),
        "",
        ("HubSpot is the system of record for the CRM record (identity, owner, every deal field); GrowthOps is the "
         "system of record for the analytics it computes. `lifecyclestage` is shared and GrowthOps only moves it "
         "forward. **Push** means GrowthOps may write the field on an existing record; **create** means it sets the "
         "field when it creates the record; **PII** fields land in the warehouse as a keyed hash."),
        "",
        (f"{sum(r.push for r in rules)} of {len(rules)} fields are pushable; "
         f"{sum(r.owner == 'hubspot' for r in rules)} belong to HubSpot."),
        "",
        "| Object | Field | Owner | Push | Create | PII | Why |",
        "|---|---|---|:-:|:-:|:-:|---|",
    ]
    order = {"shared": 0, "hubspot": 1, "growthops": 2}
    for rule in sorted(rules, key=lambda r: (r.object_type, order[r.owner], r.name)):
        mark = {True: "yes", False: ""}
        lines.append(f"| {rule.object_type} | `{rule.name}` | {rule.owner} | {mark[rule.push]} | "
                     f"{mark[rule.on_create]} | {mark[rule.pii]} | {rule.note} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Write docs/hubspot-contract.md from the field contract.")
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--check", action="store_true", help="fail if the committed document is out of date")
    args = parser.parse_args()
    from growthops.db import connect

    connection = connect(args.database)
    try:
        text = render(connection)
    finally:
        connection.close()
    if args.check:
        if not CONTRACT_DOC.exists() or CONTRACT_DOC.read_text(encoding="utf-8") != text:
            raise SystemExit(f"{CONTRACT_DOC} is out of date; run python -m growthops.hubspot_contract")
        print(f"{CONTRACT_DOC} is current")
        return
    CONTRACT_DOC.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {CONTRACT_DOC}")


if __name__ == "__main__":
    main()
