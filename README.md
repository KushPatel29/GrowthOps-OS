# GrowthOps OS

Growth and revenue analytics for **ScaleLab**, a fictional creator-led B2B education business. This portfolio project prioritizes marketing measurement: acquisition, CRM hygiene, funnel conversion, attribution, collected revenue, and an evidence-based executive brief. Lifecycle automation demonstrates how the underlying customer systems produce trustworthy data.

**Data provenance:** Commercial, advertising, CRM, and customer records in this repository are synthetic. No ScaleLab customer or Martell operational data is used. Any future live portfolio-site telemetry must be labelled separately and must not be joined to synthetic people.

## What works now

- Deterministic synthetic scenario with duplicate contacts, missing UTMs, owner gaps, stage conflicts, and unmatched payments.
- SQLite-backed local analytical slice with controlled campaign taxonomy, lifecycle history, four cash attribution models, full-funnel timing, reconciliation, marketing KPIs, and a daily executive brief.
- FastAPI payment webhook with HMAC verification, durable idempotency, retryable step state, and an operations lookup.
- Registered campaign link builder with canonical UTM parameters.
- Tests for duplicate delivery, partial failure and retry, attribution conservation, reconciliation, and metric definitions.

The local slice uses SQLite to run without cloud credentials. The target architecture uses PostgreSQL for operational state and BigQuery or PostgreSQL/dbt for the warehouse; see [implementation blueprint](docs/implementation-blueprint.md). The local slice is intentionally small and does not claim production integrations or production-scale data.

## Quick start

```powershell
cd growthops-os
python -m pip install -e ".[dev]"
python -m growthops.seed --database .\data\growthops-sample.db
python -m growthops.report --database .\data\growthops-sample.db
python -m uvicorn growthops.api:app --reload
python -m pytest
```

`GET /health` checks the API. `GET /ops/customers/{customer_id}` shows payment, CRM, access, and workflow state. A signed `POST /webhooks/payments` models a Stripe-like payment event; [API contracts](docs/api-contracts.md) document it. No external API keys are needed for the simulator.

Docker alternative: run `docker compose --profile tools run --rm seed`, then `docker compose up api`. The API is bound to localhost for this local demonstration.

## Current scope and next increments

1. **Current:** one local, reproducible acquisition-to-cash dataset and payment-to-access workflow.
2. Add dbt staging/intermediate/marts and test the metric contracts against a warehouse engine.
3. Add tracking and UTM registry UI, experiment analysis, and Power BI pages.
4. Add provider adapters, renewal workflows, operational deployment, and evidence-grounded AI summaries.

The design and acceptance criteria are in [implementation blueprint](docs/implementation-blueprint.md). This is portfolio evidence of implementation, not a claim of work performed for Martell.
