"""Two-way HubSpot sync: pull what changed, reconcile it with the warehouse, push only what GrowthOps owns.

The portal build (``hubspot_portal``) loads a portal once, the way a CRM migration lands. This module keeps it and the
warehouse in step afterwards, under the ownership rules in ``hubspot_contract``:

1. ``preflight``  the account type, a pinned portal ID and every API the sync needs, probed read-only, so a missing
                  scope fails the run before anything is read or written.
2. ``pull``       contacts and deals changed since the last watermark, through the CRM search API with key-set
                  pages (see :func:`pull`: offset pages skip records that change mid-scan). Search is indexed
                  asynchronously, so every run re-reads a five-minute overlap; landing is idempotent (a row is only
                  replaced by the same or a newer version). Deal-to-contact associations, owners and the
                  deal pipeline come with it. Contact data is landed as a keyed hash, never in clear text.
3. ``reconcile``  the landed portal against the warehouse, field by field: GrowthOps-owned fields that differ are
                  **drift** (to push); HubSpot-owned fields that differ are **divergence** (reps changed the CRM, so
                  the CRM wins and nothing is pushed); records only HubSpot has are reported, not deleted.
4. ``plan``       drift becomes a content-addressed change set: the same drift always plans the same ID, so
                  re-planning never duplicates work. Nothing is written.
5. ``approve``    a named person approves a change set. Only approved change sets can be applied.
6. ``apply``      each change is re-checked against HubSpot's current value first: if a rep changed the field after
                  the plan, the change is a **conflict** and is skipped, never overwritten. The rest are written in
                  batches of 100, partial batch failures are recorded per record, and every write is read back and
                  marked verified. Applying twice writes nothing the second time.
7. ``tasks``      renewal follow-up tasks for customer success, created once per subscription and due date (an
                  idempotency log plus a search for the task, so a crash between create and log cannot duplicate it).

Webhooks (``hubspot_webhooks``) tell the sync which records changed; the records themselves are always fetched,
never trusted from the event. ``python -m growthops.hubspot_sync --help`` lists the commands.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from growthops.hubspot_client import (
    HubSpotClient,
    HubSpotError,
    account,
    batch_outcome,
    load_token,
)
from growthops.hubspot_contract import (
    LIFECYCLE_RANK,
    ContractViolation,
    check_push,
    pull_properties,
    rules_by_field,
)
from growthops.hubspot_portal import (
    PIPELINE_LABEL,
    PIPELINE_STAGES,
    _normalize,
    records,
)
from growthops.scenario import AS_OF

OBJECTS = ("contacts", "deals")
MODIFIED = {"contacts": "lastmodifieddate", "deals": "hs_lastmodifieddate"}
ID_PROPERTY = {"contacts": "growthops_contact_id", "deals": "growthops_deal_id"}
PAGE = 100  # records per search request
LOOKBACK = timedelta(minutes=5)  # search is indexed asynchronously; re-read an overlap, idempotently
BATCH = 100
DEMO_PII_KEY = "growthops-local-pii-key"
EVIDENCE_JSON = Path("build/hubspot/sync_evidence.json")
EVIDENCE_DOC = Path("docs/hubspot-sync.md")
TASK_CONTACT_ASSOCIATION = 204  # HubSpot-defined task-to-contact association type
# The capabilities a run needs, probed read-only by preflight: name -> (path, needed by)
CAPABILITIES = {
    "contacts": ("/crm/v3/objects/contacts?limit=1", "pull, apply"),
    "deals": ("/crm/v3/objects/deals?limit=1", "pull, apply"),
    "contact_properties": ("/crm/v3/properties/contacts/growthops_contact_id", "contract check"),
    "deal_pipelines": ("/crm/v3/pipelines/deals", "reconcile"),
    "owners": ("/crm/v3/owners?limit=1", "tasks, routing"),
    "tasks": ("/crm/v3/objects/tasks?limit=1", "tasks"),
    "companies": ("/crm/v3/objects/companies?limit=1", "not used by the sync"),
    "line_items": ("/crm/v3/objects/line_items?limit=1", "not used by the sync"),
}
REQUIRED = ("contacts", "deals", "contact_properties", "deal_pipelines", "owners")


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _ms(moment: str | datetime) -> int:
    value = datetime.fromisoformat(moment.replace("Z", "+00:00")) if isinstance(moment, str) else moment
    return int(value.timestamp() * 1000)


def protect(value: str) -> str:
    """Contact data as it is stored in the landing tables: a keyed hash that can be matched, not read."""
    key = (os.environ.get("GROWTHOPS_PII_HASH_KEY") or DEMO_PII_KEY).encode()
    return "hmac:" + hmac.new(key, value.strip().lower().encode(), hashlib.sha256).hexdigest()[:32]


# --------------------------------------------------------------------------- preflight

def preflight(client: HubSpotClient, allow_portal: str | None = None) -> dict:
    """The account and every capability the sync needs, read-only. ``ready`` is False if a required one is missing."""
    found = account(client, allow_portal)
    capabilities = {}
    for name, (path, used_by) in CAPABILITIES.items():
        try:
            client.get(path)
            capabilities[name] = {"status": "available", "used_by": used_by}
        except HubSpotError as exc:
            capabilities[name] = {"status": "missing_scope" if exc.status == 403 else f"http_{exc.status}",
                                  "category": exc.category, "used_by": used_by}
    missing = [name for name in REQUIRED if capabilities[name]["status"] != "available"]
    return {"account": found, "capabilities": capabilities, "missing_required": missing, "ready": not missing,
            "rate_limit": dict(client.rate_limit), "writes": client.writes}


# --------------------------------------------------------------------------- pull

def _state(connection: sqlite3.Connection, object_type: str) -> dict:
    row = connection.execute("SELECT * FROM hubspot_sync_state WHERE object_type=?", (object_type,)).fetchone()
    return dict(row) if row else {"object_type": object_type, "watermark": None, "runs": 0, "records_seen": 0}


def _land(connection: sqlite3.Connection, object_type: str, record: dict, rules: dict, now: datetime) -> None:
    props = record.get("properties") or {}
    stored = {}
    for name, value in props.items():
        rule = rules.get((object_type, name))
        stored[name] = protect(value) if rule is not None and rule.pii and value else value
    updated = record.get("updatedAt") or props.get(MODIFIED[object_type]) or now.isoformat()
    connection.execute(
        """INSERT INTO hubspot_records VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(object_type, hs_id) DO UPDATE SET growthops_id=excluded.growthops_id,
             properties_json=excluded.properties_json, hs_updated_at=excluded.hs_updated_at,
             archived=excluded.archived, synced_at=excluded.synced_at
           WHERE excluded.hs_updated_at >= hubspot_records.hs_updated_at""",
        (object_type, str(record["id"]), props.get(ID_PROPERTY[object_type]) or None,
         json.dumps(stored, sort_keys=True), updated, 1 if record.get("archived") else 0, now.isoformat()))


def _reference(connection: sqlite3.Connection, client: HubSpotClient, now: datetime) -> dict:
    """Owners and the GrowthOps deal pipeline, stored so reconcile can map stage IDs without calling HubSpot."""
    owners = client.paged("/crm/v3/owners?limit=100")
    pipelines = client.get("/crm/v3/pipelines/deals").get("results", [])
    pipeline = next((p for p in pipelines if p.get("label") == PIPELINE_LABEL), None)
    rows = [("owner", str(o["id"]), json.dumps({"archived": bool(o.get("archived"))})) for o in owners]
    if pipeline:
        by_label = {key: label for key, label, _, _ in PIPELINE_STAGES}
        label_to_key = {label: key for key, label in by_label.items()}
        rows += [("deal_stage", str(stage["id"]), json.dumps({"key": label_to_key.get(stage["label"]),
                                                               "label": stage["label"],
                                                               "pipeline": str(pipeline["id"])}))
                 for stage in pipeline.get("stages", [])]
    for kind, key, value in rows:
        connection.execute("""INSERT INTO hubspot_reference VALUES (?, ?, ?, ?)
                              ON CONFLICT(kind, key) DO UPDATE SET value_json=excluded.value_json,
                                synced_at=excluded.synced_at""", (kind, key, value, now.isoformat()))
    return {"owners": len(owners), "deal_stages": sum(1 for kind, _, _ in rows if kind == "deal_stage")}


def _associations(connection: sqlite3.Connection, client: HubSpotClient, deal_ids: list[str], now: datetime) -> int:
    count = 0
    for start in range(0, len(deal_ids), BATCH):
        chunk = deal_ids[start:start + BATCH]
        page = client.post("/crm/v4/associations/deals/contacts/batch/read", {"inputs": [{"id": d} for d in chunk]})
        connection.executemany("DELETE FROM hubspot_associations WHERE from_type='deals' AND from_id=? "
                               "AND to_type='contacts'", [(d,) for d in chunk])
        for result in page.get("results", []):
            for target in result.get("to", []):
                connection.execute("INSERT OR IGNORE INTO hubspot_associations VALUES ('deals', ?, 'contacts', ?, ?)",
                                   (str(result["from"]["id"]), str(target["toObjectId"]), now.isoformat()))
                count += 1
    return count


def _stamp(record: dict, modified: str) -> str | None:
    return record.get("updatedAt") or (record.get("properties") or {}).get(modified)


def pull(client: HubSpotClient, connection: sqlite3.Connection, object_type: str, *, full: bool = False,
         now: datetime | None = None, page_size: int = PAGE) -> dict:
    """Land every record of ``object_type`` modified since the watermark (all of them on a full pull).

    Pages are **key-set**, never offset: each request is a fresh search for records past the last key read. HubSpot's
    ``after`` cursor is an offset into a sorted result, so when a record already read is modified mid-scan it jumps
    to the end of the sort, every later record shifts down one place, and the record at the page boundary is
    skipped. The first live pull hit exactly that (961 records read, 959 distinct, two missed). A full pull keys on
    ``hs_object_id``, which never changes; an incremental pull keys on the modified date, re-reads a short overlap
    and drops records it has already read in this run. Key-set paging also never reaches HubSpot's 10,000-result
    search limit.
    """
    now = _now(now)
    rules = rules_by_field(connection)
    properties = pull_properties(connection, object_type)
    modified = MODIFIED[object_type]
    state = _state(connection, object_type)
    watermark = None if full else state.get("watermark")
    connection.execute(
        """INSERT INTO hubspot_sync_state (object_type, watermark, last_started_at, runs) VALUES (?, ?, ?, 1)
           ON CONFLICT(object_type) DO UPDATE SET last_started_at=excluded.last_started_at, runs=runs+1""",
        (object_type, watermark, now.isoformat()))
    if watermark is None:
        key, operator, last = "hs_object_id", "GT", 0
    else:
        key, operator, last = modified, "GTE", _ms(watermark) - int(LOOKBACK.total_seconds() * 1000)
    newest, read, requests, offset, landed_ids = watermark, 0, 0, 0, set()
    try:
        while True:
            body: dict = {"filterGroups": [{"filters": [{"propertyName": key, "operator": operator,
                                                         "value": str(last)}]}],
                          "properties": properties, "sorts": [{"propertyName": key, "direction": "ASCENDING"}],
                          "limit": page_size}
            if offset:  # only inside a run of records that share one modified time
                body["after"] = str(offset)
            results = client.post(f"/crm/v3/objects/{object_type}/search", body).get("results", [])
            requests += 1
            for record in results:
                read += 1
                if str(record["id"]) in landed_ids:
                    continue
                landed_ids.add(str(record["id"]))
                _land(connection, object_type, record, rules, now)
                stamp = _stamp(record, modified)
                if stamp and (newest is None or _ms(stamp) > _ms(newest)):
                    newest = stamp
            if len(results) < page_size:
                break
            stamp = _stamp(results[-1], modified)
            following = int(results[-1]["id"]) if key == "hs_object_id" else (_ms(stamp) if stamp else None)
            if following is None:
                raise HubSpotError(0, "POST", f"/crm/v3/objects/{object_type}/search",
                                   note="a record came back without its modified time")
            if following == last:
                offset += page_size
            else:
                last, offset = following, 0
        extra = {}
        if object_type == "deals":
            extra["associations"] = _associations(connection, client, sorted(landed_ids), now)
        connection.execute(
            """UPDATE hubspot_sync_state SET watermark=?, last_success_at=?, last_error=NULL,
                 records_seen=records_seen+? WHERE object_type=?""",
            (newest, now.isoformat(), len(landed_ids), object_type))
    except Exception as exc:
        connection.execute("UPDATE hubspot_sync_state SET last_error=? WHERE object_type=?",
                           (f"{type(exc).__name__}: {exc}"[:500], object_type))
        raise
    return {"object_type": object_type, "mode": "full" if watermark is None else "incremental",
            "records": read, "distinct": len(landed_ids), "requests": requests, "watermark": newest, **extra}


def pull_all(client: HubSpotClient, connection: sqlite3.Connection, *, full: bool = False,
             now: datetime | None = None) -> dict:
    now = _now(now)
    result = {"reference": _reference(connection, client, now)}
    for object_type in OBJECTS:
        result[object_type] = pull(client, connection, object_type, full=full, now=now)
    return result


def pull_archived(client: HubSpotClient, connection: sqlite3.Connection, object_type: str,
                  now: datetime | None = None) -> dict:
    """Mark landed records that HubSpot has archived (deleted to its recycle bin, or merged away)."""
    now = _now(now)
    archived = client.paged(f"/crm/v3/objects/{object_type}?archived=true&limit=100&properties=hs_object_id")
    marked = 0
    for record in archived:
        cursor = connection.execute(
            "UPDATE hubspot_records SET archived=1, synced_at=? WHERE object_type=? AND hs_id=? AND archived=0",
            (now.isoformat(), object_type, str(record["id"])))
        marked += cursor.rowcount
    return {"object_type": object_type, "archived_in_hubspot": len(archived), "newly_marked": marked}


# --------------------------------------------------------------------------- reconcile

def _landed(connection: sqlite3.Connection, object_type: str) -> tuple[dict[str, list[tuple[str, dict]]], list[str]]:
    by_id: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    native = []
    for row in connection.execute("SELECT hs_id, growthops_id, properties_json FROM hubspot_records "
                                  "WHERE object_type=? AND archived=0 ORDER BY hs_id", (object_type,)):
        if row["growthops_id"]:
            by_id[row["growthops_id"]].append((row["hs_id"], json.loads(row["properties_json"])))
        else:
            native.append(row["hs_id"])
    return by_id, native


def expected_stage(contact: dict, with_deal: bool) -> str:
    """The lifecycle stage the warehouse implies: forward-only, as HubSpot's deal sync and payments move it."""
    stage = contact["lifecyclestage"]
    if with_deal and LIFECYCLE_RANK.get(stage, 0) < LIFECYCLE_RANK["opportunity"]:
        stage = "opportunity"
    if contact["growthops_has_closed_won"] == "true" or float(contact["growthops_net_cash"] or 0) > 0:
        stage = "customer"
    return stage


