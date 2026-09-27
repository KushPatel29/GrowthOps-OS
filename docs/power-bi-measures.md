# Power BI measures

Generated from `growthops/bi/model_spec.py` by `python -m growthops.bi.build_pbip`; do not edit by hand.
211 business measures in 14 display folders. The report's own SVG tile, header and button measures live in the *Report UI* folder and are not listed.


## 00 Calendar

| Measure | Definition | Format | DAX |
|---|---|---|---|
| As-of date | The last complete day of data. Every 'last 28 days' figure ends here. | `d mmm yyyy` | `CALCULATE(MAX(dim_date[date]), REMOVEFILTERS(dim_date))` |
| Days in view | Days in the current date filter. | `#,0` | `COUNTROWS(dim_date)` |

## 01 Cash

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Net cash | Payments received minus refunds, on the day each happened (event date). | `\$#,0` | `DIVIDE(SUM(mart_growth_daily[net_cash_cents]), 100)` |
| Gross collected | Payments received, on the payment date. | `\$#,0` | `DIVIDE(SUM(mart_growth_daily[gross_collected_cents]), 100)` |
| Refunds | Money returned, on the refund date. | `\$#,0` | `DIVIDE(SUM(mart_growth_daily[refunds_cents]), 100)` |
| CRM booked | Closed-won deal value on the close date, as the CRM records it. | `\$#,0` | `DIVIDE(SUM(mart_growth_daily[booked_cents]), 100)` |
| Ad spend | Paid media spend on Meta, Google and LinkedIn, on the spend date. | `\$#,0` | `DIVIDE(SUM(mart_growth_daily[spend_cents]), 100)` |
| Leads | Contacts created, every channel. | `#,0` | `SUM(mart_growth_daily[leads])` |
| MQLs | Contacts reaching marketing-qualified, on the day they qualified. | `#,0` | `SUM(mart_growth_daily[mqls])` |
| Calls booked | Discovery calls booked, on the booking day. | `#,0` | `SUM(mart_growth_daily[calls_booked])` |
| Deals won | Deals closed-won, on the close day. | `#,0` | `SUM(mart_growth_daily[closed_won_deals])` |
| MQL rate | MQLs / leads in the same window (event basis): a lagging read of lead quality. | `0.0%` | `DIVIDE([MQLs], [Leads])` |
| Spend per deal won | Ad spend / deals won in the same window. Blended: organic deals are in the denominator. | `\$#,0` | `DIVIDE([Ad spend], [Deals won])` |

## 02 Paid media

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid spend | Spend by paid campaign and day. | `\$#,0` | `DIVIDE(SUM(mart_paid_efficiency_daily[spend_cents]), 100)` |
| Impressions | Ad impressions. | `#,0` | `SUM(mart_paid_efficiency_daily[impressions])` |
| Ad clicks | Ad clicks. | `#,0` | `SUM(mart_paid_efficiency_daily[clicks])` |
| Paid leads | Leads whose creating touch was a paid campaign, on the creation day. | `#,0` | `SUM(mart_paid_efficiency_daily[leads])` |
| Paid MQLs | MQLs reached in the window by leads a paid campaign created. | `#,0` | `SUM(mart_paid_efficiency_daily[mqls])` |
| Paid calls booked | Discovery calls booked in the window by paid-created leads. | `#,0` | `SUM(mart_paid_efficiency_daily[calls_booked])` |
| Paid deals won | Deals won in the window by paid-created leads. | `#,0` | `SUM(mart_paid_efficiency_daily[closed_won_deals])` |
| CPM | Cost per thousand impressions. | `\$#,0.00` | `DIVIDE([Paid spend] * 1000, [Impressions])` |
| CTR | Ad clicks / impressions. | `0.00%` | `DIVIDE([Ad clicks], [Impressions])` |
| CPC | Paid spend / ad clicks. | `\$#,0.00` | `DIVIDE([Paid spend], [Ad clicks])` |
| Cost per lead | Paid spend / paid leads in the same window (CPL). | `\$#,0.00` | `DIVIDE([Paid spend], [Paid leads])` |
| Cost per MQL | Paid spend / MQLs from paid-created leads in the same window. | `\$#,0.00` | `DIVIDE([Paid spend], [Paid MQLs])` |
| Cost per booked call | Paid spend / discovery calls booked by paid-created leads (CPDM). | `\$#,0.00` | `DIVIDE([Paid spend], [Paid calls booked])` |
| Paid MQL rate | Paid MQLs / paid leads in the same window: lead quality by campaign. | `0.0%` | `DIVIDE([Paid MQLs], [Paid leads])` |
| Cost per deal won | Paid spend / deals won by paid-created leads. | `\$#,0` | `DIVIDE([Paid spend], [Paid deals won])` |

