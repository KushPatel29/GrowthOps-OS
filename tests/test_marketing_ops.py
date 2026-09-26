import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from growthops import scenario as sc
from growthops.api import app
from growthops.brief import findings
from growthops.campaign_links import audit_short_links
from growthops.email_analytics import deliverability, email_performance, list_source_mix, newsletter_pipeline
from growthops.hubspot import (LIFECYCLE_ORDER, audit as hubspot_audit, export as hubspot_export,
                               parse_search_response, property_definitions, search_request, source_mismatches)
from growthops.performance import daily_update, paid_efficiency
from growthops.report import metrics

FIXTURE = Path(__file__).parent / "fixtures" / "hubspot_contacts_search.json"


def test_email_rates_are_bounded_and_privacy_opens_are_separated(connection):
    sends = email_performance(connection)
    assert {row["email_type"] for row in sends} == {"newsletter", "webinar_invite", "promo", "nurture"}
    for row in sends:
        assert row["delivered"] + row["bounces"] == row["sends"]
        assert 0 <= row["human_open_rate"] <= row["reported_open_rate"] <= 1
        assert row["clicks"] <= row["human_opens"]
    # Machine opens inflate the reported open rate by well over ten points.
    newsletter = [row for row in sends if row["email_type"] == "newsletter"]
    assert all(row["reported_open_rate"] - row["human_open_rate"] > 0.1 for row in newsletter)
    assert len([row for row in sends if row["email_type"] == "promo"]) == len(sc.PROMO_DATES)


def test_deliverability_check_recovers_the_planted_domain_switch(connection):
    check = deliverability(connection)
    assert check["flagged_domains"] == [sc.NEW_EMAIL_DOMAIN]
    assert min(row["sent_date"] for row in check["affected_emails"]) == sc.EMAIL_DOMAIN_SWITCH.isoformat()
    recent = {row["sending_domain"]: row for row in check["recent_by_domain"]}
    assert recent[sc.NEW_EMAIL_DOMAIN]["bounce_rate"] > 0.02 > recent[sc.EMAIL_DOMAIN]["bounce_rate"]
    # Before the switch nothing is flagged, so the check is not simply always on.
    assert deliverability(connection, date(2026, 8, 31))["flagged_domains"] == []
    item = next(f for f in findings(connection) if f["id"] == "email_deliverability")
    assert sc.NEW_EMAIL_DOMAIN in item["finding"] and "promos" in item["finding"]


def test_newsletter_pipeline_credits_each_newsletter_lead_once(connection):
    issues = newsletter_pipeline(connection)
    first = issues[0]["sent_date"]
    expected = connection.execute(
        """SELECT COUNT(*) FROM (SELECT contact_id, campaign_id, occurred_at, ROW_NUMBER() OVER
           (PARTITION BY contact_id ORDER BY occurred_at DESC, touch_id DESC) rn FROM touches
           WHERE touch_type='lead_creation') WHERE rn=1 AND campaign_id='newsletter_weekly'
           AND occurred_at >= (SELECT MIN(sent_at) FROM email_campaigns WHERE email_type='newsletter')"""
    ).fetchone()[0]
    assert sum(row["leads"] for row in issues) == expected and first == sc.START.isoformat()
    assert all(row["customers"] <= row["calls_booked"] + row["customers"] <= row["leads"] + row["customers"]
               for row in issues)
    mix = list_source_mix(connection)
    assert abs(sum(row["share"] for row in mix) - 1) < 0.001


def test_link_audit_finds_exactly_the_planted_defects(connection):
    audit = audit_short_links(connection)
    broken = {link["link_id"]: link["issues"] for link in audit["links"] if link["issues"]}
    assert set(broken) == {"ig-bio", "pod-ep41", "yt-q3-guide", "li-launch"}
    assert broken["ig-bio"] == ["missing utm_source, utm_medium, utm_campaign"]
    assert broken["pod-ep41"] == ["utm_source 'Podcast' should be 'partner'"]
    assert broken["yt-q3-guide"] == ["campaign 'youtube_q3_guide' is not registered"]
    assert 0 < audit["share_of_recent_clicks_broken"] < 1


def test_paid_efficiency_ties_to_governed_metrics(connection):
    rows = paid_efficiency(connection, sc.START, sc.AS_OF)
    total, kpis = rows[-1], metrics(connection)
    assert total["segment"] == "Total paid"
    assert total["spend_cents"] == kpis["spend_cents"]
    assert total["leads"] == kpis["paid_leads"]
    assert total["cost_per_lead_cents"] == kpis["cost_per_lead_cents"]
    assert abs(total["net_cash_roas"] - kpis["net_cash_roas"]) < 0.01
    by_campaign = {row["segment"]: row for row in rows[:-1]}
    assert by_campaign["meta_broad_v17"]["cost_per_booked_call_cents"] > \
        2 * by_campaign["meta_prospecting_founder"]["cost_per_booked_call_cents"]
    platforms = paid_efficiency(connection, sc.START, sc.AS_OF, by="platform")
    assert sum(row["spend_cents"] for row in platforms[:-1]) == total["spend_cents"]
    for row in platforms[:-1]:
        assert row["cpm_cents"] == round(row["spend_cents"] * 1000 / row["impressions"])


