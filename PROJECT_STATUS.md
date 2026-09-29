# Project status — 2026-09-29

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
  20 governed answers and the metric catalog, then a governed answer, a cited definition or a refusal.
  `ask_slots.py` reads the period ("last week", "in August", "since 1 September", "between July and August",
  "Q2", "the first half of 2026", "the week before last", "July vs August", resolved against the data's as-of date), the ad
  platform or platforms compared, the campaign and the measure (including refund rate), so an answer is the one
  asked for; windowed totals compare with the period before, or with the other period named; an untracked ad platform, an impossible date or a
  period outside the data is refused, and an answer that cannot be cut by period says so. Themed
  suggested questions and per-answer follow-ups are each held by a test to reach a governed answer. On the
  152-question contract, which also fails an answer that read the wrong platform, measure or period, hybrid and
  keyword-only both score 152 right, 0 wrong, 0 refused. A second test runs every contract question through the
  full answer and recomputes the stated figure of each period and platform answer from the mart. All three holdout sets' first scorings (30 questions,
  then 15 for the details a question names: 13/1/1, then 15 for dates, ranges and comparisons: 13/2/0) are kept
  in the file with the cause of each miss. Every question is audited in `ask_log`.
- **Production runtime**: fail-fast settings (`config.py`); API keys; replay-safe signed webhooks; request IDs,
  JSON logs and security headers; `/ready` and Prometheus `/metrics`; versioned migrations; verified backups and
  restore (`ops.py`); per-source freshness.
- **Automation services**: provider adapters (simulated by default, HubSpot batch upsert, signed idempotent
  webhooks); a worker (`worker.py`) that retries due events, sends deduplicated alerts to a Slack-compatible
  webhook and posts the daily update once a day; container heartbeat health check.
- **Deployment**: a non-root, read-only, multi-stage image with the verified embedding model baked in;
  `compose.yaml` with api, worker and dashboard; `.env.example`; runbook and security notes. CI gates coverage
  at 80%, type-checks the package with mypy (zero errors), runs pip-audit, and smoke-tests the container in production mode.
- **Lifecycle engine** (`workflow.py`): HMAC-signed webhooks, event and payment idempotency, per-step traces,
  exponential backoff retry worker, dead-letter queue, role-gated operator replay, operations health metrics.
  The last 30 days of payments are replayed through it with simulated provider faults.
- **Marketing operations**: paid efficiency (CPM, CTR, CPC, CPL, cost per MQL, cost per booked call, window
  ROAS; `performance.py`), a written daily update (CLI, API, Streamlit copy block), email analytics with a
  sending-domain deliverability check that recovers the planted domain switch (`email_analytics.py`), newsletter
  to pipeline, list source mix, and a short-link registry audit that finds exactly the four planted defects.
- **HubSpot CRM layer** (`hubspot.py`, `hubspot_portal.py`): lifecycle, deal-stage and owner mapping,
  Properties API definitions, import-ready contacts/deals CSVs, a CRM v3 search request builder and response
  parser, Original Traffic Source mismatch check and a CRM hygiene audit. A developer test portal holds 959
  synthetic contacts, 130 deals, 33 custom properties, six lists, three published workflows and a four-report
  dashboard; read-back reconciles the sample to the warehouse. See [portal evidence](docs/hubspot-portal.md).
- **Attribution** (five models, exact-cent conservation), funnel timing and by-channel conversion, content to
  pipeline, renewal-risk queue, migration audit with logged safe repairs, allowlisted ask-your-data.
- **Warehouse**: SQLite reference marts and a DuckDB dbt project (staging → intermediate → marts, schema and
  singular tests, including bridge tie-out, email and link-hygiene marts) verified against the Python reference.
- **BI**: ten-view Streamlit app (password gate and configured database in a deployment) (light and dark),
  FastAPI Executive Pulse page, and a governed BI snapshot (`export_bi`: 16 marts plus `dim_date`,
  `dim_campaign`, payment-grain cash attribution and a quality scorecard) read by two generated deliverables.
  **Power BI** (`growthops/bi`): 22 tables, 8 relationships, 214 described measures, 7 pages and 121 visuals
  with SVG KPI tiles, dynamic headers, page navigation, a bookmark filter panel, conditional colours and a
  DAX-written executive summary; parsed by Desktop's TMDL serializer, validated with zero errors by
  Microsoft's report validator, opened, refreshed and queried in Power BI Desktop 2.157 for the earlier baseline.
  The v2.1 regenerated project passes structural tests and Python/dbt parity but has not yet been reopened in Desktop.
  **Excel**: one window control drives a dashboard with KPI tiles
  and formula-written findings, a campaign scorecard, email health, revenue truth, funnel and test, data
  quality, 14 zero-difference audit checks and named-range formulas; calculated in Excel and held to Python
  figures in the tests. The two agree to the cent. Both are regenerated and drift-checked in CI.
- **Quality gates**: Ruff, pytest (unit, invariant, ground-truth, API, BI and case-study drift tests), the
  guardrail eval, a Streamlit render test, dbt build and parity, and a Docker build in GitHub Actions.
- **v2.1 local control plane**: person and identity evidence, explicit synthetic deal qualification,
  CRM quality issues, marketing-contact governance, campaign QA and instrumentation contracts,
  four-value Revenue Truth, outbox trace,
  audited replay, a four-view local console with readable Customer 360 and incident timelines,
  and a discoverable failed-workflow queue,
  six new staging models, two intermediate models, two marts and regenerated PBIP. The qualification
  decisions are synthetic fixtures, not imported HubSpot judgments. The new UI and semantic model pass
  local structural and parity tests; the regenerated PBIP has not yet been reopened in Desktop.

## Not implemented yet

- The connected HubSpot developer portal has zero tickets as of 2026-09-29, including zero unresolved
  high-priority tickets. A read-only live audit found all 33 baseline custom properties present with zero
  definition drift; the five proposed v2.1 properties are still absent. No v2.1 property or record values
  have been written to it.

- Live runtime integrations: the HubSpot developer test portal is built and verified, but the payment workflow's
  HubSpot and webhook adapters are tested against a fake HTTP transport rather than a real provider bridge.
  There are no live Stripe, GA4, ad-platform, email-platform or link-shortener ingestion jobs (the data is
  generated).
- Hosting, TLS, a secret manager and off-host backups belong to the deployment owner (see the runbook).
- PostgreSQL, for several API hosts or point-in-time recovery (SQLite with WAL is the single-host choice).
- Incrementality measurement (geo holdouts, conversion-lift studies); attribution here is descriptive.
- A published Power BI Service report (the PBIP is verified in Desktop only), and row-level security.

## Next increment

Continue the [v2.1 engineering specification](docs/growthops-os-v2.1-engineering-spec.md) with lifecycle
event policies beyond successful payments, a provider test bridge, and held-out AI evaluation. The Stripe
test-mode bridge, ad/email ingestion and Power BI Service publication remain later integrations.
