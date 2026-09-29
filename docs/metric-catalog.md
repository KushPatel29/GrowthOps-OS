# Metric catalog v0.2

All timestamps are UTC. A report must show its time range, cohort basis, data-as-of time, and metric version. Currency metrics use a single reporting currency only after an explicit FX conversion table exists. The current local simulator uses USD cents.

| Metric | Definition | Grain / caveat |
|---|---|---|
| Lead | CRM contact with a lead-creation event | Raw CRM count includes migration duplicates; `unique_people` (normalized email) is the deduplicated count |
| MQL | Distinct person with a validated first `mql` lifecycle event | Count at first MQL date; later reversals stay in history |
| Booked call | Distinct person with a `call_booked` lifecycle event | Local slice does not model meeting IDs or cancellations |
| Attended call | Distinct person with a `call_attended` lifecycle event | Local slice does not model meeting attendance evidence |
| Opportunity | Distinct deal entering an opportunity stage | One first entry per deal |
| Closed won | Distinct deal first entering closed-won | Booked outcome, not cash |
| Win rate | Closed-won sales deals / (closed-won + closed-lost sales deals) | Excludes self-serve community add-on deals, which exist only when won |
| Open pipeline | Sum of amount on deals still open at the data cut-off | Not revenue; excluded from bookings and cash |
| Qualified opportunities (v2.1) | Distinct deals with an explicit accepted qualification decision | Synthetic rep-assessment fixture; one decision per deal, never inferred from a booked call |
| Qualified pipeline created (v2.1) | Sum deal amount at first accepted qualification | Deal grain, USD cents, all-time synthetic snapshot; separate from booked revenue and cash |
| Open qualified pipeline (v2.1) | Sum current amount of qualified deals whose current stage is open | As-of snapshot, not a cash or closed-won metric |
| Qualified people (v2.1) | Distinct resolved person keys attached to qualified deals | One person may have more than one qualified deal |
| CRM health component (v2.1) | Passing eligible records / eligible records for the named rule | Actionable owner, source present, unique email candidate, and deal campaign each expose a denominator; the displayed score weights them equally |
| Customer | Distinct person with a successful payment and valid product entitlement | Local workflow uses payment plus access state |
| Spend | Sum paid-media daily spend | Local slice is campaign/day; excludes agency fee |
| Gross collected | Sum successful captured payment amounts | Payment event date, gross of refunds |
| Refunds | Sum successful refund amounts | Refund event date; tied to original payment |
| Net collected | Gross collected minus refunds | Cash basis; excludes tax/fees until modeled explicitly |
| Booked revenue | Sum value of closed-won deals | Deal close date; separate from cash |
| CPL | Paid spend / leads attributed to paid campaigns | Same period and acquisition cohort; null for zero paid leads |
| Cost/MQL | Paid spend / MQLs attributed to paid campaigns | Same period and acquisition cohort; null for zero paid MQLs |
| Lead-to-MQL rate | MQL people / lead people | Cohort-based with maturity window; current local slice uses all-time records |
| MQL-to-call rate | Booked-call people / MQL people | Same MQL cohort and maturity window |
| Show rate | Attended calls / booked calls | Meeting date basis |
| Win rate | Closed-won deals / eligible opportunities | Opportunity cohort; fixed maturity window |
| Net cash ROAS | Net collected cash attributed to paid campaigns / paid media spend | State attribution model; never mix deal value into numerator |
| Renewal risk | Active subscriptions overdue at the as-of date or with a failed attempt in the current cycle (high); due within 14 days (medium) | As-of date is the data cut-off, not the viewer's clock |
| Automation success | Completed unique workflow events / processed unique events | Window by received time; retries do not inflate denominator |
| UTM completeness | Acquisition touches with valid `utm_source` / eligible acquisition touches | Include unresolved source in denominator |
| Registry match | Acquisition touches mapped to a valid campaign / eligible acquisition touches | Invalid naming fails even when source is present |
| Deal/payment reconciliation | Successful non-renewal payments mapped to a valid deal / successful non-renewal payments | Renewals legitimately have no new deal; count and money value reported separately |
| Untracked net cash | Net cash whose lead-creation touch has no campaign (lost UTMs) | Shown as its own line; never spread across campaigns |
| Platform-reported value | Sum of purchase conversion value each ad platform attributes to itself | Each platform's own window (Meta 28-day click / 1-day view, Google 30-day click, LinkedIn 30-day click / 7-day view) at pixel (contract) value; overlaps across platforms; comparison series only |
| Platform ROAS | Platform-reported value / that platform's spend | Self-reported; contrast with warehouse ROAS |
| Warehouse ROAS | Lead-creation net cash for a platform's campaigns / that platform's spend | The governed return measure |
| Workflow success rate | Events completed / unique payment events received | Dead-lettered and retrying events count as not completed |
| Time to access | Seconds from first receipt to workflow completion | p50/p95 and share within 5 minutes; includes retry backoff |
| Duplicate deliveries absorbed | Provider redeliveries of an already-received event | Internal retries are excluded; every duplicate must cause zero extra side effects |
| Content-influenced net cash | Net cash from contacts whose first identified content engagement is the given item | Descriptive first-content influence, not causal incrementality or fractional attribution |
| Experiment lead rate | Exposed visitors with a linked lead / exposed visitors in variant | Visitor assignment unit; one exposure per experiment/visitor |
| Experiment MQL per lead | Exposed linked leads reaching MQL / exposed linked leads | Guardrail for lead quality |
| CPM / CTR / CPC | Spend × 1000 / impressions; clicks / impressions; spend / clicks | Platform-reported delivery; campaign/day grain |
| Cost per booked call (CPDM) | Paid spend / booked discovery calls from paid-created leads in the window | Activity basis (`growthops/performance.py`): leads, MQLs, calls, wins and cash inside the window, credited to the lead-creating campaign. "CPDM" is read here as cost per discovery meeting; confirm the house definition |
| Window ROAS | Lead-creation net cash collected in the window less refunds issued in it / spend in the window | Cash lags leads by weeks, so 7- and 30-day ROAS understate; use the all-time figure to judge a campaign |
| Delivery / bounce rate | Delivered / sends; bounces / sends | Per send; bounce above 2% or complaints above 0.1% flags the sending domain |
| Human open rate | (Opens − machine opens) / delivered | Machine opens come from mailbox privacy proxies (e.g. Apple Mail Privacy Protection) and are excluded |
| Reported open rate | Opens / delivered | Shown only to explain the gap; never used to judge engagement |
| Click rate (CTR) | Clicks / delivered | Unique clicks per send |
| Click-to-open rate | Clicks / human opens | Content relevance among people who actually opened |
| Unsubscribe / complaint rate | Unsubscribes / delivered; spam complaints / delivered | Complaint limit 0.1% (Gmail/Yahoo bulk-sender guidance) |
| Newsletter leads | Contacts whose lead-creation touch is `newsletter_weekly`, credited to the issue in whose window (send to next send) it falls | Followed to MQL, booked call, customers and net cash; descriptive |
| List source mix | New CRM contacts in the last three months by original source | Includes `(no source)` for lost UTMs |
| Short-link defect share | Last-30-day clicks on short links whose UTMs are missing, unregistered or off-taxonomy / all short-link clicks | Registry match is exact and case-sensitive |
| Experiment cash per visitor | Net cash from exposed linked customers / all exposed visitors in variant | Primary experiment metric; refunds included; exploratory synthetic sample |

## Attribution rules

Use one row per net payment allocation and preserve unassigned cash. First touch, lead-creation touch, last non-direct, U-shaped and linear models are separate model versions. U-shaped assigns 40% to first, 40% to lead-creation, and 20% across middle touches; when touch positions collapse to one event, it receives 100%. Refunds inherit the original payment's allocation. Each model must satisfy: **allocated net cash + unassigned net cash = total net collected cash** within rounding tolerance. Platform-reported revenue is a comparison series, never added to warehouse cash.

## Metric governance

Every published measure has one definition here and one implementation in the Python reference; the SQL marts and dbt models re-implement the table-level measures and are verified against that reference in CI (`python -m growthops.verify_dbt`). Change detection (`growthops/diagnostics.py`) uses rolling 7-day windows against the prior 56 days, a minimum practical effect (3 percentage points for rates, 10% for volumes) and an exact shift-share decomposition. The local simulator monitors source freshness and exposes quality issues; automated suppression of untrusted metrics is not yet implemented. The v2.1 qualified pipeline is a synthetic decision fixture and an all-time snapshot. A live integration needs actual rep decisions, cohort policy and FX policy before period or cross-currency comparisons.
