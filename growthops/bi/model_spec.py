"""What the Power BI semantic model contains: tables, relationships and measures.

Kept apart from the writer in :mod:`growthops.bi.build_pbip` so the *shape* of the
model can be read and asserted on without wading through TMDL.

Nothing here repeats an analysis. Every table is a governed CSV that
``growthops.export_bi`` writes from the verified dbt marts, and every measure sums
one of their columns or divides two such sums. The revenue bridge, the platform
comparison and the attribution are computed once, in SQL, and tested there; a DAX
re-derivation would be a third implementation of a definition that already has two.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Tables. kind: dimension | fact | snapshot | reference
# --------------------------------------------------------------------------

TABLES: dict[str, dict] = {
    "dim_date": {"kind": "dimension", "date_table": "date"},
    "dim_campaign": {"kind": "dimension"},
    "mart_growth_daily": {"kind": "fact"},
    "mart_paid_efficiency_daily": {"kind": "fact"},
    "fact_cash_attribution": {"kind": "fact"},
    "mart_email_performance": {"kind": "fact"},
    "mart_campaign_performance": {"kind": "fact"},
    "mart_revenue": {"kind": "snapshot"},
    "mart_revenue_bridge": {"kind": "snapshot"},
    "mart_platform_comparison": {"kind": "snapshot"},
    "mart_funnel": {"kind": "snapshot"},
    "mart_experiment_variants": {"kind": "snapshot"},
    "mart_content_performance": {"kind": "snapshot"},
    "mart_measurement_health": {"kind": "snapshot"},
    "mart_migration_summary": {"kind": "snapshot"},
    "mart_link_hygiene": {"kind": "snapshot"},
    "mart_renewal_risk": {"kind": "snapshot"},
    "quality_scorecard": {"kind": "snapshot"},
    "incident_register": {"kind": "reference"},
}

# Text columns whose order carries meaning, and the numeric column that orders them.
SORT_BY: dict[str, dict[str, str]] = {
    "dim_date": {"month_label": "month_index", "quarter_label": "month_index", "day_name": "day_of_week"},
    "dim_campaign": {"campaign_id": "campaign_order", "campaign_name": "campaign_order"},
    "mart_funnel": {"stage_label": "ordinal", "stage": "ordinal"},
    "mart_revenue_bridge": {"step_label": "ordinal", "step": "ordinal"},
    "mart_renewal_risk": {"risk_level": "risk_order"},
    "quality_scorecard": {"check_name": "check_order"},
}

# Columns that are dates although their names do not say so.
DATE_COLUMNS = {"date", "day", "month_start", "week_start", "sent_date", "due_date", "paid_date",
                "started_on", "ended_on"}

# Row-level ratios a mart carries for its own consumers. Summing or averaging them
# across rows is wrong (a ratio of sums is not a mean of ratios), so they never
# summarise; the measures below recompute each one from its numerator and denominator.
ROW_RATIOS = {"bounce_rate", "human_open_rate", "click_rate", "click_to_open_rate", "complaint_rate",
              "lead_rate", "mql_per_lead", "net_cash_per_visitor_cents", "from_previous_rate",
              "owner_match_rate", "source_match_rate", "stage_match_rate"}

# --------------------------------------------------------------------------
# Relationships. All single-direction many-to-one.
# --------------------------------------------------------------------------

RELATIONSHIPS: list[tuple[str, str, str, str]] = [
    ("mart_growth_daily", "day", "dim_date", "date"),
    ("mart_paid_efficiency_daily", "day", "dim_date", "date"),
    ("mart_paid_efficiency_daily", "campaign_id", "dim_campaign", "campaign_id"),
    ("fact_cash_attribution", "paid_date", "dim_date", "date"),
    ("fact_cash_attribution", "campaign_id", "dim_campaign", "campaign_id"),
    ("mart_email_performance", "sent_date", "dim_date", "date"),
    ("mart_campaign_performance", "campaign_id", "dim_campaign", "campaign_id"),
]

# Tables intentionally left unrelated, with the reason.
UNRELATED: dict[str, str] = {
    "mart_revenue": "One row: the all-time revenue totals. A date filter would not move it, which is right.",
    "mart_revenue_bridge": "The all-time CRM-to-cash bridge; its steps are not dated.",
    "mart_platform_comparison": "One row per ad platform, all time: what each platform claims against warehouse "
                                "cash. Its own platform column is its filter.",
    "mart_funnel": "One row per lifecycle stage, all time.",
    "mart_experiment_variants": "One row per CTA variant; the test ran on its own randomized visitors.",
    "mart_content_performance": "One row per content item, first-identified-content credit, all time.",
    "mart_measurement_health": "One row: tracking-quality counts for the whole history.",
    "mart_migration_summary": "One row: the legacy-to-new CRM migration audit.",
    "mart_link_hygiene": "One row per short link, checked against the campaign registry.",
    "mart_renewal_risk": "Subscriptions due from the as-of date forward; the calendar ends at the as-of date.",
    "quality_scorecard": "One row per data-quality check, from the single-row health and migration marts.",
    "incident_register": "One row per logged incident; read as a list, not filtered by the calendar.",
}

# --------------------------------------------------------------------------
# Measures. (name, DAX, format string, folder, description)
#
# Every VAR is prefixed `v`: Power BI reserves far more VAR names than it
# documents, and a measure that trips one fails at runtime, not at load.
# --------------------------------------------------------------------------

MONEY = "\\$#,0"
MONEY_2 = "\\$#,0.00"
COUNT = "#,0"
PCT = "0.0%"
PCT_2 = "0.00%"
PCT_3 = "0.000%"
CHANGE = "+0.0%;-0.0%;0.0%"
ROAS = '0.00"x"'

G, P, A, E = "mart_growth_daily", "mart_paid_efficiency_daily", "fact_cash_attribution", "mart_email_performance"


def _usd(table: str, column: str) -> str:
    return f"DIVIDE(SUM({table}[{column}]), 100)"


MEASURES: list[tuple[str, str, str, str, str]] = [
    # --- Calendar ------------------------------------------------------------
    ("As-of date", "CALCULATE(MAX(dim_date[date]), REMOVEFILTERS(dim_date))", "d mmm yyyy", "00 Calendar",
     "The last complete day of data. Every 'last 28 days' figure ends here."),
    ("Days in view", "COUNTROWS(dim_date)", COUNT, "00 Calendar",
     "Days in the current date filter."),

    # --- Cash (event date) ----------------------------------------------------
    ("Net cash", _usd(G, "net_cash_cents"), MONEY, "01 Cash",
     "Payments received minus refunds, on the day each happened (event date)."),
    ("Gross collected", _usd(G, "gross_collected_cents"), MONEY, "01 Cash",
     "Payments received, on the payment date."),
    ("Refunds", _usd(G, "refunds_cents"), MONEY, "01 Cash",
     "Money returned, on the refund date."),
    ("CRM booked", _usd(G, "booked_cents"), MONEY, "01 Cash",
     "Closed-won deal value on the close date, as the CRM records it."),
    ("Ad spend", _usd(G, "spend_cents"), MONEY, "01 Cash",
     "Paid media spend on Meta, Google and LinkedIn, on the spend date."),
    ("Leads", f"SUM({G}[leads])", COUNT, "01 Cash",
     "Contacts created, every channel."),
    ("MQLs", f"SUM({G}[mqls])", COUNT, "01 Cash",
     "Contacts reaching marketing-qualified, on the day they qualified."),
    ("Calls booked", f"SUM({G}[calls_booked])", COUNT, "01 Cash",
     "Discovery calls booked, on the booking day."),
    ("Deals won", f"SUM({G}[closed_won_deals])", COUNT, "01 Cash",
     "Deals closed-won, on the close day."),
    ("MQL rate", "DIVIDE([MQLs], [Leads])", PCT, "01 Cash",
     "MQLs / leads in the same window (event basis): a lagging read of lead quality."),
    ("Spend per deal won", "DIVIDE([Ad spend], [Deals won])", MONEY, "01 Cash",
     "Ad spend / deals won in the same window. Blended: organic deals are in the denominator."),

    # --- Paid media (activity basis) -----------------------------------------
    ("Paid spend", _usd(P, "spend_cents"), MONEY, "02 Paid media",
     "Spend by paid campaign and day."),
    ("Impressions", f"SUM({P}[impressions])", COUNT, "02 Paid media", "Ad impressions."),
    ("Ad clicks", f"SUM({P}[clicks])", COUNT, "02 Paid media", "Ad clicks."),
    ("Paid leads", f"SUM({P}[leads])", COUNT, "02 Paid media",
     "Leads whose creating touch was a paid campaign, on the creation day."),
    ("Paid MQLs", f"SUM({P}[mqls])", COUNT, "02 Paid media",
     "MQLs reached in the window by leads a paid campaign created."),
    ("Paid calls booked", f"SUM({P}[calls_booked])", COUNT, "02 Paid media",
     "Discovery calls booked in the window by paid-created leads."),
    ("Paid deals won", f"SUM({P}[closed_won_deals])", COUNT, "02 Paid media",
     "Deals won in the window by paid-created leads."),
    ("CPM", "DIVIDE([Paid spend] * 1000, [Impressions])", MONEY_2, "02 Paid media",
     "Cost per thousand impressions."),
    ("CTR", "DIVIDE([Ad clicks], [Impressions])", PCT_2, "02 Paid media", "Ad clicks / impressions."),
    ("CPC", "DIVIDE([Paid spend], [Ad clicks])", MONEY_2, "02 Paid media", "Paid spend / ad clicks."),
    ("Cost per lead", "DIVIDE([Paid spend], [Paid leads])", MONEY_2, "02 Paid media",
     "Paid spend / paid leads in the same window (CPL)."),
    ("Cost per MQL", "DIVIDE([Paid spend], [Paid MQLs])", MONEY_2, "02 Paid media",
     "Paid spend / MQLs from paid-created leads in the same window."),
    ("Cost per booked call", "DIVIDE([Paid spend], [Paid calls booked])", MONEY_2, "02 Paid media",
     "Paid spend / discovery calls booked by paid-created leads (CPDM)."),
    ("Paid MQL rate", "DIVIDE([Paid MQLs], [Paid leads])", PCT, "02 Paid media",
     "Paid MQLs / paid leads in the same window: lead quality by campaign."),
    ("Cost per deal won", "DIVIDE([Paid spend], [Paid deals won])", MONEY, "02 Paid media",
     "Paid spend / deals won by paid-created leads."),

    # --- Attribution (payment date, lead-creation touch) ---------------------
    ("Attributed net cash", _usd(A, "net_cash_cents"), MONEY, "03 Attribution",
     "Net cash credited to the campaign whose touch created the lead, on the payment date. Sums to net cash."),
    ("Attributed payments", f"COUNTROWS({A})", COUNT, "03 Attribution", "Payments credited."),
    ("Paid attributed net cash", "CALCULATE([Attributed net cash], dim_campaign[is_paid] = TRUE())", MONEY,
     "03 Attribution", "Net cash credited to paid campaigns."),
    ("Cash ROAS", "DIVIDE([Paid attributed net cash], [Paid spend])", ROAS, "03 Attribution",
     "Net cash credited to paid campaigns / paid spend. Descriptive, not incremental: it credits the creating "
     "touch."),
    ("Unattributed net cash",
     'CALCULATE([Attributed net cash], dim_campaign[campaign_id] = "(unattributed)")', MONEY, "03 Attribution",
     "Net cash from buyers with no lead-creation touch before paying."),
    ("Unattributed share", "DIVIDE([Unattributed net cash], [Attributed net cash])", PCT, "03 Attribution",
     "Share of net cash no campaign can claim."),

    # --- Revenue truth (all time) --------------------------------------------
    ("Booked (CRM, all time)", _usd("mart_revenue", "booked_cents"), MONEY, "04 Revenue truth",
     "All closed-won deal value in the CRM."),
    ("Gross collected (all time)", _usd("mart_revenue", "gross_collected_cents"), MONEY, "04 Revenue truth",
     "All payments received."),
    ("Refunds (all time)", _usd("mart_revenue", "refunds_cents"), MONEY, "04 Revenue truth", "All refunds."),
    ("Net collected (all time)", _usd("mart_revenue", "net_collected_cents"), MONEY, "04 Revenue truth",
     "All payments minus all refunds: the number the bank agrees with."),
    ("Refund rate", "DIVIDE([Refunds (all time)], [Gross collected (all time)])", PCT, "04 Revenue truth",
     "Refunds / gross collected."),
    ("CRM overstatement", "[Booked (CRM, all time)] - [Net collected (all time)]", MONEY, "04 Revenue truth",
     "How far CRM bookings run ahead of the cash that arrived."),
    # Only the movements: beside a $3.3M opening bar every step is a sliver, and a
    # subtotal written in as a bar would be counted twice by the chart's own total.
    ("Bridge movement",
     "SUMX(FILTER(mart_revenue_bridge, mart_revenue_bridge[kind] = \"delta\"), mart_revenue_bridge[cents]) / 100",
     MONEY, "04 Revenue truth",
     "Each step between CRM bookings and net cash. The chart adds them, so its total is the gap between the two."),
    ("Bridge amount", _usd("mart_revenue_bridge", "cents"), MONEY, "04 Revenue truth",
     "The bridge value at each step, totals included."),
    ("Platform-reported value", _usd("mart_platform_comparison", "reported_value_cents"), MONEY,
     "04 Revenue truth", "Conversion value the ad platforms claim for themselves."),
    ("Warehouse cash (paid platforms)", _usd("mart_platform_comparison", "warehouse_net_cash_cents"), MONEY,
     "04 Revenue truth", "Net cash the warehouse credits to each platform's campaigns."),
    ("Platform spend", _usd("mart_platform_comparison", "spend_cents"), MONEY, "04 Revenue truth",
     "Spend on each platform, all time."),
    ("Platform overstatement", "[Platform-reported value] - [Warehouse cash (paid platforms)]", MONEY,
     "04 Revenue truth", "Claimed value the warehouse cannot find in cash."),
    ("Reported ROAS", "DIVIDE([Platform-reported value], [Platform spend])", ROAS, "04 Revenue truth",
     "What each platform says its spend returned."),
    ("Warehouse ROAS", "DIVIDE([Warehouse cash (paid platforms)], [Platform spend])", ROAS, "04 Revenue truth",
     "What the spend returned in collected, net cash."),
    ("Claim multiple", "DIVIDE([Platform-reported value], [Warehouse cash (paid platforms)])", ROAS,
     "04 Revenue truth", "Platform claims / warehouse cash: 1.00x would mean the platforms are right."),

    # --- Funnel and experiment -----------------------------------------------
    ("Funnel people", "SUM(mart_funnel[people])", COUNT, "05 Funnel",
     "People who reached the stage, all time."),
    ("Lead to paid", "DIVIDE(CALCULATE([Funnel people], mart_funnel[stage] = \"paid\"),\n"
                     "    CALCULATE([Funnel people], mart_funnel[stage] = \"lead\"))", PCT_2, "05 Funnel",
     "Paying customers / leads, all time."),
    ("Stage conversion",
     "VAR vStage = SELECTEDVALUE(mart_funnel[ordinal])\n"
     "VAR vHere = [Funnel people]\n"
     "VAR vBefore = CALCULATE([Funnel people], REMOVEFILTERS(mart_funnel), mart_funnel[ordinal] = vStage - 1)\n"
     "RETURN IF(NOT ISBLANK(vStage) && vStage > 1, DIVIDE(vHere, vBefore))", PCT, "05 Funnel",
     "People at this stage / people at the stage before."),
    ("Test visitors", "SUM(mart_experiment_variants[visitors])", COUNT, "05 Funnel",
     "Visitors randomized into the CTA test."),
    ("Test lead rate", "DIVIDE(SUM(mart_experiment_variants[leads]), [Test visitors])", PCT_2, "05 Funnel",
     "Leads / visitors in the CTA test."),
    ("Test MQLs per lead", "DIVIDE(SUM(mart_experiment_variants[mqls]), SUM(mart_experiment_variants[leads]))",
     PCT, "05 Funnel", "MQLs / leads in the CTA test."),
    ("Test customers", "SUM(mart_experiment_variants[customers])", COUNT, "05 Funnel",
     "Buyers in the CTA test. Fifteen buyers cannot carry a cash decision."),
    ("Test cash per visitor",
     "DIVIDE(SUM(mart_experiment_variants[net_cash_cents]), [Test visitors]) / 100", MONEY_2, "05 Funnel",
     "Net cash / visitors: the metric the test should be decided on."),
    ("Variant lead-rate lift",
     "VAR vControl = CALCULATE([Test lead rate], mart_experiment_variants[variant_id] = \"cta_a\")\n"
     "VAR vVariant = CALCULATE([Test lead rate], mart_experiment_variants[variant_id] = \"cta_b\")\n"
     "RETURN DIVIDE(vVariant, vControl) - 1", CHANGE, "05 Funnel",
     "Variant lead rate against control."),
    ("Variant cash-per-visitor lift",
     "VAR vControl = CALCULATE([Test cash per visitor], mart_experiment_variants[variant_id] = \"cta_a\")\n"
     "VAR vVariant = CALCULATE([Test cash per visitor], mart_experiment_variants[variant_id] = \"cta_b\")\n"
     "RETURN DIVIDE(vVariant, vControl) - 1", CHANGE, "05 Funnel",
     "Variant cash per visitor against control."),

    # --- Content --------------------------------------------------------------
    ("Content views", "SUM(mart_content_performance[views])", COUNT, "06 Content", "Views, all time."),
    ("Content engaged leads", "SUM(mart_content_performance[engaged_leads])", COUNT, "06 Content",
     "Leads whose first identified engagement was the item."),
    ("Content customers", "SUM(mart_content_performance[customers])", COUNT, "06 Content",
     "Buyers whose first identified engagement was the item."),
    ("Content-influenced cash", _usd("mart_content_performance", "influenced_net_cash_cents"), MONEY,
     "06 Content", "Net cash from buyers whose first identified engagement was the item."),
    ("Cash per 1K views", "DIVIDE([Content-influenced cash] * 1000, [Content views])", MONEY_2, "06 Content",
     "Influenced cash per thousand views: views are not buyers."),

    # --- Email ----------------------------------------------------------------
    ("Emails sent", f"SUM({E}[sends])", COUNT, "07 Email", "Messages sent."),
    ("Emails delivered", f"SUM({E}[delivered])", COUNT, "07 Email", "Delivered sends."),
    ("Human open rate", f"DIVIDE(SUM({E}[human_opens]), [Emails delivered])", PCT, "07 Email",
     "Opens excluding privacy-proxy machine opens / delivered."),
    ("Reported open rate", f"DIVIDE(SUM({E}[opens]), [Emails delivered])", PCT, "07 Email",
     "All opens / delivered, inflated by machine opens; shown only for comparison."),
    ("Machine open share", f"DIVIDE(SUM({E}[machine_opens]), SUM({E}[opens]))", PCT, "07 Email",
     "Share of reported opens made by privacy proxies, not people."),
    ("Email click rate", f"DIVIDE(SUM({E}[clicks]), [Emails delivered])", PCT_2, "07 Email",
     "Clicks / delivered."),
    ("Click-to-open rate", f"DIVIDE(SUM({E}[clicks]), SUM({E}[human_opens]))", PCT, "07 Email",
     "Clicks / human opens."),
    ("Bounce rate", f"DIVIDE(SUM({E}[bounces]), [Emails sent])", PCT_2, "07 Email",
     "Bounces / sends. Above 2% the sending domain is at risk."),
    ("Complaint rate", f"DIVIDE(SUM({E}[spam_complaints]), [Emails delivered])", PCT_3, "07 Email",
     "Spam complaints / delivered. The mailbox providers' limit is 0.1%."),
    ("Unsubscribe rate", f"DIVIDE(SUM({E}[unsubscribes]), [Emails delivered])", PCT_2, "07 Email",
     "Unsubscribes / delivered."),
    ("Bounce limit", "0.02", PCT, "07 Email", "The 2% bounce threshold, as a reference line."),

    # --- Tracking, data quality and operations -------------------------------
    ("UTM completeness",
     "DIVIDE(SUM(mart_measurement_health[touches_with_utm]), SUM(mart_measurement_health[eligible_touches]))",
     PCT, "08 Data quality", "Eligible touches that arrived with UTM parameters."),
    ("Registry coverage",
     "DIVIDE(SUM(mart_measurement_health[registered_touches]), SUM(mart_measurement_health[eligible_touches]))",
     PCT, "08 Data quality", "Eligible touches whose campaign is in the registry."),
    ("Contacts with an owner",
     "DIVIDE(SUM(mart_measurement_health[owned_contacts]), SUM(mart_measurement_health[contacts]))", PCT,
     "08 Data quality", "CRM contacts with an owner assigned."),
    ("Valid paid journeys",
     "DIVIDE(SUM(mart_measurement_health[valid_paid_journeys]), SUM(mart_measurement_health[paid_contact_count]))",
     PCT, "08 Data quality", "Paying contacts whose journey has a lead-creation touch before payment."),
    ("Unmatched payments", "SUM(mart_measurement_health[unmatched_payments])", COUNT, "08 Data quality",
     "Payments the CRM cannot tie to a contact."),
    ("Duplicate contact rows", "SUM(mart_measurement_health[duplicate_contact_rows])", COUNT,
     "08 Data quality", "Contact rows sharing an email address."),
    ("Short links", "COUNTROWS(mart_link_hygiene)", COUNT, "08 Data quality", "Short links in use."),
    ("Links with defects",
     "COUNTROWS(FILTER(mart_link_hygiene, mart_link_hygiene[missing_utm] || "
     "mart_link_hygiene[unregistered_campaign] || mart_link_hygiene[off_taxonomy]))", COUNT, "08 Data quality",
     "Short links with missing, unregistered or off-taxonomy UTMs."),
    ("Defective link click share",
     "DIVIDE(\n    CALCULATE(SUM(mart_link_hygiene[recent_clicks]),\n"
     "        FILTER(mart_link_hygiene, mart_link_hygiene[missing_utm] || mart_link_hygiene[unregistered_campaign]"
     " || mart_link_hygiene[off_taxonomy])),\n    SUM(mart_link_hygiene[recent_clicks]))", PCT, "08 Data quality",
     "Share of the last 30 days' short-link clicks that land without a valid campaign."),
    ("Link recent clicks", "SUM(mart_link_hygiene[recent_clicks])", COUNT, "08 Data quality",
     "Short-link clicks in the last 30 days."),
    ("Legacy contacts", "SUM(mart_migration_summary[legacy_contacts])", COUNT, "08 Data quality",
     "Contacts in the legacy CRM snapshot."),
    ("Missing after migration", "SUM(mart_migration_summary[missing_contacts])", COUNT, "08 Data quality",
     "Legacy contacts with no match in the new CRM."),
    ("Owner match", "AVERAGE(mart_migration_summary[owner_match_rate])", PCT, "08 Data quality",
     "Migrated contacts whose owner survived the move."),
    ("Source match", "AVERAGE(mart_migration_summary[source_match_rate])", PCT, "08 Data quality",
     "Migrated contacts whose original source survived the move."),
    ("Stage match", "AVERAGE(mart_migration_summary[stage_match_rate])", PCT, "08 Data quality",
     "Migrated contacts whose lifecycle stage survived the move."),
    ("Renewals due", "COUNTROWS(mart_renewal_risk)", COUNT, "08 Data quality",
     "Community subscriptions due for renewal from the as-of date."),
    ("Renewals at risk",
     "CALCULATE(COUNTROWS(mart_renewal_risk), mart_renewal_risk[risk_level] IN {\"high\", \"medium\"})", COUNT,
     "08 Data quality", "Renewals due within 14 days (medium), or overdue or with a failed card attempt (high)."),
    ("Failed renewal attempts", "SUM(mart_renewal_risk[failed_attempts])", COUNT, "08 Data quality",
     "Card declines on renewals that are still open."),
    ("Open incidents", "COUNTROWS(FILTER(incident_register, ISBLANK(incident_register[ended_on])))", COUNT,
     "08 Data quality", "Logged incidents with no end date."),
    ("Incidents", "COUNTROWS(incident_register)", COUNT, "08 Data quality", "Logged incidents."),
    ("Check rate", "AVERAGE(quality_scorecard[rate])", PCT, "08 Data quality",
     "The share of records passing a data-quality check."),
    ("Check target", "AVERAGE(quality_scorecard[target])", PCT, "08 Data quality",
     "The level the check is held to."),
    ("Checks below target",
     "COUNTROWS(FILTER(quality_scorecard, quality_scorecard[rate] < quality_scorecard[target]))", COUNT,
     "08 Data quality", "Data-quality checks under their target."),
]

# Colours returned by a measure, for a chart that marks the bars that need a look.
# Bound through a wildcard data-point selector; without one Power BI colours nothing.
ALERT, CALM = "#ef6f61", "#3987e5"
MEASURES += [
    ("Check colour", f'IF([Check rate] < [Check target], "{ALERT}", "{CALM}")', "", "13 Colours",
     "Red where a check is under its target."),
    ("ROAS colour", f'IF([Cash ROAS] < 1, "{ALERT}", "{CALM}")', "", "13 Colours",
     "Red where a campaign returned less cash than it cost."),
    ("MQL rate colour",
     "VAR vAll = CALCULATE([Paid MQL rate], REMOVEFILTERS(dim_campaign))\n"
     f'RETURN IF([Paid MQL rate] < 0.75 * vAll, "{ALERT}", "{CALM}")', "", "13 Colours",
     "Red where a campaign qualifies leads at under three-quarters of the paid average."),
]

# --------------------------------------------------------------------------
# Windows. A figure on its own is not a finding; against the period before it is.
# --------------------------------------------------------------------------

# (base measure, kind): kind "money"/"count" compare as a % change, "rate" as points,
# "cost" as a % change where up is bad. The kind only changes the caption's wording.
WINDOWED: tuple[tuple[str, str], ...] = (
    ("Net cash", "money"), ("Ad spend", "money"), ("Leads", "count"), ("MQL rate", "rate"),
    ("Paid spend", "money"), ("Cost per lead", "cost"), ("Cost per booked call", "cost"), ("CTR", "rate"),
    ("Paid MQL rate", "rate"), ("Calls booked", "count"), ("Deals won", "count"),
    ("Attributed net cash", "money"), ("Cash ROAS", "ratio"),
    ("Emails delivered", "count"), ("Human open rate", "rate"), ("Bounce rate", "rate"),
    ("Complaint rate", "rate"),
)
# Shown on the executive page: fixed to the 28 days ending on the as-of date, so
# the page answers "how are we doing now" whatever the calendar says.
LAST_28 = ("Net cash", "Ad spend", "Leads", "MQL rate", "Paid spend", "Paid leads", "Paid MQLs",
           "Cost per lead", "Paid MQL rate", "Bounce rate", "Calls booked")


def _prior(name: str) -> str:
    return (
        "VAR vStart = MIN(dim_date[date])\n"
        "VAR vEnd = MAX(dim_date[date])\n"
        "VAR vDays = INT(vEnd - vStart) + 1\n"
        f"RETURN CALCULATE([{name}], REMOVEFILTERS(dim_date),\n"
        "    dim_date[date] >= vStart - vDays, dim_date[date] < vStart)"
    )


def _change(current: str, prior: str, kind: str) -> tuple[str, str]:
    if kind == "rate":
        return f"IF(NOT ISBLANK([{prior}]), [{current}] - [{prior}])", "+0.00%;-0.00%;0.00%"
    if kind == "ratio":
        return f"IF(NOT ISBLANK([{prior}]), [{current}] - [{prior}])", '+0.00"x";-0.00"x";0.00"x"'
    return f"DIVIDE([{current}] - [{prior}], ABS([{prior}]))", CHANGE


def _caption(change: str, kind: str, prior_label: str) -> str:
    if kind == "rate":
        shown = f'FORMAT([{change}] * 100, "+0.0;-0.0;0.0") & " pts"'
    elif kind == "ratio":
        shown = f'FORMAT([{change}], "+0.00;-0.00;0.00") & "x"'
    else:
        shown = f'FORMAT([{change}], "+0%;-0%;0%")'
    return (f'IF(ISBLANK([{change}]), "all dates in view · pick months to compare", '
            f'{shown} & " vs {prior_label}")')


for _name, _kind in WINDOWED:
    _fmt = next(m[2] for m in MEASURES if m[0] == _name)
    MEASURES.append((f"{_name} prior period", _prior(_name), _fmt, "09 Windows",
                     f"{_name} over the same number of days immediately before the dates in view."))
    _dax, _cfmt = _change(_name, f"{_name} prior period", _kind)
    MEASURES.append((f"{_name} vs prior", _dax, _cfmt, "09 Windows",
                     f"{_name} against the period before" + (" (points)." if _kind == "rate" else ".")))
    MEASURES.append((f"{_name} caption", _caption(f"{_name} vs prior", _kind, "prior period"), "", "10 Captions",
                     f"Tile caption for {_name}."))

_WINDOW_KINDS = dict(WINDOWED) | {"Paid leads": "count", "Paid MQLs": "count"}
for _name in LAST_28:
    _fmt = next(m[2] for m in MEASURES if m[0] == _name)
    _kind = _WINDOW_KINDS[_name]
    MEASURES.append((f"{_name}, last 28 days",
                     f"CALCULATE([{_name}], REMOVEFILTERS(dim_date), dim_date[is_last_28_days] = TRUE())", _fmt,
                     "11 Last 28 days", f"{_name} in the 28 days ending on the as-of date."))
    MEASURES.append((f"{_name}, prior 28 days",
                     f"CALCULATE([{_name}], REMOVEFILTERS(dim_date), dim_date[is_prior_28_days] = TRUE())", _fmt,
                     "11 Last 28 days", f"{_name} in the 28 days before those."))
    _dax, _cfmt = _change(f"{_name}, last 28 days", f"{_name}, prior 28 days", _kind)
    MEASURES.append((f"{_name}, 28-day change", _dax, _cfmt, "11 Last 28 days",
                     f"{_name}, last 28 days against the 28 days before."))
    MEASURES.append((f"{_name}, 28-day caption", _caption(f"{_name}, 28-day change", _kind, "prior 28 days"), "",
                     "10 Captions", f"Tile caption for {_name}, last 28 days."))

# Captions that are a sentence about a figure rather than a comparison.
MEASURES += [
    ("Platform claim caption",
     'FORMAT([Claim multiple], "0.00") & "x the cash the warehouse can find"', "", "10 Captions",
     "Tile caption: how far platform claims exceed cash."),
    ("CRM booked caption",
     'FORMAT(DIVIDE([Booked (CRM, all time)], [Net collected (all time)]) - 1, "+0.0%") & " over net collected cash"',
     "", "10 Captions", "Tile caption: how far bookings run ahead of cash."),
    ("Net collected caption",
     '"after " & FORMAT([Refunds (all time)] / 1000, "$#,0") & "K of refunds"', "", "10 Captions",
     "Tile caption: refunds taken out of gross."),
    ("Refund caption", 'FORMAT([Refund rate], "0.0%") & " of gross collected"', "", "10 Captions",
     "Tile caption: refund rate."),
    ("Unattributed caption",
     'FORMAT([Unattributed net cash] / 1000, "$#,0") & "K with no creating touch"', "", "10 Captions",
     "Tile caption: cash no campaign can claim."),
    ("Content caption",
     'FORMAT([Content customers], "#,0") & " buyers from " & FORMAT([Content views] / 1000, "#,0") & "K views"',
     "", "10 Captions", "Tile caption: views against buyers."),
    ("Links caption",
     'FORMAT([Defective link click share], "0%") & " of recent short-link clicks"', "", "10 Captions",
     "Tile caption: clicks on defective links."),
    ("Renewals caption",
     'FORMAT([Failed renewal attempts], "#,0") & IF([Failed renewal attempts] = 1, " failed card attempt · ", '
     '" failed card attempts · ") & FORMAT([Renewals due], "#,0") '
     '& " due in total"', "", "10 Captions", "Tile caption: renewal queue."),
    ("UTM caption",
     'FORMAT([Registry coverage], "0.0%") & " of touches carry a registered campaign"', "", "10 Captions",
     "Tile caption: registry coverage."),
    ("Migration caption",
     'FORMAT([Missing after migration], "#,0") & " legacy contacts missing · " '
     '& FORMAT([Duplicate contact rows], "#,0") & " duplicates"', "", "10 Captions",
     "Tile caption: migration defects."),
    ("Funnel caption",
     'FORMAT([Lead to paid], "0.00%") & " of all leads became customers"', "", "10 Captions",
     "Tile caption: lead to paid."),
    ("ROAS caption",
     '"platforms report " & FORMAT([Reported ROAS], "0.0") & "x on the same spend"', "", "10 Captions",
     "Tile caption: the ROAS the platforms claim."),
    ("Quality caption",
     'FORMAT([Checks below target], "0") & " of " & FORMAT(COUNTROWS(quality_scorecard), "0") '
     '& " checks under target"', "", "10 Captions", "Tile caption: data-quality checks failing."),
    ("Complaint caption",
     'IF([Complaint rate] > 0.001, "above", "within") & " the 0.1% limit · " & [Complaint rate caption]', "",
     "10 Captions", "Tile caption: complaint rate against the mailbox-provider limit."),
]

# The executive summary, as a sentence built from the figures on the page. Every
# number in it is a measure above; nothing is typed.
MEASURES.append((
    "Executive summary",
    "VAR vDate = FORMAT([As-of date], \"d mmm yyyy\")\n"
    "VAR vCash = [Net cash, last 28 days]\n"
    "VAR vCashMove = [Net cash, 28-day change]\n"
    "VAR vRateNow = [Paid MQL rate, last 28 days]\n"
    "VAR vRateBefore = [Paid MQL rate, prior 28 days]\n"
    "VAR vRanked =\n"
    "    FILTER(\n"
    "        ADDCOLUMNS(VALUES(dim_campaign[campaign_id]),\n"
    "            \"@leads\", [Paid leads, last 28 days], \"@rate\", [Paid MQL rate, last 28 days]),\n"
    "        [@leads] >= 50 && NOT ISBLANK([@rate]))\n"
    "VAR vWorst = MINX(vRanked, [@rate])\n"
    "VAR vWorstNames = CONCATENATEX(FILTER(vRanked, [@rate] = vWorst), dim_campaign[campaign_id], \", \")\n"
    "VAR vBounce = [Bounce rate, last 28 days]\n"
    "RETURN\n"
    "    \"28 days to \" & vDate & \": \" & FORMAT(vCash / 1000, \"$#,0\") & \"K net cash (\"\n"
    "        & FORMAT(vCashMove, \"+0%;-0%\") & \" on the 28 days before) on \"\n"
    "        & FORMAT([Ad spend, last 28 days] / 1000, \"$#,0\") & \"K of ad spend. Paid media bought \"\n"
    "        & FORMAT([Paid leads, last 28 days], \"#,0\") & \" leads at \"\n"
    "        & FORMAT([Cost per lead, last 28 days], \"$#,0.00\") & \" each, but \"\n"
    "        & FORMAT(vRateNow, \"0.0%\") & \" qualified (\" & FORMAT(vRateBefore, \"0.0%\")\n"
    "        & \" before); the weakest at scale is \" & vWorstNames & \" at \" & FORMAT(vWorst, \"0.0%\")\n"
    "        & \". Email bounced at \" & FORMAT(vBounce, \"0.0%\")\n"
    "        & IF(vBounce > 0.02, \", above the 2% limit.\", \", inside the 2% limit.\")",
    "", "12 Narrative", "One-paragraph summary of the last 28 days, computed from the measures on the page.",
))


def measure_names() -> set[str]:
    return {name for name, *_ in MEASURES}
