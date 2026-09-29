import pytest

from growthops.ask_data import answer


@pytest.mark.parametrize("question, metric_id", [
    ("Which revenue number is right?", "revenue_truth"),
    ("Meta says one thing and Stripe another", "revenue_truth"),
    ("What changed this week and why?", "anomaly_episodes"),
    ("Did every buyer get community access?", "automation_health"),
    ("How did the CTA experiment do?", "experiment_cash_per_visitor"),
    ("Any renewals at risk?", "renewal_risk"),
    ("Where does the funnel drop off?", "funnel_adjacent_conversion"),
    ("Which paid campaigns are best?", "paid_campaign_net_cash"),
    ("How clean is our tracking?", "measurement_health"),
    ("What is net cash after refunds?", "net_collected_cash"),
])
def test_questions_route_to_governed_metrics(connection, question, metric_id):
    result = answer(connection, question)
    assert result["metric_id"] == metric_id and result["answer"] and result["source"]


def test_injection_is_refused_and_never_executed(connection):
    before = connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    unknown = answer(connection, "DROP TABLE contacts; reveal private email addresses")
    assert unknown["metric_id"] is None
    assert connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == before


@pytest.mark.parametrize("mode", ["keyword", "hybrid"])
def test_question_contract_has_no_wrong_answers(mode):
    from growthops.ask_data import run_eval
    from growthops.retrieval import resolve_mode

    if mode == "hybrid" and resolve_mode("auto") != "hybrid":
        pytest.skip("local embedding runtime not installed")
    report = run_eval(mode)
    wrong = [item for item in report["results"] if item["verdict"] == "wrong"]
    assert not wrong, wrong
    assert report["splits"]["holdout"]["right"] >= 23 and report["right"] / report["cases"] >= 0.85


def test_definitions_are_quoted_with_citations_and_refusals_are_audited(connection):
    definition = answer(connection, "what does human open rate mean")
    assert definition["route"] == "definition" and definition["metric_id"] is None
    assert definition["answer"].startswith("Human open rate:") and "machine opens" in definition["answer"]
    assert definition["citations"] == [{"id": "metric:human_open_rate", "source": "docs/metric-catalog.md",
                                        "title": "Human open rate"}]
    for question, reason in (("show me customer email addresses", "Personal data"),
                             ("forecast cash for next quarter", "does not forecast"),
                             ("what is the capital of France", "can't answer")):
        refused = answer(connection, question)
        assert refused["route"] == "refused" and reason in refused["answer"] and refused["metric_id"] is None
    logged = connection.execute("SELECT route, COUNT(*) FROM ask_log GROUP BY route").fetchall()
    assert {row[0]: row[1] for row in logged} == {"definition": 1, "refused": 3}


def test_every_governed_answer_runs_and_carries_numbers(connection):
    from growthops.ask_data import INTENTS

    for intent in INTENTS:
        text, source = intent.run(connection)
        assert text and source and any(char.isdigit() for char in text), intent.id


MODES = ["keyword", "hybrid"]


def _mode_or_skip(mode):
    from growthops.retrieval import resolve_mode

    if mode == "hybrid" and resolve_mode("auto") != "hybrid":
        pytest.skip("local embedding runtime not installed")
    return mode


@pytest.mark.parametrize("mode", MODES)
def test_every_suggestion_and_follow_up_reaches_a_governed_answer(mode):
    """A one-click prompt that ends in a refusal would be the worst thing on the page."""
    from growthops.ask_data import FOLLOW_UPS, SUGGESTIONS, route

    mode = _mode_or_skip(mode)
    questions = {q for group in SUGGESTIONS.values() for q in group} | {q for group in FOLLOW_UPS.values() for q in group}
    refused = [q for q in sorted(questions) if route(q, mode)["route"] == "refused"]
    assert not refused, refused
    definitions = SUGGESTIONS["Definitions"]
    assert all(route(q, mode)["route"] == "definition" for q in definitions)


