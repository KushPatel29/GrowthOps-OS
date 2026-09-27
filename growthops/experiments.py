"""Visitor-level experiment analysis with a cash guardrail and honest uncertainty."""

from __future__ import annotations

import math
import random
import sqlite3
from statistics import NormalDist


def format_p(p: float | None) -> str:
    """A p-value for a reader: "p < 0.001" rather than a rounded "p = 0.0"."""
    if p is None:
        return "p n/a"
    if p < 0.001:
        return "p < 0.001"
    return f"p = {p:.3f}" if p < 0.01 else f"p = {p:.2f}"


def _bootstrap_mean_difference(a: list[int], b: list[int], draws: int, seed: int = 29) -> list[float]:
    """Exact multinomial bootstrap of mean(b) - mean(a), fast for zero-inflated cash.

    Resampling n values with replacement is equivalent to drawing how many
    non-zero values appear (binomial) and then which ones (uniform), so the cost
    scales with the number of paying visitors rather than all visitors.
    """
    rng = random.Random(seed)

    def resample_mean(values: list[int]) -> float:
        nonzero = [value for value in values if value]
        if not values:
            return 0.0
        k = rng.binomialvariate(len(values), len(nonzero) / len(values)) if nonzero else 0
        return sum(rng.choice(nonzero) for _ in range(k)) / len(values)

    return sorted(resample_mean(b) - resample_mean(a) for _ in range(draws))


