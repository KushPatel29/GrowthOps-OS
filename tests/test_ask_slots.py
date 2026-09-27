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
    # Named dates, ranges, "since", quarters, halves and years.
    ("net cash on 3 September", "2026-09-03", "2026-09-03"),
    ("leads on September 3rd 2025", "2025-09-03", "2025-09-03"),
    ("revenue since August", "2026-08-01", "2026-09-25"),
    ("spend since the start of August", "2026-08-01", "2026-09-25"),
    ("spend August onwards", "2026-08-01", "2026-09-25"),
    ("revenue between July and August", "2026-07-01", "2026-08-31"),
    ("revenue between November and February", "2025-11-01", "2026-02-28"),  # the range runs forwards
    ("net cash from the start of June to the end of July", "2026-06-01", "2026-07-31"),
    ("revenue from 1 Aug 2025 to 15 Aug", "2025-08-01", "2025-08-15"),  # the named year carries across
    ("leads from 2025-12-01 to 2026-01-15", "2025-12-01", "2026-01-15"),
    ("spend in Q2", "2026-04-01", "2026-06-30"),
    ("spend in the fourth quarter of 2025", "2025-10-01", "2025-12-31"),
    ("revenue in the first half of 2026", "2026-01-01", "2026-06-30"),
    ("revenue 2026", "2026-01-01", "2026-09-25"),
    ("leads the week before last", "2026-09-12", "2026-09-18"),
    ("calls booked in the last fortnight", "2026-09-12", "2026-09-25"),
    ("leads last calendar week", "2026-09-14", "2026-09-20"),  # Monday to Sunday; "last week" stays rolling
])
def test_windows_resolve_against_the_data_not_the_calendar(question, start, end):
    window = resolve_window(parse(question).window, AS_OF, FIRST)
    assert (window["start"].isoformat(), window["end"].isoformat()) == (start, end)


def test_a_window_before_the_data_is_none_and_a_partial_one_is_clipped():
    assert resolve_window(parse("revenue in 2019").window, AS_OF, FIRST) is None
    clipped = resolve_window(parse("net cash in 2025").window, AS_OF, FIRST)
    assert clipped["start"] == FIRST and clipped["clipped"]


@pytest.mark.parametrize("question, phrase", [("leads on 30 February", "30 February"),
                                              ("leads on 2026-13-01", "2026-13-01")])
def test_an_impossible_date_is_kept_so_it_can_be_refused(question, phrase):
    window = parse(question).window
    assert window == {"kind": "invalid", "phrase": phrase} and resolve_window(window, AS_OF, FIRST) is None


def test_on_after_a_month_names_a_platform_not_a_start():
    assert parse("spend in august on meta").window["kind"] == "month"


def test_two_platforms_named_together_are_a_comparison():
    slots = parse("meta cpl vs google cpl")
    assert slots.platform is None and set(slots.platforms) == {"meta", "google"} and slots.measure == "cpl"
    assert slots.describe() == "Meta vs Google · cost per lead"


@pytest.mark.parametrize("question", ["May we see the cost per lead", "mar the numbers", "how are leads",
                                      "may I see revenue from google", "campaigns with spend over 2000"])
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


@pytest.mark.parametrize("question, answer_span, against_span", [
    ("compare revenue in July and August", ("2026-08-01", "2026-08-31"), ("2026-07-01", "2026-07-31")),
    ("net cash August vs July", ("2026-08-01", "2026-08-31"), ("2026-07-01", "2026-07-31")),
    ("leads December vs January", ("2026-01-01", "2026-01-31"), ("2025-12-01", "2025-12-31")),
])
def test_two_named_periods_compare_the_later_with_the_earlier(question, answer_span, against_span):
    window = resolve_window(parse(question).window, AS_OF, FIRST)
    assert (window["start"].isoformat(), window["end"].isoformat()) == answer_span
    other = window["compare_with"]
    assert (other["start"].isoformat(), other["end"].isoformat()) == against_span


def test_a_range_is_not_a_comparison():
    assert parse("revenue between July and August").window["kind"] == "span"
    assert resolve_window(parse("compare leads in June 2025 and July 2025").window, AS_OF, FIRST) is None
