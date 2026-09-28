"""The HubSpot portal build, run end to end against an in-memory fake of the HubSpot APIs it calls."""

import csv
import io
import json
import re
from datetime import datetime, timezone
from itertools import count

import pytest

from growthops.adapters import ProviderError
from growthops.hubspot_portal import (
    PIPELINE_STAGES,
    Portal,
    apply,
    load_token,
    plan,
    portal_properties,
    records,
)

TOKEN = "pat-na2-secret-value"


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day[:10]).replace(tzinfo=timezone.utc).timestamp() * 1000)


class FakeHubSpot:
    """Just enough of the CRM, Imports, Lists and Automation APIs to exercise every step, with their quirks:
    unique-property upserts, 207 multi-status batch reads, and a search API that pages and totals."""

    def __init__(self, account_type="DEVELOPER_TEST"):
        self.account = {"portalId": 4242, "accountType": account_type, "timeZone": "US/Eastern",
                        "companyCurrency": "USD", "dataHostingLocation": "na2"}
        self.groups = {"contacts": set(), "deals": set()}
        self.props = {"contacts": {}, "deals": {}}
        self.pipelines, self.lists, self.flows = [], [], []
        self.owners = [{"id": "90001", "email": "owner@scalelab.test", "archived": False}]
        self.objects = {"contacts": {}, "deals": {}}
        self.archived = {"contacts": {}, "deals": {}}  # HubSpot's recycle bin
        self.links = set()
        self.ids = count(1000)
        self.fail_next = []  # statuses to return before serving the next request
        self.hide_flows = False

    # -- helpers
    def _find(self, obj, prop, value):
        return next((hs for hs, p in self.objects[obj].items() if p.get(prop) == value), None)

    def _write(self, obj, props, hs=None):
        hs = hs or str(next(self.ids))
        stored = self.objects[obj].setdefault(hs, {})
        stored.update({k: ("" if v is None else str(v)) for k, v in props.items()})
        return hs

    def _match(self, props, f):
        value = props.get(f["propertyName"]) or ""
        op = f["operator"]
        if op == "HAS_PROPERTY":
            return value != ""
        if op == "NOT_HAS_PROPERTY":
            return value == ""
        if op == "EQ":
            return value == f["value"]
        if op == "NEQ":
            return value != f["value"]
        if op == "IN":
            return value in f["values"]
        if not value:
            return False
        left = _ms(value) if f["propertyName"].endswith("_date") else float(value)
        return left > float(f["value"]) if op == "GT" else left < float(f["value"])

    def __call__(self, method, url, headers, body, timeout):
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        if self.fail_next:
            return self.fail_next.pop(0), b'{"message":"slow down"}'
        path = url.split("api.hubapi.com", 1)[1]
        route, _, query = path.partition("?")
        is_json = headers["Content-Type"] == "application/json"
        data = json.loads(body) if body and is_json else None
        status, payload = self.route(method, route, query, data, body, headers)
        return status, (b"" if payload is None else json.dumps(payload).encode())

    def route(self, method, route, query, data, raw, headers):
        if route == "/account-info/v3/details":
            return 200, self.account
        if m := re.fullmatch(r"/crm/v3/properties/(\w+)/groups", route):
            if method == "POST":
                self.groups[m[1]].add(data["name"])
                return 201, data
            return 200, {"results": [{"name": g} for g in self.groups[m[1]]]}
        if m := re.fullmatch(r"/crm/v3/properties/(\w+)", route):
            if method == "POST":
                assert data["groupName"] in self.groups[m[1]]
                self.props[m[1]][data["name"]] = data
                return 201, data
            return 200, {"results": list(self.props[m[1]].values())}
        if m := re.fullmatch(r"/crm/v3/properties/(\w+)/(\w+)", route):
            self.props[m[1]][m[2]].update(data)
            return 200, self.props[m[1]][m[2]]
        if route == "/crm/v3/pipelines/deals":
            if method == "POST":
                pipeline = {"id": str(next(self.ids)), "label": data["label"],
                            "stages": [{**s, "id": str(next(self.ids))} for s in data["stages"]]}
                self.pipelines.append(pipeline)
                return 201, pipeline
            return 200, {"results": self.pipelines}
        if route == "/crm/v3/owners":
            return 200, {"results": self.owners}
        if route == "/crm/v3/imports":
            boundary = headers["Content-Type"].split("boundary=")[1].encode()
            parts = raw.split(b"--" + boundary)
            request = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0])
            assert request["importOperations"] == {"0-1": "UPSERT"}
            text = parts[2].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0].decode()
            rows = list(csv.DictReader(io.StringIO(text)))
            for row in rows:
                self._write("contacts", {k: v for k, v in row.items() if v != ""},
                            self._find("contacts", "email", row["email"]))
            self.imported = len(rows)
            return 200, {"id": "imp-1", "state": "STARTED"}
        if route == "/crm/v3/imports/imp-1":
            return 200, {"id": "imp-1", "state": "DONE",
                         "metadata": {"counters": {"TOTAL_ROWS": self.imported, "CREATED_OBJECTS": self.imported}}}
        if route == "/crm/v3/imports/imp-1/errors":
            return 200, {"results": []}
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/batch/read", route):
            results, errors = [], []
            for item in data["inputs"]:
                hs = self._find(m[1], data["idProperty"], item["id"])
                if hs:
                    props = self.objects[m[1]][hs]
                    results.append({"id": hs, "properties": {k: props.get(k) for k in data["properties"]}
                                    | {data["idProperty"]: item["id"]}})
                else:
                    errors.append({"status": "error", "category": "OBJECT_NOT_FOUND"})
            return (207 if errors else 200), {"results": results, "errors": errors}
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/batch/upsert", route):
            results = []
            for item in data["inputs"]:
                known = self.props[m[1]]
                bad = [k for k, v in item["properties"].items() if k in known and known[k].get("options")
                       and v not in {o["value"] for o in known[k]["options"]} | {""}]
                if bad:
                    return 400, {"message": f"invalid option for {bad}"}
                hs = self._write(m[1], item["properties"], self._find(m[1], item["idProperty"], item["id"]))
                results.append({"id": hs, "properties": dict(self.objects[m[1]][hs])})
            return 200, {"status": "COMPLETE", "results": results}
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/batch/archive", route):
            for item in data["inputs"]:
                self.archived[m[1]][item["id"]] = self.objects[m[1]].pop(item["id"])
            return 204, None
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/batch/update", route):
            for item in data["inputs"]:
                self._write(m[1], item["properties"], item["id"])
            return 200, {"status": "COMPLETE"}
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/search", route):
            filters = data["filterGroups"][0]["filters"] if data["filterGroups"] else []
            hits = sorted((hs for hs, p in self.objects[m[1]].items() if all(self._match(p, f) for f in filters)),
                          key=int)
            start = int(data.get("after", 0))
            page = hits[start:start + data["limit"]]
            payload = {"total": len(hits), "results": [
                {"id": hs, "properties": {k: self.objects[m[1]][hs].get(k) for k in data["properties"]}}
                for hs in page]}
            if start + data["limit"] < len(hits):
                payload["paging"] = {"next": {"after": str(start + data["limit"])}}
            return 200, payload
        if route == "/crm/v4/associations/deals/contacts/batch/read":
            return 200, {"results": [{"from": {"id": i["id"]}, "to": [{"toObjectId": int(c)}
                                                                      for d, c in self.links if d == i["id"]]}
                                     for i in data["inputs"]]}
        if route == "/crm/v4/associations/deals/contacts/batch/associate/default":
            self.links |= {(i["from"]["id"], i["to"]["id"]) for i in data["inputs"]}
            # HubSpot's deal lifecycle sync: a deal lifts its contacts to Opportunity, a closed-won deal to Customer.
            won = {s["id"] for p in self.pipelines for s in p["stages"] if s["label"] == "Closed won"}
            rank = ["subscriber", "lead", "marketingqualifiedlead", "salesqualifiedlead", "opportunity", "customer"]
            for item in data["inputs"]:
                contact = self.objects["contacts"][item["to"]["id"]]
                target = "customer" if self.objects["deals"][item["from"]["id"]].get("dealstage") in won else "opportunity"
                if rank.index(contact.get("lifecyclestage") or "subscriber") < rank.index(target):
                    contact["lifecyclestage"] = target
            return 200, {"status": "COMPLETE"}
        if route == "/crm/v3/lists/search":
            return 200, {"lists": [x for x in self.lists if data["query"] in x["name"]]}
        if route == "/crm/v3/lists":
            assert data["filterBranch"]["filterBranchType"] == "OR"
            item = {"listId": str(next(self.ids)), "name": data["name"], "filterBranch": data["filterBranch"]}
            self.lists.append(item)
            return 200, {"list": item}
        if route == "/automation/v4/flows":
            if method == "POST":
                assert not any("email" in a["actionTypeId"] for a in data["actions"])
                self.flows.append({**data, "id": str(next(self.ids))})
                return 201, self.flows[-1]
            # Live HubSpot lists API-created flows as an empty result; the fake can reproduce that.
            return 200, {"results": [] if self.hide_flows else self.flows}
        raise AssertionError(f"unhandled {method} {route}")