def reconcile(connection: sqlite3.Connection, as_of: date = AS_OF) -> dict:
    """Compare the landed portal with the warehouse under the ownership contract. Reads nothing from HubSpot."""
    desired = records(connection, as_of)
    rules = rules_by_field(connection)
    stage_key = {row["key"]: json.loads(row["value_json"]).get("key") for row in connection.execute(
        "SELECT key, value_json FROM hubspot_reference WHERE kind='deal_stage'")}
    with_deal = {deal["growthops_contact_id"] for deal in desired["deals"]}
    drift: list[dict] = []
    divergence: dict[str, int] = defaultdict(int)
    report: dict = {"objects": {}}
    for object_type in OBJECTS:
        landed, native = _landed(connection, object_type)
        wanted = desired[object_type]
        id_property = ID_PROPERTY[object_type]
        matched, missing = 0, []
        duplicates = sorted(key for key, rows in landed.items() if len(rows) > 1)
        hubspot_ahead = 0
        for row in wanted:
            hits = landed.get(row[id_property])
            if not hits:
                missing.append(row[id_property])
                continue
            matched += 1
            hs_id, props = hits[0]
            for name, value in row.items():
                if name == "pipeline_stage":
                    name, current = "dealstage", stage_key.get(props.get("dealstage") or "")
                else:
                    current = props.get(name)
                rule = rules.get((object_type, name))
                if rule is None:
                    continue
                if rule.pii:
                    if value and current != protect(value):
                        divergence[f"{object_type}.{name}"] += 1
                    continue
                if name == "lifecyclestage":
                    value = expected_stage(row, row["growthops_contact_id"] in with_deal)
                    have, want = LIFECYCLE_RANK.get(current or "", -1), LIFECYCLE_RANK[value]
                    if have < want:
                        drift.append({"object_type": object_type, "hs_id": hs_id, "growthops_id": row[id_property],
                                      "property": name, "before": current or "", "after": value})
                    elif have > want:
                        hubspot_ahead += 1
                    continue
                if _normalize(name, current) == _normalize(name, value):
                    continue
                if rule.push and rule.owner == "growthops":
                    drift.append({"object_type": object_type, "hs_id": hs_id, "growthops_id": row[id_property],
                                  "property": name, "before": current or "", "after": value})
                elif not rule.push:
                    divergence[f"{object_type}.{name}"] += 1
        wanted_ids = {row[id_property] for row in wanted}
        report["objects"][object_type] = {
            "warehouse": len(wanted), "landed": sum(len(rows) for rows in landed.values()) + len(native),
            "matched": matched, "missing_in_hubspot": missing[:20], "missing_count": len(missing),
            "not_in_warehouse_sample": sorted(set(landed) - wanted_ids)[:20],
            "hubspot_only_records": len(native), "hubspot_only_ids": native[:10],
            "duplicate_growthops_ids": duplicates, "hubspot_ahead_lifecycle": hubspot_ahead,
        }
    by_property: dict[str, int] = defaultdict(int)
    for item in drift:
        by_property[f"{item['object_type']}.{item['property']}"] += 1
    report.update({"drift": drift, "drift_by_property": dict(sorted(by_property.items())),
                   "divergence_by_property": dict(sorted(divergence.items())),
                   "in_sync": not drift and not any(o["missing_count"] or o["duplicate_growthops_ids"]
                                                    for o in report["objects"].values())})
    return report


