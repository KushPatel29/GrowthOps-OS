"""Revenue truth: why ad platforms, the CRM and the payment processor disagree.

Two bridges explain every cent between the systems. Each step is a named,
independently computed quantity; there is no balancing "other" line, and tests
assert that each bridge lands exactly on its end point.

* Platform bridge: what Meta, Google and LinkedIn claim  ->  warehouse net cash
  credited to paid media under the governed lead-creation model.
* CRM bridge: closed-won deal value  ->  gross collected  ->  net collected cash.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from growthops.attribution import allocations
from growthops.report import paid_campaigns

PLATFORM_ORDER = ("meta", "google", "linkedin")


def _net_by_payment(connection: sqlite3.Connection) -> dict[str, dict]:
    rows = connection.execute(
        """SELECT p.payment_id, p.customer_id, p.amount_cents gross, p.payment_type, p.product_id,
                  COALESCE(SUM(r.amount_cents),0) refunds
           FROM payments p LEFT JOIN refunds r ON r.payment_id=p.payment_id
           WHERE p.status='succeeded'
           GROUP BY p.payment_id, p.customer_id, p.amount_cents, p.payment_type, p.product_id"""
    ).fetchall()
    return {row["payment_id"]: dict(row) for row in rows}


def duplicate_contacts(connection: sqlite3.Connection) -> set[str]:
    """Non-surviving contacts that share a normalized email with another contact.

    The survivor is the migrated record (has a legacy ID) or else the earliest created.
    """
    rows = connection.execute(
        """SELECT contact_id, LOWER(TRIM(email)) email, legacy_id, created_at FROM contacts
           WHERE LOWER(TRIM(email)) IN (
             SELECT LOWER(TRIM(email)) FROM contacts GROUP BY 1 HAVING COUNT(*) > 1)"""
    ).fetchall()
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        groups[row["email"]].append(row)
    duplicates: set[str] = set()
    for members in groups.values():
        survivor = min(members, key=lambda r: (r["legacy_id"] is None, r["created_at"] or "", r["contact_id"]))
        duplicates.update(r["contact_id"] for r in members if r["contact_id"] != survivor["contact_id"])
    return duplicates


def crm_bridge(connection: sqlite3.Connection) -> dict:
    duplicates = duplicate_contacts(connection)
    deals = connection.execute(
        "SELECT deal_id, contact_id, amount_cents FROM deals WHERE stage='closed_won'"
    ).fetchall()
    collected_by_deal = dict(connection.execute(
        """SELECT deal_id, SUM(amount_cents) FROM payments
           WHERE status='succeeded' AND deal_id IS NOT NULL GROUP BY deal_id"""
    ).fetchall())
    booked = sum(row["amount_cents"] for row in deals)
    duplicate_value = sum(row["amount_cents"] for row in deals if row["contact_id"] in duplicates)
    uncollected = sum(max(row["amount_cents"] - collected_by_deal.get(row["deal_id"], 0), 0)
                      for row in deals if row["contact_id"] not in duplicates)
    unlinked = connection.execute(
        """SELECT COALESCE(SUM(p.amount_cents),0) FROM payments p LEFT JOIN deals d ON d.deal_id=p.deal_id
           WHERE p.status='succeeded' AND p.payment_type<>'renewal' AND d.deal_id IS NULL"""
    ).fetchone()[0]
    renewals = connection.execute(
        "SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE status='succeeded' AND payment_type='renewal'"
    ).fetchone()[0]
    gross = connection.execute(
        "SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE status='succeeded'"
    ).fetchone()[0]
    refunds = connection.execute("SELECT COALESCE(SUM(amount_cents),0) FROM refunds").fetchone()[0]
    steps = [
        {"step": "crm_booked", "label": "CRM closed-won deal value", "cents": booked, "kind": "total"},
        {"step": "duplicate_deals", "label": "Duplicate deals created by the CRM migration",
         "cents": -duplicate_value, "kind": "delta"},
        {"step": "not_yet_collected", "label": "Booked but not collected (plan instalments due, failed or unpaid)",
         "cents": -uncollected, "kind": "delta"},
        {"step": "unlinked_payments", "label": "Payments whose deal link was lost in migration",
         "cents": unlinked, "kind": "delta"},
        {"step": "renewals", "label": "Subscription renewals (cash with no new deal)",
         "cents": renewals, "kind": "delta"},
        {"step": "gross_collected", "label": "Payment processor: gross collected", "cents": gross, "kind": "total"},
        {"step": "refunds", "label": "Refunds", "cents": -refunds, "kind": "delta"},
        {"step": "net_collected", "label": "Net collected cash", "cents": gross - refunds, "kind": "total"},
    ]
    return {"steps": steps, "duplicate_contacts": len(duplicates),
            "residual_cents": booked - duplicate_value - uncollected + unlinked + renewals - gross}


def platform_bridge(connection: sqlite3.Connection) -> dict:
    paid = paid_campaigns(connection)
    payments = _net_by_payment(connection)
    credited_paid: dict[str, int] = defaultdict(int)  # payment -> net cash credited to paid media
    for row in allocations(connection, "lead_creation"):
        if row["campaign_id"] in paid:
            credited_paid[row["payment_id"]] += row["credited_cents"]
    claims = connection.execute(
        """SELECT contact_id, platform, reported_value_cents value, click_through
           FROM platform_conversions ORDER BY contact_id"""
    ).fetchall()
    by_contact: dict[str, list] = defaultdict(list)
    for claim in claims:
        by_contact[claim["contact_id"]].append(claim)
    reported = sum(claim["value"] for claim in claims)
    duplicate_claims = view_through = 0
    clicked: dict[str, int] = {}
    for contact, items in by_contact.items():
        primary = min(items, key=lambda c: (-c["click_through"], PLATFORM_ORDER.index(c["platform"])))
        duplicate_claims += sum(c["value"] for c in items if c is not primary)
        if primary["click_through"]:
            clicked[contact] = primary["value"]
        else:
            view_through += primary["value"]
    core_gross: dict[str, int] = defaultdict(int)
    addon_gross: dict[str, int] = defaultdict(int)
    refunds: dict[str, int] = defaultdict(int)
    for payment in payments.values():
        person = payment["customer_id"]
        if person not in clicked:
            continue
        if payment["product_id"] == "community" or payment["payment_type"] == "renewal":
            addon_gross[person] += payment["gross"]
        else:
            core_gross[person] += payment["gross"]
        refunds[person] += payment["refunds"]
    uncollected = sum(clicked[p] - core_gross[p] for p in clicked)
    addons = sum(addon_gross.values())
    refunded = sum(refunds.values())
    organic_credit = sum(p["gross"] - p["refunds"] - credited_paid.get(pid, 0)
                         for pid, p in payments.items() if p["customer_id"] in clicked)
    missed = sum(cents for pid, cents in credited_paid.items() if payments[pid]["customer_id"] not in clicked)
    warehouse = sum(credited_paid.values())
    steps = [
        {"step": "platform_reported", "label": "Ad platforms report (Meta + Google + LinkedIn)",
         "cents": reported, "kind": "total"},
        {"step": "cross_platform_duplicates", "label": "Same buyer claimed by more than one platform",
         "cents": -duplicate_claims, "kind": "delta"},
        {"step": "view_through", "label": "View-through claims with no click",
         "cents": -view_through, "kind": "delta"},
        {"step": "uncollected_contract", "label": "Pixel counts full contract value; instalments not yet collected",
         "cents": -uncollected, "kind": "delta"},
        {"step": "addons_renewals", "label": "Add-on and renewal cash the platforms never see",
         "cents": addons, "kind": "delta"},
        {"step": "refunds", "label": "Refunds the platforms ignore", "cents": -refunded, "kind": "delta"},
        {"step": "organic_created", "label": "Clicked buyers whose lead was created by organic or untracked touches",
         "cents": -organic_credit, "kind": "delta"},
        {"step": "missed_by_platforms", "label": "Paid-created buyers no platform claimed (outside window or tracking loss)",
         "cents": missed, "kind": "delta"},
        {"step": "warehouse_paid_cash", "label": "Warehouse: net cash credited to paid media (lead-creation model)",
         "cents": warehouse, "kind": "total"},
    ]
    residual = reported - duplicate_claims - view_through - uncollected + addons - refunded - organic_credit + missed - warehouse
    return {"steps": steps, "residual_cents": residual, "claimed_buyers": len(by_contact),
            "click_claimed_buyers": len(clicked)}


def platform_comparison(connection: sqlite3.Connection) -> list[dict]:
    """Per-platform self-reported value and ROAS versus the warehouse view."""
    spend = dict(connection.execute(
        """SELECT c.platform, SUM(s.spend_cents) FROM ad_spend_daily s
           JOIN campaigns c ON c.campaign_id=s.campaign_id GROUP BY c.platform"""
    ).fetchall())
    claimed = {row[0]: (row[1], row[2]) for row in connection.execute(
        "SELECT platform, COUNT(*), SUM(reported_value_cents) FROM platform_conversions GROUP BY platform"
    ).fetchall()}
    platform_of = dict(connection.execute("SELECT campaign_id, platform FROM campaigns").fetchall())
    warehouse: dict[str, int] = defaultdict(int)
    for row in allocations(connection, "lead_creation"):
        if row["campaign_id"]:
            warehouse[platform_of[row["campaign_id"]]] += row["credited_cents"]
    result = []
    for platform in PLATFORM_ORDER:
        platform_spend = spend.get(platform, 0)
        conversions, value = claimed.get(platform, (0, 0))
        result.append({
            "platform": platform,
            "spend_cents": platform_spend,
            "reported_conversions": conversions,
            "reported_value_cents": value,
            "warehouse_net_cash_cents": warehouse[platform],
            "platform_roas": round(value / platform_spend, 2) if platform_spend else None,
            "warehouse_roas": round(warehouse[platform] / platform_spend, 2) if platform_spend else None,
            "overstatement_ratio": round(value / warehouse[platform], 2) if warehouse[platform] else None,
        })
    return result


def four_numbers(connection: sqlite3.Connection) -> dict:
    """The executive question in one object: which revenue number is right?"""
    by_platform = {row["platform"]: row["reported_value_cents"] for row in platform_comparison(connection)}
    crm = crm_bridge(connection)["steps"]
    platform = platform_bridge(connection)["steps"]
    value = {step["step"]: step["cents"] for step in crm + platform}
    return {
        "platform_reported_cents": by_platform,
        "platform_reported_total_cents": value["platform_reported"],
        "crm_booked_cents": value["crm_booked"],
        "gross_collected_cents": value["gross_collected"],
        "net_collected_cents": value["net_collected"],
        "paid_media_net_cash_cents": value["warehouse_paid_cash"],
        "answer": ("Net collected cash is the revenue number. Platform and CRM figures are "
                   "claims or bookings; the bridges show exactly why each differs."),
    }
