"""HubSpot webhooks: verify the v3 signature, record each event once, and fetch what changed.

**Signature.** HubSpot signs every delivery with the app's client secret in ``X-HubSpot-Signature-v3``: base64 of an
HMAC-SHA256 over the request method, the full request URI, the raw body and the ``X-HubSpot-Request-Timestamp``
header, concatenated in that order. Before signing, HubSpot decodes a fixed set of URL-encoded characters in the
URI, so the receiver does the same. A delivery whose timestamp is more than five minutes old is rejected, so a
captured request cannot be replayed. Behind a proxy or load balancer the URI HubSpot signed is the public one, so a
deployment sets ``GROWTHOPS_PUBLIC_BASE_URL`` and the receiver rebuilds the URI from it.

**Delivery.** HubSpot batches up to 100 events per request and retries a failed delivery for up to three days, so
each event is stored once by its ``eventId``: a retried delivery is acknowledged and changes nothing. Events from a
portal other than the pinned one are recorded as ignored.

**Processing.** An event is a signal, not data. The worker reads the record's current state from HubSpot (batch read
by ID) and lands it, so a stale, duplicated or out-of-order event can never write an old value. A deletion archives
the landed record; a **privacy deletion** (a GDPR request made in HubSpot) removes the landed record entirely; a
merge archives the merged-away records and refetches the survivor.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime

from growthops.hubspot_client import HubSpotClient, HubSpotError
from growthops.hubspot_contract import pull_properties, rules_by_field
from growthops.hubspot_sync import _land, _read_by_id

TOLERANCE_MS = 300_000
MAX_EVENTS = 1_000
# HubSpot decodes these before it signs the URI (its v3 signature documentation lists them).
_DECODED = {"%3A": ":", "%2F": "/", "%3F": "?", "%40": "@", "%21": "!", "%24": "$", "%27": "'", "%28": "(",
            "%29": ")", "%2A": "*", "%2C": ",", "%3B": ";"}
_ENCODED = re.compile("|".join(_DECODED), re.IGNORECASE)
OBJECT_TYPES = {"contact": "contacts", "deal": "deals"}


def canonical_uri(uri: str) -> str:
    return _ENCODED.sub(lambda match: _DECODED[match.group(0).upper()], uri)


def signature(secret: str, method: str, uri: str, body: bytes, timestamp: str) -> str:
    message = method.upper().encode() + canonical_uri(uri).encode() + body + timestamp.encode()
    return base64.b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest()).decode()


def verify(secret: str, method: str, uri: str, body: bytes, timestamp: str, provided: str, *,
           now: datetime | None = None) -> bool:
    """True only for a correctly signed delivery made within the last five minutes."""
    if not secret or not provided or not timestamp.isdecimal() or len(timestamp) > 13:
        return False
    now_ms = int((now or datetime.now(UTC)).timestamp() * 1000)
    if abs(now_ms - int(timestamp)) > TOLERANCE_MS:
        return False
    return hmac.compare_digest(signature(secret, method, uri, body, timestamp), provided)


def record(connection: sqlite3.Connection, events: list, portal_id: str | None = None,
           now: datetime | None = None) -> dict:
    """Store each event once. Returns how many were new, repeated deliveries, or ignored."""
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError(f"a HubSpot delivery is a JSON list of at most {MAX_EVENTS} events")
    now = now or datetime.now(UTC)
    counts = {"accepted": 0, "duplicates": 0, "ignored": 0}
    for event in events:
        if not isinstance(event, dict) or "eventId" not in event or "subscriptionType" not in event:
            raise ValueError("each event needs an eventId and a subscriptionType")
        kind = str(event["subscriptionType"])
        object_type = OBJECT_TYPES.get(kind.partition(".")[0])
        status, detail = "pending", None
        if portal_id and str(event.get("portalId")) != str(portal_id):
            status, detail = "ignored", "event for another portal"
        elif object_type is None:
            status, detail = "ignored", f"{kind} is not synced"
        if kind.endswith(".merge"):
            detail = json.dumps({"merged": [str(i) for i in event.get("mergedObjectIds") or []]})
        object_id = event.get("primaryObjectId") or event.get("newObjectId") or event.get("objectId")
        occurred = event.get("occurredAt")
        inserted = connection.execute(
            "INSERT OR IGNORE INTO hubspot_webhook_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (str(event["eventId"]), str(event.get("portalId", "")), kind, object_type,
             str(object_id) if object_id is not None else None, event.get("propertyName"),
             datetime.fromtimestamp(occurred / 1000, UTC).isoformat() if isinstance(occurred, int) else None,
             int(event.get("attemptNumber") or 0), now.isoformat(), status, detail)).rowcount
        if not inserted:
            counts["duplicates"] += 1
        else:
            counts["accepted" if status == "pending" else "ignored"] += 1
    return counts


def _done(connection: sqlite3.Connection, event_ids: list[str], status: str, now: datetime,
          detail: str | None = None) -> None:
    connection.executemany("UPDATE hubspot_webhook_events SET status=?, processed_at=?, detail=COALESCE(?, detail) "
                           "WHERE event_id=?", [(status, now.isoformat(), detail, e) for e in event_ids])


def process(client: HubSpotClient, connection: sqlite3.Connection, now: datetime | None = None,
            limit: int = 500) -> dict:
    """Apply pending events by reading each changed record from HubSpot. Transient failures stay pending."""
    now = now or datetime.now(UTC)
    pending = connection.execute(
        "SELECT * FROM hubspot_webhook_events WHERE status='pending' ORDER BY received_at, event_id LIMIT ?",
        (limit,)).fetchall()
    rules = rules_by_field(connection)
    result = {"events": len(pending), "refetched": 0, "archived": 0, "erased": 0, "failed": 0}
    refetch: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for event in pending:
        object_type, object_id, kind = event["object_type"], event["object_id"], event["subscription_type"]
        if kind.endswith(".privacyDeletion"):
            result["erased"] += connection.execute("DELETE FROM hubspot_records WHERE object_type=? AND hs_id=?",
                                                   (object_type, object_id)).rowcount
            _done(connection, [event["event_id"]], "processed", now)
        elif kind.endswith(".deletion"):
            result["archived"] += connection.execute(
                "UPDATE hubspot_records SET archived=1, synced_at=? WHERE object_type=? AND hs_id=?",
                (now.isoformat(), object_type, object_id)).rowcount
            _done(connection, [event["event_id"]], "processed", now)
        else:
            if kind.endswith(".merge"):
                merged = json.loads(event["detail"] or "{}").get("merged", [])
                connection.executemany("UPDATE hubspot_records SET archived=1, synced_at=? WHERE object_type=? "
                                       "AND hs_id=? AND hs_id<>?",
                                       [(now.isoformat(), object_type, m, object_id) for m in merged])
            refetch[object_type][object_id].append(event["event_id"])
    for object_type, by_id in refetch.items():
        try:
            found = _read_by_id(client, object_type, sorted(by_id), pull_properties(connection, object_type))
        except HubSpotError as exc:
            if not exc.transient:
                _done(connection, [e for ids in by_id.values() for e in ids], "failed", now, exc.category or str(exc))
                result["failed"] += sum(map(len, by_id.values()))
            continue  # a transient failure leaves the events pending for the next pass
        for object_id, event_ids in by_id.items():
            if object_id in found:
                _land(connection, object_type, found[object_id], rules, now)
                result["refetched"] += 1
            else:  # gone since the event: HubSpot answers OBJECT_NOT_FOUND
                result["archived"] += connection.execute(
                    "UPDATE hubspot_records SET archived=1, synced_at=? WHERE object_type=? AND hs_id=?",
                    (now.isoformat(), object_type, object_id)).rowcount
            _done(connection, event_ids, "processed", now)
    return result