# --------------------------------------------------------------------------- plan, approve, apply

def plan(connection: sqlite3.Connection, report: dict | None = None, now: datetime | None = None) -> dict:
    """Drift as a content-addressed change set. The same drift always gets the same ID; nothing is written."""
    report = report if report is not None else reconcile(connection)
    rules = rules_by_field(connection)
    items, refused = [], []
    for item in report["drift"]:
        try:
            check_push(rules, item["object_type"], item["property"], item["before"], str(item["after"]))
        except ContractViolation as exc:
            refused.append({**item, "reason": str(exc)})
            continue
        items.append(item)
    if not items:
        return {"changeset_id": None, "items": 0, "refused": refused}
    canonical = json.dumps(sorted((i["object_type"], i["hs_id"], i["property"], str(i["before"]), str(i["after"]))
                                  for i in items))
    changeset_id = "cs_" + hashlib.sha256(canonical.encode()).hexdigest()[:16]
    by_property: dict[str, int] = defaultdict(int)
    for item in items:
        by_property[f"{item['object_type']}.{item['property']}"] += 1
    created = connection.execute(
        "INSERT OR IGNORE INTO hubspot_changesets VALUES (?, ?, 'reconcile', ?, 'planned', NULL, NULL, NULL, ?)",
        (changeset_id, _now(now).isoformat(), len(items), json.dumps({"by_property": by_property}))).rowcount
    if created:
        connection.executemany(
            "INSERT INTO hubspot_changeset_items VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL)",
            [(changeset_id, i["object_type"], i["hs_id"], i["growthops_id"], i["property"], str(i["before"]),
              str(i["after"])) for i in items])
        connection.execute("UPDATE hubspot_changesets SET status='superseded' WHERE status='planned' "
                           "AND changeset_id<>?", (changeset_id,))
    status = connection.execute("SELECT status FROM hubspot_changesets WHERE changeset_id=?",
                                (changeset_id,)).fetchone()[0]
    return {"changeset_id": changeset_id, "items": len(items), "by_property": dict(sorted(by_property.items())),
            "status": status, "new": bool(created), "refused": refused}


