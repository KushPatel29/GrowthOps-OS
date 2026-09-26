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
| Experiment cash per visitor | Net cash from exposed linked customers / all exposed visitors in variant | Primary experiment metric; refunds included; exploratory synthetic sample |

## Attribution rules

Use one row per net payment allocation and preserve unassigned cash. First touch, lead-creation touch, last non-direct, U-shaped and linear models are separate model versions. U-shaped assigns 40% to first, 40% to lead-creation, and 20% across middle touches; when touch positions collapse to one event, it receives 100%. Refunds inherit the original payment's allocation. Each model must satisfy: **allocated net cash + unassigned net cash = total net collected cash** within rounding tolerance. Platform-reported revenue is a comparison series, never added to warehouse cash.

## Metric governance

Every published measure has one definition here and one implementation in `growthops/report.py`, `growthops/reconciliation.py` or `growthops/workflow.py`; the SQL marts and dbt models re-implement the table-level measures and are verified against the Python reference in CI (`python -m growthops.verify_dbt`). Change detection (`growthops/diagnostics.py`) uses rolling 7-day windows against the prior 56 days, a minimum practical effect (3 percentage points for rates, 10% for volumes) and an exact shift-share decomposition. The local simulator has no source-freshness monitoring or quality-based suppression yet.
