"""Schema-only HubSpot extension must fail closed before any portal write."""

import pytest

from growthops.hubspot_v21 import EXPECTED_PORTAL_ID, apply_schema, definitions


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