## 03 Attribution

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Attributed net cash | Net cash credited to the campaign whose touch created the lead, on the payment date. Sums to net cash. | `\$#,0` | `DIVIDE(SUM(fact_cash_attribution[net_cash_cents]), 100)` |
| Attributed payments | Payments credited. | `#,0` | `COUNTROWS(fact_cash_attribution)` |
| Paid attributed net cash | Net cash credited to paid campaigns. | `\$#,0` | `CALCULATE([Attributed net cash], dim_campaign[is_paid] = TRUE())` |
| Cash ROAS | Net cash credited to paid campaigns / paid spend. Descriptive, not incremental: it credits the creating touch. | `0.00"x"` | `DIVIDE([Paid attributed net cash], [Paid spend])` |
| Unattributed net cash | Net cash from buyers with no lead-creation touch before paying. | `\$#,0` | `CALCULATE([Attributed net cash], dim_campaign[campaign_id] = "(unattributed)")` |
| Unattributed share | Share of net cash no campaign can claim. | `0.0%` | `DIVIDE([Unattributed net cash], [Attributed net cash])` |

## 04 Revenue truth

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Booked (CRM, all time) | All closed-won deal value in the CRM. | `\$#,0` | `DIVIDE(SUM(mart_revenue[booked_cents]), 100)` |
| Gross collected (all time) | All payments received. | `\$#,0` | `DIVIDE(SUM(mart_revenue[gross_collected_cents]), 100)` |
| Refunds (all time) | All refunds. | `\$#,0` | `DIVIDE(SUM(mart_revenue[refunds_cents]), 100)` |
| Net collected (all time) | All payments minus all refunds: the number the bank agrees with. | `\$#,0` | `DIVIDE(SUM(mart_revenue[net_collected_cents]), 100)` |
| Refund rate | Refunds / gross collected. | `0.0%` | `DIVIDE([Refunds (all time)], [Gross collected (all time)])` |
| CRM overstatement | How far CRM bookings run ahead of the cash that arrived. | `\$#,0` | `[Booked (CRM, all time)] - [Net collected (all time)]` |
| Bridge movement | Each step between CRM bookings and net cash. The chart adds them, so its total is the gap between the two. | `\$#,0` | `SUMX(FILTER(mart_revenue_bridge, mart_revenue_bridge[kind] = "delta"), mart_revenue_bridge[cents]) / 100` |
| Bridge amount | The bridge value at each step, totals included. | `\$#,0` | `DIVIDE(SUM(mart_revenue_bridge[cents]), 100)` |
| Platform-reported value | Conversion value the ad platforms claim for themselves. | `\$#,0` | `DIVIDE(SUM(mart_platform_comparison[reported_value_cents]), 100)` |
| Warehouse cash (paid platforms) | Net cash the warehouse credits to each platform's campaigns. | `\$#,0` | `DIVIDE(SUM(mart_platform_comparison[warehouse_net_cash_cents]), 100)` |
| Platform spend | Spend on each platform, all time. | `\$#,0` | `DIVIDE(SUM(mart_platform_comparison[spend_cents]), 100)` |
| Platform overstatement | Claimed value the warehouse cannot find in cash. | `\$#,0` | `[Platform-reported value] - [Warehouse cash (paid platforms)]` |
| Reported ROAS | What each platform says its spend returned. | `0.00"x"` | `DIVIDE([Platform-reported value], [Platform spend])` |
| Warehouse ROAS | What the spend returned in collected, net cash. | `0.00"x"` | `DIVIDE([Warehouse cash (paid platforms)], [Platform spend])` |
| Claim multiple | Platform claims / warehouse cash: 1.00x would mean the platforms are right. | `0.00"x"` | `DIVIDE([Platform-reported value], [Warehouse cash (paid platforms)])` |

## 05 Funnel

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Funnel people | People who reached the stage, all time. | `#,0` | `SUM(mart_funnel[people])` |
| Lead to paid | Paying customers / leads, all time. | `0.00%` | `DIVIDE(CALCULATE([Funnel people], mart_funnel[stage] = "paid"), CALCULATE([Funnel people], mart_funnel[stage] = "lead"))` |
| Stage conversion | People at this stage / people at the stage before. | `0.0%` | `VAR vStage = SELECTEDVALUE(mart_funnel[ordinal]) VAR vHere = [Funnel people] VAR vBefore = CALCULATE([Funnel people], REMOVEFILTERS(mart_funnel), mart_funnel[ordinal] = vStage - 1) RETURN IF(NOT ISBLANK(vStage) && vStage > 1, DIVIDE(vHere, vBefore))` |
| Test visitors | Visitors randomized into the CTA test. | `#,0` | `SUM(mart_experiment_variants[visitors])` |
| Test lead rate | Leads / visitors in the CTA test. | `0.00%` | `DIVIDE(SUM(mart_experiment_variants[leads]), [Test visitors])` |
| Test MQLs per lead | MQLs / leads in the CTA test. | `0.0%` | `DIVIDE(SUM(mart_experiment_variants[mqls]), SUM(mart_experiment_variants[leads]))` |
| Test customers | Buyers in the CTA test. Fifteen buyers cannot carry a cash decision. | `#,0` | `SUM(mart_experiment_variants[customers])` |
| Test cash per visitor | Net cash / visitors: the metric the test should be decided on. | `\$#,0.00` | `DIVIDE(SUM(mart_experiment_variants[net_cash_cents]), [Test visitors]) / 100` |
| Variant lead-rate lift | Variant lead rate against control. | `+0.0%;-0.0%;0.0%` | `VAR vControl = CALCULATE([Test lead rate], mart_experiment_variants[variant_id] = "cta_a") VAR vVariant = CALCULATE([Test lead rate], mart_experiment_variants[variant_id] = "cta_b") RETURN DIVIDE(vVariant, vControl) - 1` |
| Variant cash-per-visitor lift | Variant cash per visitor against control. | `+0.0%;-0.0%;0.0%` | `VAR vControl = CALCULATE([Test cash per visitor], mart_experiment_variants[variant_id] = "cta_a") VAR vVariant = CALCULATE([Test cash per visitor], mart_experiment_variants[variant_id] = "cta_b") RETURN DIVIDE(vVariant, vControl) - 1` |

