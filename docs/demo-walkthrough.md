# GrowthOps OS reviewer walkthrough

This is a recording-ready script for the **synthetic** dashboard. It is not a claim of a recorded video or
live provider traffic. The public app is at <https://growthops-os.streamlit.app/>.

## Story 1 — marketing measurement (about three minutes)

1. Open the Decision Center in **Operations console**. Read the four separate values: platform-reported
   revenue, qualified pipeline, CRM bookings and net collected cash. Open the bridge to see why the totals
   differ and confirm the reconciliation residual is zero.
2. Show **Acquisition** and **Funnel**. Inspect the broad Meta campaign's lead quality and the lead-to-cash
   progression. Explain that qualification decisions in this demo are explicit synthetic fixtures.
3. In **Data quality**, inspect the planted UTM defect and campaign QA. Use **Customer 360** to trace one
   pseudonymous person from touch through CRM stage and payment.
4. In **Growth lab**, switch cohort grouping from month to campaign, inspect paid CAC and the unsupported
   lifetime metrics, then change one scenario assumption. Say that the calculator is arithmetic, not a forecast.
5. Close with **Ask your data** and a governed question; show the time window and citation rather than reading
   an ungrounded summary.

## Story 2 — automation and AI (about three minutes)

1. Open **Automation & renewals**. Select a failed payment-to-access trace and show the step, retry, error
   and dead-letter state. In **Operations console**, use the incident queue to reach the same trace.
2. Explain the audited replay path and its local operator token. The public dashboard stays read-only.
3. Show the renewal-risk list and the suggested action proposals; no CRM task or message is dispatched.
4. In **Growth lab**, show synthetic consent counts and the explicit zero connected ad-conversion providers.
   The conversion preview requires settled cash, registered campaign and ads consent at purchase and now.
5. Open **Sales copilot** for a pseudonymous prospect and inspect a cited closed-won case. The classifier is
   deterministic and evaluated on synthetic phrases; it does not claim human-level sales judgment.

Use the [final target audit](final-target-audit.md) when answering which integrations are demonstrated locally
and which need provider credentials and read-back evidence.