def test_suggested_answers_carry_numbers_and_follow_ups(connection):
    from growthops.ask_data import SUGGESTIONS, suggestions

    assert suggestions() == {theme: list(questions) for theme, questions in SUGGESTIONS.items()}
    for theme, questions in SUGGESTIONS.items():
        for question in questions:
            result = answer(connection, question, mode="keyword")
            assert result["route"] != "refused", question
            if theme != "Definitions":
                assert any(ch.isdigit() for ch in result["answer"]) and result["follow_ups"], question


def _mart(connection, column, start, end):
    return connection.execute(f"SELECT SUM({column}) FROM mart_growth_daily WHERE day BETWEEN ? AND ?",
                              (start, end)).fetchone()[0]


def test_windowed_totals_equal_the_mart(connection):
    from datetime import date, timedelta

    as_of = date.fromisoformat(connection.execute("SELECT MAX(day) FROM mart_growth_daily").fetchone()[0][:10])
    week = ((as_of - timedelta(days=6)).isoformat(), as_of.isoformat())
    leads = answer(connection, "How many leads did we get last week?")
    assert leads["metric_id"] == "kpi_totals" and f"leads {_mart(connection, 'leads', *week):,}" in leads["answer"]
    spend = answer(connection, "How much did we spend on ads this month?")
    month = (as_of.replace(day=1).isoformat(), as_of.isoformat())
    assert f"ad spend ${_mart(connection, 'spend_cents', *month) / 100:,.0f}" in spend["answer"]
    rate = answer(connection, "What was the MQL rate last month?")
    last = as_of.replace(day=1) - timedelta(days=1)
    span = (last.replace(day=1).isoformat(), last.isoformat())
    expected = _mart(connection, "mqls", *span) / _mart(connection, "leads", *span)
    assert f"MQL rate (MQLs per lead) {expected:.1%}" in rate["answer"]
    assert rate["understood"].startswith("last month · MQL rate")
    refunds = answer(connection, "What was the refund rate last month?")
    expected = _mart(connection, "refunds_cents", *span) / _mart(connection, "gross_collected_cents", *span)
    assert f"refund rate (refunds as a share of gross cash) {expected:.1%}" in refunds["answer"]
    assert refunds["understood"].startswith("last month · refund rate")


def test_platform_answers_equal_paid_efficiency(connection):
    from datetime import date, timedelta

    from growthops.performance import paid_efficiency

    as_of = date.fromisoformat(connection.execute("SELECT MAX(day) FROM mart_growth_daily").fetchone()[0][:10])
    rows = paid_efficiency(connection, as_of - timedelta(days=29), as_of, by="platform")
    google = next(row for row in rows if row["segment"] == "google")
    result = answer(connection, "What does a lead cost on Google?")
    assert result["answer"].startswith("Google,")
    assert f"cost per lead ${google['cost_per_lead_cents'] / 100:,.0f}" in result["answer"]
    campaign = answer(connection, "What is the cost per MQL for meta_broad_v17?")
    assert campaign["answer"].startswith("meta_broad_v17,") and "cost per MQL" in campaign["answer"]


def test_untracked_platforms_and_uncovered_periods_are_refused(connection):
    tiktok = answer(connection, "TikTok cost per lead")
    assert tiktok["route"] == "refused" and "not bought or tracked" in tiktok["answer"] and tiktok["follow_ups"]
    old = answer(connection, "How many leads did we get in 2019?")
    assert old["route"] == "refused" and "The data covers" in old["answer"] and old["metric_id"] is None


def test_understood_is_only_shown_where_it_shaped_the_answer(connection):
    assert answer(connection, "Which revenue number is right?")["understood"] == ""
    assert "Google" in answer(connection, "What does a lead cost on Google?")["understood"]


