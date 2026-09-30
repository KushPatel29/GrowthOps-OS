"""The HubSpot portal build, run end to end against an in-memory fake of the HubSpot APIs it calls."""

import json

import pytest
from hubspot_fake import TOKEN, FakeHubSpot

from growthops.adapters import ProviderError
from growthops.hubspot_client import PortalRefused
from growthops.hubspot_portal import (
    PIPELINE_STAGES,
    Portal,
    apply,
    load_token,
    plan,
    portal_properties,
    records,
)


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
    with pytest.raises(PortalRefused, match="Refusing to write synthetic data to a STANDARD portal"):
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