def analyze(connection: sqlite3.Connection, experiment_id: str, bootstrap_draws: int = 2000) -> dict:
    experiment = connection.execute(
        "SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)
    ).fetchone()
    if experiment is None:
        raise LookupError("experiment not found")
    variants = connection.execute(
        "SELECT * FROM experiment_variants WHERE experiment_id=? ORDER BY variant_id", (experiment_id,)
    ).fetchall()
    exposures = connection.execute(
        "SELECT variant_id, contact_id FROM experiment_exposures WHERE experiment_id=?",
        (experiment_id,),
    ).fetchall()
    mql_people = {row[0] for row in connection.execute(
        "SELECT DISTINCT contact_id FROM lifecycle_events WHERE stage='mql'"
    )}
    payments = connection.execute(
        """SELECT p.customer_id, SUM(p.amount_cents-COALESCE(r.refund_cents,0)) net_cash_cents
           FROM payments p LEFT JOIN (
             SELECT payment_id, SUM(amount_cents) refund_cents FROM refunds GROUP BY payment_id
           ) r ON r.payment_id=p.payment_id
           WHERE p.status='succeeded' GROUP BY p.customer_id"""
    ).fetchall()
    cash_by_person = {row["customer_id"]: row["net_cash_cents"] for row in payments}
    rows = []
    cash_samples = {}
    for variant in variants:
        people = [row["contact_id"] for row in exposures if row["variant_id"] == variant["variant_id"]]
        visitors = len(people)
        leads = sum(person is not None for person in people)
        mqls = sum(person in mql_people for person in people if person is not None)
        customers = sum(person in cash_by_person for person in people if person is not None)
        values = [cash_by_person.get(person, 0) for person in people]
        cash_samples[variant["variant_id"]] = values
        net_cash = sum(values)
        rows.append({
            "variant_id": variant["variant_id"], "label": variant["label"],
            "cta_text": variant["cta_text"], "visitors": visitors, "leads": leads,
            "mqls": mqls, "customers": customers, "net_cash_cents": net_cash,
            "lead_rate": round(leads / visitors, 4) if visitors else None,
            "mql_per_lead": round(mqls / leads, 4) if leads else None,
            "net_cash_per_visitor_cents": round(net_cash / visitors, 2) if visitors else None,
        })
    comparison = None
    if len(rows) == 2 and all(row["visitors"] for row in rows):
        a, b = rows
        normal = NormalDist()
        pa, pb = a["leads"] / a["visitors"], b["leads"] / b["visitors"]
        difference = pb - pa
        standard_error = math.sqrt(pa * (1 - pa) / a["visitors"] + pb * (1 - pb) / b["visitors"])
        pooled = (a["leads"] + b["leads"]) / (a["visitors"] + b["visitors"])
        null_se = math.sqrt(pooled * (1 - pooled) * (1 / a["visitors"] + 1 / b["visitors"]))
        p_value = 2 * (1 - normal.cdf(abs(difference / null_se))) if null_se else None
        qa = a["mqls"] / a["leads"] if a["leads"] else 0
        qb = b["mqls"] / b["leads"] if b["leads"] else 0
        quality_se = math.sqrt(qa * (1 - qa) / max(a["leads"], 1) + qb * (1 - qb) / max(b["leads"], 1))
        total = a["visitors"] + b["visitors"]
        srm_z = (a["visitors"] - total / 2) / math.sqrt(total / 4)
        srm_p = 2 * (1 - normal.cdf(abs(srm_z)))
        draws = _bootstrap_mean_difference(cash_samples[a["variant_id"]], cash_samples[b["variant_id"]],
                                           bootstrap_draws)
        cash_difference = b["net_cash_cents"] / b["visitors"] - a["net_cash_cents"] / a["visitors"]
        lower = draws[int(0.025 * bootstrap_draws)]
        upper = draws[min(bootstrap_draws - 1, int(0.975 * bootstrap_draws))]
        cash_ci_includes_zero = lower <= 0 <= upper
        lead_significant = p_value is not None and p_value < 0.05
        quality_interval = (qb - qa - 1.96 * quality_se, qb - qa + 1.96 * quality_se)
        buyers = a["customers"] + b["customers"]
        if srm_p < 0.01:
            decision = "Sample ratio mismatch: fix assignment before reading any result."
        elif not cash_ci_includes_zero and cash_difference > 0:
            decision = "Ship B: it raises cash per visitor with an interval above zero."
        elif not cash_ci_includes_zero and cash_difference < 0:
            decision = "Keep A: B lowers cash per visitor with an interval below zero."
        elif lead_significant and difference > 0 and quality_interval[1] < 0:
            decision = ("Keep A: B lifts lead rate but lowers lead quality, and its cash effect is not "
                        "distinguishable from zero.")
        elif lead_significant and difference > 0:
            decision = (f"Do not ship on lead rate alone: B lifts leads {difference / pa:.0%}, but cash per visitor "
                        f"rests on {buyers} buyers and its interval spans zero. Keep A and extend the test "
                        "until the cash interval is decisive.")
        elif lead_significant and difference < 0:
            decision = "Keep A: B lowers lead rate and shows no cash benefit."
        else:
            decision = "No decision: neither lead rate nor cash per visitor moved beyond noise."
        comparison = {
            "variant_b_minus_a_lead_rate": round(difference, 4),
            "lead_rate_relative_lift": round(difference / pa, 4) if pa else None,
            "lead_rate_difference_95_ci": [round(difference - 1.96 * standard_error, 4),
                                           round(difference + 1.96 * standard_error, 4)],
            "lead_rate_p_value": round(p_value, 4) if p_value is not None else None,
            "variant_b_minus_a_mql_per_lead": round(qb - qa, 4),
            "mql_per_lead_difference_95_ci": [round(value, 4) for value in quality_interval],
            "variant_b_minus_a_cash_per_visitor_cents": round(cash_difference, 2),
            "cash_per_visitor_bootstrap_95_ci_cents": [round(lower, 2), round(upper, 2)],
            "cash_ci_includes_zero": cash_ci_includes_zero,
            "sample_ratio_p_value": round(srm_p, 4),
            "buyers": buyers,
            "decision": decision,
        }
    return {
        "experiment_id": experiment_id,
        "hypothesis": experiment["hypothesis"],
        "assignment_unit": experiment["assignment_unit"],
        "primary_metric": experiment["primary_metric"],
        "synthetic": True,
        "variants": rows,
        "comparison": comparison,
    }
