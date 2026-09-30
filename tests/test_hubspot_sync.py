"""The production HubSpot integration, end to end against the in-memory HubSpot: client, sync, webhooks, API, worker."""

import base64
import copy
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from hubspot_fake import TOKEN, FakeHubSpot

from growthops.hubspot_client import (
    BudgetExhausted,
    CircuitOpen,
    HubSpotClient,
    HubSpotError,
    batch_outcome,
)
from growthops.hubspot_contract import (
    ContractViolation,
    check_push,
    contract,
    rules_by_field,
)
from growthops.hubspot_portal import apply as build_portal
from growthops.hubspot_portal import records
from growthops.hubspot_sync import apply as apply_changeset
from growthops.hubspot_sync import (
    approve,
    plan,
    preflight,
    protect,
    pull,
    pull_all,
    pull_archived,
    reconcile,
    renewal_tasks,
)
from growthops.hubspot_webhooks import process, record, signature, verify

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def client(fake, **kwargs):
    return HubSpotClient(TOKEN, fake, sleep=lambda seconds: None, clock=lambda: 0.0, **kwargs)


@pytest.fixture(scope="module")
def built_portal(sample_path, tmp_path_factory):
    """One portal build, copied into every test: the warehouse sample loaded, cleaned and verified."""
    from growthops.db import connect

    fake = FakeHubSpot()
    connection = connect(sample_path)
    try:
        evidence = build_portal(client(fake), connection, output=tmp_path_factory.mktemp("portal"))
    finally:
        connection.close()
    assert evidence["steps"]["verify"]["reconciled"]
    return fake


@pytest.fixture
def fake(built_portal):
    return copy.deepcopy(built_portal)


def _contact(fake, contact_id):
    return next(hs for hs, p in fake.objects["contacts"].items() if p.get("growthops_contact_id") == contact_id)


# --------------------------------------------------------------------------- client

def test_client_waits_as_long_as_retry_after_asks_and_stops_at_the_daily_floor():
    fake = FakeHubSpot(headers=True, daily=6)
    fake.fail_next = [429]
    waits = []
    hub = HubSpotClient(TOKEN, fake, sleep=waits.append, clock=lambda: 0.0, daily_floor=4)
    assert hub.get("/account-info/v3/details")["portalId"] == 4242
    assert max(waits) >= 1 and hub.rate_limit["daily_remaining"] == 4
    with pytest.raises(BudgetExhausted, match="at the floor of 4"):
        hub.get("/account-info/v3/details")
    assert len(hub.calls) == 2  # the refused call was never sent


def test_client_opens_its_circuit_after_repeated_exhausted_retries():
    sent = []

    def down(method, url, headers, body, timeout):
        sent.append(url)
        return 503, b"{}"

    now = [0.0]
    hub = HubSpotClient(TOKEN, down, sleep=lambda s: None, clock=lambda: now[0], max_attempts=2,
                        breaker_threshold=2, breaker_cooldown=60)
    for _ in range(2):
        with pytest.raises(HubSpotError, match="still failing after retries"):
            hub.get("/crm/v3/owners")
    with pytest.raises(CircuitOpen):
        hub.get("/crm/v3/owners")
    assert len(sent) == 4
    now[0] = 61
    with pytest.raises(HubSpotError):
        hub.get("/crm/v3/owners")  # the cool-down is over: HubSpot is tried again


def test_errors_carry_category_properties_and_correlation_never_values_or_token():
    def refuses(method, url, headers, body, timeout):
        return 400, json.dumps({"status": "error", "category": "VALIDATION_ERROR", "correlationId": "abc-123",
                                "message": "Property values were not valid: jane.doe@example.com",
                                "errors": [{"message": "jane.doe@example.com is not valid",
                                            "context": {"propertyName": ["email"]}}]}).encode()

    with pytest.raises(HubSpotError) as caught:
        client(refuses).post("/crm/v3/objects/contacts/batch/update", {"inputs": []})
    message = str(caught.value)
    assert "VALIDATION_ERROR" in message and "properties email" in message and "abc-123" in message
    assert "jane.doe" not in message and TOKEN not in message
    assert batch_outcome({"results": [{"id": "1"}], "errors": [
        {"category": "OBJECT_NOT_FOUND", "context": {"ids": ["7", "8"]}}]}) == (
        [{"id": "1"}], [{"category": "OBJECT_NOT_FOUND", "ids": ["7", "8"]}])