## 06 Content

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Content views | Views, all time. | `#,0` | `SUM(mart_content_performance[views])` |
| Content engaged leads | Leads whose first identified engagement was the item. | `#,0` | `SUM(mart_content_performance[engaged_leads])` |
| Content customers | Buyers whose first identified engagement was the item. | `#,0` | `SUM(mart_content_performance[customers])` |
| Content-influenced cash | Net cash from buyers whose first identified engagement was the item. | `\$#,0` | `DIVIDE(SUM(mart_content_performance[influenced_net_cash_cents]), 100)` |
| Cash per 1K views | Influenced cash per thousand views: views are not buyers. | `\$#,0.00` | `DIVIDE([Content-influenced cash] * 1000, [Content views])` |

## 07 Email

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Emails sent | Messages sent. | `#,0` | `SUM(mart_email_performance[sends])` |
| Emails delivered | Delivered sends. | `#,0` | `SUM(mart_email_performance[delivered])` |
| Human open rate | Opens excluding privacy-proxy machine opens / delivered. | `0.0%` | `DIVIDE(SUM(mart_email_performance[human_opens]), [Emails delivered])` |
| Reported open rate | All opens / delivered, inflated by machine opens; shown only for comparison. | `0.0%` | `DIVIDE(SUM(mart_email_performance[opens]), [Emails delivered])` |
| Machine open share | Share of reported opens made by privacy proxies, not people. | `0.0%` | `DIVIDE(SUM(mart_email_performance[machine_opens]), SUM(mart_email_performance[opens]))` |
| Email click rate | Clicks / delivered. | `0.00%` | `DIVIDE(SUM(mart_email_performance[clicks]), [Emails delivered])` |
| Click-to-open rate | Clicks / human opens. | `0.0%` | `DIVIDE(SUM(mart_email_performance[clicks]), SUM(mart_email_performance[human_opens]))` |
| Bounce rate | Bounces / sends. Above 2% the sending domain is at risk. | `0.00%` | `DIVIDE(SUM(mart_email_performance[bounces]), [Emails sent])` |
| Complaint rate | Spam complaints / delivered. The mailbox providers' limit is 0.1%. | `0.000%` | `DIVIDE(SUM(mart_email_performance[spam_complaints]), [Emails delivered])` |
| Unsubscribe rate | Unsubscribes / delivered. | `0.00%` | `DIVIDE(SUM(mart_email_performance[unsubscribes]), [Emails delivered])` |
| Bounce limit | The 2% bounce threshold, as a reference line. | `0.0%` | `0.02` |

## 08 Data quality

| Measure | Definition | Format | DAX |
|---|---|---|---|
| UTM completeness | Eligible touches that arrived with UTM parameters. | `0.0%` | `DIVIDE(SUM(mart_measurement_health[touches_with_utm]), SUM(mart_measurement_health[eligible_touches]))` |
| Registry coverage | Eligible touches whose campaign is in the registry. | `0.0%` | `DIVIDE(SUM(mart_measurement_health[registered_touches]), SUM(mart_measurement_health[eligible_touches]))` |
| Contacts with an owner | CRM contacts with an owner assigned. | `0.0%` | `DIVIDE(SUM(mart_measurement_health[owned_contacts]), SUM(mart_measurement_health[contacts]))` |
| Valid paid journeys | Paying contacts whose journey has a lead-creation touch before payment. | `0.0%` | `DIVIDE(SUM(mart_measurement_health[valid_paid_journeys]), SUM(mart_measurement_health[paid_contact_count]))` |
| Unmatched payments | Payments the CRM cannot tie to a contact. | `#,0` | `SUM(mart_measurement_health[unmatched_payments])` |
| Duplicate contact rows | Contact rows sharing an email address. | `#,0` | `SUM(mart_measurement_health[duplicate_contact_rows])` |
| Short links | Short links in use. | `#,0` | `COUNTROWS(mart_link_hygiene)` |
| Links with defects | Short links with missing, unregistered or off-taxonomy UTMs. | `#,0` | `COUNTROWS(FILTER(mart_link_hygiene, mart_link_hygiene[missing_utm] \|\| mart_link_hygiene[unregistered_campaign] \|\| mart_link_hygiene[off_taxonomy]))` |
| Defective link click share | Share of the last 30 days' short-link clicks that land without a valid campaign. | `0.0%` | `DIVIDE( CALCULATE(SUM(mart_link_hygiene[recent_clicks]), FILTER(mart_link_hygiene, mart_link_hygiene[missing_utm] \|\| mart_link_hygiene[unregistered_campaign] \|\| mart_link_hygiene[off_taxonomy])), SUM(mart_link_hygiene[recent_clicks]))` |
| Link recent clicks | Short-link clicks in the last 30 days. | `#,0` | `SUM(mart_link_hygiene[recent_clicks])` |
| Legacy contacts | Contacts in the legacy CRM snapshot. | `#,0` | `SUM(mart_migration_summary[legacy_contacts])` |
| Missing after migration | Legacy contacts with no match in the new CRM. | `#,0` | `SUM(mart_migration_summary[missing_contacts])` |
| Owner match | Migrated contacts whose owner survived the move. | `0.0%` | `AVERAGE(mart_migration_summary[owner_match_rate])` |
| Source match | Migrated contacts whose original source survived the move. | `0.0%` | `AVERAGE(mart_migration_summary[source_match_rate])` |
| Stage match | Migrated contacts whose lifecycle stage survived the move. | `0.0%` | `AVERAGE(mart_migration_summary[stage_match_rate])` |
| Renewals due | Community subscriptions due for renewal from the as-of date. | `#,0` | `COUNTROWS(mart_renewal_risk)` |
| Renewals at risk | Renewals due within 14 days (medium), or overdue or with a failed card attempt (high). | `#,0` | `CALCULATE(COUNTROWS(mart_renewal_risk), mart_renewal_risk[risk_level] IN {"high", "medium"})` |
| Failed renewal attempts | Card declines on renewals that are still open. | `#,0` | `SUM(mart_renewal_risk[failed_attempts])` |
| Open incidents | Logged incidents with no end date. | `#,0` | `COUNTROWS(FILTER(incident_register, ISBLANK(incident_register[ended_on])))` |
| Incidents | Logged incidents. | `#,0` | `COUNTROWS(incident_register)` |
| Check rate | The share of records passing a data-quality check. | `0.0%` | `AVERAGE(quality_scorecard[rate])` |
| Check target | The level the check is held to. | `0.0%` | `AVERAGE(quality_scorecard[target])` |
| Checks below target | Data-quality checks under their target. | `#,0` | `COUNTROWS(FILTER(quality_scorecard, quality_scorecard[rate] < quality_scorecard[target]))` |

