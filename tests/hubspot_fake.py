"""An in-memory HubSpot for the tests: the CRM, Properties, Imports, Lists and Automation APIs the portal build and
the sync call, with the quirks that matter to them.

* Unique-property upserts, and 207 multi-status answers when some batch inputs fail.
* A search API that pages, totals, sorts by last-modified date, filters on it, and refuses to page past a cap
  (10,000 in HubSpot; smaller here so a test can reach it).
* Every write stamps the record's ``updatedAt`` and last-modified property from a clock that only moves forward.
* Archived records answer on ``?archived=true`` and nowhere else.
* Optionally, rate-limit headers on every response, the way HubSpot reports the day's remaining allowance.
"""

import csv
import io
import json
import re
from datetime import UTC, datetime, timezone

TOKEN = "pat-na2-secret-value"
MODIFIED = {"contacts": "lastmodifieddate", "deals": "hs_lastmodifieddate", "tasks": "hs_lastmodifieddate"}


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day[:10]).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


class FakeHubSpot:
    def __init__(self, account_type="DEVELOPER_TEST", search_cap=10_000, headers=False, daily=250_000):
        self.account = {"portalId": 4242, "accountType": account_type, "timeZone": "US/Eastern",
                        "companyCurrency": "USD", "dataHostingLocation": "na2"}
        self.groups = {"contacts": set(), "deals": set()}
        self.props = {"contacts": {}, "deals": {}}
        self.pipelines, self.lists, self.flows = [], [], []
        self.owners = [{"id": "90001", "email": "owner@scalelab.test", "archived": False}]
        self.objects = {"contacts": {}, "deals": {}, "tasks": {}}
        self.archived = {"contacts": {}, "deals": {}, "tasks": {}}  # HubSpot's recycle bin
        self.updated = {"contacts": {}, "deals": {}, "tasks": {}}  # hs id -> epoch ms of the last write
        self.links = set()
        self.task_links = set()
        self.last_id = 1000
        self.clock = 1_790_000_000_000
        self.fail_next = []  # statuses to return before serving the next request
        self.hide_flows = False
        self.search_cap = search_cap
        self.headers = headers
        self.daily = daily
        self.forbidden = set()  # routes (regex) that answer 403 MISSING_SCOPES
        self.on_search = None  # called before each search: a rep editing records mid-scan

    # -- helpers
    def _new_id(self):
        self.last_id += 1
        return self.last_id

    def _find(self, obj, prop, value):
        return next((hs for hs, p in self.objects[obj].items() if p.get(prop) == value), None)

    def _write(self, obj, props, hs=None):
        hs = hs or str(self._new_id())
        stored = self.objects[obj].setdefault(hs, {})
        stored.update({k: ("" if v is None else str(v)) for k, v in props.items()})
        self.clock += 1000
        self.updated[obj][hs] = self.clock
        stored[MODIFIED[obj]] = _iso(self.clock)
        stored["hs_object_id"] = hs
        return hs

    def touch(self, obj, hs, **props):
        """A change made in HubSpot itself (a rep editing a record)."""
        self._write(obj, props, hs)

    def _record(self, obj, hs, properties):
        props = self.objects[obj][hs]
        return {"id": hs, "properties": {k: props.get(k) for k in properties}, "updatedAt": _iso(self.updated[obj][hs])}

    def _match(self, obj, hs, props, f):
        name, op = f["propertyName"], f["operator"]
        if name == "associations.contact":
            return (hs, f["value"]) in self.task_links
        value = props.get(name) or ""
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
        if name == MODIFIED.get(obj):
            left = float(self.updated[obj][hs])
        elif name.endswith("_date"):
            left = _ms(value)
        else:
            left = float(value)
        right = float(f["value"])
        return {"GT": left > right, "GTE": left >= right, "LT": left < right, "LTE": left <= right}[op]

    def __call__(self, method, url, headers, body, timeout):
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        if self.fail_next:
            status = self.fail_next.pop(0)
            return self._respond(status, {"message": "slow down"}, retry_after="1" if status == 429 else None)
        path = url.split("api.hubapi.com", 1)[1]
        route, _, query = path.partition("?")
        if any(re.fullmatch(pattern, route) for pattern in self.forbidden):
            return self._respond(403, {"category": "MISSING_SCOPES", "correlationId": "corr-1",
                                       "message": "This app hasn't been granted all required scopes"})
        is_json = headers["Content-Type"] == "application/json"
        data = json.loads(body) if body and is_json else None
        status, payload = self.route(method, route, query, data, body, headers)
        return self._respond(status, payload)

    def _respond(self, status, payload, retry_after=None):
        raw = b"" if payload is None else json.dumps(payload).encode()
        if not self.headers:
            return status, raw
        self.daily -= 1
        extra = {"X-HubSpot-RateLimit-Daily-Remaining": str(self.daily), "X-HubSpot-RateLimit-Daily": "250000"}
        if retry_after:
            extra["Retry-After"] = retry_after
        return status, raw, extra

    def route(self, method, route, query, data, raw, headers):
        params = dict(item.split("=", 1) for item in query.split("&") if "=" in item)
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
            if method == "GET":
                found = self.props[m[1]].get(m[2])
                return (200, found) if found else (404, {"category": "OBJECT_NOT_FOUND"})
            self.props[m[1]][m[2]].update(data)
            return 200, self.props[m[1]][m[2]]
        if route == "/crm/v3/pipelines/deals":
            if method == "POST":
                pipeline = {"id": str(self._new_id()), "label": data["label"],
                            "stages": [{**s, "id": str(self._new_id())} for s in data["stages"]]}
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
        if route == "/crm/v3/objects/tasks" and method == "POST":
            hs = self._write("tasks", data["properties"])
            for link in data.get("associations", []):
                self.task_links.add((hs, str(link["to"]["id"])))
            return 201, {"id": hs, "properties": dict(self.objects["tasks"][hs])}
        if (m := re.fullmatch(r"/crm/v3/objects/(\w+)", route)) and method == "GET":
            if m[1] not in self.objects:  # objects the key has no scope for, as the live portal answers
                return 403, {"category": "MISSING_SCOPES", "message": "This app hasn't been granted all required scopes"}
            source = self.archived if params.get("archived") == "true" else self.objects
            ids = sorted(source[m[1]], key=int)
            start, limit = int(params.get("after", 0)), int(params.get("limit", 10))
            payload = {"results": [{"id": hs, "properties": {}, "archived": source is self.archived}
                                   for hs in ids[start:start + limit]]}
            if start + limit < len(ids):
                payload["paging"] = {"next": {"after": str(start + limit)}}
            return 200, payload
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/batch/read", route):
            results, errors = [], []
            for item in data["inputs"]:
                if "idProperty" in data:
                    hs = self._find(m[1], data["idProperty"], item["id"])
                else:
                    hs = item["id"] if item["id"] in self.objects[m[1]] else None
                if hs:
                    result = self._record(m[1], hs, data["properties"])
                    if "idProperty" in data:
                        result["properties"][data["idProperty"]] = item["id"]
                    results.append(result)
                else:
                    errors.append({"status": "error", "category": "OBJECT_NOT_FOUND", "context": {"ids": [item["id"]]}})
            return (207 if errors else 200), {"results": results, "errors": errors}
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/batch/upsert", route):
            results = []
            for item in data["inputs"]:
                if bad := self._invalid(m[1], item["properties"]):
                    return 400, {"category": "VALIDATION_ERROR", "message": f"invalid option for {bad}",
                                 "context": {"propertyName": bad}}
                hs = self._write(m[1], item["properties"], self._find(m[1], item["idProperty"], item["id"]))
                results.append({"id": hs, "properties": dict(self.objects[m[1]][hs])})
            return 200, {"status": "COMPLETE", "results": results}
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/batch/archive", route):
            for item in data["inputs"]:
                self.archived[m[1]][item["id"]] = self.objects[m[1]].pop(item["id"])
            return 204, None
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/batch/update", route):
            results, errors = [], []
            for item in data["inputs"]:
                hs = self._find(m[1], item["idProperty"], item["id"]) if "idProperty" in item else \
                    (item["id"] if item["id"] in self.objects[m[1]] else None)
                if hs is None:
                    errors.append({"status": "error", "category": "OBJECT_NOT_FOUND", "context": {"ids": [item["id"]]}})
                    continue
                if bad := self._invalid(m[1], item["properties"]):
                    errors.append({"status": "error", "category": "VALIDATION_ERROR",
                                   "context": {"ids": [item["id"]], "propertyName": bad}})
                    continue
                self._write(m[1], item["properties"], hs)
                results.append({"id": hs})
            return (207 if errors else 200), {"status": "COMPLETE", "results": results, "errors": errors}
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/search", route):
            if self.on_search:
                self.on_search(self)
            filters = data["filterGroups"][0]["filters"] if data["filterGroups"] else []
            obj = m[1]
            hits = [hs for hs, p in self.objects[obj].items() if all(self._match(obj, hs, p, f) for f in filters)]
            sort = (data.get("sorts") or [{}])[0].get("propertyName")
            if sort == MODIFIED.get(obj):
                hits.sort(key=lambda hs: (self.updated[obj][hs], int(hs)))
            else:
                hits.sort(key=int)
            start = int(data.get("after", 0))
            if start >= self.search_cap:
                return 400, {"category": "VALIDATION_ERROR", "message": "paging past the search limit"}
            page = hits[start:start + data["limit"]]
            payload = {"total": len(hits), "results": [self._record(obj, hs, data["properties"]) for hs in page]}
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
            item = {"listId": str(self._new_id()), "name": data["name"], "filterBranch": data["filterBranch"]}
            self.lists.append(item)
            return 200, {"list": item}
        if route == "/automation/v4/flows":
            if method == "POST":
                assert not any("email" in a["actionTypeId"] for a in data["actions"])
                self.flows.append({**data, "id": str(self._new_id())})
                return 201, self.flows[-1]
            # Live HubSpot lists API-created flows as an empty result; the fake can reproduce that.
            return 200, {"results": [] if self.hide_flows else self.flows}
        raise AssertionError(f"unhandled {method} {route}")

    def _invalid(self, obj, properties):
        known = self.props.get(obj, {})
        return [k for k, v in properties.items() if k in known and known[k].get("options")
                and v not in {o["value"] for o in known[k]["options"]} | {""}]
