# Connected HubSpot marketing audit

**Scope:** synthetic sample in developer test portal 247549241. **Observed:** 2026-09-29 13:13 UTC. **Method:** read-only CRM property and contact search; zero portal writes. Run again with `python -m growthops.hubspot_marketing_audit` to refresh the counts. The command checks the portal ID and required property names before reporting.

The current search returned **961 contacts**, two more than the prior 959-contact snapshot. The snapshot was a count, not an ID list, so the audit does not identify which records changed.

| Measure | Current count | Interpretation |
|---|---:|---|
| Not marked marketable | 961 | `hs_marketable_status=false` for every sampled contact |
| Lead / MQL / opportunity / customer | 664 / 185 / 59 / 53 | Current HubSpot lifecycle stage |
| Blank email opt-out field | 961 | A blank opt-out value is not affirmative consent or marketing eligibility |
| Complete tracking | 824 | GrowthOps tracking status |
| Off-taxonomy / missing UTM / blank tracking | 78 / 31 / 2 | 111 contacts need tracking review; these queues may overlap other queues |
| Direct tracking | 26 | Kept separate from tracking defects |
| Blank original UTM source | 89 | Review source evidence; direct traffic may legitimately have no UTM |
| Missing owner, all stages | 4 | Two are in active lead, MQL or opportunity stages |
| Active lead, MQL or opportunity without owner | 2 | Highest-priority routing review |
| Stale lead flag | 102 | Review candidate, not an automatic suppression decision |
| Closed-won flag | 53 | Cross-check against the 53 customer-stage contacts |

The inspected fields do not establish consent or marketing eligibility for any of these 961 contacts. The audit recommends **zero marketing-status changes** and performs none. It also does not delete, suppress, email, or reassign anyone. The output includes a small sample of opaque HubSpot IDs for each review queue, so an operator can inspect underlying records before proposing a change.

The portal's five empty v2.1 custom fields are documented in the separate [schema change record](hubspot-v21-change-plan.md). The local full-scenario marketing audit covers 14,693 synthetic contacts; its totals must not be presented as live portal totals.