def approve(connection: sqlite3.Connection, changeset_id: str, approved_by: str, now: datetime | None = None) -> dict:
    if not approved_by.strip():
        raise ValueError("an approval needs the approver's name")
    row = connection.execute("SELECT status FROM hubspot_changesets WHERE changeset_id=?", (changeset_id,)).fetchone()
    if row is None:
        raise LookupError(f"no change set {changeset_id}")
    if row["status"] != "planned":
        raise ValueError(f"change set {changeset_id} is {row['status']}; only a planned change set can be approved")
    connection.execute("UPDATE hubspot_changesets SET status='approved', approved_by=?, approved_at=? "
                       "WHERE changeset_id=?", (approved_by.strip(), _now(now).isoformat(), changeset_id))
    return {"changeset_id": changeset_id, "status": "approved", "approved_by": approved_by.strip()}


def _read_by_id(client: HubSpotClient, object_type: str, ids: list[str], properties: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for start in range(0, len(ids), BATCH):
        page = client.post(f"/crm/v3/objects/{object_type}/batch/read",
                           {"properties": properties, "inputs": [{"id": i} for i in ids[start:start + BATCH]]})
        results, _ = batch_outcome(page)
        for result in results:
            found[str(result["id"])] = result
    return found


def _mark(connection: sqlite3.Connection, changeset_id: str, item: sqlite3.Row, status: str,
          category: str | None = None, when: str | None = None) -> None:
    connection.execute(
        """UPDATE hubspot_changeset_items SET status=?, error_category=?, applied_at=COALESCE(?, applied_at)
           WHERE changeset_id=? AND object_type=? AND hs_id=? AND property=?""",
        (status, category, when, changeset_id, item["object_type"], item["hs_id"], item["property"]))


def apply(client: HubSpotClient, connection: sqlite3.Connection, changeset_id: str,
          now: datetime | None = None) -> dict:
    """Write an approved change set: check each value is still what was planned, write, read back, verify."""
    now = _now(now)
    head = connection.execute("SELECT status FROM hubspot_changesets WHERE changeset_id=?", (changeset_id,)).fetchone()
    if head is None:
        raise LookupError(f"no change set {changeset_id}")
    if head["status"] not in ("approved", "partially_applied", "applied"):
        raise PermissionError(f"change set {changeset_id} is {head['status']}; approve it before applying")
    if head["status"] == "applied":  # applying twice writes nothing
        counts = {row["status"]: row["n"] for row in connection.execute(
            "SELECT status, COUNT(*) n FROM hubspot_changeset_items WHERE changeset_id=? GROUP BY status",
            (changeset_id,))}
        return {"changeset_id": changeset_id, "status": "applied", "items": dict(sorted(counts.items())),
                "writes": client.writes}
    rules = rules_by_field(connection)
    pending = connection.execute(
        "SELECT * FROM hubspot_changeset_items WHERE changeset_id=? AND status IN ('pending','failed','applied') "
        "ORDER BY object_type, hs_id, property", (changeset_id,)).fetchall()
    by_type: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for item in pending:
        by_type[item["object_type"]].append(item)
    for object_type, items in by_type.items():
        props = sorted({item["property"] for item in items} | {ID_PROPERTY[object_type]})
        current = _read_by_id(client, object_type, sorted({item["hs_id"] for item in items}), props)
        updates: dict[str, dict[str, str]] = defaultdict(dict)
        for item in items:
            record = current.get(item["hs_id"])
            if record is None:
                _mark(connection, changeset_id, item, "failed", "OBJECT_NOT_FOUND")
                continue
            value = (record.get("properties") or {}).get(item["property"]) or ""
            if _normalize(item["property"], value) == _normalize(item["property"], item["after_value"]):
                _mark(connection, changeset_id, item, "verified", None, now.isoformat())
                continue
            if _normalize(item["property"], value) != _normalize(item["property"], item["before_value"]):
                _mark(connection, changeset_id, item, "conflict", "CHANGED_IN_HUBSPOT")  # a rep got there first
                continue
            try:
                check_push(rules, object_type, item["property"], value, item["after_value"])
            except ContractViolation:
                _mark(connection, changeset_id, item, "conflict", "CONTRACT")
                continue
            updates[item["hs_id"]][item["property"]] = item["after_value"]
        ids = sorted(updates)
        for start in range(0, len(ids), BATCH):
            chunk = ids[start:start + BATCH]
            page = client.post(f"/crm/v3/objects/{object_type}/batch/update",
                               {"inputs": [{"id": hs_id, "properties": updates[hs_id]} for hs_id in chunk]})
            _, failures = batch_outcome(page)
            failed = {hs_id: failure["category"] for failure in failures for hs_id in failure["ids"]}
            for item in items:
                if item["hs_id"] in chunk and item["property"] in updates[item["hs_id"]]:
                    if item["hs_id"] in failed:
                        _mark(connection, changeset_id, item, "failed", failed[item["hs_id"]])
                    else:
                        _mark(connection, changeset_id, item, "applied", None, now.isoformat())
        # Read back what was written and land it, so the next reconcile sees HubSpot's copy.
        written = sorted({item["hs_id"] for item in items if item["hs_id"] in updates})
        back = _read_by_id(client, object_type, written, pull_properties(connection, object_type)) if written else {}
        for record in back.values():
            _land(connection, object_type, record, rules, now)
        for item in connection.execute("SELECT * FROM hubspot_changeset_items WHERE changeset_id=? AND "
                                       "object_type=? AND status='applied'", (changeset_id, object_type)).fetchall():
            value = ((back.get(item["hs_id"]) or {}).get("properties") or {}).get(item["property"]) or ""
            verified = _normalize(item["property"], value) == _normalize(item["property"], item["after_value"])
            _mark(connection, changeset_id, item, "verified" if verified else "failed",
                  None if verified else "VERIFY_MISMATCH")
    counts = {row["status"]: row["n"] for row in connection.execute(
        "SELECT status, COUNT(*) n FROM hubspot_changeset_items WHERE changeset_id=? GROUP BY status",
        (changeset_id,))}
    total = sum(counts.values())
    status = "applied" if counts.get("verified", 0) + counts.get("conflict", 0) == total else "partially_applied"
    connection.execute("UPDATE hubspot_changesets SET status=?, applied_at=? WHERE changeset_id=?",
                       (status, now.isoformat(), changeset_id))
    return {"changeset_id": changeset_id, "status": status, "items": dict(sorted(counts.items())),
            "writes": client.writes}


# --------------------------------------------------------------------------- renewal tasks

def renewal_tasks(client: HubSpotClient | None, connection: sqlite3.Connection, *, create: bool = False,
                  now: datetime | None = None) -> dict:
    """One follow-up task per renewal proposal whose contact is in HubSpot, created once.

    Without ``create`` (or without a client) it only reports what it would create. The idempotency key is the
    subscription and its due date; before creating, the sync also searches HubSpot for a task already carrying that
    key, so a crash between the create and the log entry cannot produce a duplicate.
    """
    from growthops.renewals import action_proposals

    now = _now(now)
    contacts = {row["growthops_id"]: (row["hs_id"], json.loads(row["properties_json"])) for row in connection.execute(
        "SELECT growthops_id, hs_id, properties_json FROM hubspot_records WHERE object_type='contacts' "
        "AND archived=0 AND growthops_id IS NOT NULL")}
    # A task nobody owns sits in no one's queue: fall back to the portal's first active owner.
    default_owner = next((row["key"] for row in connection.execute(
        "SELECT key, value_json FROM hubspot_reference WHERE kind='owner' ORDER BY CAST(key AS INTEGER)")
        if not json.loads(row["value_json"]).get("archived")), None)
    planned: list[dict] = []
    created: list[str] = []
    existing: list[str] = []
    outside = 0
    for proposal in action_proposals(connection)["proposals"]:
        target = contacts.get(proposal["person_key"])
        if target is None:
            outside += 1
            continue
        hs_id, props = target
        key = f"renewal:{proposal['subscription_id']}:{proposal['due_date']}"
        subject = f"Renewal follow-up: {proposal['suggested_task']} [{key}]"
        due = datetime.combine(date.fromisoformat(proposal["due_date"]), datetime.min.time(), UTC)
        properties: dict[str, str] = {
            "hs_task_subject": subject,
            "hs_task_body": (f"{proposal['reason'].replace('_', ' ').capitalize()}; renewal due {proposal['due_date']}. "
                             f"Suggested channel: {proposal['suggested_channel'].replace('_', ' ')} (email consent: "
                             f"{proposal['email_consent_status']}). Created by GrowthOps from the renewal monitor."),
            "hs_timestamp": str(_ms(max(due, now))),
            "hs_task_status": "NOT_STARTED",
            "hs_task_priority": "HIGH" if proposal["severity"] == "high" else "MEDIUM",
            "hs_task_type": "TODO",
            **({"hubspot_owner_id": owner} if (owner := props.get("hubspot_owner_id") or default_owner) else {}),
        }
        task = {"properties": properties, "associations": [{"to": {"id": hs_id}, "types": [
            {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": TASK_CONTACT_ASSOCIATION}]}]}
        planned.append({"key": key, "contact_hs_id": hs_id, "priority": properties["hs_task_priority"]})
        if connection.execute("SELECT 1 FROM hubspot_created_objects WHERE idempotency_key=?", (key,)).fetchone():
            existing.append(key)
            continue
        if not (create and client):
            continue
        # The contact's own tasks, matched on the key in the subject (token search would split the key on hyphens).
        found = client.search("tasks", [{"propertyName": "associations.contact", "operator": "EQ", "value": hs_id}],
                              ["hs_task_subject"])
        match = next((t for t in found if key in ((t.get("properties") or {}).get("hs_task_subject") or "")), None)
        hs_task = str(match["id"]) if match else str(client.post("/crm/v3/objects/tasks", task)["id"])
        connection.execute("INSERT INTO hubspot_created_objects VALUES (?, 'tasks', ?, 'renewal_follow_up', ?)",
                           (key, hs_task, now.isoformat()))
        (existing if match else created).append(key)
    return {"proposals_in_hubspot": len(planned), "proposals_outside_portal": outside, "planned": planned,
            "created": created, "already_present": existing, "mode": "create" if create and client else "dry_run"}


# --------------------------------------------------------------------------- status and evidence

def status(connection: sqlite3.Connection) -> dict:
    state = [dict(row) for row in connection.execute("SELECT * FROM hubspot_sync_state ORDER BY object_type")]
    landed = {row["object_type"]: {"active": row["active"], "archived": row["archived"]} for row in connection.execute(
        """SELECT object_type, SUM(archived=0) active, SUM(archived=1) archived FROM hubspot_records
           GROUP BY object_type""")}
    webhooks = {row["status"]: row["n"] for row in connection.execute(
        "SELECT status, COUNT(*) n FROM hubspot_webhook_events GROUP BY status")}
    changesets = [dict(row) for row in connection.execute(
        "SELECT changeset_id, status, items, approved_by, applied_at FROM hubspot_changesets ORDER BY created_at DESC "
        "LIMIT 10")]
    return {"state": state, "landed": landed, "webhook_events": webhooks, "changesets": changesets,
            "tasks_created": connection.execute("SELECT COUNT(*) FROM hubspot_created_objects").fetchone()[0]}


def _merge_evidence(new: dict, path: Path = EVIDENCE_JSON) -> dict:
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged = {**previous, **new, "runs": previous.get("runs", 0) + 1}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return merged


def render_doc(evidence: dict) -> str:
    """docs/hubspot-sync.md from the merged evidence of the live runs."""
    pre, pulled, before = evidence["preflight"], evidence["pull"]["pull"], evidence["reconcile"]
    planned, approved, applied = evidence["plan"], evidence["approve"], evidence["apply"]
    after, tasks, live = evidence["run"], evidence["tasks"], evidence["live_checks"]
    account_info, limits = pre["account"], pre["rate_limit"]
    contacts, deals = before["objects"]["contacts"], before["objects"]["deals"]
    lines = [
        "# HubSpot two-way sync (live)",
        "",
        ("Generated by `python -m growthops.hubspot_sync doc` from `build/hubspot/sync_evidence.json`, which the "
        "live runs write with `--evidence`; do not edit. The portal is the HubSpot developer test account that "
        "holds the synthetic ScaleLab sample ([portal build](hubspot-portal.md)); the rules for who owns each field "
        "are in the [field contract](hubspot-contract.md) and the design in "
        "[HubSpot in production](hubspot-production.md)."),
        "",
        (f"Last run {evidence['last_run_at']} · account type `{account_info['account_type']}` · "
        f"{evidence['runs']} recorded runs."),
        "",
        "## Preflight (read-only)",
        "",
        "| Capability | Status | Needed by |",
        "|---|---|---|",
        *[f"| {name} | {cap['status'].replace('_', ' ')} | {cap['used_by']} |"
          for name, cap in pre["capabilities"].items()],
        "",
        (f"Ready: **{'yes' if pre['ready'] else 'no'}**. HubSpot's rate-limit headers on this key: "
        f"{limits.get('interval_max', '?')} requests per 10 seconds and {limits.get('daily_max', 0):,} a day "
        f"({limits.get('daily_remaining', 0):,} left when checked). The client paces under the first and can stop at a "
        "configured floor of the second (`GROWTHOPS_HUBSPOT_DAILY_FLOOR`)."),
        "",
        "## Pull, then reconcile against the warehouse",
        "",
        "| | Contacts | Deals |",
        "|---|---:|---:|",
        (f"| Read (key-set pages) | {pulled['contacts']['records']:,} in {pulled['contacts']['requests']} requests | "
        f"{pulled['deals']['records']:,} in {pulled['deals']['requests']} requests |"),
        f"| Distinct records landed | {pulled['contacts']['distinct']:,} | {pulled['deals']['distinct']:,} |",
        (f"| Matched to the warehouse sample | {contacts['matched']:,} of {contacts['warehouse']:,} | "
        f"{deals['matched']:,} of {deals['warehouse']:,} |"),
        f"| Missing in HubSpot | {contacts['missing_count']} | {deals['missing_count']} |",
        (f"| Duplicate GrowthOps IDs | {len(contacts['duplicate_growthops_ids'])} | "
        f"{len(deals['duplicate_growthops_ids'])} |"),
        (f"| Only in HubSpot | {contacts['hubspot_only_records']} (HubSpot's own sample contacts) | "
        f"{deals['hubspot_only_records']} |"),
        "",
        (f"Deal-to-contact associations landed: {pulled['deals'].get('associations', 0)}. Owners: "
        f"{pulled['reference']['owners']}; GrowthOps pipeline stages: {pulled['reference']['deal_stages']}. Contact "
        "data (email, names) is landed as a keyed hash; identity still matched for every contact."),
        "",
        "**Drift** (GrowthOps-owned fields that differ, to push): "
        + (", ".join(f"`{k}` {v}" for k, v in before["drift_by_property"].items()) or "none") + ".",
        "",
        "**Divergence** (HubSpot-owned fields that differ, never pushed): "
        + (", ".join(f"`{k}` {v}" for k, v in before["divergence_by_property"].items()) or "none")
        + ". The rep assignments are the portal's lead routing, which owns the field after create.",
        "",
        "## Plan, approve, apply",
        "",
        f"Change set `{planned['changeset_id']}` planned {planned['items']} writes: "
        + ", ".join(f"`{k}` {v}" for k, v in planned["by_property"].items())
        + f". Approved by {approved['approved_by']}; applied with {applied['writes']} batch write, every value read "
        f"back: {', '.join(f'{v} {k}' for k, v in applied['items'].items())}, status **{applied['status']}**.",
        "",
        (f"The next pass read {after['pull']['contacts']['distinct']} contacts and {after['pull']['deals']['distinct']} "
        f"deals incrementally, found drift "
        f"{after['reconcile']['drift_by_property'] or 'none'}, planned "
        f"{after['plan']['changeset_id'] or 'nothing'}, and wrote {after['api']['writes']} times: "
        f"**in sync: {'yes' if after['reconcile']['in_sync'] else 'no'}**."),
        "",
        "## Renewal follow-up tasks",
        "",
        f"{tasks['proposals_in_hubspot']} renewal proposals belong to contacts in HubSpot "
        f"({tasks['proposals_outside_portal']} to contacts outside the sample). Created: "
        + (", ".join(f"`{key}`" for key in tasks["created"]) or "none")
        + f"; already present: {len(tasks['already_present'])}. Each task is assigned to the contact's owner or the "
        "portal's default owner, due on the renewal date, and associated with the contact. A second run creates "
        "nothing.",
        "",
        "## Event paths, checked live without writing",
        "",
        (f"* **Webhook.** A two-event delivery for real contacts, signed as HubSpot signs (v3): signature verified "
        f"{'yes' if live['webhook']['signature_verified'] else 'no'}, tampered body rejected "
        f"{'yes' if live['webhook']['tampered_body_rejected'] else 'no'}, repeated delivery absorbed as "
        f"{live['webhook']['repeated_delivery']['duplicates']} duplicates, and processing refetched "
        f"{live['webhook']['processed']['refetched']} records from HubSpot, landing HubSpot's current value rather "
        f"than the event's ({'yes' if live['webhook']['landed_value_is_hubspots_not_the_events'] else 'no'})."),
        (f"* **Payment adapter.** An existing customer: "
        f"{len(live['payment_adapter']['existing_customer']['calls'])} read, "
        f"{'a write' if live['payment_adapter']['existing_customer']['wrote'] else 'no write needed'}. An unknown "
        f"contact: refused, nothing created (`{live['payment_adapter']['unknown_contact']['outcome']}`)."),
        "",
        "## What the live portal taught",
        "",
        ("* **Offset pages skip records that change mid-scan.** The first live pull sorted by last-modified date and "
        "paged with HubSpot's `after` cursor, which is an offset. HubSpot was updating contacts in the background "
        "during the scan; each record already read that changed moved to the end of the sort and pushed the rest "
        "down a place, so 961 records came back with only 959 distinct and two never read. The pull now pages by "
        "key (`hs_object_id` for a full pull, the modified date for an incremental one) and a test reproduces the "
        "skip under offset paging."),
        ("* **A new account comes with HubSpot's own sample contacts.** They carry no GrowthOps ID, so reconcile "
        "reports them as HubSpot-only rather than as drift, and nothing deletes them."),
        ("* **A private-app service key cannot reach tickets at all**, whatever its scopes; companies, products and "
        "line items need scopes this key does not hold. Preflight names each one before a run starts."),
    ]
    return "\n".join(lines) + "\n"


def _client() -> HubSpotClient:
    floor = int(os.environ.get("GROWTHOPS_HUBSPOT_DAILY_FLOOR", "0") or 0)
    return HubSpotClient(load_token(), daily_floor=floor)


def main() -> None:
    parser = argparse.ArgumentParser(description="Two-way HubSpot sync under the GrowthOps field contract.")
    parser.add_argument("command", choices=("preflight", "pull", "reconcile", "plan", "approve", "apply", "tasks",
                                            "status", "run", "doc"))
    parser.add_argument("changeset", nargs="?", help="change set ID for approve and apply")
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--full", action="store_true", help="pull everything, ignoring the watermark")
    parser.add_argument("--by", default="", help="approver name for approve")
    parser.add_argument("--create", action="store_true", help="tasks: create them (default is a dry run)")
    parser.add_argument("--allow-portal", help="portal ID to allow when it is not a developer test account")
    parser.add_argument("--evidence", action="store_true", help="merge the result into build/hubspot evidence")
    args = parser.parse_args()
    if args.command == "doc":
        evidence = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
        EVIDENCE_DOC.write_text(render_doc(evidence), encoding="utf-8", newline="\n")
        print(f"wrote {EVIDENCE_DOC}")
        return
    from growthops.db import connect, initialize

    connection = connect(args.database)
    try:
        initialize(connection)
        client = None if args.command in ("reconcile", "plan", "approve", "status") else _client()
        if args.command == "preflight":
            assert client is not None
            result = preflight(client, args.allow_portal)
        elif args.command in ("pull", "run"):
            assert client is not None
            checked = preflight(client, args.allow_portal)
            if not checked["ready"]:
                raise SystemExit(f"preflight failed; missing: {checked['missing_required']}")
            result = {"preflight": checked, "pull": pull_all(client, connection, full=args.full)}
            if args.command == "run":
                result["reconcile"] = {k: v for k, v in reconcile(connection).items() if k != "drift"}
                result["plan"] = plan(connection)
        elif args.command == "reconcile":
            result = reconcile(connection)
            result["drift"] = result["drift"][:25]
        elif args.command == "plan":
            result = plan(connection)
        elif args.command == "approve":
            result = approve(connection, args.changeset or "", args.by)
        elif args.command == "apply":
            assert client is not None
            account(client, args.allow_portal)
            result = apply(client, connection, args.changeset or "")
        elif args.command == "tasks":
            result = renewal_tasks(client, connection, create=args.create)
        else:
            result = status(connection)
        if client is not None:
            result["api"] = {"calls": len(client.calls), "writes": client.writes, "rate_limit": client.rate_limit}
        if args.evidence:
            _merge_evidence({args.command: result, "last_run_at": datetime.now(UTC).isoformat(timespec="seconds")})
        print(json.dumps(result, indent=2, default=str))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
