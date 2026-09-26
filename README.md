# GrowthOps OS

**Live dashboard:** [growthops-os.streamlit.app](https://growthops-os.streamlit.app/) · **Source:** [GitHub](https://github.com/KushPatel29/GrowthOps-OS)

Growth and revenue analytics for **ScaleLab**, a fictional creator-led B2B education business. This portfolio project prioritizes marketing measurement: acquisition, CRM hygiene, funnel conversion, attribution, collected revenue, and an evidence-based executive brief. Lifecycle automation demonstrates how the underlying customer systems produce trustworthy data.

**Data provenance:** Commercial, advertising, CRM, and customer records in this repository are synthetic. No ScaleLab customer or Martell operational data is used. Any future live portfolio-site telemetry must be labelled separately and must not be joined to synthetic people.

## What works now

- Deterministic synthetic scenario with duplicate contacts, missing UTMs, owner gaps, stage conflicts, and unmatched payments.
- SQLite-backed local analytical slice with controlled campaign taxonomy, lifecycle history, four cash attribution models, full-funnel timing, reconciliation, marketing KPIs, and an all-time executive snapshot.
- SQLite marts and a DuckDB/dbt staging-to-marts DAG for revenue, campaign, funnel, daily growth, content, migration, experiments, and measurement quality, checked against the Python/SQLite metric reference.
- FastAPI payment webhook with HMAC verification, durable idempotency, retryable step state, and an operations lookup.
- Registered campaign link builder with canonical UTM parameters.
- Responsive Executive Pulse page at `/dashboard`, with cash reconciliation, recent-week brief, funnel, selectable attribution, content-to-pipeline, CTA experiment, migration audit, and measurement health.
- An editable [Power BI project](dashboards/powerbi-project/GrowthOpsOS.pbip) with nine embedded synthetic marts and four report pages (Executive, Acquisition, Funnel and Content, Quality and Lifecycle); [import and validation notes](docs/power-bi-handoff.md). It has not been opened in Power BI Desktop or published to a Power BI workspace yet.
- A six-view [Streamlit portfolio app](streamlit_app.py) for executive reporting, acquisition, funnel, experiments, renewal and data quality monitoring, and safe metric questions. It runs on a fresh synthetic sample with no external credentials.
- A formula-backed [Excel dashboard](dashboards/GrowthOps_OS_Excel_Dashboard.xlsx) with native charts, raw mart sheets, and a zero-difference audit.
- A read-only renewal risk monitor and an evidence-grounded AI brief. The brief is deterministic by default; optional LLM ranking can only select validated evidence IDs. The ask-your-data interface maps common questions to approved queries.
- Tests for duplicate delivery, partial failure and retry, attribution conservation, reconciliation, and metric definitions.

The local slice uses SQLite to run without cloud credentials. The target architecture uses PostgreSQL for operational state and BigQuery or PostgreSQL/dbt for the warehouse; see [implementation blueprint](docs/implementation-blueprint.md). The local slice is intentionally small and does not claim production integrations or production-scale data.

## Quick start

```powershell
cd growthops-os
python -m pip install -e ".[dev,warehouse]"
python -m pip install -r requirements.txt
python -m growthops.seed --database .\data\growthops-sample.db
python -m growthops.warehouse --database .\data\growthops-sample.db
python -m growthops.report --database .\data\growthops-sample.db
python -m growthops.export_warehouse
cd warehouse/dbt
dbt seed --profiles-dir . --quiet
dbt build --profiles-dir . --select path:models path:tests --quiet
cd ../..
python -m growthops.verify_dbt
python -m growthops.export_bi
python -m pytest
python -m uvicorn growthops.api:app --reload
```

For the public portfolio dashboard, run `python -m streamlit run streamlit_app.py`. The entrypoint for Streamlit Community Cloud is `streamlit_app.py` on `main`; the repository's `requirements.txt` installs its dependencies. The public dashboard uses synthetic data only. See [deployment notes](docs/streamlit-deployment.md).

Open `http://127.0.0.1:8000/dashboard` for Executive Pulse. `GET /health` checks the API. `/metrics/executive`, `/metrics/daily`, `/metrics/brief`, `/metrics/funnel`, `/metrics/content`, `/metrics/experiments/cta_growth_plan`, and `/metrics/attribution/{model}` expose the analytics. `/ops/migration` and `/ops/customers/{customer_id}` expose operational audits. A signed `POST /webhooks/payments` models a Stripe-like payment event; [API contracts](docs/api-contracts.md) document it. No external API keys are needed for the simulator.

Docker alternative: run `docker compose --profile tools run --rm seed`, then `docker compose up api`. The API is bound to localhost for this local demonstration.

## Current scope

This is a reproducible portfolio slice, with deliberately broken measurement and a documented [case study](docs/case-study.md). The [implementation blueprint](docs/implementation-blueprint.md) describes the larger target system. Real provider adapters, Power BI Desktop verification and workspace publication, a scheduled renewal worker, PostgreSQL deployment, and production AI pipelines remain targets rather than implemented capabilities. This is portfolio evidence of implementation, not a claim of work performed for Martell.