def _portal(fake):
    return Portal(TOKEN, fake, sleep=lambda seconds: None, clock=lambda: 0.0)


def test_plan_runs_offline_and_every_value_fits_its_property(connection):
    summary = plan(connection)
    emails = connection.execute("SELECT COUNT(DISTINCT LOWER(TRIM(email))) FROM contacts").fetchone()[0]
    assert summary["warehouse_contacts"] == emails
    assert summary["warehouse_deals"] == connection.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    # A developer test account caps contacts at 1,000; the sample fits with headroom and keeps every stage.
    assert 900 <= summary["contacts"] < 1000
    assert sum(summary["tracking_status"].values()) == summary["contacts"]
    assert set(summary["expected_after_cleanup"]["lifecycle"]) == {"lead", "marketingqualifiedlead", "opportunity",
                                                                    "customer"}
    sample, full = records(connection), records(connection, sample=None)
    assert sample == records(connection)  # deterministic
    by_id = {c["growthops_contact_id"]: c for c in full["contacts"]}
    assert all(by_id[c["growthops_contact_id"]] == c for c in sample["contacts"])  # same values as the full build
    kept = {c["growthops_contact_id"] for c in sample["contacts"]}
    assert sample["deals"] == [d for d in full["deals"] if d["growthops_contact_id"] in kept]
    won = sum(float(d["amount"]) for d in sample["deals"] if d["pipeline_stage"] == "closedwon")
    assert summary["expected_after_cleanup"]["closed_won_amount"] == pytest.approx(won)
    # An enumeration rejects unknown values, so every value written must already be an option.
    desired = full
    for object_type, props in portal_properties(connection).items():
        for prop in props:
            if "options" not in prop:
                continue
            allowed = {o["value"] for o in prop["options"]} | {""}
            used = {row.get(prop["name"], "") for row in desired[object_type]}
            assert used <= allowed, (object_type, prop["name"], used - allowed)
    # HubSpot rejects .test addresses; the portal copy uses an RFC 2606 domain that cannot receive mail.
    assert all(c["email"].endswith("@scalelab.example.com") for c in desired["contacts"])
    assert len({c["email"] for c in desired["contacts"]}) == len(desired["contacts"])


