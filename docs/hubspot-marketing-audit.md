# Connected HubSpot marketing audit

**Scope:** synthetic sample in developer test portal 247549241. **Observed:** 2026-09-29 22:42 UTC (first run 13:13 UTC, same counts). **Method:** read-only CRM property and contact search; zero portal writes. Run again with `python -m growthops.hubspot_marketing_audit` to refresh the counts. The command checks the portal ID and required property names before reporting.

The search returned **961 contacts**: the 959 GrowthOps contacts of the reconciled sample, every one present once, and **two sample contacts HubSpot created itself** when the account was set up (`@hubspot.com` addresses, source `sample-contact`, created 2026-09-28 21:05 UTC, before the GrowthOps import). They carry no GrowthOps ID or properties, so they land in the blank-field queues below; the audit now reports them as `contacts_without_growthops_id` and gives each queue its GrowthOps-only count. The earlier 959 was a count of contacts with a GrowthOps ID, so nothing changed between the two runs.

| Measure | Portal count | GrowthOps records | Interpretation |
|---|---:|---:|---|
| Not marked marketable | 961 | 959 | `hs_marketable_status=false` for every sampled contact |
| Lead / MQL / opportunity / customer | 664 / 185 / 59 / 53 | 662 / 185 / 59 / 53 | Current HubSpot lifecycle stage |
| Blank email opt-out field | 961 | 959 | A blank opt-out value is not affirmative consent or marketing eligibility |
| Complete tracking | 824 | 824 | GrowthOps tracking status |
| Off-taxonomy / missing UTM / blank tracking | 78 / 31 / 2 | 78 / 31 / 0 | 109 GrowthOps contacts need tracking review; the two blanks are HubSpot's sample contacts |
| Direct tracking | 26 | 26 | Kept separate from tracking defects |
| Blank original UTM source | 89 | 87 | Review source evidence; direct traffic may legitimately have no UTM |
| Missing owner, all stages | 4 | 2 | The two in active stages are HubSpot's sample contacts |
| Active lead, MQL or opportunity without owner | 2 | 0 | No GrowthOps contact needs routing; both are HubSpot's sample contacts |
| Stale lead flag | 102 | 102 | Review candidate, not an automatic suppression decision |
| Closed-won flag | 53 | 53 | Cross-check against the 53 customer-stage contacts |

The inspected fields do not establish consent or marketing eligibility for any of these 961 contacts. The audit recommends **zero marketing-status changes** and performs none. It also does not delete, suppress, email, or reassign anyone. The output includes a small sample of opaque HubSpot IDs for each review queue, so an operator can inspect underlying records before proposing a change.

The portal's five empty v2.1 custom fields are documented in the separate [schema change record](hubspot-v21-change-plan.md). The local full-scenario marketing audit covers 14,693 synthetic contacts; its totals must not be presented as live portal totals.
