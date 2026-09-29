"""Normalized engagement contract for trusted YouTube, Zoom and Vimeo adapters."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

PROVIDERS = {
    "youtube": {"video_progress", "video_complete"},
    "vimeo": {"video_progress", "video_complete"},
    "zoom": {"webinar_registered", "webinar_attended"},
}


class MediaEvent(BaseModel):
    source_event_id: str = Field(min_length=1, max_length=128)
    platform: Literal["youtube", "zoom", "vimeo"]
    event_type: str = Field(min_length=1, max_length=64)
    content_id: str = Field(min_length=1, max_length=128)
    occurred_at: datetime
    person_key: str | None = Field(default=None, max_length=128)
    anonymous_id_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    duration_seconds: int = Field(default=0, ge=0, le=86400)

    @model_validator(mode="after")
    def valid_contract(self) -> MediaEvent:
        if self.event_type not in PROVIDERS[self.platform]:
            raise ValueError("event type is not registered for this media provider")
        if not self.person_key and not self.anonymous_id_hash:
            raise ValueError("person_key or hashed anonymous ID is required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return self


def ingest_media_event(connection: sqlite3.Connection, event: MediaEvent) -> dict:
    """Idempotently accept one normalized event; ambiguous identity stays unresolved."""
    event_id = "media:" + hashlib.sha256(
        f"{event.platform}:{event.source_event_id}".encode()
    ).hexdigest()[:24]
    existing = connection.execute(
        """SELECT platform, event_type, person_key, anonymous_id_hash, content_id,
                  occurred_at, duration_seconds FROM engagement_events WHERE event_id=?""",
        (event_id,),
    ).fetchone()
    occurred_utc = event.occurred_at.astimezone(UTC).isoformat()
    values = (event.platform, event.event_type, event.person_key, event.anonymous_id_hash,
              event.content_id, occurred_utc, event.duration_seconds)
    if existing:
        if tuple(existing) != values:
            raise ValueError("source event ID conflicts with its first payload")
        return {"event_id": event_id, "duplicate": True, "identity_state":
                "resolved" if event.person_key else "unresolved"}
    if event.person_key and not connection.execute(
        "SELECT 1 FROM persons WHERE person_key=?", (event.person_key,),
    ).fetchone():
        raise ValueError("person_key is not in the identity registry")
    connection.execute(
        """INSERT INTO engagement_events
           (event_id, person_key, anonymous_id_hash, platform, content_id,
            event_type, occurred_at, duration_seconds, source_event_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, event.person_key, event.anonymous_id_hash, event.platform,
         event.content_id, event.event_type, occurred_utc,
         event.duration_seconds, f"{event.platform}:{event.source_event_id}"),
    )
    return {"event_id": event_id, "duplicate": False, "identity_state":
            "resolved" if event.person_key else "unresolved"}


def media_health(connection: sqlite3.Connection) -> dict:
    rows = connection.execute(
        """SELECT platform, COUNT(*) events, SUM(person_key IS NOT NULL) resolved,
                  SUM(person_key IS NULL) unresolved FROM engagement_events
           GROUP BY platform ORDER BY platform"""
    ).fetchall()
    return {"scope": "synthetic_plus_trusted_normalized_events",
            "providers": [dict(row) for row in rows],
            "provider_webhooks_connected": False}
