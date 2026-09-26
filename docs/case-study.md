# ScaleLab acquisition-to-cash case study

## Scenario

ScaleLab is fictional. Its post-migration CRM, ad, content, sales, and payment records disagree in useful ways. The project models the journey, defines the cash source of truth, and exposes gaps before recommending changes. Every figure below comes from deterministic synthetic data. This is an interview demonstration, not a claim of real business impact.

## Measurement decision

The executive cash line is **$19,500 gross collected − $600 refunds = $18,900 net collected**. Closed-won deal value is **$19,500 booked**. These are separate measures even when their totals match. Lead-creation, first-touch, last non-direct, and U-shaped allocations each sum to $18,900 net cash; unassigned cash would be visible if a payment lacked an eligible touch.

The 240 recorded leads produced 100 MQLs and 30 paid customers. Paid spend is $13,400. Lead-creation attribution assigns $10,500 net cash to paid campaigns, giving **0.78× net cash ROAS**; this is not platform-reported revenue. Daily cash is grouped by payment/refund event date, so the recent-week cash trend cannot be treated as a cohort return on that week's spend.

## Diagnosis

The synthetic audit finds **93.4% UTM completeness** against a 95% target, **80.0% campaign registry mapping** against 98%, **93.8% owner coverage** against 99%, and **96.7% payment-to-deal matching** against 99%. Ten duplicate CRM rows and three legacy contacts missing after migration are visible. The repair command fills only missing owner/source values from unambiguous legacy matches and logs each update. Duplicates and missing contacts remain for review; the simulator does not auto-merge identities.

## Experiment decision

CTA B produces 140 leads from 500 visitors versus A's 100 from 500. Its lead rate is 28% versus 20%; the difference is 8 percentage points (95% normal interval 2.73 to 13.27 points, two-sided p = 0.0031). Qualification weakens: B has 30 MQLs from 140 leads versus A's 70 from 100. A has $25.20 net cash per visitor; B has $12.60. The bootstrap interval for B minus A cash per visitor spans approximately **−$25.60 to +$0.90**, so the observed cash shortfall is uncertain. The decision is to retain A while collecting a larger revenue sample rather than ship B on lead lift alone. Visitor assignments are deliberately balanced synthetic records, not evidence from a live randomized experiment.

## Engineering evidence and limits

The API's signed payment event persists an idempotency claim and workflow step status before applying simulated CRM and access changes. A partial-failure test retries without creating a second payment or entitlement. The dbt marts are rebuilt from generated CSV seeds in DuckDB and compared to the SQLite reference. CI runs Python checks, dbt data tests, and parity verification. The HTML dashboard renders the analyst story; generated mart CSVs are ready for Power BI import, but no native Power BI report is present.

Production use would require real source contracts, authentication, credential rotation, privacy review, provider sandbox tests, scheduled ingestion, freshness monitoring, renewal state, and persistent deployment. None is inferred from the local simulation.
