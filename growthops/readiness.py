"""Fail-closed data provenance and freshness checks for a live deployment."""

from __future__ import annotations

import sqlite3

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


def readiness_status(connection: sqlite3.Connection, settings: Settings) -> dict:
    version = schema_version(connection)
    sources = freshness_check(connection, settings)
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
