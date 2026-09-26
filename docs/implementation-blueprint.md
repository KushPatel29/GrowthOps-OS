# GrowthOps OS implementation blueprint

Status: **design contract plus a runnable local slice**. The code in `growthops/` implements a subset with SQLite and simulated adapters. `warehouse/dbt/` now implements a DuckDB dbt DAG for the local sources and marts; the BigQuery/PostgreSQL warehouse and provider architecture below remain targets. The exact current state is in [project status](../PROJECT_STATUS.md).

## Product and evidence boundary

Primary user: a Marketing Data Analyst who needs a defensible acquisition-to-cash view and a daily explanation of changes. Secondary users: revenue operations and support staff investigating failed customer workflows. All business and CRM records are synthetic and clearly labelled. This is a portfolio simulation, not a claim of access to any real company's systems.

The working question is: which acquisition and content activity produced qualified pipeline and **net collected cash**, and how much of that answer is supported by healthy tracking and CRM data?

## Deployment and data flow

```text
Media / ads / site / forms -> ingestion contracts -> raw event store
                                      |                 |
                                      v                 v
                              operational Postgres   dbt warehouse
                                      |                 |
                         payment -> CRM -> access   staged sources
                                      |                 v
                                 workflow log       identity / lifecycle / attribution
                                      |                 v
                                 ops console         governed marts
                                                        |
                                        Power BI / executive brief / diagnostics
```

Target operational system: FastAPI, PostgreSQL, Alembic, a queue worker, and adapter interfaces for CRM, payment, community, and messaging providers. Target warehouse: BigQuery or PostgreSQL with dbt; the decision depends on hosting budget and real data volume. A raw landing layer must preserve provider IDs, event times, ingestion times, schema versions, and payload references. Identifiable data stays out of public dashboard exports. The local simulator uses SQLite and in-process adapters to keep the first slice runnable.

## Exact target schema: 39 tables

Keys shown are logical keys. Every fact also carries `source_system`, `ingested_at`, and `schema_version` where applicable. Currency is integer minor units plus ISO currency code; timestamps are UTC. Personally identifying values belong in restricted operational schemas.