# --------------------------------------------------------------------------- contract

def test_contract_gives_hubspot_the_crm_record_and_growthops_its_analytics(connection):
    rules = rules_by_field(connection)
    assert rules[("deals", "amount")].owner == "hubspot" and not rules[("deals", "amount")].push
    assert rules[("contacts", "email")].pii and not rules[("contacts", "email")].push
    assert rules[("contacts", "growthops_net_cash")].push and rules[("contacts", "growthops_renewal_risk")].push
    assert not rules[("contacts", "growthops_contact_id")].push  # identifiers are immutable
    assert rules[("contacts", "lifecyclestage")].owner == "shared"
    with pytest.raises(ContractViolation, match="owned by hubspot"):
        check_push(rules, "deals", "amount", "10.00", "20.00")
    with pytest.raises(ContractViolation, match="only move forward"):
        check_push(rules, "contacts", "lifecyclestage", "customer", "opportunity")
    check_push(rules, "contacts", "lifecyclestage", "opportunity", "customer")
    with pytest.raises(ContractViolation, match="not in the HubSpot contract"):
        check_push(rules, "contacts", "notes_last_contacted", "", "x")
    pushable = {(r.object_type, r.name) for r in contract(connection) if r.push}
    assert all(name.startswith("growthops_") for _, name in pushable - {("contacts", "lifecyclestage")})


# --------------------------------------------------------------------------- preflight and pull

def test_preflight_names_a_missing_scope_before_anything_is_read(fake):
    assert preflight(client(fake))["ready"]
    fake.forbidden = {r"/crm/v3/objects/deals"}
    checked = preflight(client(fake))
    assert not checked["ready"] and checked["missing_required"] == ["deals"]
    assert checked["capabilities"]["deals"]["category"] == "MISSING_SCOPES" and checked["writes"] == 0


def test_a_full_pull_lands_every_record_with_contact_data_hashed(fake, connection):
    hub = client(fake)
    result = pull_all(hub, connection, now=NOW)
    assert result["contacts"]["distinct"] == len(fake.objects["contacts"])
    assert result["deals"]["distinct"] == len(fake.objects["deals"]) == result["deals"]["associations"]
    assert result["reference"]["deal_stages"] == 5 and hub.writes == 0
    landed = [json.loads(row[0]) for row in connection.execute("SELECT properties_json FROM hubspot_records")]
    assert not any("@" in str(value) for props in landed for value in props.values())
    first = next(p for p in landed if p.get("email"))
    assert first["email"].startswith("hmac:") and first["firstname"] == protect("Synthetic")


def test_an_incremental_pull_reads_only_what_changed_after_the_overlap(fake, connection):
    hub = client(fake)
    full = pull(hub, connection, "contacts", now=NOW)
    again = pull(hub, connection, "contacts", now=NOW)
    assert again["mode"] == "incremental" and again["records"] < full["records"]
    fake.clock += 3_600_000  # an hour later, a rep edits one record in HubSpot
    target = _contact(fake, records(connection)["contacts"][0]["growthops_contact_id"])
    fake.touch("contacts", target, growthops_tracking_status="off_taxonomy")
    pull(hub, connection, "contacts", now=NOW)
    quiet = pull(hub, connection, "contacts", now=NOW)
    assert quiet["records"] == 1  # only the edit is inside the overlap now
    stored = json.loads(connection.execute("SELECT properties_json FROM hubspot_records WHERE hs_id=?",
                                           (target,)).fetchone()[0])
    assert stored["growthops_tracking_status"] == "off_taxonomy"


def _edits_mid_scan(after_searches):
    count = []

    def edit(fake):
        count.append(1)
        if len(count) == after_searches:  # a rep edits five records from the first page, mid-scan
            for hs in sorted(fake.objects["contacts"], key=int)[:5]:
                fake.touch("contacts", hs, growthops_tracking_status="direct")
    return edit


