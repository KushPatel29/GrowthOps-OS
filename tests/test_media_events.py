from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from growthops.api import app
from growthops.media_events import MediaEvent, ingest_media_event, media_health


def test_normalized_media_ingress_preserves_identity_and_idempotency(connection):
    event = MediaEvent(source_event_id="yt-test-1", platform="youtube",
                       event_type="video_progress", content_id="video-1",
                       occurred_at=datetime(2026, 9, 20, tzinfo=UTC),
                       person_key="c-000789", duration_seconds=120)
    accepted = ingest_media_event(connection, event)
    assert not accepted["duplicate"] and accepted["identity_state"] == "resolved"
    assert ingest_media_event(connection, event)["duplicate"]
    with pytest.raises(ValueError, match="conflicts"):
        ingest_media_event(connection, event.model_copy(update={"duration_seconds": 180}))
    anonymous = event.model_copy(update={"source_event_id": "yt-test-2", "person_key": None,
                                         "anonymous_id_hash": "a" * 64})
    assert ingest_media_event(connection, anonymous)["identity_state"] == "unresolved"
    assert media_health(connection)["provider_webhooks_connected"] is False


def test_media_contract_and_api(db_path, monkeypatch):
    with pytest.raises(ValidationError):
        MediaEvent(source_event_id="x", platform="zoom", event_type="video_progress",
                   content_id="webinar", occurred_at=datetime(2026, 9, 20, tzinfo=UTC),
                   person_key="c-000789")
    monkeypatch.setenv("GROWTHOPS_DATABASE", str(db_path))
    payload = {"source_event_id": "zoom-attended-1", "platform": "zoom",
               "event_type": "webinar_attended", "content_id": "webinar-growth",
               "occurred_at": "2026-09-20T10:00:00+00:00", "person_key": "c-000789",
               "duration_seconds": 3600}
    with TestClient(app) as client:
        first = client.post("/v2/engagement/events", json=payload)
        assert first.status_code == 202 and not first.json()["duplicate"]
        assert client.post("/v2/engagement/events", json=payload).json()["duplicate"]
        changed = client.post("/v2/engagement/events", json={**payload, "duration_seconds": 30})
        assert changed.status_code == 409
        assert client.get("/v2/engagement/health").status_code == 200
