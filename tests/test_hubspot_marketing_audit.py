"""The live marketing audit classifies records without changing the portal."""

import pytest

from growthops.hubspot_marketing_audit import PROPERTIES, audit_marketing_contacts
from growthops.hubspot_v21 import EXPECTED_PORTAL_ID


class FakePortal:
    def __init__(self, rows, *, portal_id=EXPECTED_PORTAL_ID, available=PROPERTIES):
        self.rows = rows
        self.portal_id = portal_id
        self.available = available
        self.writes = 0
        self.search_calls = []

    def get(self, path):
        if path == "/account-info/v3/details":
            return {"accountType": "DEVELOPER_TEST", "portalId": self.portal_id}
        assert path == "/crm/v3/properties/contacts"
        return {"results": [{"name": name} for name in self.available]}

    def search(self, object_type, filters, properties):
        self.search_calls.append((object_type, filters, properties))
        return self.rows


def contact(contact_id, **properties):
    return {"id": contact_id, "properties": properties}


def test_marketing_audit_counts_review_queues_without_implying_consent():
    portal = FakePortal([
        contact("1", lifecyclestage="lead", growthops_tracking_status="missing_utm",
                growthops_stale_lead="true", hs_marketable_status="false"),
        contact("2", lifecyclestage="customer", hubspot_owner_id="owner",
                growthops_original_utm_source="youtube", growthops_tracking_status="complete",
                growthops_has_closed_won="true", hs_marketable_status="false"),
    ])
    result = audit_marketing_contacts(portal, previous_snapshot_count=1)

    assert result["contacts"] == 2 and result["count_change_since_snapshot"] == 1
    assert result["marketing_status"] == {"false": 2}
    assert result["email_optout_field"] == {"blank": 2}
    assert result["consent_evidence"] == "not_established_by_these_fields"
    assert result["marketing_eligibility_unknown"] == 2
    assert result["review_queues"]["missing_original_utm_source"] == {"count": 1, "sample_ids": ["1"]}
    assert result["review_queues"]["missing_owner_all_stages"] == {"count": 1, "sample_ids": ["1"]}
    assert result["review_queues"]["actionable_without_owner"] == {"count": 1, "sample_ids": ["1"]}
    assert result["review_queues"]["tracking_defect_or_blank"] == {"count": 1, "sample_ids": ["1"]}
    assert result["review_queues"]["stale_lead_flag"]["count"] == 1
    assert result["review_queues"]["closed_won_flag"]["count"] == 1
    assert result["marketing_status_changes_recommended"] == result["writes"] == portal.writes == 0
    assert portal.search_calls == [("contacts", [], list(PROPERTIES))]


def test_marketing_audit_refuses_wrong_portal_or_missing_property():
    wrong = FakePortal([], portal_id="different")
    with pytest.raises(ValueError, match="refusing portal"):
        audit_marketing_contacts(wrong)
    assert not wrong.search_calls

    missing = FakePortal([], available=PROPERTIES[:-1])
    with pytest.raises(RuntimeError, match="growthops_has_closed_won"):
        audit_marketing_contacts(missing)
    assert not missing.search_calls