def test_records_edited_mid_scan_are_neither_skipped_nor_doubled(built_portal, connection):
    total = len(built_portal.objects["contacts"])
    # What the first live pull did: offset pages over a changing sort order skip records.
    offset_fake = copy.deepcopy(built_portal)
    offset_fake.on_search = _edits_mid_scan(3)
    by_offset = client(offset_fake).search("contacts", [], ["email"],
                                           sorts=[{"propertyName": "lastmodifieddate", "direction": "ASCENDING"}])
    assert len({r["id"] for r in by_offset}) < total
    # Key-set pages read every record exactly once under the same edits.
    fake = copy.deepcopy(built_portal)
    fake.on_search = _edits_mid_scan(3)
    result = pull(client(fake), connection, "contacts", now=NOW)
    assert result["distinct"] == total == result["records"]
    assert connection.execute("SELECT COUNT(*) FROM hubspot_records WHERE object_type='contacts'").fetchone()[0] == total
    fake.on_search = None
    pull(client(fake), connection, "contacts", now=NOW)  # the edits land on the next incremental pass
    edited = min(fake.objects["contacts"], key=int)
    stored = json.loads(connection.execute("SELECT properties_json FROM hubspot_records WHERE hs_id=?",
                                           (edited,)).fetchone()[0])
    assert stored["growthops_tracking_status"] == "direct"


def test_more_records_than_a_page_sharing_one_modified_time_are_all_read(built_portal, connection):
    fake = copy.deepcopy(built_portal)
    hub = client(fake)
    pull(hub, connection, "contacts", now=NOW)
    fake.clock += 3_600_000
    tied = sorted(fake.objects["contacts"], key=int)[:250]
    for hs in tied:  # a bulk edit: 250 records stamped with the same millisecond
        fake.objects["contacts"][hs]["growthops_tracking_status"] = "direct"
        fake.updated["contacts"][hs] = fake.clock
    result = pull(hub, connection, "contacts", now=NOW)
    assert result["distinct"] >= 250 and result["requests"] >= 3
    landed = {row[0]: json.loads(row[1])["growthops_tracking_status"] for row in connection.execute(
        "SELECT hs_id, properties_json FROM hubspot_records WHERE object_type='contacts'")}
    assert all(landed[hs] == "direct" for hs in tied)


def test_archived_records_are_marked_not_deleted(fake, connection):
    hub = client(fake)
    pull(hub, connection, "contacts", now=NOW)
    gone = next(iter(fake.objects["contacts"]))
    fake.archived["contacts"][gone] = fake.objects["contacts"].pop(gone)
    assert pull_archived(hub, connection, "contacts", now=NOW)["newly_marked"] == 1
    assert connection.execute("SELECT archived FROM hubspot_records WHERE hs_id=?", (gone,)).fetchone()[0] == 1


# --------------------------------------------------------------------------- reconcile, plan, approve, apply

def test_a_freshly_built_portal_reconciles_with_no_drift(fake, connection):
    pull_all(client(fake), connection, now=NOW)
    report = reconcile(connection)
    assert report["drift"] == [] and report["in_sync"], report["drift_by_property"]
    contacts = report["objects"]["contacts"]
    assert contacts["matched"] == len(records(connection)["contacts"]) and contacts["missing_count"] == 0


def test_hubspot_owned_changes_are_divergence_and_are_never_pushed(fake, connection):
    deal = next(iter(fake.objects["deals"]))
    fake.touch("deals", deal, amount="1.00")
    pull_all(client(fake), connection, now=NOW)
    report = reconcile(connection)
    # The amount a rep edited, and the reps the portal's lead routing assigned after create: HubSpot's to own.
    assert report["divergence_by_property"] == {"contacts.growthops_owner": 29, "deals.amount": 1}
    assert report["drift"] == []
    assert plan(connection, report)["changeset_id"] is None


