"""What the report contains: seven pages, and every visual on them.

Each page answers one of the questions the README opens with, from the same governed
CSVs the Excel workbook and the Streamlit app read.

Visual shorthand::

    card(measure, subtitle=caption measure, trend=change measure, good="up"|"down")
    bar / column / stacked_column / line / scatter / funnel / table / matrix / slicer / waterfall / narrative

Fields are written ``table[column]`` for a column and ``[Measure]`` for a measure,
the notation Desktop shows in the field well.
"""

from __future__ import annotations

# Canvas is 1280x720 at FitToPage. report_chrome adds the header, the page buttons
# and the filter panel, and reflows what is written here into the body below them.
CARD_Y = 20
CARD_H = 118
ROW1_Y = 152
ROW2_Y = 442
CARDS = ((20, 300), (330, 296), (634, 300), (942, 318))
SLICER = (1068, 192, 76)  # x, width, height of a slicer slot; the panel takes it


def cards(*specs: dict) -> list[dict]:
    """Four KPI tiles across the top."""
    out = []
    for (x, width), spec in zip(CARDS, specs):
        out.append({"type": "card", "pos": (x, CARD_Y, width, CARD_H), **spec})
    return out


def tile(field: str, alt: str, *, subtitle: str | None = None, trend: str | None = None,
         good: str | None = None, label: str | None = None, rail: bool = True) -> dict:
    spec: dict[str, object] = {"field": field, "alt": alt}
    if subtitle:
        spec["subtitle"] = subtitle
    if trend:
        spec["trend"], spec["good"] = trend, good
    if label:
        spec["label"] = label
    if not rail:
        spec["rail"] = False
    return spec


def windowed(measure: str, alt: str, good: str | None, *, rail: bool = True, label: str | None = None) -> dict:
    """A tile that compares the dates in view with the same number of days before them."""
    return tile(f"[{measure}]", alt, subtitle=f"[{measure} caption]",
                trend=f"[{measure} vs prior]" if good else None, good=good, rail=rail, label=label)


def last_28(measure: str, alt: str, good: str | None, *, rail: bool = True, label: str | None = None) -> dict:
    return tile(f"[{measure}, last 28 days]", alt, subtitle=f"[{measure}, 28-day caption]",
                trend=f"[{measure}, 28-day change]" if good else None, good=good, rail=rail,
                label=label or f"{measure} · last 28 days")


def slicers(*fields: tuple[str, str, str]) -> list[dict]:
    """Slicers stacked in the right-hand slot; report_chrome moves them into the filter panel."""
    x, width, height = SLICER
    return [{"type": "slicer", "field": field, "title": title, "pos": (x, ROW2_Y + i * 84, width, height),
             "alt": alt} for i, (field, title, alt) in enumerate(fields)]


MONTH_SLICER = ("dim_date[month_label]", "Month", "Slicer. Filters the page by month label.")
PLATFORM = ("dim_campaign[platform]", "Platform", "Slicer. Filters the page by ad platform.")
CAMPAIGN = ("dim_campaign[campaign_id]", "Campaign", "Slicer. Filters the page by campaign ID.")
CHANNEL = ("dim_campaign[channel_group]", "Channel", "Slicer. Filters the page by channel group.")

WEEK = "dim_date[week_start]"
MONTH = "dim_date[month_label]"

