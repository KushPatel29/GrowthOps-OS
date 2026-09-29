# GrowthOps OS final target audit — 2026-09-29

This is the evidence map for the two user-supplied v2 and v2.1 targets. **The portfolio demo is a
deterministic synthetic system.** A HubSpot developer portal holds a separately verified sample; no
claim below turns local fixtures into live production integrations.

| Target area | Delivered evidence | Boundary still open |
|---|---|---|
| Identity and customer journey | `persons`, `identity_links`, journey and Customer 360 APIs, 14,693 local people, quality issues for ambiguous duplicates | Real GA4 client ID, HubSpot contact ID and Stripe customer ID links need verified provider identities |
| CRM health and lifecycle | CRM score components, issue queue, explicit transition history, qualification decisions, marketing-contact audit, five live empty HubSpot schema fields | The local qualification decisions are synthetic; portal field values remain empty until real identity and sales evidence is verified |
| Qualified pipeline and revenue truth | Qualified created/open/won pipeline plus platform claims, bookings, collected cash and exact reconciliation bridges | Live platform, CRM and payment data ingestion is not connected |
| Tracking and content | Campaign taxonomy, UTM/link QA, instrumentation registry/validator, content-to-pipeline marts, normalized media event contract | Direct GA4/GTM, Bitly, YouTube, Zoom and Vimeo ingestion and source read-back need credentials |
| Attribution and funnel | First, lead, last non-direct, U-shaped, linear and time-decay cash models with exact-cent conservation; funnel and acquisition cohorts | Markov and causal incrementality are not implemented; descriptive credit does not prove lift |
| Customer economics and planning | Observed paid CAC, paid-cohort cash ratio, contracted ARR/MRR, matured renewal rate and assumption-based scenario calculator | Lifetime LTV, NRR/GRR, payback and statistical forecasts need longer account/cost histories |
| Data trust and BI | Quality queue, source freshness, schema/foreign-key checks, dbt data tests/parity, governed Power BI and Excel artifacts | Dashboard usage, last certification, live CI status, Power BI Service publication and row security are not connected |
| Lifecycle automation and incidents | Signed payment/lifecycle contracts, idempotent event ledger, trace, retries, dead letters, replay, local community adapter and renewal task proposals | Connected Stripe test read-back, HubSpot/community writes and CRM task dispatch are unverified |
| Consent and communications | Explicit synthetic email/ads/SMS decisions, current and purchase-time conversion gates, local outbox, observed synthetic email deliverability | No ad conversion delivery, DNS verification, SMS provider, A2P registration or portal consent import |
| AI and self-service | 182-question governed ask-data contract, 30 narrative guardrail cases, 148 synthetic classifier phrase variants, 20 grounded sales-copilot checks, local console | The classifier and copilot are deterministic prototypes; no production LLM, human-labelled sales data or Chrome extension |
| Deployment | FastAPI/Streamlit, Docker Compose, CI, local SQLite and DuckDB/dbt, versioned migrations, tests and runbooks | GCP, BigQuery, PostgreSQL, off-host backups and production secrets are deployment work |

The [API contracts](api-contracts.md) label each local action. The [project status](../PROJECT_STATUS.md)
records exact implementation and verification results. The [demo walkthrough](demo-walkthrough.md) gives
two short evidence-led stories for a reviewer.