def test_daily_update_is_complete_and_matches_the_daily_mart(connection):
    update = daily_update(connection)
    day = connection.execute("SELECT * FROM mart_growth_daily WHERE day=?", (update["day"],)).fetchone()
    assert update["yesterday"]["leads"] == day["leads"] and update["yesterday"]["mqls"] == day["mqls"]
    text = update["text"]
    assert f"{day['leads']} leads" in text and "None" not in text and "nan" not in text.lower()
    assert "paying customers have no community access" in text
    assert "short links have UTM defects" in text and sc.NEW_EMAIL_DOMAIN in text
    assert len(text.splitlines()) <= 25


def test_hubspot_mapping_exports_valid_import_files(connection, tmp_path):
    counts = hubspot_export(connection, tmp_path)
    audit = hubspot_audit(connection)
    assert counts["contacts"] == audit["contacts_after_email_dedupe"] == audit["contact_rows"] - audit["rows_merged_on_email"]
    with (tmp_path / "hubspot_contacts.csv").open(encoding="utf-8") as file:
        contacts = list(csv.DictReader(file))
    emails = [row["email"] for row in contacts]
    assert len(emails) == len(set(emails)) and all(email == email.strip().lower() for email in emails)
    assert {row["lifecyclestage"] for row in contacts} <= set(LIFECYCLE_ORDER)
    definitions = json.loads((tmp_path / "hubspot_properties.json").read_text())
    assert definitions == property_definitions(connection)
    enum = next(p for p in definitions["contacts"] if p["name"] == "growthops_original_source")
    allowed = {option["value"] for option in enum["options"]}
    assert "FB" not in allowed and {row["growthops_original_source"] for row in contacts} <= allowed | {""}
    with (tmp_path / "hubspot_deals.csv").open(encoding="utf-8") as file:
        deals = list(csv.DictReader(file))
    assert {row["dealstage"] for row in deals} <= {"presentationscheduled", "closedwon", "closedlost"}
    assert {row["contact_email"] for row in deals} <= set(emails)  # every deal can be associated
    assert audit["lifecycle_values_valid"] and audit["paying_contacts_not_customer"] > 0


def test_hubspot_search_round_trip_and_source_mismatch(connection):
    body = search_request(datetime(2026, 9, 1, tzinfo=timezone.utc), after="100")
    assert body["filterGroups"][0]["filters"][0] == {
        "propertyName": "lastmodifieddate", "operator": "GTE", "value": "1788220800000"}
    assert body["limit"] == 100 and body["after"] == "100"
    parsed = parse_search_response(json.loads(FIXTURE.read_text()))
    first = parsed["contacts"][0]
    assert first["email"] == "person2@synthetic.scalelab.test" and first["owner_id"] == "owner-chloe"
    assert first["current_stage"] == "customer" and parsed["next_after"] == "3"
    assert {problem["issue"] for problem in parsed["problems"]} == {
        "lifecycle stage 'salesqualifiedlead' has no internal mapping", "owner 99999999 is not in the owner map"}
    mismatches = source_mismatches(connection, parsed["contacts"])
    assert mismatches == [{"hubspot_id": "51002", "contact_id": "c-000003", "lead_campaign": "google_nonbrand_growth",
                           "expected": "PAID_SEARCH", "hubspot_says": "OFFLINE"}]


def test_marketing_ops_endpoints(db_path, monkeypatch):
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    with TestClient(app) as client:
        update = client.get("/metrics/daily-update").json()
        assert update["day"] == sc.AS_OF.isoformat() and update["text"].startswith("Daily performance update")
        paid = client.get("/metrics/paid-efficiency", params={"days": 7, "by": "platform"}).json()
        assert paid[-1]["segment"] == "Total paid" and {"cpm_cents", "ctr", "cost_per_booked_call_cents"} <= set(paid[0])
        assert client.get("/metrics/paid-efficiency", params={"by": "ad_set"}).status_code == 422
        email = client.get("/metrics/email").json()
        assert email["deliverability"]["flagged_domains"] == [sc.NEW_EMAIL_DOMAIN]
        assert client.get("/metrics/link-hygiene").json()["links_with_issues"] == 4
        assert client.get("/crm/hubspot/audit").json()["audit"]["lifecycle_values_valid"] is True
