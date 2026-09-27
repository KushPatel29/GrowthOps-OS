import random

from growthops.experiments import _bootstrap_mean_difference, analyze


def test_experiment_reads_quality_and_cash_not_just_lead_rate(connection):
    result = analyze(connection, "cta_growth_plan")
    a, b = result["variants"]
    comparison = result["comparison"]
    assert min(a["visitors"], b["visitors"]) > 5000
    assert comparison["sample_ratio_p_value"] > 0.01  # assignment is balanced
    assert b["lead_rate"] > a["lead_rate"] and comparison["lead_rate_p_value"] < 0.05
    assert b["mql_per_lead"] < a["mql_per_lead"]
    lo, hi = comparison["cash_per_visitor_bootstrap_95_ci_cents"]
    assert lo <= comparison["variant_b_minus_a_cash_per_visitor_cents"] <= hi
    assert comparison["cash_ci_includes_zero"] == (lo <= 0 <= hi)
    assert comparison["decision"].startswith("Do not ship on lead rate alone")
    exposed_cash = connection.execute(
        """SELECT COALESCE(SUM(p.amount_cents),0) - COALESCE((SELECT SUM(r.amount_cents) FROM refunds r
               JOIN payments q ON q.payment_id=r.payment_id
               WHERE q.customer_id IN (SELECT contact_id FROM experiment_exposures)),0)
           FROM payments p WHERE p.status='succeeded'
             AND p.customer_id IN (SELECT contact_id FROM experiment_exposures)""").fetchone()[0]
    assert a["net_cash_cents"] + b["net_cash_cents"] == exposed_cash


def test_fast_bootstrap_matches_a_naive_bootstrap():
    rng = random.Random(3)
    a = [rng.choice([0] * 40 + [100, 250]) for _ in range(400)]
    b = [rng.choice([0] * 35 + [100, 250]) for _ in range(400)]
    fast = _bootstrap_mean_difference(a, b, 4000)
    naive = sorted(sum(rng.choice(b) for _ in b) / len(b) - sum(rng.choice(a) for _ in a) / len(a)
                   for _ in range(4000))
    for q in (0.025, 0.5, 0.975):
        assert abs(fast[int(q * 4000)] - naive[int(q * 4000)]) < 1.5


def test_p_values_read_as_a_reader_expects():
    from growthops.experiments import format_p

    assert format_p(0.0) == "p < 0.001"
    assert format_p(0.0042) == "p = 0.004"
    assert format_p(0.9305) == "p = 0.93"
    assert format_p(None) == "p n/a"