def test_apply_builds_the_portal_cleans_it_and_reconciles(connection, tmp_path):
    fake = FakeHubSpot()
    evidence = apply(_portal(fake), connection, output=tmp_path)
    steps = evidence["steps"]
    assert evidence["account"]["account_type"] == "DEVELOPER_TEST"
    assert len(steps["properties"]["created"]) == sum(len(v) for v in portal_properties(connection).values())
    assert [s["label"] for s in fake.pipelines[0]["stages"]] == [label for _, label, _, _ in PIPELINE_STAGES]
    assert steps["import"]["state"] == "DONE" and steps["import"]["rows"] == len(fake.objects["contacts"])
    assert steps["sync"]["contacts_created"] == 0  # the import already created every contact
    assert steps["sync"]["deals_created"] == len(fake.objects["deals"]) == steps["sync"]["associations_added"]
    cleanup = steps["cleanup"]
    for rule, result in cleanup.items():
        assert result["fixed"] == result["found"] and result["remaining"] == 0, rule
    # The migration left real gaps: unowned open leads and unflagged stale leads. The 19 closed-won contacts short
    # of Customer never reach the cleanup, because HubSpot's deal lifecycle sync promotes them on association.
    assert cleanup["open_leads_without_owner"]["found"] > 0 and cleanup["stale_leads_unflagged"]["found"] > 0
    assert cleanup["closed_won_not_customer"]["found"] == cleanup["paying_not_customer"]["found"] == 0
    assert len(steps["lists"]["created"]) == 6 and len(steps["workflows"]["created"]) == 3
    assert steps["verify"]["reconciled"], steps["verify"]["differences"]
    assert TOKEN not in json.dumps(evidence)


