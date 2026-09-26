# API contracts v0.2

The implemented API is a **local Stripe-like simulator**. It is not Stripe's actual webhook schema and does not call HubSpot or a community provider.

## `POST /webhooks/payments`

Raw JSON body is signed with HMAC SHA-256 using `GROWTHOPS_WEBHOOK_SECRET` (default for local demonstration only: `local-demo-secret`). Send the hexadecimal digest in `X-GrowthOps-Signature`. A real Stripe adapter will verify Stripe's own signature scheme and translate its versioned event into this internal contract.

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

Returns CRM state, recorded payments, access state, and recent workflow attempts for that customer. It is read-only. The local simulator has no authentication; it must not be exposed to a public network. The target console requires role-based access and audit logging before handling any real customer data.

## `POST /campaign-links`

Accepts `campaign_id`, HTTPS `destination_url`, and snake-case `content`. Looks up the campaign registry and rejects invalid names or mismatched source/medium taxonomy. Existing UTM parameters are replaced; unrelated query parameters and URL fragments remain. Returns the canonical URL and its UTM fields. This local endpoint does not create campaigns; a registry management UI is planned.

## Read-only analytics endpoints

`GET /metrics/revenue-truth` returns the five system totals plus the platform→warehouse and CRM→cash bridges (each with `residual_cents`, always 0) and per-platform self-reported vs warehouse ROAS. `GET /metrics/anomalies` returns anomaly episodes with top drivers and the ground-truth check against the planted-incident manifest. `GET /metrics/narrative` returns the validated executive narrative and the mode used (`deterministic`, `llm_validated`, `deterministic_fallback`).

`GET /metrics/executive` returns the **all-time synthetic scenario** metrics, quality measures, and deterministic observations. `GET /metrics/funnel` returns stage counts, conversion from previous stage, and median/p90 transition time. `GET /metrics/attribution/{model}` accepts `first_touch`, `lead_creation`, `last_non_direct`, `u_shaped`, or `linear` and returns net cash by campaign.

`GET /metrics/daily?days=90` returns event-date spend, leads, and payment/refund cash from the local daily mart. `GET /metrics/brief?days=7` returns the Morning Brief: the latest week against the prior week plus prioritized findings, each with evidence, drivers, a recommended investigation, confidence and a source ID. `GET /metrics/content` returns first identified content influence through MQL, calls, customers, and net cash. `GET /metrics/experiments/{experiment_id}` returns variant-level visitor, lead, MQL, customer, and cash results with lead-rate, lead-quality and bootstrap cash intervals, a sample-ratio-mismatch check and a decision derived from those intervals. Assignment is simulated per visitor; it is not a live experiment.

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
| Scheduled executive brief | Delivery schedule, source freshness, and quality suppression |
| `GET /ops/quality/issues` | Paginated data-quality queue with source links |
| `POST /experiments` | Register hypothesis, variants, exposure unit, primary and guardrail metrics |
| Real provider adapters | Stripe signature scheme, CRM and community-platform APIs behind the same step interface |
