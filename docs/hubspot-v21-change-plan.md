# HubSpot v2.1 schema change record

**Portal:** HubSpot developer test account 247549241, containing the sampled synthetic ScaleLab data. **Applied and verified:** 2026-09-29. **Scope:** five empty custom properties; no record values, associations, marketing statuses or workflows changed.

The prior portal snapshot had 959 synthetic contacts and 130 deals. Read-only searches on 2026-09-29 returned **961 contacts and 130 deals**. A read-only ID comparison on 2026-09-30 found every one of the 959 GrowthOps contacts present once; the other two are sample contacts HubSpot creates with a new account (source `sample-contact`, no GrowthOps ID), so the portal and the warehouse sample still reconcile. The portal now has **38 GrowthOps custom properties**: all 33 baseline fields and the five v2.1 fields below. The prior build published three GrowthOps workflows. A HubSpot connector ticket search on 2026-09-29 returned **0 tickets total**, so there are no unresolved high-priority tickets to triage. The separate private-app token lacks ticket-search scope; ticket counts come from the connector. The local v2.1 control plane covers all 14,693 synthetic contacts; its counts must not be shown as portal counts. See the [current marketing-contact audit](hubspot-marketing-audit.md).

## Applied schema additions

Each internal name was confirmed absent before creation. The schema-only apply checked the exact developer portal ID and existing definitions, created five fields, and read them back. A second read-only audit found **33/33 baseline fields and all five additions present with zero definition drift**. A full search of 961 contacts and 130 deals found **zero populated values** in the new fields; the read-back made zero writes.
The create payloads specify both `type` and `fieldType`, as required by [HubSpot's CRM v3 property validation](https://developers.hubspot.com/changelog/crm-object-property-validattion).

| Object | Internal name | Current | Type / values | Record-value policy |
|---|---|---|---|---|
| Contact | `growthops_person_key` | Present, empty | String, external identity key | Populate only for verified portal contact ↔ person links after duplicate review |
| Contact | `growthops_sql_date` | Present, empty | Date | Populate only from an accepted SQL transition; historical unknown remains blank |
| Deal | `growthops_qualification_status` | Present, empty | Enumeration: `qualified`, `unqualified`, `unknown` | Populate from an actual rep decision, not the local synthetic fixture |
| Deal | `growthops_qualified_at` | Present, empty | Date/time | Populate with the source decision timestamp, never inferred from meeting attendance |
| Deal | `growthops_qualification_reason` | Present, empty | String | Populate from the decision reason and preserve user-entered context |

The local registry continues to describe the 33-field baseline and five v2.1 target definitions; it is a static contract, while `audit` reports the live portal state. No record-value update was included in the schema action.

The checked schema-only command is `python -m growthops.hubspot_v21 plan`; `audit` performs a read-only comparison with the live portal. The `apply` command requires `--approved-schema-only`, checks that portal ID 247549241 is a developer test account, compares existing definitions before a write, creates only missing fields, and verifies the complete definitions on read-back. Running it again is idempotent when the fields match.

## Gates before record changes

1. Verify contact and deal identity mappings against the 959 GrowthOps contacts and 130 deals (the portal's other two contacts are HubSpot's own samples). The local person key is the synthetic contact ID, while the portal deduplicated some contacts by email.
2. Record exact current and proposed values per object ID. Each `manage_crm_objects` batch is at most ten objects and needs a reviewed table of object type, ID, property, current value and new value.
3. Do not copy local hash-based `synthetic_rep_assessment` into HubSpot as if a sales representative made the decision. Rep decisions need their own source and timestamp.
4. Keep marketing-contact status unchanged without consent evidence. The connected portal's read-only audit cannot establish eligibility for any of its 961 sampled contacts from the inspected fields. The local v2.2 scenario plants explicit **synthetic** consent decisions for a small buyer sample to exercise gating; those decisions are not linked to or authoritative for portal contacts.
5. Recheck workflow triggers and owner routing before publishing a new workflow version. The local workflow registry is desired state, not live drift evidence.

## Local evidence ready now

- `GET /v2/registries/property/versions` exposes the 33-field baseline and five v2.1 target definitions; use `audit` for current portal status.
- `GET /v2/crm/health` exposes four measured components with passing and eligible counts.
- `GET /v2/quality/issues` and the local console expose evidence and dry-run repair proposals.
- `GET /v2/crm/marketing-contacts/audit` proposes review groups without marketing-status changes.
- `GET /v2/metrics/qualified-pipeline` is a synthetic decision fixture, labeled separately from bookings and cash.