| Domain | Table | Primary key | Required columns and links |
|---|---|---|---|
| Identity | `dim_person` | `person_id` | `created_at`, `first_known_at`, `person_status` |
| Identity | `dim_company` | `company_id` | `name`, `industry`, `size_band` |
| Identity | `bridge_identity` | `(identity_type, identity_value_hash)` | `person_id`, `first_seen_at`, `confidence`, `resolution_rule` |
| Identity | `dim_account` | `account_id` | `company_id`, `owner_id`, `account_tier` |
| Acquisition | `dim_channel` | `channel_id` | `source`, `medium`, `channel_group` |
| Acquisition | `dim_campaign_registry` | `campaign_id` | `channel_id`, `campaign_name`, `offer_id`, `owner_id`, `valid_from`, `valid_to` |
| Acquisition | `dim_ad_group` | `ad_group_id` | `campaign_id`, `audience_key` |
| Acquisition | `dim_creative` | `creative_id` | `ad_group_id`, `format`, `message_key` |
| Acquisition | `dim_content` | `content_id` | `platform`, `publish_at`, `topic`, `cta_offer_id` |
| Acquisition | `dim_landing_page` | `landing_page_id` | `canonical_url`, `offer_id`, `tracking_version` |
| Acquisition | `dim_offer` | `offer_id` | `offer_name`, `funnel_tier`, `product_id` |
| Acquisition | `fact_ad_performance` | `(date, platform, creative_id)` | `campaign_id`, `impressions`, `clicks`, `spend_minor` |
| Acquisition | `fact_web_sessions` | `session_id` | `anonymous_id`, `person_id`, `landing_page_id`, `started_at`, `utm_*`, `click_id` |
| Acquisition | `fact_content_engagement` | `engagement_id` | `content_id`, `person_id`, `event_type`, `occurred_at`, `seconds_watched` |
| Acquisition | `fact_link_clicks` | `click_id` | `campaign_id`, `content_id`, `session_id`, `occurred_at` |
| Acquisition | `fact_form_submissions` | `submission_id` | `person_id`, `session_id`, `form_id`, `offer_id`, `submitted_at` |
| CRM | `dim_contact` | `contact_id` | `person_id`, `hubspot_id`, `legacy_id`, `original_source`, `current_stage` |
| CRM | `dim_owner` | `owner_id` | `team`, `active_from`, `active_to` |
| CRM | `dim_lifecycle_stage` | `stage_id` | `stage_name`, `stage_order`, `is_terminal` |
| CRM | `dim_deal` | `deal_id` | `contact_id`, `account_id`, `pipeline_id`, `amount_minor`, `stage` |
| CRM | `dim_pipeline` | `pipeline_id` | `pipeline_name`, `active_from` |
| CRM | `fact_lifecycle_events` | `lifecycle_event_id` | `contact_id`, `stage_id`, `occurred_at`, `source_event_id` |
| CRM | `fact_owner_history` | `owner_event_id` | `contact_id`, `owner_id`, `valid_from`, `valid_to` |
| CRM | `fact_deal_stage_history` | `deal_stage_event_id` | `deal_id`, `stage`, `occurred_at` |
| CRM | `fact_sales_activity` | `activity_id` | `contact_id`, `deal_id`, `activity_type`, `occurred_at`, `outcome` |
| Revenue | `fact_payments` | `payment_id` | `stripe_payment_id`, `customer_id`, `deal_id`, `gross_minor`, `paid_at`, `status` |
| Revenue | `fact_refunds` | `refund_id` | `payment_id`, `refund_minor`, `refunded_at` |
| Revenue | `fact_subscriptions` | `subscription_id` | `customer_id`, `product_id`, `started_at`, `ended_at`, `status` |
| Revenue | `fact_renewals` | `renewal_id` | `subscription_id`, `due_at`, `collected_minor`, `status` |
| Revenue | `fact_expansion` | `expansion_id` | `subscription_id`, `deal_id`, `incremental_minor`, `effective_at` |
| Revenue | `fact_failed_payments` | `failure_id` | `subscription_id`, `attempt_at`, `failure_code`, `recovered_at` |
| Operations | `processed_events` | `event_id` | `event_type`, `payload_hash`, `status`, `attempts`, `received_at`, `completed_at` |
| Operations | `workflow_steps` | `(event_id, step_name)` | `status`, `completed_at`, `error_code`, `trace_id` |
| Operations | `access_entitlements` | `(customer_id, product_id)` | `provider_id`, `status`, `granted_at`, `revoked_at` |
| Operations | `property_registry` | `(object_type, property_name)` | `data_type`, `definition`, `source_system`, `owner`, `required` |
| Operations | `workflow_registry` | `workflow_id` | `trigger`, `owner`, `version`, `enabled`, `sla_seconds` |
| Quality | `fact_quality_issues` | `issue_id` | `check_id`, `entity_type`, `entity_id`, `detected_at`, `status` |
| Quality | `fact_migration_matches` | `(legacy_id, hubspot_id)` | `match_rule`, `match_confidence`, `owner_match`, `source_match`, `stage_match` |
| Analysis | `fact_experiment_exposures` | `exposure_id` | `experiment_id`, `variant_id`, `person_id`, `session_id`, `exposed_at` |

All foreign keys above are enforced in the operational store where the relationship is synchronous. Warehouse relationships are tested with dbt. Identity links can be unresolved initially; unresolved records remain visible in a quality queue rather than being silently dropped.

## Synthetic data generator contract

The first local generator is implemented in `growthops.seed`: fixed random seed, 240 contacts, five acquisition campaigns plus direct traffic, lifecycle events, deals, payments, refunds, and deliberate defects. It is deterministic and rerunnable. The full generator will use a configuration file for 2–3 years of daily cohorts and stage transition probabilities, then inject a separate defect manifest. This separation preserves a clean expected truth for evaluating repair logic.

Target scale: 450K sessions, 85K leads, 21K MQLs, 8K calls, 5K opportunities, 1.6K customers, 10K payment/refund records, and 25K workflow executions. Generation is chunked by month with reproducible seeds. Invariants: no refund exceeds its payment; each person has at most one first acquisition; payments can be unmatched to deals but never lack a stable payment ID; events may arrive late or twice. Defect manifest includes missing UTMs, invalid campaign names, duplicate CRM contacts, missing owners, inconsistent stages, missing original source, and payment/deal mismatches. Baseline defect percentages from the brief are *scenario targets*, not claims about an actual company.

