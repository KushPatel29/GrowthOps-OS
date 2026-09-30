"""The product, line-item and support build-out is derived from the warehouse, deterministically."""

from pathlib import Path

from growthops.hubspot_buildout import DOC, plan, render
from growthops.hubspot_portal import records


def test_the_buildout_plan_is_the_warehouse_catalog_deals_and_support_cases(connection):
    spec = plan(connection)
    assert spec == plan(connection)  # deterministic
    products = {p["hs_sku"]: p for p in spec["products"]}
    assert len(products) == connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    assert products["community"]["recurringbillingfrequency"] == "annually"
    assert all("recurringbillingfrequency" not in p for sku, p in products.items() if sku != "community")
    deals = {d["growthops_deal_id"]: d for d in records(connection)["deals"]}
    assert {item["deal"] for item in spec["line_items"]} == set(deals)
    for item in spec["line_items"]:  # HubSpot recalculates deal amounts from line items: they must agree
        assert item["properties"]["price"] == products[item["sku"]]["price"]
        assert abs(float(item["properties"]["price"]) - float(deals[item["deal"]]["amount"])) < 0.005
    assert spec["summary"]["line_item_value"] == spec["summary"]["deal_value"]
    keys = [t["key"] for t in spec["tickets"]]
    assert len(keys) == len(set(keys)) and all(f"[{t['key']}]" in t["subject"] for t in spec["tickets"])
    sample = {c["growthops_contact_id"] for c in records(connection)["contacts"]}
    assert all(t["contact"] in sample for t in spec["tickets"])
    assert {t["key"].split(":")[0] for t in spec["tickets"]} == {"access", "refund", "renewal"}


def test_the_committed_buildout_document_matches_the_plan(connection):
    committed = (Path(__file__).resolve().parents[1] / DOC).read_text(encoding="utf-8")
    assert committed.startswith(render(plan(connection), None).rstrip("\n")), \
        "run python -m growthops.hubspot_buildout doc"


def test_every_test_the_production_document_cites_exists():
    import re

    root = Path(__file__).resolve().parents[1]
    cited = set(re.findall(r"`(test_[a-z0-9_]+)`", (root / "docs/hubspot-production.md").read_text(encoding="utf-8")))
    defined = {name for path in (root / "tests").glob("test_*.py")
               for name in re.findall(r"^def (test_[a-z0-9_]+)", path.read_text(encoding="utf-8"), re.MULTILINE)}
    assert cited and cited <= defined, sorted(cited - defined)
