# Production runbook

How to deploy, operate and recover GrowthOps OS. The repository's hosted dashboard is a
**synthetic portfolio demo**, not a live production deployment. Runtime safety gates are tested,
but real provider ingestion, identity-backed dashboard access, TLS, monitoring and off-host
recovery require deployment-owner evidence before customer data is used.

## Services

| Service | Command | Health | Scales |
|---|---|---|---|
| `api` | `uvicorn growthops.api:app` | `GET /health` (liveness), `GET /ready` (database, schema version, freshness) | One process per host; SQLite allows one writer, WAL lets readers run concurrently |
| `worker` | `python -m growthops.worker` | `python -m growthops.worker --healthcheck 300` (heartbeat age) | One per database; `job_runs` makes a second worker harmless, not useful |
| `dashboard` | `streamlit run streamlit_app.py` with `GROWTHOPS_DASHBOARD_DATABASE` | `/_stcore/health` | Read-only; any number |

All three use the same image and the same data volume (`compose.yaml`). The
image runs as a non-root user, has a read-only root filesystem, and carries the
checksum-verified embedding model, so nothing is downloaded at runtime.

## First deployment

1. `cp .env.example .env`, generate independent secrets with
   `python -c "import secrets; print(secrets.token_urlsafe(48))"` and fill them in.
   Set a dashboard password of at least 16 characters when using the configured database.
   Use different values for `GROWTHOPS_WEBHOOK_SECRET`, `GROWTHOPS_ACCESS_WEBHOOK_SECRET`,
   and `GROWTHOPS_MESSAGING_WEBHOOK_SECRET`; configure each receiving bridge with its matching key.
2. `docker compose run --rm api python -m growthops.ops check-config`: it prints the
   settings with secrets masked and exits non-zero if production is unsafe.
3. Load data. For a local synthetic rehearsal, set `GROWTHOPS_ENV=development` and
   `GROWTHOPS_DATA_MODE=synthetic`, then run `docker compose --profile tools run --rm seed`.
   For a real deployment, keep `GROWTHOPS_ENV=production` and `GROWTHOPS_DATA_MODE=live`,
   configure real CRM, access and messaging adapters, and ingest live source data. The ingestion
   process must write `dataset_origin.origin='live_verified'` with a source-evidence reference.
   A generated database is stamped `synthetic_fixture` and production startup rejects it even
   if an environment variable says `live`. An absent marker also fails closed. No live ingestion
   process in this repository currently produces that evidence, so this step is an external gate.
   Never run the seed command against the production volume or relabel generated rows as live.
4. `docker compose up -d`, then check `curl -s localhost:8000/ready`.
   The API builds marts before the dashboard starts; the dashboard only reads the
   existing database. A missing, stale, malformed or future-dated source makes `/ready` return **503**.
   Route user traffic only when `/ready` is 200; `/health` is process liveness alone.
5. Point the payment provider bridge at `POST /webhooks/payments` with the shared
   secret. Each request must send `X-GrowthOps-Timestamp` (Unix seconds) and
   `X-GrowthOps-Signature` = hex HMAC-SHA256 of `"{timestamp}.{raw body}"`.
   Requests older than `GROWTHOPS_WEBHOOK_TOLERANCE_SECONDS` are rejected.
6. Put a TLS-terminating reverse proxy (Caddy, nginx, a cloud load balancer) in
   front of ports 8000 and 8501. The compose file binds them to 127.0.0.1 only.
   Do not expose the dashboard with only its shared password: require external SSO and
   role-based access before showing real customer records. Person-level API reads require both
   an API key and the separate `X-GrowthOps-Ops-Token`; the token is still a shared operator
   credential, so use a gateway for individual accountability.

## Service levels

| Indicator | Objective | Where to see it |
|---|---|---|
| API availability | 99.5% of `/ready` checks succeed over 30 days | uptime probe on `/ready` |
| Paid-to-access | 99% of payment events reach access within 5 minutes; zero customers paid-without-access for more than 1 hour | `growthops_paid_without_access_customers`, `/ops/workflows` |
| Webhook integrity | zero accepted unsigned or replayed webhooks | `growthops_webhook_rejected_total` |
| Data freshness | every source within its SLA (36 h; email 8 days) by the daily update | `growthops_source_stale`, `/ready` |
| Daily update | posted once per day after `GROWTHOPS_DAILY_UPDATE_TIME_UTC` | `job_runs` (job `daily_update`) |
| Ask your data | zero wrong answers on the question contract (CI) | `python -m growthops.ask_data --eval` |

