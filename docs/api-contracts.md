# API contracts v0.3

The payment webhook uses an internal, Stripe-like contract; a provider bridge translates the real provider's
event into it. Side effects go through provider adapters (simulated by default; HubSpot and signed webhook
bridges when configured; see `growthops/adapters.py`).

## Authentication

| Route group | Requirement |
|---|---|
| `/health`, `/ready` | Open (probes) |
| `POST /webhooks/payments` | Signed request (below) |
| `/metrics/*`, `/metrics` (Prometheus), `/ops/*`, `/crm/*`, `/ask`, `/campaign-links`, `/docs`, `/openapi.json` | `X-API-Key: <key>` or `Authorization: Bearer <key>`: always in production, and in development whenever `GROWTHOPS_API_KEYS` is set |
| `POST /ops/events/{id}/replay` | API key plus `X-GrowthOps-Ops-Token` |

Every response carries `X-Request-ID` (echoed from the request when supplied) and security headers. A 500
returns `{"detail": "internal error", "request_id": ...}`; the traceback stays in the logs.

## `POST /webhooks/payments`

Send `X-GrowthOps-Timestamp` (Unix seconds) and `X-GrowthOps-Signature`, the hex HMAC-SHA256 of
`"{timestamp}.{raw body}"` with `GROWTHOPS_WEBHOOK_SECRET`. Requests outside `GROWTHOPS_WEBHOOK_TOLERANCE_SECONDS`
(default 300) are rejected, so a captured request cannot be replayed. Development also accepts the legacy
body-only signature when no timestamp is sent; production does not, and refuses to start with the demo secret.

```json
{
  "event_id": "evt_001",
  "event_type": "payment.succeeded",
  "payment_id": "pay_001",
  "customer_id": "c-00001",
  "deal_id": null,
  "amount_cents": 50000,
  "paid_at": "2026-09-02T00:00:00Z",
  "payment_type": "new",
  "subscription_id": null,
  "product_id": "accelerator"
}
```

Returns HTTP 202 with `event_id`, `status` (`completed`, `failed`, or `dead_letter`), `attempts`, `last_error`, `next_attempt_at`, `trace_id`, and `duplicate`. A repeated completed or dead-lettered event returns `duplicate: true` without redoing steps. A repeated failed event resumes after its last completed step. Reusing an event or payment ID with another payload returns 409. Invalid signature returns 401.

Workflow by `payment_type`: `new` runs record payment → update CRM → grant access → send onboarding; `installment` runs the first two; `renewal` runs the first three. Each completed step is persisted with `(event_id, step_name)` uniqueness and every attempt, successful or not, is logged in `workflow_step_attempts` with its duration and error. A failed step schedules a retry with exponential backoff (5, 10, 20, 40 minutes); `growthops.workflow.run_due` is the retry worker. After five attempts the event moves to the dead-letter queue. Provider redeliveries increment `deliveries`; internal retries do not.

## `GET /ops/customers/{customer_id}`

Returns CRM state, recorded payments, access state, and recent workflow attempts for that customer. It is read-only and needs an API key.

## `POST /campaign-links`

Accepts `campaign_id`, HTTPS `destination_url`, and snake-case `content`. Looks up the campaign registry and rejects invalid names or mismatched source/medium taxonomy. Existing UTM parameters are replaced; unrelated query parameters and URL fragments remain. Returns the canonical URL and its UTM fields. This local endpoint does not create campaigns; a registry management UI is planned.

## Read-only analytics endpoints

`GET /metrics/revenue-truth` returns the five system totals plus the platform→warehouse and CRM→cash bridges (each with `residual_cents`, always 0) and per-platform self-reported vs warehouse ROAS. `GET /metrics/anomalies` returns anomaly episodes with top drivers and the ground-truth check against the planted-incident manifest. `GET /metrics/narrative` returns the validated executive narrative and the mode used (`deterministic`, `llm_validated`, `deterministic_fallback`).

`GET /metrics/executive` returns the **all-time synthetic scenario** metrics, quality measures, and deterministic observations. `GET /metrics/funnel` returns stage counts, conversion from previous stage, and median/p90 transition time. `GET /metrics/attribution/{model}` accepts `first_touch`, `lead_creation`, `last_non_direct`, `u_shaped`, or `linear` and returns net cash by campaign.

