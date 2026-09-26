from growthops.db import connect
from growthops.experiments import analyze
from growthops.report import metrics
from growthops.seed import seed


def test_experiment_uses_business_guardrail(tmp_path):
    database = tmp_path / "experiment.db"
    seed(str(database))
    connection = connect(database)
    result = analyze(connection, "cta_growth_plan", bootstrap_draws=300)
    a, b = result["variants"]
    assert a["visitors"] == b["visitors"] == 500
    assert a["leads"] == 100
    assert b["leads"] == 140
    assert b["mql_per_lead"] < a["mql_per_lead"]
    assert b["net_cash_per_visitor_cents"] < a["net_cash_per_visitor_cents"]
    assert a["net_cash_cents"] + b["net_cash_cents"] == metrics(connection)["net_collected_cents"]
    assert result["comparison"]["variant_b_minus_a_lead_rate"] > 0
    assert result["comparison"]["variant_b_minus_a_cash_per_visitor_cents"] < 0
    connection.close()
