"""Strict Stripe snapshot-event bridge for the local synthetic test workflow.

Only signed test-mode events with explicit GrowthOps IDs and USD amounts are
translated. This does not fetch Stripe objects or enable a production webhook.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Literal

from growthops.lifecycle import LifecycleEvent
from growthops.workflow import PaymentEvent

SIGNATURE_TOLERANCE_SECONDS = 300


class StripeEventError(ValueError):
    """A Stripe test event is untrusted, unsupported or lacks required mapping."""


def verify_stripe_signature(body: bytes, header: str, secret: str, *,
                            now: datetime | None = None) -> bool:
    """Verify Stripe's t/v1 HMAC over the exact raw payload within five minutes."""
    if not secret.startswith("whsec_") or len(secret) < 32:
        return False
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        key, separator, value = item.strip().partition("=")
        if separator:
            parts.setdefault(key, []).append(value)
    timestamps = parts.get("t", [])
    signatures = parts.get("v1", [])
    if len(timestamps) != 1 or not timestamps[0].isdecimal() or not signatures:
        return False
    timestamp = int(timestamps[0])
    present = int((now or datetime.now(UTC)).timestamp())
    if abs(present - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    signed = timestamps[0].encode() + b"." + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in signatures)


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StripeEventError(f"{name} is required")
    return value


def _metadata(obj: dict, name: str) -> str:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        raise StripeEventError("Stripe object metadata is required for identity mapping")
    return _required(metadata.get(name), f"metadata.{name}")


def _usd(obj: dict) -> None:
    if obj.get("currency") != "usd":
        raise StripeEventError("only USD test events are supported")


def _amount(obj: dict, name: str) -> int:
    value = obj.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise StripeEventError(f"{name} must be a positive integer amount")
    return value


def normalize_stripe_test_event(body: bytes) -> PaymentEvent | LifecycleEvent | None:
    """Translate an allowlisted snapshot event; return None for other event types."""
    try:
        envelope = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StripeEventError("invalid Stripe JSON") from exc
    if not isinstance(envelope, dict) or envelope.get("object") != "event":
        raise StripeEventError("Stripe event envelope is required")
    if envelope.get("livemode") is not False:
        raise StripeEventError("only Stripe test-mode events are accepted")
    event_id = _required(envelope.get("id"), "event.id")
    created = envelope.get("created")
    if not isinstance(created, int) or isinstance(created, bool) or created <= 0:
        raise StripeEventError("event.created must be a Unix timestamp")
    at = datetime.fromtimestamp(created, UTC)
    event_type = envelope.get("type")
    allowed = {"payment_intent.succeeded", "customer.subscription.updated",
               "customer.subscription.deleted", "refund.created"}
    if event_type not in allowed:
        return None
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise StripeEventError("event.data must be a snapshot")
    obj = data.get("object")
    if not isinstance(obj, dict):
        raise StripeEventError("data.object must be a snapshot object")
    if event_type == "payment_intent.succeeded":
        if obj.get("object") != "payment_intent" or obj.get("status") != "succeeded":
            raise StripeEventError("a succeeded PaymentIntent is required")
        _usd(obj)
        metadata = obj.get("metadata")
        if not isinstance(metadata, dict):
            raise StripeEventError("Stripe object metadata is required for identity mapping")
        payment_type = metadata.get("growthops_payment_type", "new")
        return PaymentEvent(
            event_id=event_id, event_type="payment.succeeded",
            payment_id=_required(obj.get("id"), "payment_intent.id"),
            customer_id=_metadata(obj, "growthops_customer_id"),
            deal_id=metadata.get("growthops_deal_id") or None,
            amount_cents=_amount(obj, "amount_received"), paid_at=at,
            payment_type=payment_type,
            subscription_id=metadata.get("growthops_subscription_id") or None,
            product_id=metadata.get("growthops_product_id") or None,
        )
    if event_type == "refund.created":
        if obj.get("object") != "refund":
            raise StripeEventError("a Refund object is required")
        if obj.get("status") != "succeeded":
            raise StripeEventError("refund is not succeeded; wait for verified settlement")
        _usd(obj)
        metadata = obj.get("metadata")
        if not isinstance(metadata, dict):
            raise StripeEventError("Stripe object metadata is required for identity mapping")
        return LifecycleEvent(
            event_id=event_id, event_type="refund.created",
            customer_id=_metadata(obj, "growthops_customer_id"),
            payment_id=_required(obj.get("payment_intent"), "refund.payment_intent"),
            subscription_id=metadata.get("growthops_subscription_id") or None,
            refund_id=_required(obj.get("id"), "refund.id"),
            amount_cents=_amount(obj, "amount"), occurred_at=at,
        )
    if obj.get("object") != "subscription":
        raise StripeEventError("a Subscription object is required")
    customer_id = _metadata(obj, "growthops_customer_id")
    subscription_id = _metadata(obj, "growthops_subscription_id")
    if event_type == "customer.subscription.deleted":
        if obj.get("status") != "canceled":
            raise StripeEventError("a canceled Subscription is required")
        return LifecycleEvent(event_id=event_id, event_type="subscription.cancelled",
                              customer_id=customer_id, subscription_id=subscription_id,
                              occurred_at=at)
    previous = data.get("previous_attributes")
    if obj.get("status") != "active" or not isinstance(previous, dict) or "items" not in previous:
        raise StripeEventError("tier update requires active status and changed items")
    marker = _metadata(obj, "growthops_tier_change")
    if marker not in {"upgrade", "downgrade"}:
        raise StripeEventError("tier update requires explicit upgrade or downgrade marker")
    lifecycle_type: Literal["subscription.upgraded", "subscription.downgraded"] = (
        "subscription.upgraded" if marker == "upgrade" else "subscription.downgraded"
    )
    return LifecycleEvent(event_id=event_id, event_type=lifecycle_type,
                          customer_id=customer_id, subscription_id=subscription_id,
                          new_tier=_metadata(obj, "growthops_new_tier"), occurred_at=at)
