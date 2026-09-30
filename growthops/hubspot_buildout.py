"""The rest of what ScaleLab runs in HubSpot: product catalog, line items, and the support queue.

The portal build and the sync cover contacts, deals, lists, workflows and tasks through the portal's service key.
That key cannot reach products or line items (missing scopes) or tickets (not available to the key type at all), so
these objects are created through HubSpot's own connector, acting as the portal's user. What to create is still
code: :func:`plan` derives every object from the warehouse, deterministically, with a key that finds it again.

* **Product library.** The five offers in ``products``, at list price; the annual community membership is a
  recurring product billed yearly.
* **Line items.** One per portal deal, the deal's product at its list price (every portal deal's amount equals its
  product's list price, so HubSpot's amount recalculation from line items leaves each deal amount unchanged). The
  community line item bills annually over a 12-month term, which is what gives a deal HubSpot's ARR and MRR.
* **Support pipeline and tickets.** "GrowthOps support: access and billing", with the portal contacts' real cases:
  buyers whose payment dead-lettered and who still have no community access, refunds processed, and a renewal
  whose card failed.

The warehouse has no company entity (ScaleLab sells to individual founders), so no companies are invented.
``python -m growthops.hubspot_buildout plan`` prints the plan; ``doc`` writes docs/hubspot-buildout.md from it and
from the read-back recorded in ``build/hubspot/buildout_evidence.json``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from growthops.hubspot_portal import records

TICKET_PIPELINE = "GrowthOps support: access and billing"
TICKET_STAGES = (("new", "New", "OPEN"), ("investigating", "Investigating", "OPEN"),
                 ("waiting", "Waiting on provider or customer", "OPEN"), ("resolved", "Resolved", "CLOSED"))
EVIDENCE = Path("build/hubspot/buildout_evidence.json")
DOC = Path("docs/hubspot-buildout.md")


def _money(cents: int) -> str:
    return f"{cents / 100:.2f}"


def plan(connection: sqlite3.Connection) -> dict:
    """Every product, line item and ticket to create, derived from the warehouse; nothing is sent."""
    desired = records(connection)
    sample = {c["growthops_contact_id"] for c in desired["contacts"]}
    products = []
    for row in connection.execute("SELECT * FROM products ORDER BY product_id"):
        product = {"name": row["product_name"], "hs_sku": row["product_id"], "price": _money(row["list_price_cents"]),
                   "description": {"one_time": "One payment.", "plan": "Paid in instalments.",
                                   "annual": "Annual membership, renews each year."}[row["billing"]]}
        if row["billing"] == "annual":
            product.update({"recurringbillingfrequency": "annually", "hs_recurring_billing_period": "P12M"})
        products.append(product)
    by_sku = {p["hs_sku"]: p for p in products}
    line_items = []
    for deal in desired["deals"]:
        product = by_sku[deal["growthops_product"]]
        item = {"deal": deal["growthops_deal_id"], "sku": product["hs_sku"],
                "properties": {"name": product["name"], "quantity": "1", "price": product["price"],
                               "hs_sku": product["hs_sku"]}}
        if "recurringbillingfrequency" in product:
            item["properties"].update({"recurringbillingfrequency": "annually", "hs_recurring_billing_period": "P12M"})
        assert abs(float(product["price"]) - float(deal["amount"])) < 0.005, deal["growthops_deal_id"]
        line_items.append(item)

    tickets = []
    for row in connection.execute(
            """SELECT e.event_id, e.customer_id, e.payment_id, e.received_at, p.amount_cents, p.deal_id
               FROM processed_events e JOIN payments p ON p.payment_id=e.payment_id
               LEFT JOIN access_entitlements a ON a.customer_id=e.customer_id
               WHERE e.status='dead_letter' AND a.customer_id IS NULL ORDER BY e.received_at, e.event_id"""):
        if row["customer_id"] in sample:
            tickets.append({"key": f"access:{row['event_id']}", "contact": row["customer_id"], "deal": row["deal_id"],
                            "stage": "investigating", "priority": "HIGH", "category": "PRODUCT_ISSUE",
                            "subject": f"Paid but no community access [access:{row['event_id']}]",
                            "content": (f"Payment of ${row['amount_cents'] / 100:,.0f} on {row['received_at'][:10]} "
                                        "exhausted its retries while the community-access provider was down. Replay "
                                        "the dead-lettered event, confirm access, and apologise.")})
    for row in connection.execute(
            """SELECT r.refund_id, r.amount_cents, r.refunded_at, p.customer_id, p.deal_id FROM refunds r
               JOIN payments p ON p.payment_id=r.payment_id ORDER BY r.refunded_at, r.refund_id"""):
        if row["customer_id"] in sample:
            tickets.append({"key": f"refund:{row['refund_id']}", "contact": row["customer_id"], "deal": row["deal_id"],
                            "stage": "resolved", "priority": "LOW", "category": "BILLING_ISSUE",
                            "subject": f"Refund processed [refund:{row['refund_id']}]",
                            "content": f"Refund of ${row['amount_cents'] / 100:,.0f} issued on {row['refunded_at'][:10]}."})
    for row in connection.execute(
            """SELECT a.attempt_id, a.subscription_id, a.attempted_at, a.failure_code, s.customer_id
               FROM renewal_attempts a JOIN subscriptions s ON s.subscription_id=a.subscription_id
               WHERE a.outcome='failed' ORDER BY a.attempted_at, a.attempt_id"""):
        if row["customer_id"] in sample:
            reason = (row["failure_code"] or "declined").replace("_", " ")
            tickets.append({"key": f"renewal:{row['attempt_id']}", "contact": row["customer_id"], "deal": None,
                            "stage": "waiting", "priority": "HIGH", "category": "BILLING_ISSUE",
                            "subject": f"Renewal payment failed: {reason} [renewal:{row['attempt_id']}]",
                            "content": (f"The {row['subscription_id']} renewal on {row['attempted_at'][:10]} failed "
                                        f"({reason}). Ask the member to update the card; a follow-up task is on the "
                                        "contact.")})
    return {"products": products, "line_items": line_items,
            "ticket_pipeline": {"label": TICKET_PIPELINE,
                                "stages": [{"key": key, "label": label, "state": state}
                                           for key, label, state in TICKET_STAGES]},
            "tickets": tickets,
            "summary": {"products": len(products), "line_items": len(line_items), "tickets": len(tickets),
                        "line_item_value": _money(sum(round(float(i["properties"]["price"]) * 100)
                                                      for i in line_items)),
                        "deal_value": _money(sum(round(float(d["amount"]) * 100) for d in desired["deals"]))}}


def render(spec: dict, evidence: dict | None) -> str:
    summary = spec["summary"]
    lines = [
        "# HubSpot build-out: products, line items and support",
        "",
        ("Generated by `python -m growthops.hubspot_buildout doc` from the plan (derived from the warehouse) and the "
        "read-back in `build/hubspot/buildout_evidence.json`; do not edit."),
        "",
        ("The portal's service key cannot reach products or line items (missing scopes) or tickets (not available "
        "to the key type), so these objects were created through HubSpot's own connector, acting as the portal's "
        "user, from the plan below. The warehouse has no company entity, so no companies were created."),
        "",
        "## Product library",
        "",
        "| SKU | Product | List price | Billing |",
        "|---|---|---:|---|",
        *[f"| `{p['hs_sku']}` | {p['name']} | ${float(p['price']):,.2f} | "
          f"{'annual, recurring' if 'recurringbillingfrequency' in p else p['description'].rstrip('.').lower()} |"
          for p in spec["products"]],
        "",
        "## Line items",
        "",
        (f"{summary['line_items']} line items, one per portal deal, worth ${float(summary['line_item_value']):,.2f} in "
        f"total against ${float(summary['deal_value']):,.2f} of deal value: every deal's amount is its product's list "
        "price, so HubSpot's recalculation of deal amounts from line items changes none of them. Community line items "
        "bill annually over 12 months, which gives those deals HubSpot's ARR and MRR."),
        "",
        f"## Support pipeline: {spec['ticket_pipeline']['label']}",
        "",
        " → ".join(f"{s['label']} ({s['state'].lower()})" for s in spec["ticket_pipeline"]["stages"]),
        "",
        "| Ticket | Contact | Stage | Priority |",
        "|---|---|---|---|",
        *[f"| {t['subject'].split(' [')[0]} | `{t['contact']}` | {t['stage']} | {t['priority'].lower()} |"
          for t in spec["tickets"]],
    ]
    if evidence:
        lines += ["", "## Read back from HubSpot", "", "| Check | Result |", "|---|---|",
                  *[f"| {check} | {result} |" for check, result in evidence.get("checks", {}).items()],
                  "", f"Created {evidence.get('created_at', '')} through the HubSpot connector."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan the product, line-item and ticket build-out from the warehouse.")
    parser.add_argument("command", choices=("plan", "doc"))
    parser.add_argument("--database", default="data/growthops-sample.db")
    args = parser.parse_args()
    from growthops.db import connect

    connection = connect(args.database)
    try:
        spec = plan(connection)
    finally:
        connection.close()
    if args.command == "plan":
        print(json.dumps(spec, indent=2))
        return
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8")) if EVIDENCE.exists() else None
    DOC.write_text(render(spec, evidence), encoding="utf-8", newline="\n")
    print(f"wrote {DOC}")


if __name__ == "__main__":
    main()
