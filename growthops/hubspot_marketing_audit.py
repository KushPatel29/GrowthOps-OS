"""Read-only marketing-contact governance for the connected synthetic test portal.

This report counts the portal sample itself. Blank opt-out fields are not
evidence of consent, and no contact's marketing status is changed here.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime

from growthops.hubspot_portal import Portal, account, load_token
from growthops.hubspot_v21 import EXPECTED_PORTAL_ID

PROPERTIES = (
    "hs_marketable_status",
    "hs_email_optout",
    "lifecyclestage",
    "hubspot_owner_id",
    "growthops_original_utm_source",
    "growthops_tracking_status",
    "growthops_stale_lead",
    "growthops_has_closed_won",
)
ACTIONABLE_STAGES = frozenset(("lead", "marketingqualifiedlead", "salesqualifiedlead", "opportunity"))
TRACKING_DEFECTS = frozenset(("off_taxonomy", "missing_utm"))


def _value(row: dict, name: str) -> str:
    value = (row.get("properties") or {}).get(name)
    return str(value).strip().lower() if value is not None else ""


def _ids(rows: list[dict], limit: int = 5) -> list[str]:
    return [str(row["id"]) for row in rows[:limit]]


def audit_marketing_contacts(portal: Portal, *, previous_snapshot_count: int | None = None) -> dict:
    """Report exact sample counts, actionable queues and unresolved eligibility."""
    found = account(portal)
    if found["portal_id"] != EXPECTED_PORTAL_ID:
        raise ValueError(f"refusing portal {found['portal_id']}; expected {EXPECTED_PORTAL_ID}")
    available = {item["name"] for item in portal.get("/crm/v3/properties/contacts").get("results", [])}
    missing = sorted(set(PROPERTIES) - available)
    if missing:
        raise RuntimeError(f"portal properties unavailable: {', '.join(missing)}")
    contacts = portal.search("contacts", [], list(PROPERTIES))
    total = len(contacts)
    marketing = Counter(_value(row, "hs_marketable_status") or "blank" for row in contacts)
    lifecycle = Counter(_value(row, "lifecyclestage") or "blank" for row in contacts)
    optout = Counter(_value(row, "hs_email_optout") or "blank" for row in contacts)
    tracking = Counter(_value(row, "growthops_tracking_status") or "blank" for row in contacts)
    missing_source = [row for row in contacts if not _value(row, "growthops_original_utm_source")]
    missing_owner = [row for row in contacts if not _value(row, "hubspot_owner_id")]
    unassigned = [row for row in contacts if _value(row, "lifecyclestage") in ACTIONABLE_STAGES
                  and not _value(row, "hubspot_owner_id")]
    bad_tracking = [row for row in contacts
                    if _value(row, "growthops_tracking_status") in TRACKING_DEFECTS
                    or not _value(row, "growthops_tracking_status")]
    stale = [row for row in contacts if _value(row, "growthops_stale_lead") == "true"]
    closed_won = [row for row in contacts if _value(row, "growthops_has_closed_won") == "true"]
    delta = None if previous_snapshot_count is None else total - previous_snapshot_count
    return {
        "scope": "connected_synthetic_hubspot_sample",
        "portal_id": found["portal_id"],
        "observed_at": datetime.now(UTC).isoformat(),
        "mode": "read_only",
        "contacts": total,
        "previous_snapshot_count": previous_snapshot_count,
        "count_change_since_snapshot": delta,
        "marketing_status": dict(sorted(marketing.items())),
        "lifecycle_stage": dict(sorted(lifecycle.items())),
        "email_optout_field": dict(sorted(optout.items())),
        "tracking_status": dict(sorted(tracking.items())),
        "consent_evidence": "not_established_by_these_fields",
        "marketing_eligibility_unknown": total,
        "review_queues": {
            "missing_original_utm_source": {"count": len(missing_source), "sample_ids": _ids(missing_source)},
            "missing_owner_all_stages": {"count": len(missing_owner), "sample_ids": _ids(missing_owner)},
            "actionable_without_owner": {"count": len(unassigned), "sample_ids": _ids(unassigned)},
            "tracking_defect_or_blank": {"count": len(bad_tracking), "sample_ids": _ids(bad_tracking)},
            "stale_lead_flag": {"count": len(stale), "sample_ids": _ids(stale)},
            "closed_won_flag": {"count": len(closed_won), "sample_ids": _ids(closed_won)},
        },
        "marketing_status_changes_recommended": 0,
        "writes": portal.writes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-snapshot-count", type=int, default=959)
    args = parser.parse_args()
    portal = Portal(load_token())
    result = audit_marketing_contacts(portal, previous_snapshot_count=args.previous_snapshot_count)
    if result["writes"]:
        raise RuntimeError("read-only audit unexpectedly wrote to HubSpot")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