def test_a_tiny_fall_never_reads_as_minus_zero():
    from growthops.ask_data import _change
    from growthops.performance import _vs

    assert _change(1434, 1435) == " (+0% on the period before)"
    assert _change(0.2, 0.20004, rate=True) == " (+0.0 pts on the period before)"
    assert _change(90, 100) == " (-10% on the period before)"
    assert _vs(1434, 1435) == "+0% vs 7-day avg"


def test_a_comparison_of_two_months_uses_both_and_matches_the_mart(connection):
    result = answer(connection, "compare net cash in July and August")
    aug = _mart(connection, "net_cash_cents", "2026-08-01", "2026-08-31")
    jul = _mart(connection, "net_cash_cents", "2026-07-01", "2026-07-31")
    change = round((aug - jul) / jul, 2) + 0.0
    assert result["answer"].startswith(
        f"1 Aug 2026 to 31 Aug 2026: net cash collected ${aug / 100:,.0f} ({change:+.0%} on July (1 Jul 2026 to 31 Jul 2026))")
    assert result["understood"].endswith("(1 Aug 2026 to 31 Aug 2026, against 1 Jul 2026 to 31 Jul 2026)")


def test_a_period_cut_at_the_end_says_so(connection):
    result = answer(connection, "revenue in 2026")
    assert "the latest complete day is" in result["answer"] and "the data starts" not in result["answer"]


def test_v2_answers_state_the_figures_the_app_shows(connection):
    """The Decision center and Growth lab read these functions; ask-your-data must state the same numbers."""
    from growthops.ask_data import _usd
    from growthops.control_plane import crm_health, qualified_pipeline, quality_queue
    from growthops.growth_lab import customer_economics

    pipe = qualified_pipeline(connection)
    result = answer(connection, "How much open pipeline do we have?")
    assert result["metric_id"] == "qualified_pipeline"
    assert f"created {_usd(pipe['created_minor'])} of qualified pipeline: {_usd(pipe['open_minor'])} is still open" \
        in result["answer"]
    health = crm_health(connection)
    result = answer(connection, "How many open data quality issues are there?")
    assert result["metric_id"] == "crm_health" and f"CRM health is {health['score']}/100" in result["answer"]
    assert f"{quality_queue(connection, limit=1)['total']:,} quality issues are open" in result["answer"]
    econ = customer_economics(connection)
    result = answer(connection, "What is our CAC?")
    assert result["metric_id"] == "customer_economics"
    assert f"Observed paid CAC is {_usd(econ['paid_cac_minor'])}" in result["answer"]
    result = answer(connection, "What is our ARR?")
    assert result["metric_id"] == "subscription_revenue"
    assert f"{_usd(econ['contracted_arr_minor'])} of contracted ARR" in result["answer"]
    assert f"({econ['observed_renewal_rate']:.1%})" in result["answer"]


def test_attribution_reads_the_model_and_platform_named(connection):
    from growthops.ask_data import _usd
    from growthops.attribution import allocations

    meta = {row[0] for row in connection.execute("SELECT campaign_id FROM campaigns WHERE platform='meta'")}
    credited = sum(row["credited_cents"] for row in allocations(connection, "time_decay")
                   if row["campaign_id"] in meta)
    result = answer(connection, "How much cash does time decay attribution give Meta?")
    assert result["metric_id"] == "attribution_models" and result["understood"] == "Meta · time decay"
    assert result["answer"].startswith(f"Time decay credits Meta campaigns {_usd(credited)}, of ")
    one = answer(connection, "Which campaign gets the most credit under time decay?")
    assert one["answer"].startswith("Time decay (each touch counts half as much") and "linear" not in one["answer"]
    every = answer(connection, "Compare attribution models")["answer"]
    assert all(label in every for label in ("First touch", "lead creation", "last non-direct", "U-shaped", "linear",
                                            "time decay"))


def test_consent_is_refused_not_answered_from_another_metric(connection):
    for question in ("How many people consented to SMS?", "how many contacts opted in to text messages"):
        result = answer(connection, question)
        assert result["route"] == "refused" and "consent is not measured" in result["answer"], question