## 13 Colours

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Check colour | Red where a check is under its target. | `text` | `IF([Check rate] < [Check target], "#ef6f61", "#3987e5")` |
| ROAS colour | Red where a campaign returned less cash than it cost. | `text` | `IF([Cash ROAS] < 1, "#ef6f61", "#3987e5")` |
| MQL rate colour | Red where a campaign qualifies leads at under three-quarters of the paid average. | `text` | `VAR vAll = CALCULATE([Paid MQL rate], REMOVEFILTERS(dim_campaign)) RETURN IF([Paid MQL rate] < 0.75 * vAll, "#ef6f61", "#3987e5")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Net cash prior period | Net cash over the same number of days immediately before the dates in view. | `\$#,0` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Net cash], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Net cash vs prior | Net cash against the period before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Net cash] - [Net cash prior period], ABS([Net cash prior period]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Net cash caption | Tile caption for Net cash. | `text` | `IF(ISBLANK([Net cash vs prior]), "all dates in view · pick months to compare", FORMAT([Net cash vs prior], "+0%;-0%;0%") & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Ad spend prior period | Ad spend over the same number of days immediately before the dates in view. | `\$#,0` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Ad spend], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Ad spend vs prior | Ad spend against the period before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Ad spend] - [Ad spend prior period], ABS([Ad spend prior period]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Ad spend caption | Tile caption for Ad spend. | `text` | `IF(ISBLANK([Ad spend vs prior]), "all dates in view · pick months to compare", FORMAT([Ad spend vs prior], "+0%;-0%;0%") & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Leads prior period | Leads over the same number of days immediately before the dates in view. | `#,0` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Leads], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Leads vs prior | Leads against the period before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Leads] - [Leads prior period], ABS([Leads prior period]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Leads caption | Tile caption for Leads. | `text` | `IF(ISBLANK([Leads vs prior]), "all dates in view · pick months to compare", FORMAT([Leads vs prior], "+0%;-0%;0%") & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| MQL rate prior period | MQL rate over the same number of days immediately before the dates in view. | `0.0%` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([MQL rate], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| MQL rate vs prior | MQL rate against the period before (points). | `+0.00%;-0.00%;0.00%` | `IF(NOT ISBLANK([MQL rate prior period]), [MQL rate] - [MQL rate prior period])` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| MQL rate caption | Tile caption for MQL rate. | `text` | `IF(ISBLANK([MQL rate vs prior]), "all dates in view · pick months to compare", FORMAT([MQL rate vs prior] * 100, "+0.0;-0.0;0.0") & " pts" & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid spend prior period | Paid spend over the same number of days immediately before the dates in view. | `\$#,0` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Paid spend], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Paid spend vs prior | Paid spend against the period before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Paid spend] - [Paid spend prior period], ABS([Paid spend prior period]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid spend caption | Tile caption for Paid spend. | `text` | `IF(ISBLANK([Paid spend vs prior]), "all dates in view · pick months to compare", FORMAT([Paid spend vs prior], "+0%;-0%;0%") & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Cost per lead prior period | Cost per lead over the same number of days immediately before the dates in view. | `\$#,0.00` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Cost per lead], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Cost per lead vs prior | Cost per lead against the period before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Cost per lead] - [Cost per lead prior period], ABS([Cost per lead prior period]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Cost per lead caption | Tile caption for Cost per lead. | `text` | `IF(ISBLANK([Cost per lead vs prior]), "all dates in view · pick months to compare", FORMAT([Cost per lead vs prior], "+0%;-0%;0%") & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Cost per booked call prior period | Cost per booked call over the same number of days immediately before the dates in view. | `\$#,0.00` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Cost per booked call], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Cost per booked call vs prior | Cost per booked call against the period before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Cost per booked call] - [Cost per booked call prior period], ABS([Cost per booked call prior period]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Cost per booked call caption | Tile caption for Cost per booked call. | `text` | `IF(ISBLANK([Cost per booked call vs prior]), "all dates in view · pick months to compare", FORMAT([Cost per booked call vs prior], "+0%;-0%;0%") & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| CTR prior period | CTR over the same number of days immediately before the dates in view. | `0.00%` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([CTR], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| CTR vs prior | CTR against the period before (points). | `+0.00%;-0.00%;0.00%` | `IF(NOT ISBLANK([CTR prior period]), [CTR] - [CTR prior period])` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| CTR caption | Tile caption for CTR. | `text` | `IF(ISBLANK([CTR vs prior]), "all dates in view · pick months to compare", FORMAT([CTR vs prior] * 100, "+0.0;-0.0;0.0") & " pts" & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid MQL rate prior period | Paid MQL rate over the same number of days immediately before the dates in view. | `0.0%` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Paid MQL rate], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Paid MQL rate vs prior | Paid MQL rate against the period before (points). | `+0.00%;-0.00%;0.00%` | `IF(NOT ISBLANK([Paid MQL rate prior period]), [Paid MQL rate] - [Paid MQL rate prior period])` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid MQL rate caption | Tile caption for Paid MQL rate. | `text` | `IF(ISBLANK([Paid MQL rate vs prior]), "all dates in view · pick months to compare", FORMAT([Paid MQL rate vs prior] * 100, "+0.0;-0.0;0.0") & " pts" & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Calls booked prior period | Calls booked over the same number of days immediately before the dates in view. | `#,0` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Calls booked], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Calls booked vs prior | Calls booked against the period before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Calls booked] - [Calls booked prior period], ABS([Calls booked prior period]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Calls booked caption | Tile caption for Calls booked. | `text` | `IF(ISBLANK([Calls booked vs prior]), "all dates in view · pick months to compare", FORMAT([Calls booked vs prior], "+0%;-0%;0%") & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Deals won prior period | Deals won over the same number of days immediately before the dates in view. | `#,0` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Deals won], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Deals won vs prior | Deals won against the period before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Deals won] - [Deals won prior period], ABS([Deals won prior period]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Deals won caption | Tile caption for Deals won. | `text` | `IF(ISBLANK([Deals won vs prior]), "all dates in view · pick months to compare", FORMAT([Deals won vs prior], "+0%;-0%;0%") & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Attributed net cash prior period | Attributed net cash over the same number of days immediately before the dates in view. | `\$#,0` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Attributed net cash], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Attributed net cash vs prior | Attributed net cash against the period before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Attributed net cash] - [Attributed net cash prior period], ABS([Attributed net cash prior period]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Attributed net cash caption | Tile caption for Attributed net cash. | `text` | `IF(ISBLANK([Attributed net cash vs prior]), "all dates in view · pick months to compare", FORMAT([Attributed net cash vs prior], "+0%;-0%;0%") & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Cash ROAS prior period | Cash ROAS over the same number of days immediately before the dates in view. | `0.00"x"` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Cash ROAS], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Cash ROAS vs prior | Cash ROAS against the period before. | `+0.00"x";-0.00"x";0.00"x"` | `IF(NOT ISBLANK([Cash ROAS prior period]), [Cash ROAS] - [Cash ROAS prior period])` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Cash ROAS caption | Tile caption for Cash ROAS. | `text` | `IF(ISBLANK([Cash ROAS vs prior]), "all dates in view · pick months to compare", FORMAT([Cash ROAS vs prior], "+0.00;-0.00;0.00") & "x" & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Emails delivered prior period | Emails delivered over the same number of days immediately before the dates in view. | `#,0` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Emails delivered], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Emails delivered vs prior | Emails delivered against the period before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Emails delivered] - [Emails delivered prior period], ABS([Emails delivered prior period]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Emails delivered caption | Tile caption for Emails delivered. | `text` | `IF(ISBLANK([Emails delivered vs prior]), "all dates in view · pick months to compare", FORMAT([Emails delivered vs prior], "+0%;-0%;0%") & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Human open rate prior period | Human open rate over the same number of days immediately before the dates in view. | `0.0%` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Human open rate], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Human open rate vs prior | Human open rate against the period before (points). | `+0.00%;-0.00%;0.00%` | `IF(NOT ISBLANK([Human open rate prior period]), [Human open rate] - [Human open rate prior period])` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Human open rate caption | Tile caption for Human open rate. | `text` | `IF(ISBLANK([Human open rate vs prior]), "all dates in view · pick months to compare", FORMAT([Human open rate vs prior] * 100, "+0.0;-0.0;0.0") & " pts" & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Bounce rate prior period | Bounce rate over the same number of days immediately before the dates in view. | `0.00%` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Bounce rate], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Bounce rate vs prior | Bounce rate against the period before (points). | `+0.00%;-0.00%;0.00%` | `IF(NOT ISBLANK([Bounce rate prior period]), [Bounce rate] - [Bounce rate prior period])` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Bounce rate caption | Tile caption for Bounce rate. | `text` | `IF(ISBLANK([Bounce rate vs prior]), "all dates in view · pick months to compare", FORMAT([Bounce rate vs prior] * 100, "+0.0;-0.0;0.0") & " pts" & " vs prior period")` |