def test_drift_is_planned_approved_applied_verified_and_never_applied_twice(fake, connection):
    hub = client(fake)
    renewing = [c for c in records(connection)["contacts"] if c["growthops_renewal_risk"]]
    for contact in renewing:  # the renewal fields are new: HubSpot does not hold them yet
        fake.touch("contacts", _contact(fake, contact["growthops_contact_id"]),
                   growthops_renewal_risk="", growthops_renewal_due_date="")
    pull_all(hub, connection, now=NOW)
    planned = plan(connection, now=NOW)
    assert planned["items"] == 2 * len(renewing) and planned["status"] == "planned"
    assert plan(connection, now=NOW)["changeset_id"] == planned["changeset_id"]  # content-addressed
    with pytest.raises(PermissionError, match="approve it before applying"):
        apply_changeset(hub, connection, planned["changeset_id"], now=NOW)
    with pytest.raises(ValueError, match="approver"):
        approve(connection, planned["changeset_id"], " ")
    approve(connection, planned["changeset_id"], "cs-lead", now=NOW)
    before = hub.writes
    result = apply_changeset(hub, connection, planned["changeset_id"], now=NOW)
    assert result["status"] == "applied" and result["items"] == {"verified": 2 * len(renewing)}
    assert hub.writes - before == -(-len(renewing) // 100)  # one batch update per 100 records
    high = next(c for c in renewing if c["growthops_renewal_risk"] == "high")
    assert fake.objects["contacts"][_contact(fake, high["growthops_contact_id"])]["growthops_renewal_risk"] == "high"
    writes = hub.writes
    assert apply_changeset(hub, connection, planned["changeset_id"], now=NOW)["status"] == "applied"
    assert hub.writes == writes  # nothing left to write
    assert reconcile(connection)["in_sync"]


def test_a_value_a_rep_changed_after_the_plan_is_a_conflict_not_an_overwrite(fake, connection):
    hub = client(fake)
    contact = records(connection)["contacts"][0]
    hs = _contact(fake, contact["growthops_contact_id"])
    fake.touch("contacts", hs, growthops_tracking_status="direct" if contact["growthops_tracking_status"] != "direct"
               else "complete")
    pull_all(hub, connection, now=NOW)
    planned = plan(connection, now=NOW)
    approve(connection, planned["changeset_id"], "ops", now=NOW)
    fake.touch("contacts", hs, growthops_tracking_status="missing_utm")  # a rep edits it before the apply
    result = apply_changeset(hub, connection, planned["changeset_id"], now=NOW)
    assert result["items"] == {"conflict": 1}
    assert fake.objects["contacts"][hs]["growthops_tracking_status"] == "missing_utm"


def test_lifecycle_only_moves_forward(fake, connection):
    hub = client(fake)
    desired = records(connection)
    customer = next(c for c in desired["contacts"] if c["lifecyclestage"] == "customer")
    lead = next(c for c in desired["contacts"] if c["lifecyclestage"] == "lead"
                and c["growthops_has_closed_won"] == "false" and float(c["growthops_net_cash"]) == 0
                and c["growthops_contact_id"] not in {d["growthops_contact_id"] for d in desired["deals"]})
    fake.touch("contacts", _contact(fake, customer["growthops_contact_id"]), lifecyclestage="opportunity")
    fake.touch("contacts", _contact(fake, lead["growthops_contact_id"]), lifecyclestage="customer")
    pull_all(hub, connection, now=NOW)
    report = reconcile(connection)
    assert [(d["growthops_id"], d["after"]) for d in report["drift"]] == [(customer["growthops_contact_id"],
                                                                           "customer")]
    assert report["objects"]["contacts"]["hubspot_ahead_lifecycle"] == 1  # never pulled back to lead


# --------------------------------------------------------------------------- renewal tasks

def test_renewal_tasks_are_created_once_even_after_a_crash(fake, connection):
    hub = client(fake)
    pull_all(hub, connection, now=NOW)
    dry = renewal_tasks(hub, connection, now=NOW)
    assert dry["mode"] == "dry_run" and dry["proposals_in_hubspot"] == 2 and not fake.objects["tasks"]
    made = renewal_tasks(hub, connection, create=True, now=NOW)
    assert len(made["created"]) == 2 and len(fake.objects["tasks"]) == 2
    high = next(p for p in made["planned"] if p["priority"] == "HIGH")
    task = next(hs for hs, p in fake.objects["tasks"].items() if high["key"] in p["hs_task_subject"])
    assert (task, high["contact_hs_id"]) in fake.task_links
    assert all(p.get("hubspot_owner_id") for p in fake.objects["tasks"].values())  # every task is in a queue
    assert renewal_tasks(hub, connection, create=True, now=NOW)["already_present"] == [p["key"] for p in made["planned"]]
    connection.execute("DELETE FROM hubspot_created_objects")  # the log lost after a crash
    again = renewal_tasks(hub, connection, create=True, now=NOW)
    assert again["created"] == [] and len(again["already_present"]) == 2 and len(fake.objects["tasks"]) == 2


# --------------------------------------------------------------------------- webhooks

SECRET = "hubspot-app-client-secret-0123456789"


def _signed(uri, body, secret=SECRET, when=NOW):
    timestamp = str(int(when.timestamp() * 1000))
    message = b"POST" + uri.encode() + body + timestamp.encode()
    return timestamp, base64.b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest()).decode()


