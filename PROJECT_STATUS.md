# Project status — 2026-09-26

## Implemented and verified

- Reproducible local ScaleLab scenario with 240 contacts, 5 acquisition campaigns, direct returns, deliberate CRM and attribution defects, lifecycle events, payments, and refunds.
- Governed local metrics: lead, MQL, booked/attended calls, opportunity, closed-won, gross/refund/net cash, paid spend, CPL, net cash ROAS, quality rates, and funnel transition timing.
- Exact-cent first touch, lead creation, last non-direct, and U-shaped cash attribution with unassigned cash preserved.
- SQL staging and marts for lead-creation cash allocation, campaign performance, revenue, funnel, and measurement quality. Reconciliation tests compare them to the Python reference.
- HMAC-signed payment webhook, event/payment idempotency, five-minute processing claims, persisted workflow steps, retry after partial failure, CRM update, and simulated access grant.
- Campaign link builder that enforces registry taxonomy and creates canonical UTMs.
- Read-only operations customer lookup and analytics endpoints with deterministic all-time executive findings.
- Docker/Compose configuration and GitHub Actions checks. Docker execution is unverified because Docker is unavailable on this host.
- Six tests passing; Ruff passing.

## Not implemented yet

- Real HubSpot, Stripe, GA4, ad-platform, and community integrations.
- PostgreSQL operations store, BigQuery/warehouse, dbt project and Power BI artifact.
- Migration repair jobs, experiments, scheduled renewal/retry worker, alerting, RBAC, and deployed operations console.
- AI analyst/classifier/RAG evaluation pipeline.

## Next engineering increment

Move the validated local SQL views into a dbt project on a chosen warehouse engine, add periodized facts and source freshness, then build the first dashboard against those marts. The all-time snapshot should become a scheduled daily brief only after date-windowed spend and cash models are in place.