## dbt DAG and tests (local subset implemented; full design target)

```text
sources: ga4, ads, content, hubspot, legacy_crm, stripe, workflow
  -> stg_*: type casts, UTC normalization, source IDs, deduplication
  -> int_identity_resolution + int_campaign_mapping
  -> int_lifecycle_history + int_attribution_touches + int_payment_reconciliation
  -> mart_growth_daily + mart_campaign_performance + mart_content_performance
  -> mart_funnel + mart_revenue + mart_customer_lifecycle + mart_measurement_health
```

Critical tests: source ID uniqueness; valid taxonomy; nonnegative spend; payment/refund bounds; allowed lifecycle transitions; relationship of closed-won deals to contacts; one canonical contact per resolved person; campaign registry coverage; cash reconciliation; freshness of each source. Snapshot CRM contact owner, source, and stage changes to preserve history. Published marts expose `data_as_of`, `metric_version`, and unresolved-record counts.

## Dashboard wireframes (target Power BI)

| Page | Top row | Main visual | Investigation area |
|---|---|---|---|
| Executive Pulse | Spend, leads, MQLs, calls, closed-won, net cash, cash ROAS | daily trend with prior-period comparison | evidence-backed changes and quality warnings |
| Acquisition | spend, CPL, cost/MQL | channel/campaign trend | creative, audience, landing-page drill |
| Full Funnel | cohort counts and conversion rates | stage funnel plus median and p90 transition time | channel/campaign/cohort comparison |
| Content Intelligence | views, leads, MQLs, customers, cash | content-to-pipeline table | video/CTA detail |
| Attribution | first, lead-creation, last non-direct, U-shaped cash credit | model comparison | unassigned cash and touch coverage |
| Revenue | booked, gross, refunds, net, recurring | payment/deal reconciliation | unmatched records queue |
| Measurement Health | UTM, registry, owner, lifecycle, cash match | quality trend | failed checks and suggested repair |
| Automation Health | success rate, retries, DLQ, latency | workflow status timeline | trace and failed step detail |

Every monetary visual names its basis: booked deal value or net collected cash. A page warning appears when relevant quality thresholds fail. Filters: date cohort, channel, campaign, offer, content, product, and data-as-of time.

## AI evaluation dataset (target)

AI summaries consume only a versioned JSON evidence object produced by deterministic queries. A human-authored evaluation set will contain at least 30 synthetic scenarios: 10 straightforward changes, 10 conflicting or sparse-evidence cases, 5 data-quality failures, and 5 tempting but unsupported causal stories. Each case stores `input_evidence`, expected factual claims, forbidden claims, required caveat, and preferred investigation. Score factual support, numeric accuracy, citation to metric IDs, calibrated uncertainty, and actionability. A failed factual-support or numeric-accuracy check blocks the summary from the executive brief. No unconstrained warehouse querying by the model.

## Docker services and release sequence (target)

`api` handles signed webhooks and read-only operations queries; `worker` retries durable tasks; `postgres` stores operational state; `warehouse`/dbt job models analytics; `scheduler` runs freshness and executive-brief jobs. Local Docker Compose can run API, worker, and Postgres; managed deployments replace container-local state with persistent services. Secrets come from environment or secret manager, never committed files.

1. **Measurement foundation:** event taxonomy, UTM registry, synthetic source generator, CRM migration audit, quality checks. Exit: known defects are surfaced and metric definitions are agreed.
2. **Acquisition-to-cash model:** identity, lifecycle, payment reconciliation, first/lead-creation/last touch cash attribution, dbt marts. Exit: cash and attributed cash reconcile; unresolved records are explicit.
3. **Decision layer:** Power BI pages, experiment analysis, deterministic executive brief. Exit: every executive KPI maps to the metric catalog and has freshness metadata.
4. **Lifecycle automation:** provider adapters, retries, DLQ, entitlement and renewal flows, operations console. Exit: duplicate webhook causes zero duplicate side effects; partial failure resumes safely.
5. **AI and scale:** grounded narrative, transcript classifier, RAG and evals, larger synthetic dataset, deployment. Exit: zero unsupported claims in the evaluation suite.

The next code increment should implement dbt-style staging and marts against the local dataset, with the existing payment-level attribution module as a reconciliation reference.
