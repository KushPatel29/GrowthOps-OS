"""Durable, step-idempotent payment-to-access simulation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("paid_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("paid_at must include a time zone")
        return value.astimezone(timezone.utc)


class EventConflict(Exception):
    """An existing event ID was reused with a different payload."""


def _payload_digest(event: PaymentEvent) -> str:
    canonical = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def process_payment(
    connection: sqlite3.Connection, event: PaymentEvent, *, fail_step: str | None = None
) -> dict:
    """Persist each step before moving on; a redelivery resumes after the last step."""
    digest = _payload_digest(event)
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT payload_sha256, status, claimed_at FROM processed_events WHERE event_id=?", (event.event_id,)
        ).fetchone()
        if existing and existing["payload_sha256"] != digest:
            raise EventConflict("event_id already belongs to a different payload")
        other_payment_event = connection.execute(
            "SELECT event_id FROM processed_events WHERE payment_id=? AND event_id<>?",
            (event.payment_id, event.event_id),
        ).fetchone()
        if other_payment_event:
            raise EventConflict("payment_id already belongs to another event")
        if existing and existing["status"] == "completed":
            connection.commit()
            return {"event_id": event.event_id, "status": "completed", "duplicate": True}
        if existing and existing["status"] == "processing" and existing["claimed_at"]:
            claimed = datetime.fromisoformat(existing["claimed_at"])
            if datetime.now(timezone.utc) - claimed < timedelta(minutes=5):
                connection.commit()
                return {"event_id": event.event_id, "status": "processing", "duplicate": True}
        if not existing:
            connection.execute(
                """INSERT INTO processed_events
                   (event_id, event_type, payment_id, customer_id, payload_sha256, status, received_at)
                   VALUES (?, ?, ?, ?, ?, 'received', ?)""",
                (event.event_id, event.event_type, event.payment_id, event.customer_id, digest, utcnow()),
            )
        connection.execute(
            """UPDATE processed_events SET status='processing', attempts=attempts+1,
               last_error=NULL, claimed_at=? WHERE event_id=?""",
            (utcnow(), event.event_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    try:
        for step in ("record_payment", "update_crm", "grant_access"):
            if connection.execute(
                "SELECT 1 FROM workflow_steps WHERE event_id=? AND step_name=?", (event.event_id, step)
            ).fetchone():
                continue
            if fail_step == step:
                raise RuntimeError(f"simulated {step} failure")
            connection.execute("BEGIN IMMEDIATE")
            try:
                if step == "record_payment":
                    existing_payment = connection.execute(
                        "SELECT customer_id, amount_cents FROM payments WHERE payment_id=?", (event.payment_id,)
                    ).fetchone()
                    if existing_payment:
                        raise EventConflict("payment_id was already recorded outside this event")
                    connection.execute(
                        "INSERT OR IGNORE INTO payments VALUES (?, ?, ?, ?, 'succeeded', ?)",
                        (event.payment_id, event.deal_id, event.customer_id, event.amount_cents, event.paid_at.isoformat()),
                    )
                elif step == "update_crm":
                    changed = connection.execute(
                        "UPDATE contacts SET current_stage='customer' WHERE contact_id=?", (event.customer_id,)
                    ).rowcount
                    if changed != 1:
                        raise RuntimeError("CRM contact not found; retry after identity resolution")
                    connection.execute(
                        "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, 'customer', ?)",
                        (f"{event.event_id}:customer", event.customer_id, event.paid_at.isoformat()),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, 'paid', ?)",
                        (f"{event.event_id}:paid", event.customer_id, event.paid_at.isoformat()),
                    )
                elif step == "grant_access":
                    connection.execute(
                        "INSERT OR IGNORE INTO access_entitlements VALUES (?, 'active', ?, ?)",
                        (event.customer_id, utcnow(), event.event_id),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, 'activated', ?)",
                        (f"{event.event_id}:activated", event.customer_id, utcnow()),
                    )
                connection.execute(
                    "INSERT INTO workflow_steps VALUES (?, ?, ?)", (event.event_id, step, utcnow())
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        connection.execute(
            "UPDATE processed_events SET status='completed', completed_at=? WHERE event_id=?",
            (utcnow(), event.event_id),
        )
    except EventConflict as exc:
        connection.execute(
            "UPDATE processed_events SET status='failed', last_error=? WHERE event_id=?",
            (str(exc), event.event_id),
        )
        raise
    except Exception as exc:
        connection.execute(
            "UPDATE processed_events SET status='failed', last_error=? WHERE event_id=?",
            (str(exc), event.event_id),
        )
    row = connection.execute(
        "SELECT status, attempts, last_error FROM processed_events WHERE event_id=?", (event.event_id,)
    ).fetchone()
    return {"event_id": event.event_id, "status": row["status"], "attempts": row["attempts"], "last_error": row["last_error"], "duplicate": False}
