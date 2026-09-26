# Tracking and CRM property plan

## Browser and server event taxonomy

Required envelope for every event: `event_id` (UUID), `event_name`, `occurred_at` (UTC), `received_at` (UTC), `schema_version`, `anonymous_id`, `session_id`, `consent_state`, `page_url`, and `trace_id` where a server workflow produced it. Known-person ID is added only after lawful identification; it must not replace historical anonymous IDs.

| Event | Trigger | Required properties |
|---|---|---|
| `page_view` | Instrumented page loads | `landing_page_id`, `referrer`, `utm_*`, `click_id` |
| `content_engaged` | Qualified watch/read threshold | `content_id`, `engagement_seconds`, `platform` |
| `cta_click` | CTA selection | `content_id`, `offer_id`, `destination_url` |
| `form_submitted` | Server accepts form | `form_id`, `offer_id`, `campaign_id`, `person_id` |
| `lead_qualified` | CRM qualification rule fires | `contact_id`, `qualification_rule`, `owner_id` |
| `call_booked` | Calendar booking confirmed | `meeting_id`, `contact_id`, `booked_for` |
| `call_attended` | Attendance confirmed | `meeting_id`, `contact_id`, `duration_seconds` |
| `deal_stage_changed` | CRM stage changes | `deal_id`, `from_stage`, `to_stage`, `changed_at` |
| `payment_succeeded` | Verified payment event | `payment_id`, `customer_id`, `deal_id`, `amount_minor`, `currency` |
| `refund_succeeded` | Verified refund event | `refund_id`, `payment_id`, `amount_minor`, `currency` |
| `access_changed` | Entitlement provider confirms change | `customer_id`, `product_id`, `old_status`, `new_status` |
| `renewal_due` | Subscription enters due window | `subscription_id`, `due_at`, `amount_minor` |

## UTM registry

Allowed `utm_source`: `meta`, `google`, `linkedin`, `youtube`, `newsletter`, `partner`, `direct`. Allowed `utm_medium`: `paid_social`, `paid_search`, `organic_video`, `owned_email`, `referral`, `none`. Campaign identifiers are lowercase snake case with an owner, active dates, offer, channel, and registry ID. Store `original_*` acquisition properties as immutable; `latest_*` may change on a new eligible touch. A missing UTM on a direct visit does not overwrite a prior original source. Preserve raw values alongside normalized values for audit.

## HubSpot-style contact property registry (target)

| Property | Type | Rule |
|---|---|---|
| `original_utm_source` | enum | Set once from first eligible tracked touch |
| `original_utm_medium` | enum | Set once with original source |
| `original_campaign_id` | string | Must map to active registry row |
| `latest_utm_source` | enum | Update on most recent eligible non-direct touch |
| `latest_campaign_id` | string | Must map to registry |
| `first_content_id` | string | First known content touch |
| `latest_content_id` | string | Latest known content touch |
| `qualification_date` | datetime | First validated MQL transition |
| `call_booked_date` | datetime | First confirmed booking |
| `customer_tier` | enum | Derived from active paid product |
| `subscription_status` | enum | Derived from payment/subscription system |
| `renewal_date` | date | Next scheduled renewal |
| `legacy_crm_id` | string | Immutable migration reference |
| `migration_reconciliation_status` | enum | Matched, missing, conflicting, resolved |

For every property, store business definition, owner, type, requiredness, source system, update rule, and change history. The source of truth for payment and refund amounts remains the payment system. CRM records may mirror these values for operations but cannot redefine collected revenue.

## Short links (Bitly or equivalent)

Every short link must resolve to a destination whose `utm_source`, `utm_medium` and `utm_campaign` match a valid
row in the campaign registry exactly (case included). `audit_short_links` (API: `GET /metrics/link-hygiene`, dbt:
`mart_link_hygiene`) checks each link and reports the share of recent clicks landing on defective links. Build new
links with `POST /campaign-links`, then shorten the result; never shorten a hand-typed URL. The seeded defects are an
Instagram bio link with no UTMs, a podcast link tagged `Podcast` instead of `partner`, a YouTube link pointing at an
unregistered campaign and a LinkedIn post tagged `social` for a `paid_social` campaign.

## Email

Sends are logged with delivered, bounces, opens, machine opens, clicks, unsubscribes and complaints. Machine opens
(mailbox privacy proxies) are excluded from engagement. A sending-domain change must be warmed up and authenticated
(SPF, DKIM, DMARC alignment) before bulk sends move to it; the deliverability check flags any domain above a 2% bounce
or 0.1% complaint rate over the last 28 days.
