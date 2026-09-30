# HubSpot field contract

Generated from `growthops/hubspot_contract.py` by `python -m growthops.hubspot_contract`; do not edit by hand. A test regenerates it and fails on any difference.

HubSpot is the system of record for the CRM record (identity, owner, every deal field); GrowthOps is the system of record for the analytics it computes. `lifecyclestage` is shared and GrowthOps only moves it forward. **Push** means GrowthOps may write the field on an existing record; **create** means it sets the field when it creates the record; **PII** fields land in the warehouse as a keyed hash.

31 of 49 fields are pushable; 13 belong to HubSpot.

| Object | Field | Owner | Push | Create | PII | Why |
|---|---|---|:-:|:-:|:-:|---|
| contacts | `lifecyclestage` | shared | yes | yes |  | Forward only: a cleared payment moves a contact to Customer; nothing moves it back |
| contacts | `email` | hubspot |  | yes | yes | Identity; HubSpot dedupes contacts on it |
| contacts | `firstname` | hubspot |  | yes | yes | Set on create; reps correct it after |
| contacts | `hs_merged_object_ids` | hubspot |  |  |  | Records merged into this one |
| contacts | `hubspot_owner_id` | hubspot |  | yes |  | Routed on create; lead rotation and reps reassign it |
| contacts | `lastmodifieddate` | hubspot |  |  |  | Sync watermark |
| contacts | `lastname` | hubspot |  | yes | yes | Set on create; reps correct it after |
| contacts | `growthops_call_attended_date` | growthops | yes | yes |  | Discovery call attended; recomputed from the warehouse |
| contacts | `growthops_call_booked_date` | growthops | yes | yes |  | Discovery call booked; recomputed from the warehouse |
| contacts | `growthops_contact_id` | growthops |  | yes |  | Unique GrowthOps key; immutable, the upsert and reconciliation key |
| contacts | `growthops_first_content_id` | growthops | yes | yes |  | First content engaged; recomputed from the warehouse |
| contacts | `growthops_first_touch_campaign` | growthops | yes | yes |  | First-touch campaign; recomputed from the warehouse |
| contacts | `growthops_first_touch_medium` | growthops | yes | yes |  | First-touch channel; recomputed from the warehouse |
| contacts | `growthops_has_closed_won` | growthops | yes | yes |  | Has a closed-won deal; recomputed from the warehouse |
| contacts | `growthops_last_activity_date` | growthops | yes | yes |  | Last marketing activity; recomputed from the warehouse |
| contacts | `growthops_last_non_direct_campaign` | growthops | yes | yes |  | Last non-direct campaign; recomputed from the warehouse |
| contacts | `growthops_latest_content_id` | growthops | yes | yes |  | Latest content engaged; recomputed from the warehouse |
| contacts | `growthops_latest_utm_campaign` | growthops | yes | yes |  | Latest UTM campaign; recomputed from the warehouse |
| contacts | `growthops_latest_utm_source` | growthops | yes | yes |  | Latest UTM source; recomputed from the warehouse |
| contacts | `growthops_lead_creation_campaign` | growthops | yes | yes |  | Lead-creation campaign; recomputed from the warehouse |
| contacts | `growthops_legacy_id` | growthops | yes | yes |  | Legacy CRM ID; recomputed from the warehouse |
| contacts | `growthops_merged_contact_ids` | growthops | yes | yes |  | Merged duplicate contact IDs; recomputed from the warehouse |
| contacts | `growthops_mql_date` | growthops | yes | yes |  | Became MQL; recomputed from the warehouse |
| contacts | `growthops_net_cash` | growthops | yes | yes |  | Net cash collected; recomputed from the warehouse |
| contacts | `growthops_original_source` | growthops | yes | yes |  | Original source (registry); recomputed from the warehouse |
| contacts | `growthops_original_utm_campaign` | growthops | yes | yes |  | Original UTM campaign; recomputed from the warehouse |
| contacts | `growthops_original_utm_medium` | growthops | yes | yes |  | Original UTM medium; recomputed from the warehouse |
| contacts | `growthops_original_utm_source` | growthops | yes | yes |  | Original UTM source; recomputed from the warehouse |
| contacts | `growthops_owner` | growthops |  | yes |  | The rep GrowthOps routed on create; lead rotation reassigns it in HubSpot |
| contacts | `growthops_renewal_due_date` | growthops | yes | yes |  | Community renewal due; recomputed from the warehouse |
| contacts | `growthops_renewal_risk` | growthops | yes | yes |  | Renewal risk; recomputed from the warehouse |
| contacts | `growthops_stale_lead` | growthops |  | yes |  | Set by the stale-lead cleanup rule, not recomputed per record |
| contacts | `growthops_tracking_status` | growthops | yes | yes |  | Tracking status; recomputed from the warehouse |
| deals | `amount` | hubspot |  | yes |  | Reps and quotes change it after create |
| deals | `closedate` | hubspot |  | yes |  | Set by the rep who closes the deal |
| deals | `dealname` | hubspot |  | yes |  | Set on create |
| deals | `dealstage` | hubspot |  | yes |  | Reps move deals through the pipeline |
| deals | `hs_lastmodifieddate` | hubspot |  |  |  | Sync watermark |
| deals | `hubspot_owner_id` | hubspot |  | yes |  | The contact's owner on create |
| deals | `pipeline` | hubspot |  | yes |  | Set on create |
| deals | `growthops_contact_id` | growthops |  | yes |  | Unique GrowthOps key; immutable, the upsert and reconciliation key |
| deals | `growthops_deal_id` | growthops |  | yes |  | Unique GrowthOps key; immutable, the upsert and reconciliation key |
| deals | `growthops_first_content_id` | growthops | yes | yes |  | First content engaged; recomputed from the warehouse |
| deals | `growthops_first_touch_campaign` | growthops | yes | yes |  | First-touch campaign; recomputed from the warehouse |
| deals | `growthops_first_touch_medium` | growthops | yes | yes |  | First-touch channel; recomputed from the warehouse |
| deals | `growthops_last_non_direct_campaign` | growthops | yes | yes |  | Last non-direct campaign; recomputed from the warehouse |
| deals | `growthops_lead_creation_campaign` | growthops | yes | yes |  | Lead-creation campaign; recomputed from the warehouse |
| deals | `growthops_net_cash` | growthops | yes | yes |  | Net cash collected; recomputed from the warehouse |
| deals | `growthops_product` | growthops | yes | yes |  | Product; recomputed from the warehouse |
