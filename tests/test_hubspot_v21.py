"""Schema-only HubSpot extension must fail closed before any portal write."""

import pytest

from growthops.hubspot_portal import portal_properties
from growthops.hubspot_v21 import (
    EXPECTED_PORTAL_ID,
    apply_schema,
    audit_schema,
    definitions,
)


class FakePortal:
    def __init__(self, portal_id=EXPECTED_PORTAL_ID, existing=None):
        self.portal_id = portal_id
        self.properties = existing or {"contacts": {}, "deals": {}}
        self.posts = []

    def get(self, path):
        if path == "/account-info/v3/details":
            return {"accountType": "DEVELOPER_TEST", "portalId": self.portal_id}
        object_type = path.rsplit("/", 1)[-1]
        return {"results": list(self.properties[object_type].values())}

    def post(self, path, body):
        self.posts.append((path, body))
        object_type = path.rsplit("/", 1)[-1]
        self.properties[object_type][body["name"]] = body
        return body


def test_v21_schema_requires_approval_and_exact_portal():
    portal = FakePortal()
    with pytest.raises(PermissionError):
        apply_schema(portal)
    assert not portal.posts
    other = FakePortal(portal_id="different")
    with pytest.raises(ValueError, match="refusing portal"):
        apply_schema(other, approved=True)
    assert not other.posts


def test_v21_schema_checks_conflicts_then_creates_and_reads_back():
    desired = definitions()
    conflicting = {"contacts": {desired["contacts"][0]["name"]:
                                 {**desired["contacts"][0], "type": "number"}}, "deals": {}}
    portal = FakePortal(existing=conflicting)
    with pytest.raises(ValueError, match="conflict"):
        apply_schema(portal, approved=True)
    assert not portal.posts

    clean = FakePortal()
    first = apply_schema(clean, approved=True)
    assert len(first["created"]) == 5 and first["verified_properties"] == 5
    assert first["record_values_changed"] == 0
    assert apply_schema(clean, approved=True)["created"] == []
    assert len(clean.posts) == 5


def test_read_only_schema_audit_separates_baseline_drift_from_proposals(connection):
    baseline = portal_properties(connection)
    existing = {kind: {prop["name"]: dict(prop) for prop in props}
                for kind, props in baseline.items()}
    portal = FakePortal(existing=existing)
    clean = audit_schema(portal, connection)
    assert clean["mode"] == "read_only" and clean["writes"] == 0
    assert clean["baseline_expected"] == clean["baseline_present"] == 33
    assert clean["baseline_issues"] == []
    assert clean["proposed_issues"] == []
    assert len(clean["proposed_additions"]) == 5
    assert all(row["state"] == "not_created" for row in clean["proposed_additions"])
    assert portal.posts == []

    changed = baseline["contacts"][0]["name"]
    portal.properties["contacts"][changed]["label"] = "Changed in portal"
    del portal.properties["deals"][baseline["deals"][0]["name"]]
    drift = audit_schema(portal, connection)
    assert drift["baseline_present"] == 32
    assert {row["issue"] for row in drift["baseline_issues"]} == {"missing", "label_mismatch"}
    assert portal.posts == []


def test_proposed_field_readback_detects_definition_drift(connection):
    baseline = portal_properties(connection)
    existing = {kind: {prop["name"]: dict(prop) for prop in props}
                for kind, props in baseline.items()}
    portal = FakePortal(existing=existing)
    assert apply_schema(portal, approved=True)["verified_properties"] == 5
    assert audit_schema(portal, connection)["proposed_issues"] == []

    name = "growthops_qualification_status"
    portal.properties["deals"][name]["options"][0]["value"] = "changed"
    audit = audit_schema(portal, connection)
    assert audit["proposed_issues"] == [{
        "object": "deals", "property": name, "issue": "options_mismatch",
        "expected": ["qualified", "unqualified", "unknown"],
        "observed": ["changed", "unqualified", "unknown"],
    }]


def test_schema_apply_rejects_incorrect_readback():
    class DriftPortal(FakePortal):
        def get(self, path):
            response = super().get(path)
            if self.posts and path == "/crm/v3/properties/deals":
                response["results"] = [
                    {**prop, "label": "Unexpected"} if prop["name"] == "growthops_qualified_at" else prop
                    for prop in response["results"]
                ]
            return response

    with pytest.raises(RuntimeError, match="read-back mismatch"):
        apply_schema(DriftPortal(), approved=True)