def test_a_second_apply_writes_nothing_and_never_undoes_the_cleanup(connection, tmp_path):
    fake = FakeHubSpot()
    apply(_portal(fake), connection, output=tmp_path)
    customers = sum(1 for p in fake.objects["contacts"].values() if p.get("lifecyclestage") == "customer")
    again = _portal(fake)
    evidence = apply(again, connection, output=tmp_path)
    assert evidence["api_writes"] == 0, [c for c in again.calls if c[0] != "GET"][:5]
    assert "skipped" in evidence["steps"]["import"]
    assert all(r["found"] == 0 for r in evidence["steps"]["cleanup"].values())
    assert sum(1 for p in fake.objects["contacts"].values() if p.get("lifecyclestage") == "customer") == customers
    assert evidence["steps"]["verify"]["reconciled"]


def test_prune_archives_only_what_is_outside_the_sample(connection, tmp_path):
    fake = FakeHubSpot()
    full = records(connection, sample=None)
    kept = {c["growthops_contact_id"] for c in records(connection)["contacts"]}
    # What the over-cap import left behind: sampled and unsampled contacts side by side.
    for contact in full["contacts"][:300]:
        fake._write("contacts", {"growthops_contact_id": contact["growthops_contact_id"], "email": contact["email"]})
    outside = sum(1 for c in full["contacts"][:300] if c["growthops_contact_id"] not in kept)
    evidence = apply(_portal(fake), connection, ["properties", "prune"], output=tmp_path)
    assert evidence["steps"]["prune"]["contacts_archived"] == outside > 0
    assert len(fake.archived["contacts"]) == outside  # soft delete: recoverable from the recycle bin
    assert {p["growthops_contact_id"] for p in fake.objects["contacts"].values()} <= kept
    again = apply(_portal(fake), connection, ["prune"], output=tmp_path)
    assert again["steps"]["prune"]["contacts_archived"] == 0 and again["api_writes"] == 0


def test_workflows_are_created_once_even_when_hubspot_will_not_list_them(connection, tmp_path):
    fake = FakeHubSpot()
    fake.hide_flows = True  # what the live portal did: an empty list for flows the API itself created
    apply(_portal(fake), connection, ["workflows"], output=tmp_path)
    again = apply(_portal(fake), connection, ["workflows"], output=tmp_path)
    assert len(fake.flows) == 3 and again["steps"]["workflows"]["created"] == []
    state = json.loads((tmp_path / "portal_state.json").read_text(encoding="utf-8"))
    assert sorted(state["4242"]["workflows"].values()) == sorted(flow["id"] for flow in fake.flows)


def test_refuses_a_standard_portal_unless_it_is_named(connection, tmp_path):
    with pytest.raises(SystemExit, match="Refusing to write synthetic data to a STANDARD portal"):
        apply(_portal(FakeHubSpot("STANDARD")), connection, ["properties"], output=tmp_path)
    fake = FakeHubSpot("STANDARD")
    apply(_portal(fake), connection, ["properties"], allow_portal="4242", output=tmp_path)
    assert fake.props["contacts"]


def test_rate_limits_are_retried_and_errors_never_carry_the_token():
    fake = FakeHubSpot()
    fake.fail_next = [429, 502]
    waits = []
    portal = Portal(TOKEN, fake, sleep=waits.append, clock=lambda: 0.0)
    assert portal.get("/account-info/v3/details")["portalId"] == 4242
    assert [status for _, _, status in portal.calls] == [429, 502, 200]
    fake.route = lambda *args: (400, {"message": "Property values were not valid"})
    with pytest.raises(ProviderError, match=r"HTTP 400 \(permanent\)") as error:
        portal.post("/crm/v3/objects/contacts/batch/upsert", {"inputs": []})
    assert TOKEN not in str(error.value)


def test_token_comes_from_the_environment_or_a_local_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("# local\nHUBSPOT_ACCESS_TOKEN=\"pat-from-file\"\n", encoding="utf-8")
    assert load_token(env) == "pat-from-file"
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "pat-from-env")
    assert load_token(env) == "pat-from-env"
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN")
    with pytest.raises(SystemExit, match="HUBSPOT_ACCESS_TOKEN is not set"):
        load_token(tmp_path / "missing.env")
