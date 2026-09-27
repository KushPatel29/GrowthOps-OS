"""Durable, step-idempotent payment-to-access lifecycle engine.

A payment event moves through ordered steps (record payment, update CRM, grant
community access, send onboarding). Every attempt is traced. A failed step is
retried with exponential backoff by :func:`run_due`; after ``MAX_ATTEMPTS`` the
event is parked in the dead-letter queue for a person to replay. Redelivered
events never repeat a completed side effect.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from pydantic import BaseModel, Field, field_validator

from growthops.adapters import Adapters, ProviderError, call_step

MAX_ATTEMPTS = 5
BACKOFF_BASE = timedelta(minutes=5)
CLAIM_TTL = timedelta(minutes=5)
STEPS_BY_TYPE = {
    "new": ("record_payment", "update_crm", "grant_access", "send_onboarding"),
    "installment": ("record_payment", "update_crm"),
    "renewal": ("record_payment", "update_crm", "grant_access"),
}

# (step, event, attempt, now) -> error message for a simulated provider failure, or None.
FaultInjector = Callable[[str, "PaymentEvent", int, datetime], "str | None"]
# (step, event, attempt, failed) -> simulated step latency in milliseconds.
LatencyModel = Callable[[str, "PaymentEvent", int, bool], int]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaymentEvent(BaseModel):
    event_id: str = Field(min_length=1)
    event_type: Literal["payment.succeeded"]
    payment_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    deal_id: str | None = None
    amount_cents: int = Field(gt=0)
    paid_at: datetime
    payment_type: Literal["new", "installment", "renewal"] = "new"
    subscription_id: str | None = None
    product_id: str | None = None

    @field_validator("paid_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("paid_at must include a time zone")
        return value.astimezone(timezone.utc)


class EventConflict(Exception):
    """An existing event or payment ID was reused with a different payload."""


class StepFailure(Exception):
    """A retryable failure in a downstream system."""


def _payload_digest(event: PaymentEvent) -> str:
    canonical = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def trace_id_for(event_id: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"growthops:{event_id}").hex[:16]


def default_latency(step: str, event: PaymentEvent, attempt: int, failed: bool) -> int:
    """Deterministic, plausible step latency so traces are reproducible."""
    spread = int(hashlib.sha256(f"{event.event_id}:{step}:{attempt}".encode()).hexdigest()[:4], 16)
    base = {"record_payment": 40, "update_crm": 380, "grant_access": 520, "send_onboarding": 210}[step]
    return (8000 if failed else base) + spread % (base + 1)


def backoff(attempts: int) -> timedelta:
    return BACKOFF_BASE * (2 ** max(attempts - 1, 0))


def _apply_step(connection: sqlite3.Connection, step: str, event: PaymentEvent, at: datetime) -> None:
    at_text = at.isoformat()
    if step == "record_payment":
        if connection.execute("SELECT 1 FROM payments WHERE payment_id=?", (event.payment_id,)).fetchone():
            raise EventConflict("payment_id was already recorded outside this event")
        connection.execute(
            """INSERT INTO payments (payment_id, deal_id, customer_id, amount_cents, status, paid_at,
                                     payment_type, subscription_id, product_id)
               VALUES (?, ?, ?, ?, 'succeeded', ?, ?, ?, ?)""",
            (event.payment_id, event.deal_id, event.customer_id, event.amount_cents,
             event.paid_at.isoformat(), event.payment_type, event.subscription_id, event.product_id),
        )
    elif step == "update_crm":
        changed = connection.execute(
            "UPDATE contacts SET current_stage='customer' WHERE contact_id=?", (event.customer_id,)
        ).rowcount
        if changed != 1:
            raise StepFailure("CRM contact not found; retry after identity resolution")
        if event.payment_type == "new":
            for stage in ("customer", "paid"):
                connection.execute(
                    "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?)",
                    (f"{event.event_id}:{stage}", event.customer_id, stage, event.paid_at.isoformat()),
                )
        elif event.payment_type == "renewal":
            connection.execute(
                "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, 'renewed', ?)",
                (f"{event.event_id}:renewed", event.customer_id, event.paid_at.isoformat()),
            )
    elif step == "grant_access":
        connection.execute(
            "INSERT OR IGNORE INTO access_entitlements VALUES (?, 'active', ?, ?)",
            (event.customer_id, at_text, event.event_id),
        )
        connection.execute(
            "UPDATE access_entitlements SET status='active' WHERE customer_id=?", (event.customer_id,)
        )
        if event.payment_type == "new":
            connection.execute(
                "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, 'activated', ?)",
                (f"{event.event_id}:activated", event.customer_id, at_text),
            )
    elif step == "send_onboarding":
        pass  # Simulated messaging adapter; the trace records the send.
    else:
        raise ValueError(f"unknown step {step}")


def process_payment(
    connection: sqlite3.Connection,
    event: PaymentEvent,
    *,
    fail_step: str | None = None,
    now: datetime | None = None,
    faults: FaultInjector | None = None,
    latency: LatencyModel | None = None,
    delivery: bool = True,
    adapters: Adapters | None = None,
) -> dict:
    """Persist each step before moving on; a redelivery resumes after the last step.

    ``delivery`` is False when the retry worker (not the provider) re-runs the event,
    so internal retries are not counted as duplicate deliveries. ``adapters`` performs
    the real side effects; ``None`` (the synthetic scenario) simulates them. A provider
    call happens outside the database transaction, so a slow provider never holds the
    write lock, and every adapter call is idempotent so a retried step is safe.
    """
    live = adapters is not None and not adapters.simulated
    clock = now or datetime.now(timezone.utc)
    latency = latency or default_latency
    digest = _payload_digest(event)
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT payload_sha256, status, claimed_at FROM processed_events WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        if existing and existing["payload_sha256"] != digest:
            raise EventConflict("event_id already belongs to a different payload")
        if connection.execute(
            "SELECT 1 FROM processed_events WHERE payment_id=? AND event_id<>?",
            (event.payment_id, event.event_id),
        ).fetchone():
            raise EventConflict("payment_id already belongs to another event")
        if existing and delivery:
            connection.execute(
                "UPDATE processed_events SET deliveries=deliveries+1 WHERE event_id=?", (event.event_id,)
            )
        if existing and existing["status"] in ("completed", "dead_letter"):
            connection.commit()
            return {"event_id": event.event_id, "status": existing["status"], "duplicate": True}
        if existing and existing["status"] == "processing" and existing["claimed_at"]:
            if clock - datetime.fromisoformat(existing["claimed_at"]) < CLAIM_TTL:
                connection.commit()
                return {"event_id": event.event_id, "status": "processing", "duplicate": True}
        if not existing:
            connection.execute(
                """INSERT INTO processed_events
                   (event_id, event_type, payment_id, customer_id, payload_sha256, status,
                    received_at, trace_id, payload_json)
                   VALUES (?, ?, ?, ?, ?, 'received', ?, ?, ?)""",
                (event.event_id, event.event_type, event.payment_id, event.customer_id, digest,
                 clock.isoformat(), trace_id_for(event.event_id), event.model_dump_json()),
            )
        connection.execute(
            """UPDATE processed_events SET status='processing', attempts=attempts+1,
               last_error=NULL, claimed_at=?, next_attempt_at=NULL WHERE event_id=?""",
            (clock.isoformat(), event.event_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    cursor = clock
    try:
        for step in STEPS_BY_TYPE[event.payment_type]:
            if connection.execute(
                "SELECT 1 FROM workflow_steps WHERE event_id=? AND step_name=?", (event.event_id, step)
            ).fetchone():
                continue
            attempt = connection.execute(
                "SELECT COUNT(*) FROM workflow_step_attempts WHERE event_id=? AND step_name=?",
                (event.event_id, step),
            ).fetchone()[0] + 1
            error = f"simulated {step} failure" if fail_step == step else None
            if error is None and faults is not None:
                error = faults(step, event, attempt, cursor)
            if live:
                began = time.perf_counter()
                if error is None:
                    try:
                        call_step(adapters, step, event)
                    except ProviderError as exc:
                        error = str(exc)
                duration = round((time.perf_counter() - began) * 1000)
            else:
                duration = latency(step, event, attempt, error is not None)
            started = cursor
            cursor = cursor + timedelta(milliseconds=duration)
            connection.execute("BEGIN IMMEDIATE")
            try:
                if error is not None:
                    raise StepFailure(error)
                _apply_step(connection, step, event, cursor)
                connection.execute(
                    "INSERT INTO workflow_step_attempts VALUES (?, ?, ?, 'succeeded', ?, ?, NULL)",
                    (event.event_id, step, attempt, started.isoformat(), duration),
                )
                connection.execute(
                    "INSERT INTO workflow_steps VALUES (?, ?, ?)", (event.event_id, step, cursor.isoformat())
                )
                connection.commit()
            except Exception as exc:
                connection.rollback()
                connection.execute(
                    "INSERT INTO workflow_step_attempts VALUES (?, ?, ?, 'failed', ?, ?, ?)",
                    (event.event_id, step, attempt, started.isoformat(), duration, str(exc)),
                )
                raise
        connection.execute(
            "UPDATE processed_events SET status='completed', completed_at=? WHERE event_id=?",
            (cursor.isoformat(), event.event_id),
        )
    except EventConflict as exc:
        connection.execute(
            "UPDATE processed_events SET status='dead_letter', last_error=? WHERE event_id=?",
            (str(exc), event.event_id),
        )
        raise
    except Exception as exc:
        attempts = connection.execute(
            "SELECT attempts FROM processed_events WHERE event_id=?", (event.event_id,)
        ).fetchone()[0]
        if attempts >= MAX_ATTEMPTS:
            connection.execute(
                """UPDATE processed_events SET status='dead_letter', last_error=?, next_attempt_at=NULL
                   WHERE event_id=?""",
                (str(exc), event.event_id),
            )
        else:
            connection.execute(
                "UPDATE processed_events SET status='failed', last_error=?, next_attempt_at=? WHERE event_id=?",
                (str(exc), (cursor + backoff(attempts)).isoformat(), event.event_id),
            )
    row = connection.execute(
        "SELECT status, attempts, last_error, next_attempt_at, trace_id FROM processed_events WHERE event_id=?",
        (event.event_id,),
    ).fetchone()
    return {"event_id": event.event_id, "status": row["status"], "attempts": row["attempts"],
            "last_error": row["last_error"], "next_attempt_at": row["next_attempt_at"],
            "trace_id": row["trace_id"], "duplicate": False}


def run_due(
    connection: sqlite3.Connection,
    now: datetime,
    *,
    faults: FaultInjector | None = None,
    latency: LatencyModel | None = None,
    limit: int = 200,
    adapters: Adapters | None = None,
) -> list[dict]:
    """Retry worker: resume every failed event whose backoff has elapsed."""
    rows = connection.execute(
        """SELECT payload_json FROM processed_events
           WHERE status='failed' AND next_attempt_at<=? ORDER BY next_attempt_at, event_id LIMIT ?""",
        (now.isoformat(), limit),
    ).fetchall()
    results = []
    for row in rows:
        event = PaymentEvent.model_validate_json(row["payload_json"])
        results.append(process_payment(connection, event, now=now, faults=faults, latency=latency, delivery=False,
                                       adapters=adapters))
    return results


def replay_dead_letter(connection: sqlite3.Connection, event_id: str, now: datetime | None = None,
                       adapters: Adapters | None = None) -> dict:
    """Operator action: give a dead-lettered event a fresh retry budget and run it now."""
    row = connection.execute(
        "SELECT status, payload_json FROM processed_events WHERE event_id=?", (event_id,)
    ).fetchone()
    if row is None:
        raise LookupError("event not found")
    if row["status"] != "dead_letter":
        raise ValueError("only dead-lettered events can be replayed")
    connection.execute(
        "UPDATE processed_events SET status='failed', attempts=0, next_attempt_at=NULL WHERE event_id=?",
        (event_id,),
    )
    return process_payment(connection, PaymentEvent.model_validate_json(row["payload_json"]), now=now, delivery=False,
                           adapters=adapters)


def _percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(share * len(ordered)) - 1, 0)]


def health(connection: sqlite3.Connection) -> dict:
    """Operations health: reliability, retries, dead letters and time to access."""
    events = connection.execute(
        "SELECT event_id, status, attempts, received_at, completed_at, deliveries FROM processed_events"
    ).fetchall()
    total = len(events)
    status = Counter(row["status"] for row in events)
    completed = [row for row in events if row["status"] == "completed"]
    seconds = [
        (datetime.fromisoformat(row["completed_at"]) - datetime.fromisoformat(row["received_at"])).total_seconds()
        for row in completed
    ]
    attempts = connection.execute(
        """SELECT step_name, error, COUNT(*) n FROM workflow_step_attempts
           WHERE status='failed' GROUP BY step_name, error"""
    ).fetchall()
    step_failures: Counter = Counter()
    error_types: Counter = Counter()
    for row in attempts:
        step_failures[row["step_name"]] += row["n"]
        error_types[row["error"] or "unknown"] += row["n"]
    return {
        "events": total,
        "completed": status["completed"],
        "retrying": status["failed"],
        "dead_letter": status["dead_letter"],
        "in_flight": status["processing"] + status["received"],
        "success_rate": round(status["completed"] / total, 4) if total else None,
        "first_attempt_success_rate": round(
            sum(1 for row in completed if row["attempts"] == 1) / total, 4) if total else None,
        "retry_attempts": sum(max(row["attempts"] - 1, 0) for row in events),
        "duplicate_deliveries_absorbed": sum(row["deliveries"] - 1 for row in events),
        "p50_seconds_to_complete": _percentile(seconds, 0.5),
        "p95_seconds_to_complete": _percentile(seconds, 0.95),
        "within_5_minutes": round(sum(1 for value in seconds if value <= 300) / total, 4) if total else None,
        "step_failures": dict(step_failures),
        "error_types": dict(error_types.most_common()),
    }


def trace(connection: sqlite3.Connection, event_id: str) -> dict:
    event = connection.execute(
        """SELECT event_id, trace_id, payment_id, customer_id, status, attempts, deliveries,
                  received_at, completed_at, last_error, next_attempt_at
           FROM processed_events WHERE event_id=?""",
        (event_id,),
    ).fetchone()
    if event is None:
        raise LookupError("event not found")
    steps = connection.execute(
        """SELECT step_name, attempt, status, started_at, duration_ms, error
           FROM workflow_step_attempts WHERE event_id=? ORDER BY started_at, step_name, attempt""",
        (event_id,),
    ).fetchall()
    return {**dict(event), "attempts_log": [dict(row) for row in steps]}
