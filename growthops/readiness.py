"""Fail-closed data provenance and freshness checks for a live deployment."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from growthops.config import ConfigError, Settings
from growthops.db import SCHEMA_VERSION, schema_version
from growthops.freshness import check as freshness_check


def origin_problem(connection: sqlite3.Connection) -> str | None:
    """A live-mode env variable alone never turns generated rows into live data."""
    try:
        row = connection.execute(
            "SELECT origin, evidence_ref FROM dataset_origin WHERE singleton=1",
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None:
        return "dataset origin is unverified; complete a provider-backed ingestion before live mode"
    if row["origin"] != "live_verified" or not row["evidence_ref"].strip():
        return "dataset origin is synthetic or lacks live ingestion evidence"
    return None


def require_live_origin(connection: sqlite3.Connection, settings: Settings) -> None:
    """Block production startup on synthetic, old or unmarked databases."""
    if not settings.production:
        return
    if schema_version(connection) != SCHEMA_VERSION:
        raise ConfigError("database schema is not current")
    if problem := origin_problem(connection):
        raise ConfigError(problem)
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        raise ConfigError("database contains foreign-key violations")


def hubspot_freshness(connection: sqlite3.Connection, settings: Settings, now: datetime | None = None) -> dict:
    """The HubSpot sync as a source: stale after three missed sync intervals, or while its last run failed."""
    sla_hours = round(3 * settings.hubspot_sync_minutes / 60, 2)
    try:
        row = connection.execute("SELECT MIN(last_success_at) oldest, MAX(last_error IS NOT NULL) failing "
                                 "FROM hubspot_sync_state").fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None or row["oldest"] is None:
        return {"source": "hubspot_sync", "latest": None, "age_hours": None, "sla_hours": sla_hours,
                "status": "missing"}
    age = round(((now or datetime.now(UTC)) - datetime.fromisoformat(row["oldest"])).total_seconds() / 3600, 2)
    status = "stale" if age > sla_hours or row["failing"] else "fresh"
    return {"source": "hubspot_sync", "latest": row["oldest"], "age_hours": age, "sla_hours": sla_hours,
            "status": status}


def readiness_status(connection: sqlite3.Connection, settings: Settings) -> dict:
    version = schema_version(connection)
    sources = freshness_check(connection, settings)
    if settings.hubspot_sync_enabled:
        sources.append(hubspot_freshness(connection, settings))
    stale = [item["source"] for item in sources if item["status"] != "fresh"]
    provenance = origin_problem(connection) if settings.production else None
    if version != SCHEMA_VERSION:
        status = "schema_mismatch"
    elif provenance:
        status = "unverified_dataset"
    elif stale:
        status = "stale_sources"
    else:
        status = "ready"
    return {"status": status, "schema_version": version, "expected_schema_version": SCHEMA_VERSION,
            "stale_sources": stale, "sources": sources,
            "origin_verified": None if not settings.production else provenance is None}
