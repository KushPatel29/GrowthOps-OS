"""Visitor-level experiment analysis with revenue guardrail and uncertainty."""

from __future__ import annotations

import math
import random
import sqlite3
from statistics import NormalDist


def analyze(connection: sqlite3.Connection, experiment_id: str, bootstrap_draws: int = 1000) -> dict:
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
        pa, pb = a["leads"] / a["visitors"], b["leads"] / b["visitors"]
        difference = pb - pa
        standard_error = math.sqrt(pa * (1 - pa) / a["visitors"] + pb * (1 - pb) / b["visitors"])
        lead_interval = (difference - 1.96 * standard_error, difference + 1.96 * standard_error)
        pooled = (a["leads"] + b["leads"]) / (a["visitors"] + b["visitors"])
        null_se = math.sqrt(pooled * (1 - pooled) * (1 / a["visitors"] + 1 / b["visitors"]))
        p_value = 2 * (1 - NormalDist().cdf(abs(difference / null_se))) if null_se else None
        rng = random.Random(29)
        a_cash = cash_samples[a["variant_id"]]
        b_cash = cash_samples[b["variant_id"]]
        draws = sorted(
            sum(rng.choice(b_cash) for _ in b_cash) / len(b_cash)
            - sum(rng.choice(a_cash) for _ in a_cash) / len(a_cash)
            for _ in range(bootstrap_draws)
        )
        cash_difference = b["net_cash_cents"] / b["visitors"] - a["net_cash_cents"] / a["visitors"]
        lower = draws[int(0.025 * bootstrap_draws)]
        upper = draws[min(bootstrap_draws - 1, int(0.975 * bootstrap_draws))]
        if difference > 0 and cash_difference < 0:
            decision = "B raised lead conversion but lowered cash per visitor. Keep A pending a larger revenue sample."
        else:
            decision = "Review the primary cash metric and confidence interval before changing the CTA."
        comparison = {
            "variant_b_minus_a_lead_rate": round(difference, 4),
            "lead_rate_difference_95_ci": [round(value, 4) for value in lead_interval],
            "lead_rate_p_value": round(p_value, 4) if p_value is not None else None,
            "variant_b_minus_a_cash_per_visitor_cents": round(cash_difference, 2),
            "cash_per_visitor_bootstrap_95_ci_cents": [round(lower, 2), round(upper, 2)],
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
