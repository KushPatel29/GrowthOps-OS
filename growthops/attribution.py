"""Payment-level cash attribution with exact cent conservation."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from fractions import Fraction
from typing import Literal

AttributionModel = Literal["first_touch", "lead_creation", "last_non_direct", "u_shaped"]
MODELS = ("first_touch", "lead_creation", "last_non_direct", "u_shaped")


def _split_cents(amount: int, weights: dict[str, Fraction]) -> dict[str, int]:
    """Allocate exact integer cents by largest remainder, stable by touch ID."""
    if sum(weights.values(), Fraction()) != 1:
        raise ValueError("attribution weights must total one")
    base = {key: amount * value.numerator // value.denominator for key, value in weights.items()}
    remaining = amount - sum(base.values())
    order = sorted(weights, key=lambda key: (-(amount * weights[key] - base[key]), key))
    for key in order[:remaining]:
        base[key] += 1
    return base


def _weights(touches: list[sqlite3.Row], model: AttributionModel) -> dict[str, Fraction]:
    if not touches:
        return {}
    first = touches[0]
    lead = next((touch for touch in reversed(touches) if touch["touch_type"] == "lead_creation"), None)
    non_direct = [touch for touch in touches if touch["source"] != "direct"]
    if model == "first_touch":
        return {first["touch_id"]: Fraction(1)}
    if model == "lead_creation":
        return {lead["touch_id"]: Fraction(1)} if lead else {}
    if model == "last_non_direct":
        return {non_direct[-1]["touch_id"]: Fraction(1)} if non_direct else {}
    if model != "u_shaped":
        raise ValueError(f"unknown attribution model: {model}")
    if not lead or first["touch_id"] == lead["touch_id"]:
        return {lead["touch_id"]: Fraction(1)} if lead else {}
    lead_index = next(i for i, touch in enumerate(touches) if touch["touch_id"] == lead["touch_id"])
    middle = touches[1:lead_index]
    if not middle:
        return {first["touch_id"]: Fraction(1, 2), lead["touch_id"]: Fraction(1, 2)}
    weights = {first["touch_id"]: Fraction(2, 5), lead["touch_id"]: Fraction(2, 5)}
    weights.update({touch["touch_id"]: Fraction(1, 5 * len(middle)) for touch in middle})
    return weights


def allocations(connection: sqlite3.Connection, model: AttributionModel) -> list[dict]:
    if model not in MODELS:
        raise ValueError(f"unknown attribution model: {model}")
    payments = connection.execute(
        """SELECT p.payment_id, p.customer_id, p.paid_at, p.amount_cents,
                  COALESCE(SUM(r.amount_cents),0) refund_cents
           FROM payments p LEFT JOIN refunds r ON r.payment_id=p.payment_id
           WHERE p.status='succeeded'
           GROUP BY p.payment_id, p.customer_id, p.paid_at, p.amount_cents
           ORDER BY p.payment_id"""
    ).fetchall()
    result = []
    for payment in payments:
        net = payment["amount_cents"] - payment["refund_cents"]
        if net < 0:
            raise ValueError(f"refunds exceed payment {payment['payment_id']}")
        touches = connection.execute(
            """SELECT t.touch_id, t.campaign_id, t.touch_type, t.occurred_at,
                      COALESCE(c.source,'unknown') source
               FROM touches t LEFT JOIN campaigns c ON c.campaign_id=t.campaign_id
               WHERE t.contact_id=? ORDER BY t.occurred_at, t.touch_id""",
            (payment["customer_id"],),
        ).fetchall()
        paid_at = datetime.fromisoformat(payment["paid_at"])
        eligible = [t for t in touches if datetime.fromisoformat(t["occurred_at"]) <= paid_at]
        weights = _weights(eligible, model)
        if not weights:
            result.append({"model": model, "payment_id": payment["payment_id"], "touch_id": None,
                           "campaign_id": None, "credited_cents": net})
            continue
        campaigns = {t["touch_id"]: t["campaign_id"] for t in eligible}
        for touch_id, cents in _split_cents(net, weights).items():
            result.append({"model": model, "payment_id": payment["payment_id"], "touch_id": touch_id,
                           "campaign_id": campaigns[touch_id], "credited_cents": cents})
    return result


def summary(connection: sqlite3.Connection, model: AttributionModel) -> list[dict]:
    totals = defaultdict(int)
    for row in allocations(connection, model):
        totals[row["campaign_id"]] += row["credited_cents"]
    return [{"campaign_id": campaign_id, "net_cash_cents": cents, "model": model}
            for campaign_id, cents in sorted(totals.items(), key=lambda item: (item[0] is None, item[0] or ""))]
