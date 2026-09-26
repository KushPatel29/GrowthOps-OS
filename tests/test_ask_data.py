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
