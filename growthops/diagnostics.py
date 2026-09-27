"""Anomaly detection and root-cause decomposition for growth metrics.

Detection compares each rolling 7-day window with the 56 days before it:
rates use a binomial z-score inflated by the overdispersion seen in the
baseline, volumes use a robust (median/MAD) z-score. Consecutive flagged days
become an *episode*. Each episode is explained with an exact shift-share
decomposition: the change in a rate splits into per-segment mix and rate
effects that sum to the total change, so "72% of the drop came from X" is
arithmetic, not narrative.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Any

from growthops.scenario import AS_OF

WINDOW = 7
BASELINE = 56
LOOKBACK = 75
# Practical significance: a statistically unusual but tiny move is not worth an executive's time.
MIN_RATE_CHANGE = 0.03
MIN_VOLUME_CHANGE = 0.10


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    label: str
    kind: str  # "rate" or "volume"
    dimension: str
    maturity_days: int
    threshold: float
    unit: str


SPECS = (
    MetricSpec("mql_rate", "Lead-to-MQL rate (7-day cohort)", "rate", "campaign", 7, 3.0, "pp"),
    MetricSpec("utm_completeness", "UTM completeness", "rate", "landing_page", 0, 3.0, "pp"),
    MetricSpec("leads", "Leads per day", "volume", "campaign", 0, 3.5, "count"),
    MetricSpec("spend", "Paid spend per day", "volume", "campaign", 0, 3.5, "usd"),
    MetricSpec("net_cash", "Net cash per day", "volume", "product", 0, 3.5, "usd"),
)
SPEC_BY_ID = {spec.metric_id: spec for spec in SPECS}

# metric_id -> {day -> {segment -> (numerator, denominator)}}; volumes use denominator 1.
Cube = dict[date, dict[str, tuple[float, float]]]


def _cube(connection: sqlite3.Connection, metric_id: str) -> Cube:
    queries = {
        "mql_rate": """
            SELECT DATE(t.occurred_at) day, COALESCE(t.campaign_id,'(unattributed)') segment,
                   SUM(EXISTS (SELECT 1 FROM lifecycle_events e WHERE e.contact_id=t.contact_id
                       AND e.stage='mql' AND julianday(e.occurred_at)-julianday(t.occurred_at)<=7)) num,
                   COUNT(*) den
            FROM touches t WHERE t.touch_type='lead_creation' GROUP BY 1, 2""",
        "utm_completeness": """
            SELECT DATE(occurred_at) day, COALESCE(landing_page,'(none)') segment,
                   SUM(utm_source IS NOT NULL AND TRIM(utm_source)<>'') num, COUNT(*) den
            FROM touches WHERE campaign_id IS NULL OR campaign_id<>'direct' GROUP BY 1, 2""",
        "leads": """
            SELECT DATE(occurred_at) day, COALESCE(campaign_id,'(unattributed)') segment, COUNT(*) num, 1 den
            FROM touches WHERE touch_type='lead_creation' GROUP BY 1, 2""",
        "spend": """
            SELECT spend_date day, campaign_id segment, SUM(spend_cents)/100.0 num, 1 den
            FROM ad_spend_daily GROUP BY 1, 2""",
        "net_cash": """
            SELECT day, segment, SUM(cents)/100.0 num, 1 den FROM (
              SELECT DATE(paid_at) day, COALESCE(product_id,'unknown') segment, amount_cents cents
              FROM payments WHERE status='succeeded'
              UNION ALL
              SELECT DATE(r.refunded_at), COALESCE(p.product_id,'unknown'), -r.amount_cents
              FROM refunds r JOIN payments p ON p.payment_id=r.payment_id
            ) GROUP BY 1, 2""",
    }
    cube: Cube = defaultdict(dict)
    for row in connection.execute(queries[metric_id]):
        cube[date.fromisoformat(row["day"])][row["segment"]] = (float(row["num"]), float(row["den"]))
    return cube


def _window(cube: Cube, start: date, end: date) -> dict[str, tuple[float, float]]:
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    day = start
    while day <= end:
        for segment, (num, den) in cube.get(day, {}).items():
            totals[segment][0] += num
            totals[segment][1] += den
        day += timedelta(days=1)
    return {segment: (values[0], values[1]) for segment, values in totals.items()}


def _value(kind: str, window: dict[str, tuple[float, float]], days: int) -> float | None:
    num = sum(v[0] for v in window.values())
    den = sum(v[1] for v in window.values())
    if kind == "rate":
        return num / den if den else None
    return num / days


def _series(cube: Cube, spec: MetricSpec, end: date) -> list[dict]:
    """Rolling-window value, baseline and z-score for each evaluable day."""
    first = min(cube) if cube else end
    points = []
    day = first + timedelta(days=BASELINE + WINDOW)
    while day <= end:
        current = _window(cube, day - timedelta(days=WINDOW - 1), day)
        base_start, base_end = day - timedelta(days=WINDOW + BASELINE - 1), day - timedelta(days=WINDOW)
        baseline = _window(cube, base_start, base_end)
        value = _value(spec.kind, current, WINDOW)
        base_value = _value(spec.kind, baseline, BASELINE)
        z = None
        if spec.kind == "rate" and value is not None and base_value is not None and 0 < base_value < 1:
            den_now = sum(v[1] for v in current.values())
            daily = []
            expected = []
            probe = base_start
            while probe <= base_end:
                cell = cube.get(probe, {})
                num, den = sum(v[0] for v in cell.values()), sum(v[1] for v in cell.values())
                if den:
                    daily.append(num / den)
                    expected.append(base_value * (1 - base_value) / den)
                probe += timedelta(days=1)
            observed_var = sum((r - base_value) ** 2 for r in daily) / max(len(daily) - 1, 1)
            phi = max(1.0, observed_var / (sum(expected) / len(expected))) if expected else 1.0
            z = (value - base_value) / math.sqrt(phi * base_value * (1 - base_value) / den_now)
        elif spec.kind == "volume" and value is not None:
            rolling: list[float] = []
            probe = base_start + timedelta(days=WINDOW - 1)
            while probe <= base_end:
                span = _window(cube, probe - timedelta(days=WINDOW - 1), probe)
                rolling.append(sum(v[0] for v in span.values()) / WINDOW)  # the volume _value, never None
                probe += timedelta(days=1)
            centre = median(rolling)
            mad = median(abs(r - centre) for r in rolling) * 1.4826
            base_value = centre
            z = (value - centre) / mad if mad else None
        points.append({"day": day.isoformat(), "value": value, "baseline": base_value, "z": z,
                       "flag": z is not None and abs(z) >= spec.threshold})
        day += timedelta(days=1)
    return points


def share_phrase(share: float | None, detail: str = "", of: str = "") -> str:
    """How much of a change one driver explains, worded so a share over 100% is not read as a typo.

    Shift-share contributions sum to the change, so one segment can explain more than all of it when
    the others moved the other way.
    """
    if share is None:
        return "an undetermined share" + (f" of {of}" if of else "")
    if round(share, 2) > 1:
        extra = f", {detail}" if detail else ""
        return f"all of {of or 'it'} ({share:.0%}{extra}; the rest moved the other way)"
    return f"{share:.0%}" + (f" of {of}" if of else "") + (f" ({detail})" if detail else "")


def decompose(cube: Cube, spec: MetricSpec, baseline: tuple[date, date], current: tuple[date, date]) -> dict:
    """Exact shift-share split of the change between two windows across segments."""
    before = _window(cube, *baseline)
    after = _window(cube, *current)
    days_before = (baseline[1] - baseline[0]).days + 1
    days_after = (current[1] - current[0]).days + 1
    segments = sorted(set(before) | set(after))
    drivers: list[dict[str, Any]] = []
    if spec.kind == "rate":
        den0 = sum(v[1] for v in before.values())
        den1 = sum(v[1] for v in after.values())
        total0 = sum(v[0] for v in before.values()) / den0
        total1 = sum(v[0] for v in after.values()) / den1
        for segment in segments:
            n0, d0 = before.get(segment, (0.0, 0.0))
            n1, d1 = after.get(segment, (0.0, 0.0))
            w0, w1 = d0 / den0, d1 / den1
            r0 = n0 / d0 if d0 else total0
            r1 = n1 / d1 if d1 else r0
            mix = (w1 - w0) * (r0 - total0)
            rate = w1 * (r1 - r0)
            drivers.append({"segment": segment, "contribution": mix + rate, "mix_effect": mix,
                            "rate_effect": rate, "baseline_share": w0, "current_share": w1,
                            "baseline_rate": n0 / d0 if d0 else None, "current_rate": n1 / d1 if d1 else None,
                            "new_segment": d0 == 0})
    else:
        total0 = sum(v[0] for v in before.values()) / days_before
        total1 = sum(v[0] for v in after.values()) / days_after
        for segment in segments:
            v0 = before.get(segment, (0.0, 0.0))[0] / days_before
            v1 = after.get(segment, (0.0, 0.0))[0] / days_after
            drivers.append({"segment": segment, "contribution": v1 - v0, "baseline_value": v0,
                            "current_value": v1, "new_segment": segment not in before})
    change = total1 - total0
    for driver in drivers:
        driver["share_of_change"] = driver["contribution"] / change if change else None
    drivers.sort(key=lambda d: (-(d["contribution"] * (1 if change >= 0 else -1)), d["segment"]))
    return {"baseline_value": total0, "current_value": total1, "change": change,
            "residual": change - sum(d["contribution"] for d in drivers), "drivers": drivers}


def _episodes(points: list[dict], lookback_start: date) -> list[tuple[date, date, int, float]]:
    flagged = [(date.fromisoformat(p["day"]), p["z"]) for p in points
               if p["flag"] and date.fromisoformat(p["day"]) >= lookback_start]
    episodes: list[list] = []
    for day, z in flagged:
        sign = 1 if z > 0 else -1
        if episodes and episodes[-1][2] == sign and (day - episodes[-1][1]).days <= 3:
            episodes[-1][1] = day
            episodes[-1][3] = max(episodes[-1][3], abs(z))
        else:
            episodes.append([day, day, sign, abs(z)])
    return [tuple(e) for e in episodes]


def _format(spec: MetricSpec, value: float) -> str:
    if spec.unit == "pp":
        return f"{value:.1%}"
    if spec.unit == "usd":
        return f"${value:,.0f}"
    return f"{value:,.1f}"


def _format_change(spec: MetricSpec, value: float) -> str:
    if spec.unit == "pp":
        return f"{value * 100:+.1f} pp"
    if spec.unit == "usd":
        return f"{'+' if value >= 0 else '-'}${abs(value):,.0f}/day"
    return f"{value:+.1f}/day"


def detect(connection: sqlite3.Connection, as_of: date = AS_OF, lookback_days: int = LOOKBACK) -> list[dict]:
    """Anomaly episodes in the lookback window, each with its root-cause decomposition."""
    results = []
    for spec in SPECS:
        cube = _cube(connection, spec.metric_id)
        if not cube:
            continue
        end = as_of - timedelta(days=spec.maturity_days)
        points = _series(cube, spec, end)
        for index, (first, last, sign, peak) in enumerate(_episodes(points, as_of - timedelta(days=lookback_days))):
            window = (first - timedelta(days=WINDOW - 1), last)
            baseline = (window[0] - timedelta(days=BASELINE), window[0] - timedelta(days=1))
            split = decompose(cube, spec, baseline, window)
            top = [d for d in split["drivers"] if d["share_of_change"] and d["share_of_change"] > 0][:3]
            change_pct = split["change"] / split["baseline_value"] if split["baseline_value"] else None
            material = (abs(split["change"]) >= MIN_RATE_CHANGE if spec.kind == "rate"
                        else change_pct is not None and abs(change_pct) >= MIN_VOLUME_CHANGE)
            if not material:
                continue
            results.append({
                "episode_id": f"{spec.metric_id}:{first.isoformat()}",
                "metric_id": spec.metric_id,
                "label": spec.label,
                "dimension": spec.dimension,
                "direction": "up" if sign > 0 else "down",
                "detected_on": first.isoformat(),
                "window_start": window[0].isoformat(),
                "window_end": window[1].isoformat(),
                "ongoing": last >= end - timedelta(days=1),
                "peak_z": round(peak, 1),
                "baseline_value": split["baseline_value"],
                "current_value": split["current_value"],
                "change": split["change"],
                "change_pct": change_pct,
                "baseline_text": _format(spec, split["baseline_value"]),
                "current_text": _format(spec, split["current_value"]),
                "change_text": _format_change(spec, split["change"]),
                "drivers": split["drivers"],
                "top_drivers": [{"segment": d["segment"], "share_of_change": round(d["share_of_change"], 3),
                                 "contribution_text": _format_change(spec, d["contribution"]),
                                 "new_segment": d["new_segment"]} for d in top],
                "residual": split["residual"],
            })
    results.sort(key=lambda e: (-e["peak_z"], e["episode_id"]))
    return results


def series(connection: sqlite3.Connection, metric_id: str, as_of: date = AS_OF) -> list[dict]:
    spec = SPEC_BY_ID[metric_id]
    return _series(_cube(connection, metric_id), spec, as_of - timedelta(days=spec.maturity_days))


EXPECTED_SIGNALS = {
    "mql_rate_down": ("mql_rate", "down"),
    "utm_completeness_down": ("utm_completeness", "down"),
}


def incident_recall(connection: sqlite3.Connection, episodes: list[dict] | None = None) -> list[dict]:
    """Score detection against the planted-incident manifest (ground truth)."""
    episodes = detect(connection) if episodes is None else episodes
    rows = connection.execute("SELECT * FROM incidents ORDER BY starts_at").fetchall()
    results = []
    for incident in rows:
        signal = EXPECTED_SIGNALS.get(incident["expected_signal"])
        if signal is None:
            continue
        metric_id, direction = signal
        entity = incident["entity"].split(":", 1)[1]
        start = date.fromisoformat(incident["starts_at"][:10])
        matches = [e for e in episodes if e["metric_id"] == metric_id and e["direction"] == direction
                   and date.fromisoformat(e["window_end"]) >= start]
        found = next((e for e in matches if e["top_drivers"] and e["top_drivers"][0]["segment"] == entity), None)
        results.append({
            "incident_id": incident["incident_id"],
            "description": incident["description"],
            "expected": f"{metric_id} {direction}, driven by {entity}",
            "detected": bool(matches),
            "root_cause_correct": found is not None,
            "episode_id": (found or (matches[0] if matches else {})).get("episode_id"),
            "days_to_detect": (date.fromisoformat(found["detected_on"]) - start).days if found else None,
        })
    return results
