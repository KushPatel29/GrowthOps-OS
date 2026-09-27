"""The details a question names: its period, ad platform, campaign and measure."""

from datetime import date

import pytest

from growthops.ask_slots import parse, resolve_window

AS_OF, FIRST = date(2026, 9, 25), date(2025, 7, 1)


@pytest.mark.parametrize("question, start, end", [
    ("leads yesterday", "2026-09-25", "2026-09-25"),  # the last complete day, as the daily update calls it
    ("leads last week", "2026-09-19", "2026-09-25"),
    ("spend over the last 90 days", "2026-06-28", "2026-09-25"),
    ("net cash this month", "2026-09-01", "2026-09-25"),
    ("net cash last month", "2026-08-01", "2026-08-31"),
    ("MQLs this quarter", "2026-07-01", "2026-09-25"),
    ("deals last quarter", "2026-04-01", "2026-06-30"),
    ("refunds year to date", "2026-01-01", "2026-09-25"),
    ("net cash in August", "2026-08-01", "2026-08-31"),
    ("spend in December", "2025-12-01", "2025-12-31"),  # a month not yet reached this year is last year's
    ("leads for march 2026", "2026-03-01", "2026-03-31"),
    ("leads this week", "2026-09-21", "2026-09-25"),
    ("deals won all time", "2025-07-01", "2026-09-25"),
])
def test_windows_resolve_against_the_data_not_the_calendar(question, start, end):
    window = resolve_window(parse(question).window, AS_OF, FIRST)
    assert (window["start"].isoformat(), window["end"].isoformat()) == (start, end)


def test_a_window_before_the_data_is_none_and_a_partial_one_is_clipped():
    assert resolve_window(parse("revenue in 2019").window, AS_OF, FIRST) is None
    clipped = resolve_window(parse("net cash in 2025").window, AS_OF, FIRST)
    assert clipped["start"] == FIRST and clipped["clipped"]


@pytest.mark.parametrize("question", ["May we see the cost per lead", "mar the numbers", "how are leads"])
def test_ordinary_words_are_not_read_as_months(question):
    assert parse(question).window is None


@pytest.mark.parametrize("question, platform, measure, campaign", [
    ("What does a lead cost on Google?", "google", "cpl", None),
    ("What does a booked call cost on Meta?", "meta", "cost_per_booked_call", None),
    ("facebook CPM", "meta", "cpm", None),
    ("LinkedIn click-through rate", "linkedin", "ctr", None),
    ("cost per MQL for meta broad v17", "meta", "cost_per_mql", "meta_broad_v17"),
    ("cost per call for linkedin ceo abm", "linkedin", "cost_per_booked_call", "linkedin_ceo_abm"),
    ("how many deals did we close in July", None, "deals_won", None),
    ("how much cash did we bring in last month", None, "net_cash", None),
])
def test_platform_measure_and_campaign(question, platform, measure, campaign):
    slots = parse(question)
    assert (slots.platform, slots.measure, slots.campaign) == (platform, measure, campaign)


@pytest.mark.parametrize("question", ["TikTok cost per lead", "pinterest spend", "snapchat CPL", "reddit ads"])
def test_ad_platforms_that_are_not_bought_are_flagged(question):
    assert parse(question).untracked_platform
