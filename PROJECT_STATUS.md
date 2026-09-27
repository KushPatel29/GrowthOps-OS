# Project status — 2026-09-26

Live synthetic dashboard: https://growthops-os.streamlit.app/ · GitHub: https://github.com/KushPatel29/GrowthOps-OS

## Implemented and verified

- **Scenario generator** (`growthops/seed.py`, `growthops/scenario.py`): fifteen months of seeded, probabilistic
  data: 12 campaigns across Meta, Google, LinkedIn, YouTube, newsletter, webinars and partners; 30 content
  items; a lead-to-renewal funnel; payment plans, refunds, community subscriptions and renewals; ad-platform
  self-reported conversions; a legacy-CRM snapshot with migration defects; a visitor-randomized CTA test.
  Weekly newsletters, webinar invites, deadline promos and nurture sends (with privacy-proxy machine opens) and
  nine Bitly-style short links with daily clicks come from a separate random stream, so adding them changed no
  other number. Six incidents are planted and recorded in an `incidents` table as ground truth. Deterministic;
  about 3 s.
- **Revenue truth** (`reconciliation.py`): platform claims → warehouse paid cash and CRM bookings → net cash,
  each step independently computed, residual 0; per-platform self-reported vs warehouse ROAS.
- **Diagnostics** (`diagnostics.py`): rolling 7-day vs 56-day baseline detection (overdispersion-adjusted
  binomial z for rates, robust z for volumes), practical-significance filter, exact shift-share root cause.
  Both detectable planted incidents are found with the correct root cause within three days.
- **Morning brief** (`brief.py`) and **claim-validated narrative** (`narrator.py`) with a 30-case labelled eval
  set. The narrative is deterministic; the validator guards any other draft.
- **Keyless ask-your-data** (`ask_data.py`, `retrieval.py`, `embeddings.py`): the Ask Your Data design, with
  no language model or API key. Guard, then certified phrases, then hybrid BM25 + local MiniLM retrieval over
  19 governed answers and the metric catalog, then a governed answer, a cited definition or a refusal. On the
  83-question contract, hybrid scores 80 right, 0 wrong, 3 refused and keyword-only 74 right, 0 wrong, 9 refused.
  The first scoring of the 30 holdout questions is kept in the file. Every question is audited in `ask_log`.
- **Production runtime**: fail-fast settings (`config.py`); API keys; replay-safe signed webhooks; request IDs,
  JSON logs and security headers; `/ready` and Prometheus `/metrics`; versioned migrations; verified backups and
  restore (`ops.py`); per-source freshness.
- **Automation services**: provider adapters (simulated by default, HubSpot batch upsert, signed idempotent
  webhooks); a worker (`worker.py`) that retries due events, sends deduplicated alerts to a Slack-compatible
  webhook and posts the daily update once a day; container heartbeat health check.
- **Deployment**: a non-root, read-only, multi-stage image with the verified embedding model baked in;
  `compose.yaml` with api, worker and dashboard; `.env.example`; runbook and security notes. CI gates coverage
  at 80%, runs pip-audit, and smoke-tests the container in production mode.
- **Lifecycle engine** (`workflow.py`): HMAC-signed webhooks, event and payment idempotency, per-step traces,
  exponential backoff retry worker, dead-letter queue, role-gated operator replay, operations health metrics.
  The last 30 days of payments are replayed through it with simulated provider faults.
- **Marketing operations**: paid efficiency (CPM, CTR, CPC, CPL, cost per MQL, cost per booked call, window
  ROAS; `performance.py`), a written daily update (CLI, API, Streamlit copy block), email analytics with a
  sending-domain deliverability check that recovers the planted domain switch (`email_analytics.py`), newsletter
  to pipeline, list source mix, and a short-link registry audit that finds exactly the four planted defects.
- **HubSpot-shaped CRM layer** (`hubspot.py`): lifecycle, deal-stage and owner mapping, Properties API
  definitions, import-ready contacts/deals CSVs, a CRM v3 search request builder and response parser (fixture
  tested), Original Traffic Source mismatch check and a CRM hygiene audit. No portal is connected.
- **Attribution** (five models, exact-cent conservation), funnel timing and by-channel conversion, content to
  pipeline, renewal-risk queue, migration audit with logged safe repairs, allowlisted ask-your-data.
- **Warehouse**: SQLite reference marts and a DuckDB dbt project (staging → intermediate → marts, schema and
  singular tests, including bridge tie-out, email and link-hygiene marts) verified against the Python reference.
- **BI**: ten-view Streamlit app (password gate and configured database in a deployment) (light and dark), FastAPI Executive Pulse page, Power BI PBIP/TMDL with 11
  embedded marts plus a date dimension, relationships and 17 documented measures for paid, email and link data,
  and a formula-driven Excel workbook: a Marketing KPIs sheet on named, validated date inputs, 10 zero-difference
  audit checks, protected sheets and a definitions sheet. Both are rebuilt by script and checked for drift in CI;
  the Excel formulas were evaluated independently and match Python.
- **Quality gates**: Ruff, pytest (unit, invariant, ground-truth, API, BI and case-study drift tests), the
  guardrail eval, a Streamlit render test, dbt build and parity, and a Docker build in GitHub Actions.

## Not implemented yet

- Live provider accounts: the HubSpot and webhook adapters are tested against a fake HTTP transport, not a real
  portal or bridge. There are no GA4, ad-platform, email-platform or link-shortener ingestion jobs (the data is
  generated).
- Hosting, TLS, a secret manager and off-host backups belong to the deployment owner (see the runbook).
- The regenerated Power BI model and its new fifth page (paid, email and tracking) have not been reopened in
  Power BI Desktop; they are checked structurally against the existing, schema-validated visuals.
- PostgreSQL, for several API hosts or point-in-time recovery (SQLite with WAL is the single-host choice).
- Incrementality measurement (geo holdouts, conversion-lift studies); attribution here is descriptive.
- A published Power BI Service report; the PBIP project was previously opened and queried in Power BI Desktop,
  and its visual layout after the data refresh has not been re-inspected in Desktop.

## Next increment

Connect a HubSpot developer test account and a Stripe test-mode bridge through the existing adapters, add
ingestion jobs for ad platforms and the email tool, and reopen the Power BI project in Desktop to check the new page's layout.
