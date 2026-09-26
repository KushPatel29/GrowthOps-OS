# HubSpot mapping

GrowthOps OS keeps its own CRM tables, and `growthops/hubspot.py` maps them onto HubSpot's standard objects so the same
analysis can run on a HubSpot portal export, or so a clean import can be prepared. **No HubSpot portal is connected**;
owner IDs are placeholders, and the search-response parser is tested against a fixture shaped like the CRM v3 API.

## Standard properties

| Internal | HubSpot object.property | Mapping |
|---|---|---|
| `contacts.current_stage` | `contacts.lifecyclestage` | lead → `lead`, mql → `marketingqualifiedlead`, opportunity → `opportunity`, customer → `customer` |
| `contacts.owner_id` | `contacts.hubspot_owner_id` | owner map (from the Owners API in a real portal) |
| `contacts.email` | `contacts.email` | trimmed, lower-cased; HubSpot dedupes imports on email, so the export keeps one record per address |
| `deals.stage` | `deals.dealstage` (pipeline `default`) | open → `presentationscheduled`, closed_won → `closedwon`, closed_lost → `closedlost` |
| `deals.amount_cents` | `deals.amount` | dollars with two decimals |
| `deals.closed_at` | `deals.closedate` | ISO date |
| lead-creation campaign medium | `contacts.hs_analytics_source` (read-only) | paid_social → `PAID_SOCIAL`, paid_search → `PAID_SEARCH`, organic_video → `SOCIAL_MEDIA`, owned_email → `EMAIL_MARKETING`, referral → `REFERRALS`, event → `OTHER_CAMPAIGNS`, none → `DIRECT_TRAFFIC` |

`hs_analytics_source` (Original Traffic Source) is calculated by HubSpot from tracking and cannot be imported. The
registry source therefore travels in a custom property, and `source_mismatches` compares the two on an export: a
contact created by a paid-search campaign that HubSpot shows as `OFFLINE` is the classic sign of a migration import
overwriting the original source.

## Custom properties

`property_definitions` returns bodies for `POST /crm/v3/properties/{contacts|deals}` in a `growthops` group:
`growthops_contact_id` (unique), `growthops_original_source` (enumeration of registry sources only, so off-taxonomy
values such as `FB` cannot be imported), `growthops_lead_campaign`, `growthops_legacy_id`, `growthops_deal_id`
(unique) and `growthops_product` (enumeration).

## Files and API payloads

```bash
python -m growthops.hubspot --output build/hubspot
# build/hubspot/hubspot_contacts.csv, hubspot_deals.csv, hubspot_properties.json, plus the audit as JSON
```

Deals carry `contact_email` as the association key for a two-object import. `search_request` builds an incremental
pull (`POST /crm/v3/objects/contacts/search`, filter `lastmodifieddate GTE` watermark, ascending sort, 100 per page);
because search stops at 10,000 results per query, a backfill advances the watermark instead of paging past the limit.
`parse_search_response` maps results back and reports unmapped lifecycle stages and unknown owners.

## CRM audit

`GET /crm/hubspot/audit` and the Data quality view report: rows an import would merge on email; property fill rates;
paying contacts and closed-won contacts not at the customer stage; customers without a closed-won deal; and leads
untouched for a year, which are candidates to set as non-marketing contacts (HubSpot bills by marketing contacts).
