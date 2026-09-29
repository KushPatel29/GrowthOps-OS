# Project status — 2026-09-30

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
  parser, Original Traffic Source mismatch check and a CRM hygiene audit. A prior developer test portal snapshot held 959
  synthetic contacts, 130 deals, 33 baseline custom properties, six lists, three published workflows and a four-report
  dashboard; read-back reconciles the sample to the warehouse. See [portal evidence](docs/hubspot-portal.md).
- **v2.1 HubSpot schema** (`hubspot_v21.py`): five additional custom properties were created in the same
  developer test portal on 2026-09-29. Read-back verified all 38 definitions with zero drift. Current searches
  returned 961 contacts and 130 deals, with zero populated values in the five new fields. No marketing status,
  lifecycle stage, owner, association or workflow was changed. See the [change record](docs/hubspot-v21-change-plan.md).
- **Live, read-only marketing audit** (`hubspot_marketing_audit.py`): a 2026-09-29 search returned 961
  synthetic contacts, all marked non-marketable. It flagged 111 missing/off-taxonomy/blank tracking statuses,
  89 blank original UTM sources and two active-stage contacts without owners. The inspected opt-out field is
  blank for all 961, so eligibility remains unproven and no status change is recommended. See the
  [portal audit](docs/hubspot-marketing-audit.md); it made zero portal writes.
- **Attribution** (six models, including weekly time decay, exact-cent conservation), funnel timing and by-channel conversion, content to
  pipeline, renewal-risk queue, migration audit with logged safe repairs, allowlisted ask-your-data.
- **Warehouse**: SQLite reference marts and a DuckDB dbt project (staging → intermediate → marts, schema and
  singular tests, including bridge tie-out, email and link-hygiene marts) verified against the Python reference.
- **BI**: twelve-view Streamlit app (password gate and configured database in a deployment) (light and dark),
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
- **v2.1 control plane**: person and identity evidence, explicit synthetic deal qualification,
  CRM quality issues, marketing-contact governance, campaign QA and instrumentation contracts,
  four-value Revenue Truth, outbox trace,
  audited replay, a four-view local console and a read-only public Streamlit operations view with readable
  Customer 360 and incident timelines,
  and a discoverable failed-workflow queue,
  six new staging models, two intermediate models, two marts and regenerated PBIP. The qualification
  decisions are synthetic fixtures, not imported HubSpot judgments. The new UI and semantic model pass
  local structural and parity tests; the regenerated PBIP has not yet been reopened in Desktop.
- **v2.1 local lifecycle actions**: a signed canonical webhook records subscription upgrades,
  downgrades, cancellations and refunds in the shared event ledger. Version 6 permits nonpayment
  events without invented payment IDs and adds subscription-grain entitlements and an action audit;
  version 7 backfills only access supported by a completed grant trace (25 subscriptions locally).
  Refund bounds, scoped revocation, concurrent subscriptions, duplicate delivery and community
  retry/replay are covered by tests. The existing 92 payment traces survived the local migration
  with zero foreign-key violations. This is a simulated provider path, not live Stripe ingestion.
- **Stripe test snapshot bridge** (`stripe_test_bridge.py`): a development-only route verifies Stripe's raw-body
  `t`/`v1` signature and five-minute freshness, rejects live-mode events, and maps allowlisted USD payments,
  tier changes, cancellations and succeeded refunds with explicit local identity metadata into the ledger.
  A second Stripe Event for an already-recorded payment or refund is absorbed only after matching amount and
  identity; an early refund can succeed on a later delivery after its payment arrives.
  It is disabled without a separate `whsec_` secret and disabled in production. Tests use Stripe-shaped
  fixtures; no connected Stripe account, provider read-back or live ingestion has been verified.
- **v2.2 synthetic growth and operations layer**: person-grain month/source/campaign/owner cohorts that conserve
  customers and cash; observed paid CAC, cash per customer, contracted ARR and matured renewal rate with
  unsupported lifetime metrics marked unavailable; a transparent assumption calculator; local schema, freshness,
  referential-integrity and quality checks. The classifier stores 180 synthetic conversations with versioned
  evidence spans; its 37 base phrases across four context variants produce a 148-case test contract, plus 20
  grounded sales-copilot checks. This is a deterministic local classifier and retrieval prototype, not an LLM
  or a live sales assistant. Normalized YouTube/Zoom/Vimeo event ingress verifies identity and idempotency but
  does not accept direct provider webhooks. Explicit **synthetic** email/ads/SMS decisions gate a local paid
  conversion outbox; no ad provider is connected. Renewal proposals account for risk and email consent but
  do not create CRM tasks or send messages. The Growth lab and operations views expose these distinctions.
- **Production-readiness gates (v2.3 code)**: generated databases carry a synthetic-origin marker; API,
  worker and configured Streamlit dashboard reject an unverified live source. `/ready` now returns 503 for
  stale, missing or future-dated feeds. Person-level reads require the separate operator token in production,
  and production ask-data logs retain a question digest rather than raw text. Adapter and alert errors omit
  provider response bodies and dry-run message content. These gates do not create real source ingestion or
  an identity-backed operator gateway.

## Not implemented yet

- The HubSpot connector returned zero tickets as of 2026-09-29, including zero unresolved
  high-priority tickets; the separate private-app token lacks ticket-search scope. The five new schema fields
  are empty pending verified identity links and actual sales qualification decisions. The newly planted local
  consent decisions must not be treated as consent for any connected HubSpot contact.

- Live runtime integrations: the HubSpot developer test portal is built and verified, but the payment workflow's
  HubSpot and webhook adapters are tested against a fake HTTP transport rather than a real provider bridge.
  There are no live Stripe, GA4, ad-platform, email-platform or link-shortener ingestion jobs (the data is
  generated). Server-side ad conversions are local queued intents only; no Meta, Google or LinkedIn delivery
  or attribution read-back exists. Domain DNS (SPF/DKIM/DMARC), SMS delivery and A2P registration are unverified.
- Hosting, TLS, a secret manager and off-host backups belong to the deployment owner (see the runbook).
- PostgreSQL, for several API hosts or point-in-time recovery (SQLite with WAL is the single-host choice).
- BigQuery/GCP deployment, dashboard usage/certification telemetry, production-grade Markov attribution,
  causal lift, full customer lifetime/retention economics and a statistically validated forecast are not
  implemented. The planning view is scenario arithmetic on entered assumptions.
- Incrementality measurement (geo holdouts, conversion-lift studies); attribution here is descriptive.
- A published Power BI Service report (the PBIP is verified in Desktop only), and row-level security.

## Next increment

Provider-backed work requires access to real source systems and evidence: connected Stripe test-mode
verification and subscription read-back, authenticated HubSpot identity/qualification updates, GA4 and ad/email
ingestion, a consent source, community access read-back, and a production delivery stack. See the
[v2.1 engineering specification](docs/growthops-os-v2.1-engineering-spec.md) and
[API contracts](docs/api-contracts.md) for the local contracts ready for that work. Power BI Service
publication and row-level security still require a tenant and credentials.