def test_v3_signatures_verify_and_reject_tampering_replay_and_other_secrets():
    body = b'[{"eventId":1}]'
    uri = "https://growthops.example.com/v2/webhooks/hubspot?source=hubspot"
    timestamp, provided = _signed(uri, body)
    assert signature(SECRET, "POST", uri, body, timestamp) == provided
    assert verify(SECRET, "POST", uri, body, timestamp, provided, now=NOW)
    # HubSpot decodes these characters before signing, so an encoded URI still verifies.
    assert verify(SECRET, "POST", uri.replace("?", "%3F", 1).replace("https:", "https%3A"), body, timestamp,
                  provided, now=NOW)
    assert not verify(SECRET, "POST", uri, body + b" ", timestamp, provided, now=NOW)
    assert not verify("another-secret", "POST", uri, body, timestamp, provided, now=NOW)
    assert not verify(SECRET, "POST", uri, body, timestamp, provided, now=NOW + timedelta(minutes=6))
    assert not verify(SECRET, "POST", uri, body, "not-a-time", provided, now=NOW)


def test_events_are_stored_once_and_refetched_rather_than_trusted(fake, connection):
    hub = client(fake)
    pull_all(hub, connection, now=NOW)
    contacts = list(fake.objects["contacts"])
    changed, deleted, erased = contacts[0], contacts[1], contacts[2]
    fake.touch("contacts", changed, growthops_tracking_status="off_taxonomy")
    events = [
        {"eventId": 11, "portalId": 4242, "subscriptionType": "contact.propertyChange", "objectId": int(changed),
         "propertyName": "growthops_tracking_status", "propertyValue": "complete", "occurredAt": 1790000000000},
        {"eventId": 12, "portalId": 4242, "subscriptionType": "contact.deletion", "objectId": int(deleted)},
        {"eventId": 13, "portalId": 4242, "subscriptionType": "contact.privacyDeletion", "objectId": int(erased)},
        {"eventId": 14, "portalId": 9999, "subscriptionType": "contact.creation", "objectId": 1},
        {"eventId": 15, "portalId": 4242, "subscriptionType": "company.creation", "objectId": 5},
    ]
    assert record(connection, events, "4242", NOW) == {"accepted": 3, "duplicates": 0, "ignored": 2}
    assert record(connection, events[:1], "4242", NOW)["duplicates"] == 1  # HubSpot retried the delivery
    result = process(hub, connection, NOW)
    assert result == {"events": 3, "refetched": 1, "archived": 1, "erased": 1, "failed": 0}
    stored = json.loads(connection.execute("SELECT properties_json FROM hubspot_records WHERE hs_id=?",
                                           (changed,)).fetchone()[0])
    assert stored["growthops_tracking_status"] == "off_taxonomy"  # HubSpot's current value, not the event's
    assert connection.execute("SELECT archived FROM hubspot_records WHERE hs_id=?", (deleted,)).fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM hubspot_records WHERE hs_id=?", (erased,)).fetchone()[0] == 0
    assert process(hub, connection, NOW)["events"] == 0