## 09 Windows

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Complaint rate prior period | Complaint rate over the same number of days immediately before the dates in view. | `0.000%` | `VAR vStart = MIN(dim_date[date]) VAR vEnd = MAX(dim_date[date]) VAR vDays = INT(vEnd - vStart) + 1 RETURN CALCULATE([Complaint rate], REMOVEFILTERS(dim_date), dim_date[date] >= vStart - vDays, dim_date[date] < vStart)` |
| Complaint rate vs prior | Complaint rate against the period before (points). | `+0.00%;-0.00%;0.00%` | `IF(NOT ISBLANK([Complaint rate prior period]), [Complaint rate] - [Complaint rate prior period])` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Complaint rate caption | Tile caption for Complaint rate. | `text` | `IF(ISBLANK([Complaint rate vs prior]), "all dates in view · pick months to compare", FORMAT([Complaint rate vs prior] * 100, "+0.0;-0.0;0.0") & " pts" & " vs prior period")` |

## 11 Last 28 days

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Net cash, last 28 days | Net cash in the 28 days ending on the as-of date. | `\$#,0` | `CALCULATE([Net cash], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())` |
| Net cash, prior 28 days | Net cash in the 28 days before those. | `\$#,0` | `CALCULATE([Net cash], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())` |
| Net cash, 28-day change | Net cash, last 28 days against the 28 days before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Net cash, last 28 days] - [Net cash, prior 28 days], ABS([Net cash, prior 28 days]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Net cash, 28-day caption | Tile caption for Net cash, last 28 days. | `text` | `IF(ISBLANK([Net cash, 28-day change]), "all dates in view · pick months to compare", FORMAT([Net cash, 28-day change], "+0%;-0%;0%") & " vs prior 28 days")` |

## 11 Last 28 days

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Ad spend, last 28 days | Ad spend in the 28 days ending on the as-of date. | `\$#,0` | `CALCULATE([Ad spend], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())` |
| Ad spend, prior 28 days | Ad spend in the 28 days before those. | `\$#,0` | `CALCULATE([Ad spend], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())` |
| Ad spend, 28-day change | Ad spend, last 28 days against the 28 days before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Ad spend, last 28 days] - [Ad spend, prior 28 days], ABS([Ad spend, prior 28 days]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Ad spend, 28-day caption | Tile caption for Ad spend, last 28 days. | `text` | `IF(ISBLANK([Ad spend, 28-day change]), "all dates in view · pick months to compare", FORMAT([Ad spend, 28-day change], "+0%;-0%;0%") & " vs prior 28 days")` |

## 11 Last 28 days

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Leads, last 28 days | Leads in the 28 days ending on the as-of date. | `#,0` | `CALCULATE([Leads], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())` |
| Leads, prior 28 days | Leads in the 28 days before those. | `#,0` | `CALCULATE([Leads], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())` |
| Leads, 28-day change | Leads, last 28 days against the 28 days before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Leads, last 28 days] - [Leads, prior 28 days], ABS([Leads, prior 28 days]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Leads, 28-day caption | Tile caption for Leads, last 28 days. | `text` | `IF(ISBLANK([Leads, 28-day change]), "all dates in view · pick months to compare", FORMAT([Leads, 28-day change], "+0%;-0%;0%") & " vs prior 28 days")` |

## 11 Last 28 days

| Measure | Definition | Format | DAX |
|---|---|---|---|
| MQL rate, last 28 days | MQL rate in the 28 days ending on the as-of date. | `0.0%` | `CALCULATE([MQL rate], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())` |
| MQL rate, prior 28 days | MQL rate in the 28 days before those. | `0.0%` | `CALCULATE([MQL rate], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())` |
| MQL rate, 28-day change | MQL rate, last 28 days against the 28 days before. | `+0.00%;-0.00%;0.00%` | `IF(NOT ISBLANK([MQL rate, prior 28 days]), [MQL rate, last 28 days] - [MQL rate, prior 28 days])` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| MQL rate, 28-day caption | Tile caption for MQL rate, last 28 days. | `text` | `IF(ISBLANK([MQL rate, 28-day change]), "all dates in view · pick months to compare", FORMAT([MQL rate, 28-day change] * 100, "+0.0;-0.0;0.0") & " pts" & " vs prior 28 days")` |

## 11 Last 28 days

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid spend, last 28 days | Paid spend in the 28 days ending on the as-of date. | `\$#,0` | `CALCULATE([Paid spend], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())` |
| Paid spend, prior 28 days | Paid spend in the 28 days before those. | `\$#,0` | `CALCULATE([Paid spend], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())` |
| Paid spend, 28-day change | Paid spend, last 28 days against the 28 days before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Paid spend, last 28 days] - [Paid spend, prior 28 days], ABS([Paid spend, prior 28 days]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid spend, 28-day caption | Tile caption for Paid spend, last 28 days. | `text` | `IF(ISBLANK([Paid spend, 28-day change]), "all dates in view · pick months to compare", FORMAT([Paid spend, 28-day change], "+0%;-0%;0%") & " vs prior 28 days")` |

## 11 Last 28 days

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid leads, last 28 days | Paid leads in the 28 days ending on the as-of date. | `#,0` | `CALCULATE([Paid leads], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())` |
| Paid leads, prior 28 days | Paid leads in the 28 days before those. | `#,0` | `CALCULATE([Paid leads], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())` |
| Paid leads, 28-day change | Paid leads, last 28 days against the 28 days before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Paid leads, last 28 days] - [Paid leads, prior 28 days], ABS([Paid leads, prior 28 days]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid leads, 28-day caption | Tile caption for Paid leads, last 28 days. | `text` | `IF(ISBLANK([Paid leads, 28-day change]), "all dates in view · pick months to compare", FORMAT([Paid leads, 28-day change], "+0%;-0%;0%") & " vs prior 28 days")` |

## 11 Last 28 days

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid MQLs, last 28 days | Paid MQLs in the 28 days ending on the as-of date. | `#,0` | `CALCULATE([Paid MQLs], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())` |
| Paid MQLs, prior 28 days | Paid MQLs in the 28 days before those. | `#,0` | `CALCULATE([Paid MQLs], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())` |
| Paid MQLs, 28-day change | Paid MQLs, last 28 days against the 28 days before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Paid MQLs, last 28 days] - [Paid MQLs, prior 28 days], ABS([Paid MQLs, prior 28 days]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid MQLs, 28-day caption | Tile caption for Paid MQLs, last 28 days. | `text` | `IF(ISBLANK([Paid MQLs, 28-day change]), "all dates in view · pick months to compare", FORMAT([Paid MQLs, 28-day change], "+0%;-0%;0%") & " vs prior 28 days")` |

## 11 Last 28 days

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Cost per lead, last 28 days | Cost per lead in the 28 days ending on the as-of date. | `\$#,0.00` | `CALCULATE([Cost per lead], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())` |
| Cost per lead, prior 28 days | Cost per lead in the 28 days before those. | `\$#,0.00` | `CALCULATE([Cost per lead], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())` |
| Cost per lead, 28-day change | Cost per lead, last 28 days against the 28 days before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Cost per lead, last 28 days] - [Cost per lead, prior 28 days], ABS([Cost per lead, prior 28 days]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Cost per lead, 28-day caption | Tile caption for Cost per lead, last 28 days. | `text` | `IF(ISBLANK([Cost per lead, 28-day change]), "all dates in view · pick months to compare", FORMAT([Cost per lead, 28-day change], "+0%;-0%;0%") & " vs prior 28 days")` |

## 11 Last 28 days

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid MQL rate, last 28 days | Paid MQL rate in the 28 days ending on the as-of date. | `0.0%` | `CALCULATE([Paid MQL rate], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())` |
| Paid MQL rate, prior 28 days | Paid MQL rate in the 28 days before those. | `0.0%` | `CALCULATE([Paid MQL rate], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())` |
| Paid MQL rate, 28-day change | Paid MQL rate, last 28 days against the 28 days before. | `+0.00%;-0.00%;0.00%` | `IF(NOT ISBLANK([Paid MQL rate, prior 28 days]), [Paid MQL rate, last 28 days] - [Paid MQL rate, prior 28 days])` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Paid MQL rate, 28-day caption | Tile caption for Paid MQL rate, last 28 days. | `text` | `IF(ISBLANK([Paid MQL rate, 28-day change]), "all dates in view · pick months to compare", FORMAT([Paid MQL rate, 28-day change] * 100, "+0.0;-0.0;0.0") & " pts" & " vs prior 28 days")` |

## 11 Last 28 days

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Bounce rate, last 28 days | Bounce rate in the 28 days ending on the as-of date. | `0.00%` | `CALCULATE([Bounce rate], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())` |
| Bounce rate, prior 28 days | Bounce rate in the 28 days before those. | `0.00%` | `CALCULATE([Bounce rate], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())` |
| Bounce rate, 28-day change | Bounce rate, last 28 days against the 28 days before. | `+0.00%;-0.00%;0.00%` | `IF(NOT ISBLANK([Bounce rate, prior 28 days]), [Bounce rate, last 28 days] - [Bounce rate, prior 28 days])` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Bounce rate, 28-day caption | Tile caption for Bounce rate, last 28 days. | `text` | `IF(ISBLANK([Bounce rate, 28-day change]), "all dates in view · pick months to compare", FORMAT([Bounce rate, 28-day change] * 100, "+0.0;-0.0;0.0") & " pts" & " vs prior 28 days")` |

## 11 Last 28 days

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Calls booked, last 28 days | Calls booked in the 28 days ending on the as-of date. | `#,0` | `CALCULATE([Calls booked], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())` |
| Calls booked, prior 28 days | Calls booked in the 28 days before those. | `#,0` | `CALCULATE([Calls booked], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())` |
| Calls booked, 28-day change | Calls booked, last 28 days against the 28 days before. | `+0.0%;-0.0%;0.0%` | `DIVIDE([Calls booked, last 28 days] - [Calls booked, prior 28 days], ABS([Calls booked, prior 28 days]))` |

## 10 Captions

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Calls booked, 28-day caption | Tile caption for Calls booked, last 28 days. | `text` | `IF(ISBLANK([Calls booked, 28-day change]), "all dates in view · pick months to compare", FORMAT([Calls booked, 28-day change], "+0%;-0%;0%") & " vs prior 28 days")` |
| Platform claim caption | Tile caption: how far platform claims exceed cash. | `text` | `FORMAT([Claim multiple], "0.00") & "x the cash the warehouse can find"` |
| CRM booked caption | Tile caption: how far bookings run ahead of cash. | `text` | `FORMAT(DIVIDE([Booked (CRM, all time)], [Net collected (all time)]) - 1, "+0.0%") & " over net collected cash"` |
| Net collected caption | Tile caption: refunds taken out of gross. | `text` | `"after " & FORMAT([Refunds (all time)] / 1000, "$#,0") & "K of refunds"` |
| Refund caption | Tile caption: refund rate. | `text` | `FORMAT([Refund rate], "0.0%") & " of gross collected"` |
| Unattributed caption | Tile caption: cash no campaign can claim. | `text` | `FORMAT([Unattributed net cash] / 1000, "$#,0") & "K with no creating touch"` |
| Content caption | Tile caption: views against buyers. | `text` | `FORMAT([Content customers], "#,0") & " buyers from " & FORMAT([Content views] / 1000, "#,0") & "K views"` |
| Links caption | Tile caption: clicks on defective links. | `text` | `FORMAT([Defective link click share], "0%") & " of recent short-link clicks"` |
| Renewals caption | Tile caption: renewal queue. | `text` | `FORMAT([Failed renewal attempts], "#,0") & IF([Failed renewal attempts] = 1, " failed card attempt · ", " failed card attempts · ") & FORMAT([Renewals due], "#,0") & " due in total"` |
| UTM caption | Tile caption: registry coverage. | `text` | `FORMAT([Registry coverage], "0.0%") & " of touches carry a registered campaign"` |
| Migration caption | Tile caption: migration defects. | `text` | `FORMAT([Missing after migration], "#,0") & " legacy contacts missing · " & FORMAT([Duplicate contact rows], "#,0") & " duplicates"` |
| Funnel caption | Tile caption: lead to paid. | `text` | `FORMAT([Lead to paid], "0.00%") & " of all leads became customers"` |
| ROAS caption | Tile caption: the ROAS the platforms claim. | `text` | `"platforms report " & FORMAT([Reported ROAS], "0.0") & "x on the same spend"` |
| Quality caption | Tile caption: data-quality checks failing. | `text` | `FORMAT([Checks below target], "0") & " of " & FORMAT(COUNTROWS(quality_scorecard), "0") & " checks under target"` |
| Complaint caption | Tile caption: complaint rate against the mailbox-provider limit. | `text` | `IF([Complaint rate] > 0.001, "above", "within") & " the 0.1% limit · " & [Complaint rate caption]` |

## 12 Narrative

| Measure | Definition | Format | DAX |
|---|---|---|---|
| Executive summary | One-paragraph summary of the last 28 days, computed from the measures on the page. | `text` | `VAR vDate = FORMAT([As-of date], "d mmm yyyy") VAR vCash = [Net cash, last 28 days] VAR vCashMove = [Net cash, 28-day change] VAR vRateNow = [Paid MQL rate, last 28 days] VAR vRateBefore = [Paid MQL rate, prior 28 days] VAR vRanked = FILTER( ADDCOLUMNS(VALUES(dim_campaign[campaign_id]), "@leads", [Paid leads, last 28 days], "@rate", [Paid MQL rate, last 28 days]), [@leads] >= 50 && NOT ISBLANK([@rate])) VAR vWorst = MINX(vRanked, [@rate]) VAR vWorstNames = CONCATENATEX(FILTER(vRanked, [@rate] = vWorst), dim_campaign[campaign_id], ", ") VAR vBounce = [Bounce rate, last 28 days] RETURN "28 days to " & vDate & ": " & FORMAT(vCash / 1000, "$#,0") & "K net cash (" & FORMAT(vCashMove, "+0%;-0%") & " on the 28 days before) on " & FORMAT([Ad spend, last 28 days] / 1000, "$#,0") & "K of ad spend. Paid media bought " & FORMAT([Paid leads, last 28 days], "#,0") & " leads at " & FORMAT([Cost per lead, last 28 days], "$#,0.00") & " each, but " & FORMAT(vRateNow, "0.0%") & " qualified (" & FORMAT(vRateBefore, "0.0%") & " before); the weakest at scale is " & vWorstNames & " at " & FORMAT(vWorst, "0.0%") & ". Email bounced at " & FORMAT(vBounce, "0.0%") & IF(vBounce > 0.02, ", above the 2% limit.", ", inside the 2% limit.")` |
