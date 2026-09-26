# Metric catalog v0.1

All timestamps are UTC. A report must show its time range, cohort basis, data-as-of time, and metric version. Currency metrics use a single reporting currency only after an explicit FX conversion table exists. The current local simulator uses USD cents.

| Metric | Definition | Grain / caveat |
|---|---|---|
| Lead | Distinct canonical person with a first valid form submission or CRM creation event | One per person; local slice counts contact rows because identity resolution is pending |
| MQL | Distinct person with a validated first `mql` lifecycle event | Count at first MQL date; later reversals stay in history |
| Booked call | Distinct scheduled meeting linked to a person | Exclude canceled meetings unless separately reported |
| Attended call | Distinct booked call with attendance evidence | One attendance per meeting |
| Opportunity | Distinct deal entering an opportunity stage | One first entry per deal |
| Closed won | Distinct deal first entering closed-won | Booked outcome, not cash |
| Customer | Distinct person with a successful payment and valid product entitlement | Local workflow uses payment plus access state |
| Spend | Sum platform cost after source deduplication | Platform/day/creative; excludes agency fee unless configured |
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
| Renewal rate | Successful due renewals / all due renewals | Exclude future or pending renewals |
| Automation success | Completed unique workflow events / processed unique events | Window by received time; retries do not inflate denominator |
| UTM completeness | Acquisition touches with valid `utm_source` / eligible acquisition touches | Include unresolved source in denominator |
| Registry match | Acquisition touches mapped to a valid campaign / eligible acquisition touches | Invalid naming fails even when source is present |
| Deal/payment reconciliation | Successful payments mapped to a valid deal / successful payments | Report count and money value separately |

## Attribution rules

Use one row per net payment allocation and preserve unassigned cash. First touch, lead-creation touch, last non-direct, and U-shaped models are separate model versions. U-shaped assigns 40% to first, 40% to lead-creation, and 20% across middle touches; when touch positions collapse to one event, it receives 100%. Refunds inherit the original payment's allocation. Each model must satisfy: **allocated net cash + unassigned net cash = total net collected cash** within rounding tolerance. Platform-reported revenue is a comparison series, never added to warehouse cash.

## Metric governance

Each published measure has an owner, SQL expression, version, source freshness SLA, and failed-quality behavior. The executive brief suppresses or marks a metric when its source is stale or a critical reconciliation test fails. `growthops.report` currently implements the local subset: leads, MQLs, closed won, spend, gross/refund/net cash, lead-to-MQL, CPL, net cash ROAS, UTM completeness, registry match, owner completeness, duplicate rows, and payment/deal match.
