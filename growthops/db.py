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
  registry_valid INTEGER NOT NULL CHECK (registry_valid IN (0, 1)),
  platform TEXT NOT NULL DEFAULT 'unknown',
  landing_page TEXT,
  launched_on TEXT,
  ended_on TEXT
);
CREATE TABLE IF NOT EXISTS ad_spend_daily (
  campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
  spend_date TEXT NOT NULL,
  spend_cents INTEGER NOT NULL CHECK (spend_cents >= 0),
  impressions INTEGER NOT NULL CHECK (impressions >= 0),
  clicks INTEGER NOT NULL CHECK (clicks >= 0),
  PRIMARY KEY (campaign_id, spend_date)
);
CREATE TABLE IF NOT EXISTS products (
  product_id TEXT PRIMARY KEY,
  product_name TEXT NOT NULL,
  list_price_cents INTEGER NOT NULL CHECK (list_price_cents > 0),
  billing TEXT NOT NULL CHECK (billing IN ('one_time','plan','annual'))
);
CREATE TABLE IF NOT EXISTS content_items (
  content_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  platform TEXT NOT NULL,
  published_at TEXT NOT NULL,
  views INTEGER NOT NULL CHECK (views >= 0),
  clicks INTEGER NOT NULL CHECK (clicks >= 0),
  offer_id TEXT NOT NULL,
  topic TEXT NOT NULL DEFAULT 'systems'
);
CREATE TABLE IF NOT EXISTS contacts (
  contact_id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  legacy_id TEXT,
  owner_id TEXT,
  original_source TEXT,
  current_stage TEXT NOT NULL,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS legacy_contacts (
  legacy_id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  owner_id TEXT,
  original_source TEXT,
  lifecycle_stage TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS migration_repairs (
  repair_id TEXT PRIMARY KEY,
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
  legacy_id TEXT NOT NULL REFERENCES legacy_contacts(legacy_id),
  field_name TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  UNIQUE (contact_id, field_name)
);
CREATE TABLE IF NOT EXISTS touches (
  touch_id TEXT PRIMARY KEY,
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
  campaign_id TEXT REFERENCES campaigns(campaign_id),
  occurred_at TEXT NOT NULL,
  touch_type TEXT NOT NULL,
  utm_source TEXT,
  landing_page TEXT,
  -- Simulation ground truth for evaluating tracking loss; never read by metrics.
  sim_true_campaign_id TEXT
);
CREATE TABLE IF NOT EXISTS content_engagements (
  engagement_id TEXT PRIMARY KEY,
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
  content_id TEXT NOT NULL REFERENCES content_items(content_id),
  touch_id TEXT REFERENCES touches(touch_id),
  occurred_at TEXT NOT NULL,
  watched_seconds INTEGER NOT NULL CHECK (watched_seconds >= 0)
);
CREATE TABLE IF NOT EXISTS experiments (
  experiment_id TEXT PRIMARY KEY,
  hypothesis TEXT NOT NULL,
  primary_metric TEXT NOT NULL,
  assignment_unit TEXT NOT NULL,
  started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_variants (
  variant_id TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  label TEXT NOT NULL,
  cta_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_exposures (
  exposure_id TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  variant_id TEXT NOT NULL REFERENCES experiment_variants(variant_id),
  visitor_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  contact_id TEXT REFERENCES contacts(contact_id),
  exposed_at TEXT NOT NULL,
  UNIQUE (experiment_id, visitor_id)
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
  stage TEXT NOT NULL CHECK (stage IN ('open','closed_won','closed_lost')),
  closed_at TEXT,
  product_id TEXT REFERENCES products(product_id),
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS payments (
  payment_id TEXT PRIMARY KEY,
  deal_id TEXT REFERENCES deals(deal_id),
  customer_id TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  status TEXT NOT NULL CHECK (status IN ('succeeded','failed')),
  paid_at TEXT NOT NULL,
  payment_type TEXT NOT NULL DEFAULT 'new' CHECK (payment_type IN ('new','installment','renewal')),
  subscription_id TEXT,
  product_id TEXT
);
CREATE TABLE IF NOT EXISTS refunds (
  refund_id TEXT PRIMARY KEY,
  payment_id TEXT NOT NULL REFERENCES payments(payment_id),
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  refunded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
  subscription_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES contacts(contact_id),
  plan_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  renewal_due_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','canceled'))
);
CREATE TABLE IF NOT EXISTS renewal_attempts (
  attempt_id TEXT PRIMARY KEY,
  subscription_id TEXT NOT NULL REFERENCES subscriptions(subscription_id),
  attempted_at TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('failed','succeeded')),
  failure_code TEXT
);
CREATE TABLE IF NOT EXISTS platform_conversions (
  conversion_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  campaign_id TEXT REFERENCES campaigns(campaign_id),
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
  reported_at TEXT NOT NULL,
  reported_value_cents INTEGER NOT NULL CHECK (reported_value_cents >= 0),
  click_through INTEGER NOT NULL CHECK (click_through IN (0, 1)),
  attribution_setting TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
  incident_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  starts_at TEXT NOT NULL,
  ends_at TEXT,
  entity TEXT NOT NULL,
  expected_signal TEXT NOT NULL,
  description TEXT NOT NULL
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
  status TEXT NOT NULL CHECK (status IN ('received','processing','completed','failed','dead_letter')),
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  received_at TEXT NOT NULL,
  claimed_at TEXT,
  completed_at TEXT,
  trace_id TEXT,
  payload_json TEXT,
  next_attempt_at TEXT,
  deliveries INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS workflow_steps (
  event_id TEXT NOT NULL REFERENCES processed_events(event_id),
  step_name TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  PRIMARY KEY (event_id, step_name)
);
CREATE TABLE IF NOT EXISTS workflow_step_attempts (
  event_id TEXT NOT NULL REFERENCES processed_events(event_id),
  step_name TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('succeeded','failed')),
  started_at TEXT NOT NULL,
  duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
  error TEXT,
  PRIMARY KEY (event_id, step_name, attempt)
);
CREATE INDEX IF NOT EXISTS idx_touches_contact_time ON touches(contact_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_lifecycle_contact_time ON lifecycle_events(contact_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_lifecycle_stage ON lifecycle_events(stage, contact_id);
CREATE INDEX IF NOT EXISTS idx_payments_deal ON payments(deal_id);
CREATE INDEX IF NOT EXISTS idx_payments_customer ON payments(customer_id);
CREATE INDEX IF NOT EXISTS idx_processed_status ON processed_events(status, next_attempt_at);
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