def test_a_transient_failure_leaves_events_pending(fake, connection):
    hub = client(fake, max_attempts=1)
    record(connection, [{"eventId": 21, "portalId": 4242, "subscriptionType": "deal.propertyChange",
                         "objectId": int(next(iter(fake.objects["deals"])))}], "4242", NOW)
    fake.fail_next = [503]
    assert process(hub, connection, NOW)["refetched"] == 0
    assert connection.execute("SELECT status FROM hubspot_webhook_events").fetchone()[0] == "pending"
    assert process(hub, connection, NOW)["refetched"] == 1


# --------------------------------------------------------------------------- API, worker, readiness, settings

def test_webhook_route_verifies_signatures_behind_the_public_url(db_path, monkeypatch):
    from growthops.api import app

    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    with TestClient(app) as api:
        assert api.post("/v2/webhooks/hubspot", content=b"[]").status_code == 404  # not configured
    monkeypatch.setenv("GROWTHOPS_HUBSPOT_APP_SECRET", SECRET)
    monkeypatch.setenv("GROWTHOPS_PUBLIC_BASE_URL", "https://growthops.example.com")
    monkeypatch.setenv("GROWTHOPS_HUBSPOT_PORTAL_ID", "4242")
    body = json.dumps([{"eventId": 31, "portalId": 4242, "subscriptionType": "contact.propertyChange",
                        "objectId": 101, "propertyName": "email"}]).encode()
    timestamp, provided = _signed("https://growthops.example.com/v2/webhooks/hubspot", body,
                                  when=datetime.now(UTC))
    headers = {"X-HubSpot-Signature-v3": provided, "X-HubSpot-Request-Timestamp": timestamp}
    with TestClient(app) as api:
        assert api.post("/v2/webhooks/hubspot", content=body, headers={**headers, "X-HubSpot-Signature-v3": "x"}
                        ).status_code == 401
        assert api.post("/v2/webhooks/hubspot", content=body, headers=headers).json() == {
            "accepted": 1, "duplicates": 0, "ignored": 0}
        assert api.post("/v2/webhooks/hubspot", content=body, headers=headers).json()["duplicates"] == 1
        status = api.get("/v2/hubspot/sync").json()
        assert status["webhook_events"] == {"pending": 1}


def test_a_changeset_is_approved_over_the_api_only_by_a_named_operator(fake, db_path, monkeypatch):
    from growthops.api import app
    from growthops.db import connect

    connection = connect(db_path)
    contact = records(connection)["contacts"][0]
    fake.touch("contacts", _contact(fake, contact["growthops_contact_id"]), growthops_first_touch_medium="")
    pull_all(client(fake), connection, now=NOW)
    planned = plan(connection, now=NOW)
    connection.close()
    assert planned["items"] >= 1
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    monkeypatch.setenv("GROWTHOPS_OPS_TOKEN", "o" * 32)
    path = f"/v2/hubspot/changesets/{planned['changeset_id']}/approve"
    with TestClient(app) as api:
        reason = {"reason": "Reviewed the plan"}
        assert api.post(path, json=reason, headers={"X-GrowthOps-Actor": "ops"}).status_code == 403
        headers = {"X-GrowthOps-Ops-Token": "o" * 32, "X-GrowthOps-Actor": "cs.lead"}
        assert api.post(path, json=reason, headers=headers).json()["approved_by"] == "cs.lead"
        assert api.post(path, json=reason, headers=headers).status_code == 409
        assert api.post("/v2/hubspot/changesets/cs_missing/approve", json=reason, headers=headers).status_code == 404
    connection = connect(db_path)
    assert connection.execute("SELECT actor_id FROM operator_actions WHERE action_type='approve'").fetchone()[0] == \
        "cs.lead"
    connection.close()


def test_the_worker_runs_a_sync_pass_once_per_slot(fake, db_path, monkeypatch):
    from growthops.config import get_settings
    from growthops.worker import run_once

    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    monkeypatch.setenv("GROWTHOPS_HUBSPOT_SYNC", "on")
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("GROWTHOPS_HUBSPOT_PORTAL_ID", "4242")
    first = run_once(get_settings(), now=NOW, transport=fake)
    assert first["hubspot_sync"] == "succeeded"
    assert run_once(get_settings(), now=NOW + timedelta(minutes=5), transport=fake)["hubspot_sync"] is None
    assert run_once(get_settings(), now=NOW + timedelta(minutes=16), transport=fake)["hubspot_sync"] == "succeeded"
    monkeypatch.setenv("GROWTHOPS_HUBSPOT_PORTAL_ID", "1111")
    assert run_once(get_settings(), now=NOW + timedelta(minutes=31), transport=fake)["hubspot_sync"] == "failed"