`GET /metrics/daily?days=90` returns event-date spend, leads, and payment/refund cash from the local daily mart. `GET /metrics/brief?days=7` returns the Morning Brief: the latest week against the prior week plus prioritized findings, each with evidence, drivers, a recommended investigation, confidence and a source ID. `GET /metrics/content` returns first identified content influence through MQL, calls, customers, and net cash. `GET /metrics/experiments/{experiment_id}` returns variant-level visitor, lead, MQL, customer, and cash results with lead-rate, lead-quality and bootstrap cash intervals, a sample-ratio-mismatch check and a decision derived from those intervals. Assignment is simulated per visitor; it is not a live experiment.

## Readiness, metrics and ask-your-data

| Endpoint | Contract |
|---|---|
| `GET /ready` | 200 with `schema_version`, `stale_sources` and per-source freshness; 503 when the database is unreachable or the schema is not current |
| `GET /metrics` | Prometheus text: `growthops_http_requests_total`, `growthops_http_request_seconds`, `growthops_webhook_{accepted,rejected}_total`, `growthops_workflow_events{status}`, `growthops_paid_without_access_customers`, `growthops_source_age_hours`, `growthops_source_stale` |
| `GET /ask?q=` | Keyless answer: `answer`, `route` (`certified`, `metric`, `definition` or `refused`), `metric_id`, `citations`, `confidence`, `retrieval_mode`, `retrieved` and `latency_ms`. `q` is 1–300 characters |
| `GET /ops/ask-usage?days=7` | Questions by route with average latency, from `ask_log` |

## Marketing operations endpoints

| Endpoint | Contract |
|---|---|
| `GET /metrics/daily-update?day=YYYY-MM-DD` | The written daily update: yesterday against the trailing seven-day average, paid efficiency by platform, the last bulk email, and the top findings with next steps; `text` is copy-ready (also `python -m growthops.performance`) |
| `GET /metrics/paid-efficiency?days=7&by=campaign\|platform` | Spend, impressions, clicks, CPM, CTR, CPC, leads, CPL, MQLs, cost per MQL, booked calls, cost per booked call, deals won, net cash and ROAS on an activity basis; the last row is the paid total |
| `GET /metrics/email` | Per-send and per-type rates (delivery, bounce, reported and human open, click, click-to-open, unsubscribe, complaint), newsletter-to-pipeline, the deliverability check by sending domain and the list source mix |
| `GET /metrics/link-hygiene` | Every short link checked against the campaign registry, with the share of recent clicks on defective links |
| `GET /crm/hubspot/audit` | CRM hygiene against the HubSpot mapping plus custom-property definitions ([mapping](hubspot-mapping.md)) |

`GET /ops/migration` returns the legacy-to-current contact audit and issue list. Run `python -m growthops.migration --apply-safe-repairs` explicitly to fill only null owner/source values from matched legacy records; the API does not expose this mutation.

`GET /dashboard` serves the local Executive Pulse page backed by those endpoints. The root URL redirects to it. The page is a reference UI for the synthetic scenario; generated Power BI import marts and the editable native project are documented in [the handoff](power-bi-handoff.md).

## Operations endpoints

| Endpoint | Contract |
|---|---|
| `GET /ops/workflows` | Success rate, first-attempt success, retries, dead letters, duplicate deliveries absorbed, p50/p95 seconds to complete, errors by type |
| `GET /ops/events/{event_id}` | Trace: every step attempt with start time, duration, status and error |
| `GET /ops/paid-without-access` | Support queue: successful new-product payments with no active entitlement |
| `POST /ops/events/{event_id}/replay` | Operator replay of a dead-lettered event; requires `X-GrowthOps-Ops-Token` matching `GROWTHOPS_OPS_TOKEN` (403 otherwise, and disabled when unset) |

`GET /ops/customers/{customer_id}` also returns a `diagnosis` such as "Paid but no community access: replay evt-…".

## Target endpoints (not implemented)

| Endpoint | Contract |
|---|---|
| `GET /ops/quality/issues` | Paginated data-quality queue with source links |
| `POST /experiments` | Register hypothesis, variants, exposure unit, primary and guardrail metrics |
| Provider bridge for Stripe | Verify Stripe's signature, translate its event to this contract, sign and forward |
