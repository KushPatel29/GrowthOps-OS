"""Small local schema. The target warehouse schema is specified in docs."""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS campaigns (
  campaign_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  medium TEXT NOT NULL,
  campaign_name TEXT NOT NULL,
  spend_cents INTEGER NOT NULL CHECK (spend_cents >= 0),
  registry_valid INTEGER NOT NULL CHECK (registry_valid IN (0, 1))
);
CREATE TABLE IF NOT EXISTS contacts (
  contact_id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  legacy_id TEXT,
  owner_id TEXT,
  original_source TEXT,
  current_stage TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS touches (
  touch_id TEXT PRIMARY KEY,
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
  campaign_id TEXT REFERENCES campaigns(campaign_id),
  occurred_at TEXT NOT NULL,
  touch_type TEXT NOT NULL,
  utm_source TEXT
);
CREATE TABLE IF NOT EXISTS lifecycle_events (
  lifecycle_event_id TEXT PRIMARY KEY,
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
  stage TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deals (
  deal_id TEXT PRIMARY KEY,
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
  stage TEXT NOT NULL,
  closed_at TEXT
);
CREATE TABLE IF NOT EXISTS payments (
  payment_id TEXT PRIMARY KEY,
  deal_id TEXT REFERENCES deals(deal_id),
  customer_id TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  status TEXT NOT NULL,
  paid_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refunds (
  refund_id TEXT PRIMARY KEY,
  payment_id TEXT NOT NULL REFERENCES payments(payment_id),
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  refunded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS access_entitlements (
  customer_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  granted_at TEXT NOT NULL,
  source_event_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  payment_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  received_at TEXT NOT NULL,
  claimed_at TEXT,
  completed_at TEXT
);
CREATE TABLE IF NOT EXISTS workflow_steps (
  event_id TEXT NOT NULL REFERENCES processed_events(event_id),
  step_name TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  PRIMARY KEY (event_id, step_name)
);
CREATE INDEX IF NOT EXISTS idx_touches_contact_time ON touches(contact_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_lifecycle_contact_time ON lifecycle_events(contact_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_payments_deal ON payments(deal_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_processed_payment ON processed_events(payment_id);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
