"""Small local schema. The target warehouse schema is specified in docs."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
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
CREATE TABLE IF NOT EXISTS email_campaigns (
  email_id TEXT PRIMARY KEY,
  sent_at TEXT NOT NULL,
  email_type TEXT NOT NULL CHECK (email_type IN ('newsletter','webinar_invite','promo','nurture')),
  subject TEXT NOT NULL,
  sending_domain TEXT NOT NULL,
  sends INTEGER NOT NULL CHECK (sends >= 0),
  delivered INTEGER NOT NULL CHECK (delivered >= 0),
  bounces INTEGER NOT NULL CHECK (bounces >= 0),
  opens INTEGER NOT NULL CHECK (opens >= 0),
  machine_opens INTEGER NOT NULL CHECK (machine_opens >= 0),
  clicks INTEGER NOT NULL CHECK (clicks >= 0),
  unsubscribes INTEGER NOT NULL CHECK (unsubscribes >= 0),
  spam_complaints INTEGER NOT NULL CHECK (spam_complaints >= 0),
  CHECK (delivered + bounces = sends),
  CHECK (opens <= delivered AND machine_opens <= opens AND clicks <= delivered)
);
CREATE TABLE IF NOT EXISTS short_links (
  link_id TEXT PRIMARY KEY,
  destination_url TEXT NOT NULL,
  channel TEXT NOT NULL,
  created_at TEXT NOT NULL,
  owner TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS short_link_clicks (
  link_id TEXT NOT NULL REFERENCES short_links(link_id),
  click_date TEXT NOT NULL,
  clicks INTEGER NOT NULL CHECK (clicks >= 0),
  PRIMARY KEY (link_id, click_date)
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
  source TEXT NOT NULL DEFAULT 'growthops_internal',
  source_event_id TEXT,
  schema_version TEXT NOT NULL DEFAULT '1',
  entity_type TEXT,
  entity_id TEXT,
  correlation_id TEXT,
  idempotency_key TEXT,
  payload_ref TEXT,
  payment_id TEXT,
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


# Changes after the base schema, applied once each in order and recorded in schema_migrations.
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (2, """
    CREATE TABLE IF NOT EXISTS alert_log (
      alert_key TEXT PRIMARY KEY,
      first_sent_at TEXT NOT NULL,
      last_sent_at TEXT NOT NULL,
      sends INTEGER NOT NULL DEFAULT 1,
      channel TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('sent','dry_run','failed')),
      payload_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS job_runs (
      job TEXT NOT NULL,
      run_key TEXT NOT NULL,
      started_at TEXT NOT NULL,
      finished_at TEXT,
      status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed')),
      detail TEXT,
      PRIMARY KEY (job, run_key)
    );
    CREATE TABLE IF NOT EXISTS ask_log (
      ask_id TEXT PRIMARY KEY,
      asked_at TEXT NOT NULL,
      question TEXT NOT NULL,
      route TEXT NOT NULL CHECK (route IN ('certified','metric','definition','refused')),
      target TEXT,
      score REAL,
      retrieval_mode TEXT NOT NULL,
      latency_ms INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ask_log_time ON ask_log(asked_at);
    """),
    # Version 3 records the additive upgrade of databases created by the
    # original local simulator. SQLite has no ADD COLUMN IF NOT EXISTS, so
    # _upgrade_legacy_columns performs the introspection before SCHEMA runs.
    (3, "SELECT 1;"),
    (4, """
    CREATE TABLE IF NOT EXISTS persons (
      person_key TEXT PRIMARY KEY,
      created_at TEXT NOT NULL,
      resolution_version TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('resolved','ambiguous','unresolved'))
    );
    CREATE TABLE IF NOT EXISTS identity_links (
      source_system TEXT NOT NULL,
      id_type TEXT NOT NULL,
      id_hash TEXT NOT NULL,
      person_key TEXT NOT NULL REFERENCES persons(person_key),
      first_seen_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL,
      evidence_event_id TEXT,
      confidence TEXT NOT NULL CHECK (confidence IN ('verified','observed')),
      rule_version TEXT NOT NULL,
      state TEXT NOT NULL CHECK (state IN ('active','ambiguous')),
      PRIMARY KEY (source_system, id_type, id_hash)
    );
    CREATE INDEX IF NOT EXISTS idx_identity_person ON identity_links(person_key);
    CREATE TABLE IF NOT EXISTS lifecycle_transitions (
      transition_id TEXT PRIMARY KEY,
      person_key TEXT NOT NULL REFERENCES persons(person_key),
      from_stage TEXT,
      to_stage TEXT NOT NULL,
      occurred_at TEXT NOT NULL,
      source_event_id TEXT NOT NULL UNIQUE,
      policy_version TEXT NOT NULL,
      owner_id TEXT,
      campaign_id TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_transition_person_time
      ON lifecycle_transitions(person_key, occurred_at);
    CREATE TABLE IF NOT EXISTS deal_qualification (
      decision_id TEXT PRIMARY KEY,
      deal_id TEXT NOT NULL REFERENCES deals(deal_id),
      qualified_at TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('qualified','unqualified')),
      reason TEXT NOT NULL,
      amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
      currency TEXT NOT NULL,
      source_event_id TEXT NOT NULL UNIQUE,
      policy_version TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_qualification_deal_time
      ON deal_qualification(deal_id, qualified_at);
    CREATE TABLE IF NOT EXISTS quality_issues (
      issue_id TEXT PRIMARY KEY,
      rule_id TEXT NOT NULL,
      entity_type TEXT NOT NULL,
      entity_id TEXT NOT NULL,
      severity TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
      first_seen_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL,
      state TEXT NOT NULL CHECK (state IN ('open','resolved')),
      evidence_ref TEXT NOT NULL,
      UNIQUE (rule_id, entity_type, entity_id)
    );
    CREATE INDEX IF NOT EXISTS idx_quality_state
      ON quality_issues(state, severity, rule_id);
    CREATE TABLE IF NOT EXISTS registry_versions (
      registry_type TEXT NOT NULL,
      version TEXT NOT NULL,
      definition_json TEXT NOT NULL,
      owner TEXT NOT NULL,
      approved_at TEXT NOT NULL,
      effective_at TEXT NOT NULL,
      PRIMARY KEY (registry_type, version)
    );
    CREATE TABLE IF NOT EXISTS consent_ledger (
      consent_id TEXT PRIMARY KEY,
      person_key TEXT NOT NULL REFERENCES persons(person_key),
      channel TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('granted','denied','unknown','revoked')),
      source TEXT NOT NULL,
      recorded_at TEXT NOT NULL,
      evidence_ref TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS operator_actions (
      action_id TEXT PRIMARY KEY,
      actor_id TEXT NOT NULL,
      action_type TEXT NOT NULL,
      target_type TEXT NOT NULL,
      target_id TEXT NOT NULL,
      reason TEXT NOT NULL,
      requested_at TEXT NOT NULL,
      result TEXT NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_event_source_id
      ON processed_events(source, source_event_id)
      WHERE source_event_id IS NOT NULL;
    UPDATE processed_events SET source_event_id=event_id,
      correlation_id=COALESCE(correlation_id, trace_id),
      idempotency_key=COALESCE(idempotency_key, event_id)
      WHERE source_event_id IS NULL;
    """),
    (5, """
    CREATE TABLE IF NOT EXISTS event_outbox (
      outbox_id TEXT PRIMARY KEY,
      event_id TEXT NOT NULL REFERENCES processed_events(event_id),
      destination TEXT NOT NULL,
      action TEXT NOT NULL,
      idempotency_key TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL CHECK (status IN ('pending','delivered','retry','dead_letter')),
      attempts INTEGER NOT NULL DEFAULT 0,
      last_error TEXT,
      next_attempt_at TEXT,
      delivered_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_outbox_status ON event_outbox(status, next_attempt_at);
    CREATE TABLE IF NOT EXISTS engagement_events (
      event_id TEXT PRIMARY KEY,
      person_key TEXT REFERENCES persons(person_key),
      anonymous_id_hash TEXT,
      platform TEXT NOT NULL,
      content_id TEXT,
      event_type TEXT NOT NULL,
      occurred_at TEXT NOT NULL,
      duration_seconds INTEGER NOT NULL DEFAULT 0 CHECK (duration_seconds >= 0),
      source_event_id TEXT NOT NULL UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_engagement_person_time
      ON engagement_events(person_key, occurred_at);
    """),
    (6, """
    CREATE TABLE IF NOT EXISTS subscription_entitlements (
      subscription_id TEXT PRIMARY KEY,
      customer_id TEXT NOT NULL REFERENCES contacts(contact_id),
      product_id TEXT,
      tier TEXT,
      status TEXT NOT NULL CHECK (status IN ('active','revoked','legacy_unverified')),
      granted_at TEXT,
      changed_at TEXT NOT NULL,
      source_event_id TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_subscription_entitlements_customer
      ON subscription_entitlements(customer_id, status);
    CREATE TABLE IF NOT EXISTS lifecycle_action_log (
      event_id TEXT PRIMARY KEY REFERENCES processed_events(event_id),
      subscription_id TEXT,
      action TEXT NOT NULL,
      before_json TEXT NOT NULL,
      after_json TEXT NOT NULL,
      applied_at TEXT NOT NULL
    );
    """),
    (7, """
    INSERT OR IGNORE INTO subscription_entitlements
      (subscription_id, customer_id, product_id, tier, status, granted_at,
       changed_at, source_event_id)
    SELECT subscription_id, customer_id, product_id, NULL, 'active', completed_at,
           completed_at, event_id
    FROM (
      SELECT p.subscription_id, p.customer_id, p.product_id, w.completed_at,
             e.event_id,
             ROW_NUMBER() OVER (PARTITION BY p.subscription_id
                                ORDER BY w.completed_at DESC, e.event_id DESC) AS rn
      FROM payments p
      JOIN processed_events e ON e.payment_id=p.payment_id
      JOIN workflow_steps w ON w.event_id=e.event_id AND w.step_name='grant_access'
      JOIN subscriptions s ON s.subscription_id=p.subscription_id
                           AND s.customer_id=p.customer_id AND s.status='active'
      JOIN access_entitlements a ON a.customer_id=p.customer_id AND a.status='active'
      WHERE p.subscription_id IS NOT NULL AND p.status='succeeded'
    ) WHERE rn=1;
    """),
    (8, """
    CREATE TABLE IF NOT EXISTS sales_conversations (
      conversation_id TEXT PRIMARY KEY,
      deal_id TEXT NOT NULL REFERENCES deals(deal_id),
      person_key TEXT NOT NULL REFERENCES persons(person_key),
      occurred_at TEXT NOT NULL,
      transcript TEXT NOT NULL,
      source TEXT NOT NULL CHECK (source='synthetic_fixture')
    );
    CREATE INDEX IF NOT EXISTS idx_conversations_person ON sales_conversations(person_key);
    CREATE TABLE IF NOT EXISTS conversation_classifications (
      conversation_id TEXT NOT NULL REFERENCES sales_conversations(conversation_id),
      model_version TEXT NOT NULL,
      labels_json TEXT NOT NULL,
      evidence_json TEXT NOT NULL,
      classified_at TEXT NOT NULL,
      PRIMARY KEY (conversation_id, model_version)
    );
    """),
    (9, """
    CREATE TABLE IF NOT EXISTS conversion_outbox (
      conversion_id TEXT PRIMARY KEY,
      payment_id TEXT NOT NULL REFERENCES payments(payment_id),
      person_key TEXT NOT NULL REFERENCES persons(person_key),
      platform TEXT NOT NULL CHECK (platform IN ('meta','google','linkedin')),
      campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
      amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
      consent_ref TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('queued','simulated_delivered')),
      created_at TEXT NOT NULL,
      UNIQUE (platform, payment_id)
    );
    """),
)
SCHEMA_VERSION = MIGRATIONS[-1][0]


# Older releases already created these tables without the newer fields. The
# full SCHEMA script creates indexes on some new fields, so ALTERs must happen
# before it runs. Keep table and column names fixed here; no user input is
# interpolated into schema statements.
LEGACY_COLUMNS: dict[str, dict[str, str]] = {
    "campaigns": {
        "platform": "TEXT NOT NULL DEFAULT 'unknown'",
        "landing_page": "TEXT",
        "launched_on": "TEXT",
        "ended_on": "TEXT",
    },
    "content_items": {"topic": "TEXT NOT NULL DEFAULT 'systems'"},
    "contacts": {"created_at": "TEXT"},
    "touches": {"landing_page": "TEXT", "sim_true_campaign_id": "TEXT"},
    "deals": {"product_id": "TEXT REFERENCES products(product_id)", "created_at": "TEXT"},
    "payments": {
        "payment_type": "TEXT NOT NULL DEFAULT 'new' CHECK (payment_type IN ('new','installment','renewal'))",
        "subscription_id": "TEXT",
        "product_id": "TEXT",
    },
    "processed_events": {
        "source": "TEXT NOT NULL DEFAULT 'growthops_internal'",
        "source_event_id": "TEXT",
        "schema_version": "TEXT NOT NULL DEFAULT '1'",
        "entity_type": "TEXT",
        "entity_id": "TEXT",
        "correlation_id": "TEXT",
        "idempotency_key": "TEXT",
        "payload_ref": "TEXT",
        "trace_id": "TEXT",
        "payload_json": "TEXT",
        "next_attempt_at": "TEXT",
        "deliveries": "INTEGER NOT NULL DEFAULT 1",
    },
}


def _upgrade_legacy_columns(connection: sqlite3.Connection) -> None:
    """Add fields absent from an existing simulator database, preserving rows.

    An immediate transaction serializes concurrent API/worker starts, so a
    second process sees the columns added by the first instead of racing an
    ALTER TABLE. Fresh databases have no matching tables and are unchanged.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        for table, columns in LEGACY_COLUMNS.items():
            present = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if not present:
                continue
            for column, definition in columns.items():
                if column not in present:
                    connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _upgrade_processed_events_nullable_payment(connection: sqlite3.Connection) -> None:
    """Rebuild only the legacy event table so nonpayment events have no fake payment ID."""
    payment = next((row for row in connection.execute("PRAGMA table_info(processed_events)")
                    if row["name"] == "payment_id"), None)
    if payment is None or not payment["notnull"]:
        return
    columns = (
        "event_id", "event_type", "source", "source_event_id", "schema_version",
        "entity_type", "entity_id", "correlation_id", "idempotency_key", "payload_ref",
        "payment_id", "customer_id", "payload_sha256", "status", "attempts", "last_error",
        "received_at", "claimed_at", "completed_at", "trace_id", "payload_json",
        "next_attempt_at", "deliveries",
    )
    names = ", ".join(columns)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        # A second API/worker process may have completed the rebuild while this
        # connection waited for the write lock.
        current = next(row for row in connection.execute("PRAGMA table_info(processed_events)")
                       if row["name"] == "payment_id")
        if not current["notnull"]:
            connection.commit()
            return
        connection.execute("""CREATE TABLE processed_events_v6 (
          event_id TEXT PRIMARY KEY,
          event_type TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'growthops_internal',
          source_event_id TEXT,
          schema_version TEXT NOT NULL DEFAULT '1',
          entity_type TEXT,
          entity_id TEXT,
          correlation_id TEXT,
          idempotency_key TEXT,
          payload_ref TEXT,
          payment_id TEXT,
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
        )""")
        connection.execute(f"INSERT INTO processed_events_v6 ({names}) SELECT {names} FROM processed_events")
        connection.execute("DROP TABLE processed_events")
        connection.execute("ALTER TABLE processed_events_v6 RENAME TO processed_events")
        connection.execute("CREATE INDEX idx_processed_status ON processed_events(status, next_attempt_at)")
        connection.execute("CREATE UNIQUE INDEX idx_processed_payment ON processed_events(payment_id)")
        connection.execute("""CREATE UNIQUE INDEX idx_event_source_id
          ON processed_events(source, source_event_id) WHERE source_event_id IS NOT NULL""")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"event migration foreign-key violations: {len(violations)}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the operational store with durable, concurrent-safe settings.

    WAL lets the API, the worker and the dashboard read while one writer commits;
    ``synchronous=NORMAL`` is the WAL setting SQLite recommends for durability
    without an fsync on every commit.
    """
    in_memory = str(path) == ":memory:"
    if not in_memory:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    if not in_memory:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Open an existing SQLite store for dashboards without schema or data writes."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA query_only = ON")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    """Create the base schema and apply any pending migrations."""
    current = schema_version(connection)
    if current == SCHEMA_VERSION:
        return
    if current is not None and current > SCHEMA_VERSION:
        raise RuntimeError(f"database schema version {current} is newer than supported {SCHEMA_VERSION}")
    _upgrade_legacy_columns(connection)
    connection.executescript(SCHEMA)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    now = datetime.now(timezone.utc).isoformat()
    connection.execute("INSERT OR IGNORE INTO schema_migrations VALUES (1, ?)", (now,))
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        if version == 6:
            _upgrade_processed_events_nullable_payment(connection)
        connection.executescript(
            f"BEGIN IMMEDIATE;\n{sql}\nINSERT OR IGNORE INTO schema_migrations VALUES ({version}, '{now}');\nCOMMIT;"
        )


def schema_version(connection: sqlite3.Connection) -> int | None:
    try:
        return connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    except sqlite3.OperationalError:
        return None
