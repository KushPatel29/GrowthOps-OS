"""Signed local subscription-policy events on the shared durable event ledger.

This is a canonical GrowthOps test bridge, not a Stripe webhook adapter. Local
financial and entitlement projections commit once; the scoped community action
uses the same provider idempotency and retry rules as payment provisioning.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from growthops.adapters import Adapters, ProviderError, SimulatedAccess
from growthops.workflow import (
    CLAIM_TTL,
    MAX_ATTEMPTS,
    EventConflict,
    backoff,
    trace_id_for,
)

LifecycleType = Literal[
    "subscription.upgraded", "subscription.downgraded", "subscription.cancelled", "refund.created"
]


class LifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    event_type: LifecycleType
    customer_id: str = Field(min_length=1, max_length=128)
    occurred_at: datetime
    subscription_id: str | None = Field(default=None, max_length=128)
    payment_id: str | None = Field(default=None, max_length=128)
    refund_id: str | None = Field(default=None, max_length=128)
    amount_cents: int | None = Field(default=None, gt=0, le=100_000_000_000)
    new_tier: str | None = Field(default=None, max_length=128)
    product_id: str | None = Field(default=None, max_length=128)

    @field_validator("occurred_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a time zone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_for_type(self) -> LifecycleEvent:
        if self.event_type == "refund.created":
            if not self.payment_id or not self.refund_id or self.amount_cents is None:
                raise ValueError("refund requires payment_id, refund_id and amount_cents")
        elif not self.subscription_id:
            raise ValueError("subscription event requires subscription_id")
        if (self.event_type in ("subscription.upgraded", "subscription.downgraded")
                and (not self.new_tier or not self.new_tier.strip())):
            raise ValueError("tier change requires new_tier")
        return self


def _digest(event: LifecycleEvent) -> str:
    canonical = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _snapshot(connection: sqlite3.Connection, subscription_id: str | None,
              customer_id: str) -> dict:
    subscription = connection.execute(
        "SELECT plan_id, status FROM subscriptions WHERE subscription_id=?", (subscription_id,)
    ).fetchone() if subscription_id else None
    entitlement = connection.execute(
        "SELECT product_id, tier, status FROM subscription_entitlements WHERE subscription_id=?",
        (subscription_id,),
    ).fetchone() if subscription_id else None
    broad = connection.execute(
        "SELECT status FROM access_entitlements WHERE customer_id=?", (customer_id,)
    ).fetchone()
    return {"subscription": dict(subscription) if subscription else None,
            "entitlement": dict(entitlement) if entitlement else None,
            "customer_access": broad["status"] if broad else None}


def _ensure_entitlement(connection: sqlite3.Connection, subscription_id: str,
                        customer_id: str, at: str, event_id: str) -> None:
    connection.execute(
        """INSERT OR IGNORE INTO subscription_entitlements
           (subscription_id, customer_id, status, changed_at, source_event_id)
           VALUES (?, ?, 'legacy_unverified', ?, ?)""",
        (subscription_id, customer_id, at, event_id),
    )


def _revoke_subscription(connection: sqlite3.Connection, subscription_id: str,
                         customer_id: str, at: str, event_id: str) -> None:
    _ensure_entitlement(connection, subscription_id, customer_id, at, event_id)
    connection.execute(
        """UPDATE subscription_entitlements
           SET status='revoked', changed_at=?, source_event_id=? WHERE subscription_id=?""",
        (at, event_id, subscription_id),
    )
    # The legacy customer-grain flag can be revoked only when no other active
    # subscription or verified product entitlement may still justify access.
    another = connection.execute(
        """SELECT 1 FROM subscriptions s
           LEFT JOIN subscription_entitlements e ON e.subscription_id=s.subscription_id
           WHERE s.customer_id=? AND s.subscription_id<>? AND s.status='active'
             AND (e.status IS NULL OR e.status<>'revoked') LIMIT 1""",
        (customer_id, subscription_id),
    ).fetchone()
    another_entitlement = connection.execute(
        """SELECT 1 FROM subscription_entitlements
           WHERE customer_id=? AND subscription_id<>? AND status='active' LIMIT 1""",
        (customer_id, subscription_id),
    ).fetchone()
    if not another and not another_entitlement:
        connection.execute(
            "UPDATE access_entitlements SET status='revoked' WHERE customer_id=?",
            (customer_id,),
        )


def _apply_local(connection: sqlite3.Connection, event: LifecycleEvent) -> tuple[str | None, str | None]:
    at = event.occurred_at.isoformat()
    subscription_id = event.subscription_id
    action: str | None = None
    if event.event_type == "refund.created":
        payment = connection.execute(
            """SELECT payment_id, customer_id, amount_cents, subscription_id
               FROM payments WHERE payment_id=? AND status='succeeded'""",
            (event.payment_id,),
        ).fetchone()
        if payment is None or payment["customer_id"] != event.customer_id:
            raise EventConflict("refund payment is absent or belongs to another customer")
        if subscription_id and subscription_id != payment["subscription_id"]:
            raise EventConflict("refund subscription does not match the payment")
        subscription_id = payment["subscription_id"]
        existing = connection.execute(
            "SELECT payment_id FROM refunds WHERE refund_id=?", (event.refund_id,)
        ).fetchone()
        if existing:
            raise EventConflict("refund_id already belongs to another event")
        prior = connection.execute(
            "SELECT COALESCE(SUM(amount_cents),0) FROM refunds WHERE payment_id=?",
            (event.payment_id,),
        ).fetchone()[0]
        assert event.amount_cents is not None and event.refund_id is not None
        if prior + event.amount_cents > payment["amount_cents"]:
            raise EventConflict("refund would exceed captured payment")
        before = _snapshot(connection, subscription_id, event.customer_id)
        connection.execute(
            "INSERT INTO refunds VALUES (?, ?, ?, ?)",
            (event.refund_id, event.payment_id, event.amount_cents, at),
        )
        if subscription_id:
            net = connection.execute(
                """SELECT COALESCE(SUM(p.amount_cents),0) - COALESCE((
                     SELECT SUM(r.amount_cents) FROM refunds r JOIN payments q ON q.payment_id=r.payment_id
                     WHERE q.subscription_id=?),0)
                   FROM payments p WHERE p.subscription_id=? AND p.status='succeeded'""",
                (subscription_id, subscription_id),
            ).fetchone()[0]
            if net <= 0:
                prior_entitlement = connection.execute(
                    "SELECT status FROM subscription_entitlements WHERE subscription_id=?",
                    (subscription_id,),
                ).fetchone()
                if not prior_entitlement or prior_entitlement["status"] != "revoked":
                    _revoke_subscription(connection, subscription_id, event.customer_id, at, event.event_id)
                    action = "revoke_subscription"
    else:
        assert subscription_id is not None
        subscription = connection.execute(
            "SELECT customer_id, status FROM subscriptions WHERE subscription_id=?",
            (subscription_id,),
        ).fetchone()
        if subscription is None or subscription["customer_id"] != event.customer_id:
            raise EventConflict("subscription is absent or belongs to another customer")
        before = _snapshot(connection, subscription_id, event.customer_id)
        if event.event_type == "subscription.cancelled":
            prior_entitlement = connection.execute(
                "SELECT status FROM subscription_entitlements WHERE subscription_id=?",
                (subscription_id,),
            ).fetchone()
            if subscription["status"] != "canceled" or not prior_entitlement or prior_entitlement["status"] != "revoked":
                connection.execute(
                    "UPDATE subscriptions SET status='canceled' WHERE subscription_id=?",
                    (subscription_id,),
                )
                _revoke_subscription(connection, subscription_id, event.customer_id, at, event.event_id)
                action = "revoke_subscription"
        else:
            if subscription["status"] != "active":
                raise EventConflict("tier change requires an active subscription")
            assert event.new_tier is not None
            if before["subscription"]["plan_id"] != event.new_tier or (
                before["entitlement"] and before["entitlement"]["tier"] != event.new_tier
            ):
                _ensure_entitlement(connection, subscription_id, event.customer_id, at, event.event_id)
                connection.execute(
                    "UPDATE subscriptions SET plan_id=? WHERE subscription_id=?",
                    (event.new_tier, subscription_id),
                )
                connection.execute(
                    """UPDATE subscription_entitlements SET tier=?, changed_at=?, source_event_id=?
                       WHERE subscription_id=?""",
                    (event.new_tier, at, event.event_id, subscription_id),
                )
                action = "change_tier"
    after = _snapshot(connection, subscription_id, event.customer_id)
    local_action = "record_refund" if event.event_type == "refund.created" else "no_external_change"
    connection.execute(
        "INSERT INTO lifecycle_action_log VALUES (?, ?, ?, ?, ?, ?)",
        (event.event_id, subscription_id, action or local_action, json.dumps(before, sort_keys=True),
         json.dumps(after, sort_keys=True), at),
    )
    return subscription_id, action


def process_lifecycle(connection: sqlite3.Connection, event: LifecycleEvent, *,
                      now: datetime | None = None, delivery: bool = True,
                      adapters: Adapters | None = None, fail_action: bool = False) -> dict:
    """Apply local effects once, then dispatch one scoped provider action idempotently."""
    clock = now or datetime.now(UTC)
    digest = _digest(event)
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT payload_sha256, status, claimed_at FROM processed_events WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        if existing and existing["payload_sha256"] != digest:
            raise EventConflict("event_id already belongs to a different payload")
        if existing and delivery:
            connection.execute(
                "UPDATE processed_events SET deliveries=deliveries+1 WHERE event_id=?", (event.event_id,)
            )
        if existing and existing["status"] in ("completed", "dead_letter"):
            connection.commit()
            return {"event_id": event.event_id, "status": existing["status"], "duplicate": True}
        if (existing and existing["status"] == "processing" and existing["claimed_at"]
                and clock - datetime.fromisoformat(existing["claimed_at"]) < CLAIM_TTL):
            connection.commit()
            return {"event_id": event.event_id, "status": "processing", "duplicate": True}
        if not existing:
            connection.execute(
                """INSERT INTO processed_events
                   (event_id, event_type, source, source_event_id, schema_version, entity_type,
                    entity_id, correlation_id, idempotency_key, payment_id, customer_id,
                    payload_sha256, status, attempts, received_at, claimed_at, trace_id, payload_json)
                   VALUES (?, ?, 'growthops_lifecycle', ?, '2.1', 'subscription', ?, ?, ?,
                           NULL, ?, ?, 'processing', 1, ?, ?, ?, ?)""",
                (event.event_id, event.event_type, event.event_id, event.subscription_id,
                 trace_id_for(event.event_id), event.event_id, event.customer_id, digest,
                 clock.isoformat(), clock.isoformat(), trace_id_for(event.event_id),
                 event.model_dump_json()),
            )
            subscription_id, action = _apply_local(connection, event)
            connection.execute(
                "INSERT INTO workflow_steps VALUES (?, 'apply_local', ?)",
                (event.event_id, clock.isoformat()),
            )
            if action:
                connection.execute(
                    """INSERT INTO event_outbox
                       (outbox_id, event_id, destination, action, idempotency_key, status)
                       VALUES (?, ?, 'community', ?, ?, 'pending')""",
                    (f"{event.event_id}:{action}", event.event_id, action,
                     f"{event.event_id}:{action}"),
                )
                connection.execute(
                    "UPDATE processed_events SET entity_id=? WHERE event_id=?",
                    (subscription_id, event.event_id),
                )
            else:
                connection.execute(
                    """UPDATE processed_events SET status='completed', completed_at=?
                       WHERE event_id=?""",
                    (clock.isoformat(), event.event_id),
                )
                connection.commit()
                return {"event_id": event.event_id, "status": "completed",
                        "action": "record_refund" if event.event_type == "refund.created"
                        else "no_external_change", "duplicate": False}
        else:
            connection.execute(
                """UPDATE processed_events SET status='processing', attempts=attempts+1,
                   claimed_at=?, next_attempt_at=NULL, last_error=NULL WHERE event_id=?""",
                (clock.isoformat(), event.event_id),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    outbox = connection.execute(
        """SELECT o.action, e.entity_id FROM event_outbox o
           JOIN processed_events e ON e.event_id=o.event_id WHERE o.event_id=?""",
        (event.event_id,),
    ).fetchone()
    assert outbox is not None
    action = outbox["action"]
    attempt = connection.execute(
        "SELECT attempts FROM processed_events WHERE event_id=?", (event.event_id,)
    ).fetchone()[0]
    error: str | None = None
    try:
        if fail_action:
            raise ProviderError("community: HTTP 503 Service Unavailable")
        access = adapters.access if adapters else SimulatedAccess()
        scoped = event.model_copy(update={"subscription_id": outbox["entity_id"]})
        if action == "change_tier":
            access.change_tier(scoped)
        else:
            access.revoke_subscription(scoped)
    except ProviderError as exc:
        error = str(exc)
    stamp = datetime.now(UTC).isoformat() if now is None else clock.isoformat()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """INSERT INTO workflow_step_attempts
               (event_id, step_name, attempt, status, started_at, duration_ms, error)
               VALUES (?, ?, ?, ?, ?, 0, ?)""",
            (event.event_id, action, attempt, "failed" if error else "succeeded", stamp, error),
        )
        if error:
            terminal = attempt >= MAX_ATTEMPTS
            next_at = None if terminal else (clock + backoff(attempt)).isoformat()
            status = "dead_letter" if terminal else "failed"
            connection.execute(
                """UPDATE processed_events SET status=?, last_error=?, next_attempt_at=?
                   WHERE event_id=?""",
                (status, error, next_at, event.event_id),
            )
            connection.execute(
                """UPDATE event_outbox SET status=?, attempts=?, last_error=?, next_attempt_at=?
                   WHERE event_id=? AND action=?""",
                ("dead_letter" if terminal else "retry", attempt, error, next_at,
                 event.event_id, action),
            )
        else:
            connection.execute(
                "INSERT OR IGNORE INTO workflow_steps VALUES (?, ?, ?)",
                (event.event_id, action, stamp),
            )
            connection.execute(
                """UPDATE event_outbox SET status='delivered', attempts=?, last_error=NULL,
                   next_attempt_at=NULL, delivered_at=? WHERE event_id=? AND action=?""",
                (attempt, stamp, event.event_id, action),
            )
            connection.execute(
                """UPDATE processed_events SET status='completed', completed_at=?, last_error=NULL,
                   next_attempt_at=NULL WHERE event_id=?""",
                (stamp, event.event_id),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"event_id": event.event_id, "status": "failed" if error and attempt < MAX_ATTEMPTS
            else "dead_letter" if error else "completed", "action": action,
            "attempts": attempt, "last_error": error, "duplicate": False}
