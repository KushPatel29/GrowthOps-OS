# Project status — 2026-09-26

Live synthetic dashboard: https://growthops-os.streamlit.app/ · GitHub: https://github.com/KushPatel29/GrowthOps-OS

## Implemented and verified

- **Scenario generator** (`growthops/seed.py`, `growthops/scenario.py`): fifteen months of seeded, probabilistic
  data: 12 campaigns across Meta, Google, LinkedIn, YouTube, newsletter, webinars and partners; 30 content
  items; a lead-to-renewal funnel; payment plans, refunds, community subscriptions and renewals; ad-platform
  self-reported conversions; a legacy-CRM snapshot with migration defects; a visitor-randomized CTA test.
  Four incidents are planted and recorded in an `incidents` table as ground truth. Deterministic; about 3 s.
- **Revenue truth** (`reconciliation.py`): platform claims → warehouse paid cash and CRM bookings → net cash,
  each step independently computed, residual 0; per-platform self-reported vs warehouse ROAS.
- **Diagnostics** (`diagnostics.py`): rolling 7-day vs 56-day baseline detection (overdispersion-adjusted
  binomial z for rates, robust z for volumes), practical-significance filter, exact shift-share root cause.
  Both detectable planted incidents are found with the correct root cause within three days.
- **Morning brief** (`brief.py`) and **claim-validated narrative** (`narrator.py`) with a 30-case labelled eval
  set; optional Claude writer used only when its output passes the validator.
- **Lifecycle engine** (`workflow.py`): HMAC-signed webhooks, event and payment idempotency, per-step traces,
  exponential backoff retry worker, dead-letter queue, role-gated operator replay, operations health metrics.
  The last 30 days of payments are replayed through it with simulated provider faults.
- **Attribution** (five models, exact-cent conservation), funnel timing and by-channel conversion, content to
  pipeline, renewal-risk queue, migration audit with logged safe repairs, allowlisted ask-your-data.
- **Warehouse**: SQLite reference marts and a DuckDB dbt project (staging → intermediate → marts, schema and
  singular tests, including bridge tie-out) verified against the Python reference.
- **BI**: nine-view Streamlit app (light and dark), FastAPI Executive Pulse page, Power BI PBIP/TMDL with 11
  embedded marts, and a formula-driven Excel workbook with a zero-difference audit sheet, the last two rebuilt
  by script and checked for drift in CI.
- **Quality gates**: Ruff, pytest (unit, invariant, ground-truth, API, BI and case-study drift tests), the
  guardrail eval, a Streamlit render test, dbt build and parity, and a Docker build in GitHub Actions.

## Not implemented yet

- Real HubSpot, Stripe, GA4, ad-platform and community integrations (adapters are simulated).
- PostgreSQL operations store, BigQuery deployment, scheduled jobs and alert delivery.
- Incrementality measurement (geo holdouts, conversion-lift studies); attribution here is descriptive.
- A published Power BI Service report; the PBIP project was previously opened and queried in Power BI Desktop,
  and its visual layout after the data refresh has not been re-inspected in Desktop.

## Next increment

Swap the simulated payment and CRM adapters for sandbox providers behind the same step interface, move
operational state to PostgreSQL with Alembic migrations, and schedule the brief with source-freshness checks.
