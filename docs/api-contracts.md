# API contracts v0.1

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
  "paid_at": "2026-09-02T00:00:00Z"
}
```

Returns HTTP 202 with `event_id`, `status` (`completed` or `failed`), `attempts`, `last_error`, and `duplicate`. A repeated completed event returns `duplicate: true` without redoing steps. A repeated failed event resumes after its last completed step. Reusing an event ID with another payload returns 409. Invalid signature returns 401. A failed workflow stays visible for retry and alerting; the current simulator retries on redelivery and has no scheduler or DLQ yet.

Workflow: record payment → update CRM → grant access. Each completed step is persisted with `(event_id, step_name)` uniqueness. A CRM identity gap leaves the workflow failed and retryable. An entitlement is unique per customer in the local slice; target design makes it unique per customer/product.

## `GET /ops/customers/{customer_id}`

Returns CRM state, recorded payments, access state, and recent workflow attempts for that customer. It is read-only. The local simulator has no authentication; it must not be exposed to a public network. The target console requires role-based access and audit logging before handling any real customer data.

## `POST /campaign-links`

Accepts `campaign_id`, HTTPS `destination_url`, and snake-case `content`. Looks up the campaign registry and rejects invalid names or mismatched source/medium taxonomy. Existing UTM parameters are replaced; unrelated query parameters and URL fragments remain. Returns the canonical URL and its UTM fields. This local endpoint does not create campaigns; a registry management UI is planned.

## Read-only analytics endpoints

`GET /metrics/executive` returns the **all-time synthetic scenario** metrics, quality measures, and deterministic observations. `GET /metrics/funnel` returns stage counts, conversion from previous stage, and median/p90 transition time. `GET /metrics/attribution/{model}` accepts `first_touch`, `lead_creation`, `last_non_direct`, or `u_shaped` and returns net cash by campaign.

`GET /metrics/daily?days=90` returns event-date spend, leads, and payment/refund cash from the local daily mart. `GET /metrics/brief?days=7` compares equal windows anchored to the latest day with paid spend and emits evidence and an investigation step when a rule fires. It is an on-demand comparison, not a scheduled morning brief. `GET /metrics/content` returns first identified content influence through MQL, calls, customers, and net cash. `GET /metrics/experiments/{experiment_id}` returns variant-level visitor, lead, MQL, customer, and cash results with lead-rate and bootstrap cash intervals. The synthetic assignment is balanced but is not a live randomized experiment.

`GET /ops/migration` returns the legacy-to-current contact audit and issue list. Run `python -m growthops.migration --apply-safe-repairs` explicitly to fill only null owner/source values from matched legacy records; the API does not expose this mutation.

`GET /dashboard` serves the local Executive Pulse page backed by those endpoints. The root URL redirects to it. The page is a reference UI for the synthetic scenario; generated Power BI import marts and the editable native project are documented in [the handoff](power-bi-handoff.md).

## Target endpoints (not implemented)

| Endpoint | Contract |
|---|---|
| Scheduled executive brief | Delivery schedule, source freshness, and quality suppression |
| `GET /ops/workflows/{event_id}` | Trace of adapter steps, retries, timestamps, and errors |
| `POST /ops/workflows/{event_id}/retry` | Authorized, audited manual retry of a failed event |
| `GET /ops/quality/issues` | Paginated data-quality queue with source links |
| `POST /experiments` | Register hypothesis, variants, exposure unit, primary and guardrail metrics |
