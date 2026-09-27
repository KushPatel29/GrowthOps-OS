"""Every headline figure in the README is recomputed from the code, so the README cannot drift from the data.

The case study is regenerated and compared in full (test_case_study.py); the README quotes the same figures in
shorter form, and this test holds those quotes to the same numbers.
"""

import json
from pathlib import Path

import pytest

from growthops.campaign_links import audit_short_links
from growthops.diagnostics import detect, incident_recall
from growthops.email_analytics import deliverability, email_performance
from growthops.experiments import analyze as experiment_analysis
from growthops.experiments import format_p
from growthops.reconciliation import four_numbers, platform_comparison
from growthops.report import executive_brief

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _millions(cents: int) -> str:
    return f"${cents / 100 / 1e6:.2f}M"


def _thousands(cents: int) -> str:
    return f"${cents / 100 / 1e3:.0f}K"


@pytest.fixture(scope="module")
def figures(sample_path):
    from growthops.db import connect

    connection = connect(sample_path)
    try:
        kpis = executive_brief(connection)["metrics"]
        quality = executive_brief(connection)["measurement_health"]
        truth = four_numbers(connection)
        meta = {row["platform"]: row for row in platform_comparison(connection)}["meta"]
        episodes = detect(connection)
        recall = incident_recall(connection, episodes)
        by_id = {e["episode_id"]: e for e in episodes}
        quality_episode = by_id[next(r["episode_id"] for r in recall if r["incident_id"] == "inc_meta_broad_quality")]
        utm_episode = by_id[next(r["episode_id"] for r in recall if r["incident_id"] == "inc_webinar_utm_break")]
        # The lead surge the same campaign caused: the upward leads episode driven by meta_broad_v17.
        lead_surge = next(e for e in episodes if e["metric_id"] == "leads" and e["direction"] == "up"
                          and e["top_drivers"][0]["segment"] == quality_episode["top_drivers"][0]["segment"])
        experiment = experiment_analysis(connection, "cta_growth_plan")
        email_check = deliverability(connection)
        new_domain = email_check["flagged_domains"][0]
        email_now = next(r for r in email_check["recent_by_domain"] if r["sending_domain"] == new_domain)
        return {
            "kpis": kpis, "quality": quality, "truth": truth, "meta": meta, "recall": recall,
            "quality_episode": quality_episode, "utm_episode": utm_episode, "lead_surge": lead_surge,
            "experiment": experiment, "email_now": email_now, "email_base": email_check["baseline_by_domain"][0],
            "promos": email_performance(connection, "promo"), "links": audit_short_links(connection),
            # The support queue behind /ops/paid-without-access: paid for a new product, no entitlement.
            "stuck": connection.execute(
                """SELECT COUNT(DISTINCT p.customer_id) FROM payments p
                   LEFT JOIN access_entitlements a ON a.customer_id = p.customer_id
                   WHERE p.status = 'succeeded' AND p.payment_type = 'new' AND a.customer_id IS NULL""").fetchone()[0],
        }
    finally:
        connection.close()


def test_scale_of_the_scenario(figures):
    kpis = figures["kpis"]
    assert (f"({kpis['leads'] / 1000:.1f}K contacts, {kpis['customers']} customers, "
            f"{_thousands(kpis['spend_cents'])} of ad spend,\n{_millions(kpis['net_collected_cents'])} net cash)") in README


def test_which_revenue_number_is_right(figures):
    truth, meta = figures["truth"], figures["meta"]
    claimed = sum(truth["platform_reported_cents"].values())
    assert (f"Platforms claim {_millions(claimed)}; the CRM books {_millions(truth['crm_booked_cents'])}; "
            f"{_millions(truth['net_collected_cents'])} was collected.") in README
    assert (f"Meta reports {meta['platform_roas']:.1f}× ROAS; on net cash it is {meta['warehouse_roas']:.1f}×.") in README


def test_what_changed_and_why(figures):
    episode, surge = figures["quality_episode"], figures["lead_surge"]
    assert (f"lifted leads {surge['change_pct']:.0%} while the MQL rate fell from {episode['baseline_text']} to "
            f"{episode['current_text']}; it explains {episode['top_drivers'][0]['share_of_change']:.0%} of the drop.") in README
    assert all(r["detected"] and r["root_cause_correct"] and r["days_to_detect"] <= 3 for r in figures["recall"])
    assert "both planted incidents with the correct root cause within three days" in README


def test_tracking(figures):
    utm = figures["utm_episode"]
    assert (f"completeness fell to {utm['current_text']} and "
            f"{_thousands(figures['quality']['unassigned_net_cash_cents'])} of cash lost its campaign") in README


def test_access_email_links_and_experiment(figures):
    assert f"{figures['stuck']} launch-day buyers paid but have no community access" in README
    now, base = figures["email_now"], figures["email_base"]
    assert (f"bounce rate {now['bounce_rate']:.1%} and complaints {now['complaint_rate']:.2%}") in README
    assert f"human opens fell from {base['human_open_rate']:.0%} to {now['human_open_rate']:.0%}" in README
    words = {3: "three", 4: "four", 5: "five"}
    assert f"all {words[len(figures['promos'])]} enrollment-deadline promos went out on it" in README
    links = figures["links"]
    assert (f"{links['links_with_issues']} of {len(links['links'])} short links have missing, unregistered or "
            f"off-taxonomy UTMs, and they carry {links['share_of_recent_clicks_broken']:.0%}") in README
    experiment = figures["experiment"]
    a, b = experiment["variants"]
    lift = b["lead_rate"] / a["lead_rate"] - 1
    assert (f"It lifts lead rate {lift:.0%} ({format_p(experiment['comparison']['lead_rate_p_value'])}), but cash per "
            f"visitor rests on {a['customers'] + b['customers']} buyers") in README


def test_bi_and_ask_counts_match_their_sources():
    from growthops.bi.model_spec import MEASURES
    from growthops.bi.report_spec import PAGES

    assert f"a {len(PAGES)}-page Power BI report ({len(MEASURES)} described measures" in README
    cases = json.loads((ROOT / "evals/ask_questions.json").read_text(encoding="utf-8"))["cases"]
    assert f"a {len(cases)}-question contract" in README