Scrape `GET /metrics` (Prometheus text format, needs an API key) and alert on:
`growthops_paid_without_access_customers > 0` for 1 hour,
`growthops_workflow_events{status="dead_letter"}` increasing,
`max(growthops_source_stale) == 1`, `growthops_hubspot_write_problems > 0` (change-set items that failed or hit a conflict), `growthops_hubspot_changesets{status="planned"}` waiting more than a day for approval, and a rising rate of HTTP 5xx.

## Routine operations

| Task | Command |
|---|---|
| Rotate an API key | Add the new key to `GROWTHOPS_API_KEYS` (comma-separated), restart, move clients over, remove the old key, restart |
| Rotate the webhook secret | **Manual**, coordinated with the provider bridge: set the new secret on both sides during a quiet window; events rejected in between are redelivered by the provider |
| Back up | `docker compose --profile tools run --rm backup` (online, verified, keeps 14); copy `/data/backups` off the host (**manual**: S3, Azure Blob or similar) |
| Verify a backup | `python -m growthops.ops verify --database /data/backups/growthops-<stamp>.db` |
| Restore | Stop `api` and `worker`, then `python -m growthops.ops restore --from <backup> --force` (keeps `*.pre-restore.db`), start them again |
| Schema upgrade | Automatic: `initialize()` applies pending migrations in order at startup and records them in `schema_migrations` |
| HubSpot sync | `GET /v2/hubspot/sync` (operator role); approve a planned change set with `POST /v2/hubspot/changesets/{id}/approve` or `python -m growthops.hubspot_sync approve <id> --by <name>`; the next worker pass applies it. Full procedures: [HubSpot in production](hubspot-production.md) |
| Rotate the HubSpot key | Create the new key in HubSpot, store it in the secret manager, restart `api` and `worker`, run `python -m growthops.hubspot_sync preflight`, then revoke the old key |
| See what users ask | `GET /ops/ask-usage` with the operator token; production stores a SHA-256 digest of each question, plus route/target, rather than raw text |

## Incidents

**Customers paid but have no access** (alert, or the morning brief's top item).
1. `GET /ops/paid-without-access` lists them; `GET /ops/events/{id}` shows each
   trace and the provider error.
2. Fix or wait out the provider problem (the error text says `transient` or
   `permanent`).
3. Replay each dead-lettered event:
   `curl -X POST -H "X-GrowthOps-Ops-Token: $TOKEN" localhost:8000/ops/events/<id>/replay`.
   Completed steps are never repeated, and provider calls are idempotent.
4. Confirm the list is empty and tell the affected customers.

**A source is stale.** `/ready` names it. Check the ingestion job and the
provider's status page before trusting the day's numbers. The daily update and
alerts say which source is stale.

**Email deliverability alert.** Move bulk sends back to the warmed domain,
check SPF, DKIM and DMARC alignment for the new one, and warm it gradually.

**Webhooks rejected (401) after a change.** Check that the bridge signs
`"{timestamp}.{body}"` with the current secret, and that the clocks agree
within the tolerance.

**API returns 500.** The response carries `request_id`; search the JSON logs for it
to find the traceback. Internals never reach the client.

## Limits and scale-up path

SQLite in WAL mode on one persistent volume is appropriate for a single business
at this volume: thousands of webhooks a day and a handful of dashboard users.
Move to PostgreSQL (the schema is plain SQL; the store sits behind `growthops.db`)
if you need several API hosts, point-in-time recovery, or more than one writer.
The Power BI model uses embedded import partitions for a credential-free demo. In
production, switch its partitions to the warehouse (DuckDB file, PostgreSQL or a
CSV share) and schedule refresh through a gateway. See
[the Power BI handoff](power-bi-handoff.md).

## Release gate and rollback

Before enabling live traffic, verify provider identities and source timestamps, a successful
backup and restore rehearsal, secret rotation, operator access, TLS/SSO, and a provider-side
read-back of payment → CRM → access. Keep the previous image and database backup. Stop routing
traffic or roll back if `/ready` is 503, payment events dead-letter, paid customers lack access,
or reconciliation residuals become nonzero. A code rollback does not reverse an already-applied
schema migration; restore the verified database snapshot only with the API and worker stopped.