def test_readiness_reports_a_stale_or_failing_sync(connection, monkeypatch):
    from growthops.config import get_settings
    from growthops.readiness import hubspot_freshness

    settings = get_settings().model_copy(update={"hubspot_sync_enabled": True})
    assert hubspot_freshness(connection, settings)["status"] == "missing"
    connection.execute("INSERT INTO hubspot_sync_state (object_type, last_success_at) VALUES ('contacts', ?)",
                       (NOW.isoformat(),))
    assert hubspot_freshness(connection, settings, NOW + timedelta(minutes=30))["status"] == "fresh"
    assert hubspot_freshness(connection, settings, NOW + timedelta(hours=2))["status"] == "stale"
    connection.execute("UPDATE hubspot_sync_state SET last_error='HubSpotError: HTTP 403'")
    assert hubspot_freshness(connection, settings, NOW + timedelta(minutes=30))["status"] == "stale"


def test_production_refuses_an_unpinned_or_unhashed_hubspot_sync(monkeypatch):
    from growthops.config import get_settings

    monkeypatch.setenv("GROWTHOPS_HUBSPOT_SYNC", "on")
    monkeypatch.setenv("GROWTHOPS_HUBSPOT_APP_SECRET", SECRET)
    problems = get_settings().problems()
    assert any("GROWTHOPS_HUBSPOT_PORTAL_ID" in p for p in problems)
    assert any("GROWTHOPS_PII_HASH_KEY" in p for p in problems)
    assert any("GROWTHOPS_PUBLIC_BASE_URL" in p for p in problems)
    assert any("HUBSPOT_ACCESS_TOKEN is required when GROWTHOPS_HUBSPOT_SYNC" in p for p in problems)
    redacted = get_settings().redacted()
    assert redacted["hubspot_app_secret"] == "***"


def test_the_contract_document_is_generated_from_the_code(connection):
    from pathlib import Path

    from growthops.hubspot_contract import CONTRACT_DOC, render

    committed = (Path(__file__).resolve().parents[1] / CONTRACT_DOC).read_text(encoding="utf-8")
    assert committed == render(connection), "run python -m growthops.hubspot_contract"


def test_the_sync_evidence_document_renders_from_a_complete_run(fake, connection):
    from growthops.hubspot_live_checks import webhook_check
    from growthops.hubspot_sync import render_doc

    hub = client(fake)
    renewing = [c for c in records(connection)["contacts"] if c["growthops_renewal_risk"]]
    for contact in renewing:
        fake.touch("contacts", _contact(fake, contact["growthops_contact_id"]),
                   growthops_renewal_risk="", growthops_renewal_due_date="")
    pulled = pull_all(hub, connection, full=True, now=NOW)
    before = reconcile(connection)
    planned = plan(connection, before, NOW)
    approved = approve(connection, planned["changeset_id"], "ops", NOW)
    applied = apply_changeset(hub, connection, planned["changeset_id"], NOW)
    after = {"pull": pull_all(hub, connection, now=NOW), "reconcile": reconcile(connection),
             "plan": plan(connection, now=NOW), "api": {"writes": 0}}
    evidence = {"preflight": preflight(hub), "pull": {"pull": pulled}, "reconcile": before, "plan": planned,
                "approve": approved, "apply": applied, "run": after,
                "tasks": renewal_tasks(hub, connection, create=True, now=NOW),
                "live_checks": {"webhook": webhook_check(hub, connection, "4242"), "payment_adapter": {
                    "existing_customer": {"calls": [["POST", "read"]], "wrote": False},
                    "unknown_contact": {"calls": [["POST", "read"]], "outcome": "refused", "wrote": False}}},
                "last_run_at": NOW.isoformat(), "runs": 1}
    text = render_doc(evidence)
    assert f"{2 * len(renewing)} verified" in text and "in sync: yes" in text
    assert "refetched 2 records" in text and "None" not in text
