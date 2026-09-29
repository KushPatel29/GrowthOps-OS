# HubSpot v2.1 portal change plan

**Portal:** HubSpot developer test account 247549241, containing the sampled synthetic ScaleLab data. **Prepared:** 2026-09-29. **State:** review only; no v2.1 portal changes applied.

The connected portal has 959 synthetic contacts, 130 deals, 33 GrowthOps custom properties and three published GrowthOps workflows. A ticket search returned **0 tickets total**, so there are no unresolved high-priority tickets to triage. The local v2.1 control plane covers all 14,693 synthetic contacts; its counts must not be shown as portal counts.

## Proposed schema additions

Each proposed internal name was checked through the HubSpot Properties tool on 2026-09-29 and was **absent**. These are empty schema fields. Creating them would not itself change lifecycle stage, marketing status, associations or attribution values.
The create payloads specify both `type` and `fieldType`, as required by [HubSpot's CRM v3 property validation](https://developers.hubspot.com/changelog/crm-object-property-validattion).

The read-only `python -m growthops.hubspot_v21 audit` check on 2026-09-29 found **33/33 baseline properties present with zero definition drift**. It confirmed that all five additions below remain absent; the audit made zero portal writes.

| Object | Internal name | Current | Proposed type / values | Write eligibility |
|---|---|---|---|---|
| Contact | `growthops_person_key` | Absent | String, read-only external identity key | Populate only for verified portal contact ↔ person links after duplicate review |
| Contact | `growthops_sql_date` | Absent | Date | Populate only from an accepted SQL transition; historical unknown remains blank |
| Deal | `growthops_qualification_status` | Absent | Enumeration: `qualified`, `unqualified`, `unknown` | Populate from an actual rep decision, not the local synthetic fixture |
| Deal | `growthops_qualified_at` | Absent | Date/time | Populate with the source decision timestamp, never inferred from meeting attendance |
| Deal | `growthops_qualification_reason` | Absent | String | Populate from the decision reason and preserve user-entered context |

**Proposed schema action:** create these five properties in the existing `growthops` group after approval, then read them back. No record-value update is included in this action. The local registry and API already describe the fields as **proposed additions**; the 33 existing fields remain the expected baseline.

The checked schema-only command is `python -m growthops.hubspot_v21 plan`; `audit` performs a read-only comparison with the live portal. The separate `apply` command requires an explicit `--approved-schema-only` flag, checks that portal ID 247549241 is a developer test account, compares all existing definitions before the first write, creates only missing fields, and reads all five back. It has not been run against the portal.

## Gates before record changes

1. Verify contact and deal identity mappings against the 959/130 portal sample. The local person key is the synthetic contact ID, while the portal deduplicated some contacts by email.
2. Record exact current and proposed values per object ID. Each `manage_crm_objects` batch is at most ten objects and needs a reviewed table of object type, ID, property, current value and new value.
3. Do not copy local hash-based `synthetic_rep_assessment` into HubSpot as if a sales representative made the decision. Rep decisions need their own source and timestamp.
4. Keep marketing-contact status unchanged without consent evidence. The local audit reports 14,693/14,693 eligibility unknown across the full synthetic scenario.
5. Recheck workflow triggers and owner routing before publishing a new workflow version. The local workflow registry is desired state, not live drift evidence.

## Local evidence ready now

- `GET /v2/registries/property/versions` exposes the 33-field baseline and five proposed additions.
- `GET /v2/crm/health` exposes four measured components with passing and eligible counts.
- `GET /v2/quality/issues` and the local console expose evidence and dry-run repair proposals.
- `GET /v2/crm/marketing-contacts/audit` proposes review groups without marketing-status changes.
- `GET /v2/metrics/qualified-pipeline` is a synthetic decision fixture, labeled separately from bookings and cash.
