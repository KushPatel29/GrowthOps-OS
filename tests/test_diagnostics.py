from datetime import date, timedelta

import pytest

from growthops.diagnostics import SPEC_BY_ID, decompose, detect, incident_recall


def test_shift_share_decomposition_is_exact_on_a_known_example():
    day0, day1 = date(2026, 1, 1), date(2026, 1, 2)
    cube = {
        day0: {"A": (30.0, 100.0), "B": (10.0, 100.0)},          # 20% overall
        day1: {"A": (30.0, 100.0), "B": (10.0, 100.0), "C": (2.0, 100.0)},  # new low-quality segment
    }
    split = decompose(cube, SPEC_BY_ID["mql_rate"], (day0, day0), (day1, day1))
    assert split["baseline_value"] == pytest.approx(0.2)
    assert split["current_value"] == pytest.approx(42 / 300)
    assert split["residual"] == pytest.approx(0, abs=1e-12)
    top = split["drivers"][0]
    assert top["segment"] == "C" and top["new_segment"]
    assert top["contribution"] == pytest.approx((1 / 3) * (0.02 - 0.2))


def test_detector_recovers_every_planted_incident_with_the_right_root_cause(connection):
    episodes = detect(connection)
    recall = incident_recall(connection, episodes)
    assert len(recall) == 2
    assert all(item["detected"] and item["root_cause_correct"] for item in recall), recall
    assert all(item["days_to_detect"] <= 7 for item in recall)
    for episode in episodes:
        assert episode["residual"] == pytest.approx(0, abs=1e-6)


def test_detector_ignores_tiny_but_significant_moves(connection):
    for episode in detect(connection):
        if SPEC_BY_ID[episode["metric_id"]].kind == "rate":
            assert abs(episode["change"]) >= 0.03
        else:
            assert abs(episode["change_pct"]) >= 0.10


def test_quiet_period_produces_no_quality_alarm(connection):
    early = detect(connection, as_of=date(2026, 4, 30), lookback_days=30)
    assert not [e for e in early if e["metric_id"] == "mql_rate"]
    assert date(2026, 4, 30) - timedelta(days=30) < date(2026, 8, 10)


def test_a_share_over_100_percent_is_said_in_words():
    from growthops.diagnostics import share_phrase

    assert share_phrase(0.92, of="the drop") == "92% of the drop"
    assert share_phrase(0.92, "-8.6 pp") == "92% (-8.6 pp)"
    assert share_phrase(1.01, "-7.6 pp") == "all of it (101%, -7.6 pp; the rest moved the other way)"
    assert share_phrase(1.004) == "100%"
