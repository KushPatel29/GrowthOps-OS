"""Event-history funnel with transition timing and integrity checks."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from statistics import median

STAGES = (
    "lead", "mql", "call_booked", "call_attended", "opportunity",
    "closed_won", "paid", "activated", "renewed",
)


def first_stage_times(connection: sqlite3.Connection) -> dict[str, dict[str, datetime]]:
    history: dict[str, dict[str, datetime]] = {}
    rows = connection.execute(
        "SELECT contact_id, stage, MIN(occurred_at) at FROM lifecycle_events GROUP BY contact_id, stage"
    ).fetchall()
    for row in rows:
        history.setdefault(row["contact_id"], {})[row["stage"]] = datetime.fromisoformat(row["at"])
    return history


def funnel(connection: sqlite3.Connection) -> list[dict]:
    history = first_stage_times(connection)
    result = []
    previous = None
    for stage in STAGES:
        people = {person for person, events in history.items() if stage in events}
        if previous is None:
            rate = None
            median_days = None
            p90_days = None
        else:
            prior_people = {person for person, events in history.items() if previous in events}
            rate = round(len(people & prior_people) / len(prior_people), 4) if prior_people else None
            durations = sorted(
                (history[person][stage] - history[person][previous]).total_seconds() / 86400
                for person in people & prior_people
                if history[person][stage] >= history[person][previous]
            )
            median_days = round(median(durations), 2) if durations else None
            p90_days = round(durations[math.ceil(0.9 * len(durations)) - 1], 2) if durations else None
        result.append({"stage": stage, "people": len(people), "from_previous_rate": rate,
                       "median_days_from_previous": median_days, "p90_days_from_previous": p90_days})
        previous = stage
    return result


def lifecycle_integrity(connection: sqlite3.Connection) -> dict:
    history = first_stage_times(connection)
    required = STAGES[:7]
    paid = [events for events in history.values() if "paid" in events]
    valid = 0
    for events in paid:
        if all(stage in events for stage in required) and all(
            events[left] <= events[right] for left, right in zip(required, required[1:])
        ):
            valid += 1
    return {"paid_contact_count": len(paid), "valid_paid_journeys": valid,
            "lifecycle_integrity": round(valid / len(paid), 4) if paid else None}