PAGES: list[dict] = [
    # ----------------------------------------------------------------- 1
    {
        "name": "p1_executive",
        "display": "Executive summary",
        "visuals": [
            *cards(
                last_28("Net cash", "Card. Net cash, last 28 days: payments minus refunds against the 28 "
                                    "days before.", "up", label="Net cash · last 28 days"),
                last_28("Ad spend", "Card. Ad spend, last 28 days, against the 28 days before.", None,
                        label="Ad spend · last 28 days"),
                last_28("Cost per lead", "Card. Cost per lead, last 28 days: paid spend over paid leads.",
                        "down", label="Paid cost per lead · last 28 days"),
                last_28("Paid MQL rate", "Card. Paid MQL rate, last 28 days: the share of paid leads that "
                                         "qualified.", "up", label="Paid MQL rate · last 28 days"),
            ),
            {"type": "narrative", "field": "[Executive summary]", "title": "The last 28 days",
             "pos": (20, ROW1_Y, 380, 272),
             "alt": "Card titled The last 28 days. Shows Executive summary: net cash, spend, paid leads, "
                    "cost per lead, MQL rate, the weakest campaign and the email bounce rate."},
            {"type": "column", "x": MONTH, "y": ["[Net cash]", "[Ad spend]"],
             "sort": (MONTH, "Ascending"),
             "title": "Net cash and ad spend by month (September is to date)",
             "pos": (414, ROW1_Y, 846, 272),
             "alt": "Column chart titled Net cash and ad spend by month. Plots Net cash and Ad spend by "
                    "month label."},
            {"type": "line", "x": MONTH, "y": ["[Paid MQL rate]"], "series": "dim_campaign[platform]",
             "sort": (MONTH, "Ascending"),
             "title": "Paid MQL rate by month and platform: lead quality",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Line chart titled Paid MQL rate by month and platform. Plots Paid MQL rate by month "
                    "label for each platform."},
            {"type": "bar", "x": "dim_campaign[campaign_id]", "y": ["[Cash ROAS]"],
             "color": "[ROAS colour]", "sort": ("[Cash ROAS]", "Descending"),
             "title": "Cash ROAS by paid campaign, all time (red: under 1.0x)",
             "pos": (654, ROW2_Y, 606, 258),
             "alt": "Bar chart titled Cash ROAS by paid campaign. Plots Cash ROAS by campaign ID; campaigns "
                    "returning under 1.0x are red."},
        ],
    },
    # ----------------------------------------------------------------- 2
    {
        "name": "p2_revenue_truth",
        "display": "Revenue truth",
        "visuals": [
            *cards(
                tile("[Platform-reported value]", "Card. Platform-reported value: the conversion value "
                     "Meta, Google and LinkedIn claim.", subtitle="[Platform claim caption]",
                     label="Ad platforms claim"),
                tile("[Qualified pipeline created]", "Card. Qualified pipeline created: deal value at "
                     "an explicit qualification decision.", label="Qualified pipeline"),
                tile("[Booked (CRM, all time)]", "Card. Booked (CRM, all time): closed-won deal value in "
                     "the CRM.", subtitle="[CRM booked caption]", label="The CRM books"),
                tile("[Net collected (all time)]", "Card. Net collected (all time): payments minus "
                     "refunds.", subtitle="[Net collected caption]", label="The bank collected (net)"),
            ),
            {"type": "waterfall", "x": "mart_revenue_bridge[step_label]", "y": ["[Bridge movement]"],
             "sort": ("mart_revenue_bridge[step_label]", "Ascending"),
             "title": "Why net cash is below CRM bookings: each step, and the gap (total)",
             "pos": (20, ROW1_Y, 760, 272),
             "alt": "Waterfall chart titled Why net cash is below CRM bookings. Plots Bridge movement by "
                    "step label; the total bar is net collected minus CRM bookings."},
            {"type": "bar", "x": "mart_platform_comparison[platform]",
             "y": ["[Platform-reported value]", "[Warehouse cash (paid platforms)]"],
             "title": "What each platform claims against the cash it bought",
             "pos": (794, ROW1_Y, 466, 272),
             "alt": "Bar chart titled What each platform claims against the cash it bought. Plots "
                    "Platform-reported value and Warehouse cash by platform."},
            {"type": "table", "columns": ["mart_revenue_bridge[step_label]", "mart_revenue_bridge[note]",
                                          "[Bridge amount]"],
             "totals": False, "sort": ("mart_revenue_bridge[step_label]", "Ascending"),
             "title": "The bridge, step by step (every step computed; residual $0)",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Table titled The bridge, step by step. Lists step label, note and Bridge amount."},
            {"type": "table", "columns": ["mart_platform_comparison[platform]", "[Platform spend]",
                                          "[Reported ROAS]", "[Warehouse ROAS]", "[Claim multiple]"],
             "title": "ROAS as reported against ROAS on net cash",
             "pos": (654, ROW2_Y, 410, 258),
             "alt": "Table titled ROAS as reported against ROAS on net cash. Lists platform, Platform spend, "
                    "Reported ROAS, Warehouse ROAS and Claim multiple."},
            *slicers(("mart_platform_comparison[platform]", "Platform",
                      "Slicer. Filters the platform chart and table by platform.")),
        ],
    },
    # ----------------------------------------------------------------- 3
    {
        "name": "p3_paid_media",
        "display": "Paid media efficiency",
        "visuals": [
            *cards(
                windowed("Paid spend", "Card. Paid spend in the dates in view, against the period before.",
                         None),
                windowed("Cost per lead", "Card. Cost per lead: paid spend over paid leads.", "down"),
                windowed("Cost per booked call", "Card. Cost per booked call: paid spend over discovery "
                         "calls booked by paid leads.", "down"),
                windowed("CTR", "Card. CTR: ad clicks over impressions.", "up", rail=False),
            ),
            {"type": "line", "x": MONTH, "y": ["[Cost per lead]"], "series": "dim_campaign[platform]",
             "sort": (MONTH, "Ascending"),
             "title": "Cost per lead by month and platform",
             "pos": (20, ROW1_Y, 620, 272),
             "alt": "Line chart titled Cost per lead by month and platform. Plots Cost per lead by month "
                    "label for each platform."},
            {"type": "bar", "x": "dim_campaign[campaign_id]", "y": ["[Cost per booked call]"],
             "sort": ("[Cost per booked call]", "Descending"),
             "title": "Cost per booked call by campaign",
             "pos": (654, ROW1_Y, 606, 272),
             "alt": "Bar chart titled Cost per booked call by campaign. Plots Cost per booked call by "
                    "campaign ID."},
            {"type": "table",
             "columns": ["dim_campaign[campaign_id]", "dim_campaign[platform]", "[Paid spend]", "[Paid leads]",
                         "[Cost per lead]", "[Paid MQL rate]", "[Paid calls booked]", "[Cost per booked call]",
                         "[Paid attributed net cash]", "[Cash ROAS]"],
             "sort": ("[Paid spend]", "Descending"),
             "title": "Campaign scorecard for the dates in view",
             "pos": (20, ROW2_Y, 1044, 258),
             "alt": "Table titled Campaign scorecard. Lists campaign ID, platform, Paid spend, Paid leads, "
                    "Cost per lead, Paid MQL rate, Paid calls booked, Cost per booked call, Paid attributed "
                    "net cash and Cash ROAS."},
            *slicers(MONTH_SLICER, PLATFORM, CAMPAIGN),
        ],
    },
    # ----------------------------------------------------------------- 4
    {
        "name": "p4_funnel",
        "display": "Funnel and lead quality",
        "visuals": [
            *cards(
                windowed("Leads", "Card. Leads created in the dates in view, every channel.", "up"),
                windowed("MQL rate", "Card. MQL rate: MQLs over leads in the same window.", "up"),
                windowed("Calls booked", "Card. Calls booked: discovery calls booked in the dates in view.",
                         "up"),
                windowed("Deals won", "Card. Deals won in the dates in view.", "up"),
            ),
            {"type": "funnel", "x": "mart_funnel[stage_label]", "y": ["[Funnel people]"],
             "sort": ("mart_funnel[stage_label]", "Ascending"),
             "title": "Lead to renewal, all time",
             "pos": (20, ROW1_Y, 460, 272),
             "alt": "Funnel titled Lead to renewal. Plots Funnel people by stage label."},
            {"type": "line", "x": MONTH, "y": ["[Paid MQL rate]"], "series": "dim_campaign[channel_group]",
             "sort": (MONTH, "Ascending"),
             "title": "Paid MQL rate by month: paid social against paid search",
             "pos": (494, ROW1_Y, 766, 272),
             "alt": "Line chart titled Paid MQL rate by month. Plots Paid MQL rate by month label for each "
                    "channel group."},
            {"type": "bar", "x": "dim_campaign[campaign_id]", "y": ["[Paid MQL rate]"],
             "color": "[MQL rate colour]", "sort": ("[Paid MQL rate]", "Descending"),
             "title": "Paid MQL rate by campaign (red: under three-quarters of the paid average)",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Bar chart titled Paid MQL rate by campaign. Plots Paid MQL rate by campaign ID; "
                    "campaigns well under the paid average are red."},
            {"type": "table",
             "columns": ["mart_experiment_variants[label]", "[Test visitors]", "[Test lead rate]",
                         "[Test customers]", "[Test cash per visitor]"],
             "title": "CTA test: more leads, not more cash (keep the control)",
             "pos": (654, ROW2_Y, 410, 258),
             "alt": "Table titled CTA test. Lists label, Test visitors, Test lead rate, Test customers "
                    "and Test cash per visitor."},
            *slicers(MONTH_SLICER, CHANNEL),
        ],
    },
    # ----------------------------------------------------------------- 5
    {
        "name": "p5_attribution",
        "display": "Attribution and content",
        "visuals": [
            *cards(
                windowed("Attributed net cash", "Card. Attributed net cash: net cash credited to the "
                         "campaign that created the lead, on the payment date.", "up",
                         label="Net cash by payment date"),
                windowed("Cash ROAS", "Card. Cash ROAS: net cash credited to paid campaigns over paid spend.",
                         "up"),
                tile("[Unattributed share]", "Card. Unattributed share: net cash from buyers with no "
                     "creating touch.", subtitle="[Unattributed caption]", rail=False),
                tile("[Content-influenced cash]", "Card. Content-influenced cash: net cash from buyers whose "
                     "first identified engagement was a content item.", subtitle="[Content caption]"),
            ),
            {"type": "stacked_column", "x": "dim_date[month_label]", "y": ["[Attributed net cash]"],
             "series": "dim_campaign[channel_group]", "sort": ("dim_date[month_label]", "Ascending"),
             "title": "Net cash by month, credited to the channel that created the lead",
             "pos": (20, ROW1_Y, 760, 272),
             "alt": "Stacked column chart titled Net cash by month. Plots Attributed net cash by month label "
                    "for each channel group."},
            {"type": "bar", "x": "dim_campaign[campaign_id]", "y": ["[Attributed net cash]"],
             "sort": ("[Attributed net cash]", "Descending"),
             "title": "Net cash by creating campaign",
             "pos": (794, ROW1_Y, 466, 272),
             "alt": "Bar chart titled Net cash by creating campaign. Plots Attributed net cash by campaign "
                    "ID."},
            {"type": "table",
             "columns": ["mart_content_performance[title]", "[Content views]", "[Content customers]",
                         "[Content-influenced cash]", "[Cash per 1K views]"],
             "sort": ("[Content-influenced cash]", "Descending"),
             "title": "Content to cash: views are not buyers",
             "pos": (20, ROW2_Y, 760, 258),
             "alt": "Table titled Content to cash. Lists title, Content views, Content customers, "
                    "Content-influenced cash and Cash per 1K views."},
            {"type": "scatter", "x": "[Content views]", "y": ["[Content-influenced cash]"],
             "category": "mart_content_performance[title]",
             "title": "Views against cash, one dot per content item",
             "pos": (794, ROW2_Y, 270, 258),
             "alt": "Scatter chart titled Views against cash. Plots Content-influenced cash against Content "
                    "views for each title."},
            *slicers(MONTH_SLICER, CHANNEL),
        ],
    },
    # ----------------------------------------------------------------- 6
    {
        "name": "p6_email",
        "display": "Email and deliverability",
        "visuals": [
            *cards(
                windowed("Emails delivered", "Card. Emails delivered in the dates in view.", None),
                windowed("Human open rate", "Card. Human open rate: opens by people, not privacy proxies, "
                         "over delivered.", "up"),
                windowed("Bounce rate", "Card. Bounce rate: bounces over sends; above 2% the sending domain "
                         "is at risk.", "down", rail=False),
                tile("[Complaint rate]", "Card. Complaint rate: spam complaints over delivered; the limit is "
                     "0.1%.", subtitle="[Complaint caption]", trend="[Complaint rate vs prior]", good="down",
                     rail=False),
            ),
            {"type": "line", "x": WEEK, "y": ["[Bounce rate]"], "series": "mart_email_performance[sending_domain]",
             "title": "Bounce rate by week and sending domain (limit 2%)",
             "pos": (20, ROW1_Y, 620, 272),
             "alt": "Line chart titled Bounce rate by week and sending domain. Plots Bounce rate by week "
                    "start for each sending domain."},
            {"type": "line", "x": WEEK, "y": ["[Human open rate]", "[Reported open rate]"],
             "title": "Open rate as reported against opens by people",
             "pos": (654, ROW1_Y, 606, 272),
             "alt": "Line chart titled Open rate as reported against opens by people. Plots Human open rate "
                    "and Reported open rate by week start."},
            {"type": "table",
             "columns": ["mart_email_performance[email_type]", "mart_email_performance[sending_domain]",
                         "[Emails delivered]", "[Human open rate]", "[Click-to-open rate]", "[Bounce rate]",
                         "[Complaint rate]", "[Unsubscribe rate]"],
             "sort": ("[Emails delivered]", "Descending"),
             "title": "By email type and sending domain",
             "pos": (20, ROW2_Y, 1044, 258),
             "alt": "Table titled By email type and sending domain. Lists email type, sending domain, Emails "
                    "delivered, Human open rate, Click-to-open rate, Bounce rate, Complaint rate and "
                    "Unsubscribe rate."},
            *slicers(MONTH_SLICER,
                     ("mart_email_performance[email_type]", "Email type",
                      "Slicer. Filters the page by email type."),
                     ("mart_email_performance[sending_domain]", "Sending domain",
                      "Slicer. Filters the page by sending domain.")),
        ],
    },
    # ----------------------------------------------------------------- 7
    {
        "name": "p7_data_quality",
        "display": "Tracking, data quality and operations",
        "visuals": [
            *cards(
                tile("[UTM completeness]", "Card. UTM completeness: eligible touches that arrived with UTM "
                     "parameters.", subtitle="[UTM caption]"),
                tile("[Links with defects]", "Card. Links with defects: short links with missing, "
                     "unregistered or off-taxonomy UTMs.", subtitle="[Links caption]",
                     label="Short links with UTM defects"),
                tile("[Checks below target]", "Card. Checks below target: data-quality checks under the "
                     "level they are held to.", subtitle="[Quality caption]",
                     label="Data-quality checks failing"),
                tile("[Renewals at risk]", "Card. Renewals at risk: renewals due within 14 days, overdue or with a "
                     "failed card attempt.", subtitle="[Renewals caption]"),
            ),
            {"type": "column", "x": "quality_scorecard[check_name]", "y": ["[Check rate]"],
             "color": "[Check colour]", "sort": ("quality_scorecard[check_name]", "Ascending"),
             "y_start": 0.85,
             "title": "Data-quality checks, axis from 85% (red: under target)",
             "pos": (20, ROW1_Y, 500, 272),
             "alt": "Column chart titled Data-quality checks. Plots Check rate by check name; checks under "
                    "target are red."},
            {"type": "table",
             "columns": ["mart_link_hygiene[link_id]", "mart_link_hygiene[channel]",
                         "mart_link_hygiene[utm_campaign]", "mart_link_hygiene[missing_utm]",
                         "mart_link_hygiene[unregistered_campaign]", "mart_link_hygiene[off_taxonomy]",
                         "[Link recent clicks]"],
             "sort": ("[Link recent clicks]", "Descending"),
             "title": "Short links against the campaign registry (clicks, last 30 days)",
             "pos": (534, ROW1_Y, 726, 272),
             "alt": "Table titled Short links against the campaign registry. Lists link ID, channel, UTM "
                    "campaign, missing UTM, unregistered campaign, off taxonomy and Link recent clicks."},
            {"type": "table",
             "columns": ["incident_register[started_on]", "incident_register[kind]",
                         "incident_register[description]"],
             "totals": False, "sort": ("incident_register[started_on]", "Descending"),
             "title": "Incident register",
             "pos": (20, ROW2_Y, 840, 258),
             "alt": "Table titled Incident register. Lists started on, kind and description."},
            {"type": "table",
             "columns": ["mart_renewal_risk[subscription_id]", "mart_renewal_risk[due_date]",
                         "mart_renewal_risk[risk_level]"],
             "totals": False, "sort": ("mart_renewal_risk[risk_level]", "Ascending"),
             "title": "Renewal queue, riskiest first",
             "pos": (874, ROW2_Y, 190, 258),
             "alt": "Table titled Renewal queue. Lists subscription ID, due date and risk level."},
            *slicers(("mart_link_hygiene[channel]", "Link channel",
                      "Slicer. Filters the short-link table by channel.")),
        ],
    },
]

VISUAL_TYPES: dict[str, str] = {
    "card": "card",
    "narrative": "card",
    "bar": "clusteredBarChart",
    "column": "clusteredColumnChart",
    "stacked_column": "columnChart",
    "line": "lineChart",
    "area": "areaChart",
    "scatter": "scatterChart",
    "donut": "donutChart",
    "treemap": "treemap",
    "waterfall": "waterfallChart",
    "funnel": "funnel",
    "table": "tableEx",
    "matrix": "pivotTable",
    "slicer": "slicer",
    "gauge": "gauge",
    # The chrome report_chrome adds to every page.
    "page_header": "image",
    "nav": "actionButton",
    "filters_button": "actionButton",
    "panel_close": "actionButton",
    "panel_clear": "actionButton",
    "panel_background": "shape",
    "panel_title": "textbox",
}

from growthops.bi.report_chrome import (
    add_chrome,
)

PAGES = add_chrome(PAGES)
