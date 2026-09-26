"""GrowthOps OS: public, read-only portfolio dashboard on a synthetic scenario."""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from growthops.ask_data import TOPICS, answer as ask_data
from growthops.attribution import MODELS, summary as attribution_summary
from growthops.brief import period_brief
from growthops.db import connect
from growthops.diagnostics import detect, incident_recall, series as metric_series
from growthops.experiments import analyze as experiment_analysis
from growthops.funnel import funnel, funnel_by_campaign
from growthops.migration import audit as migration_audit
from growthops.narrator import narrate
from growthops.reconciliation import crm_bridge, four_numbers, platform_bridge, platform_comparison
from growthops.renewals import monitor as renewal_monitor
from growthops.report import TARGETS, campaign_performance, executive_brief
from growthops.scenario import AS_OF
from growthops.seed import seed
from growthops.warehouse import build
from growthops.workflow import health as workflow_health, trace as workflow_trace

# Reference palette (validated light and dark): slot 1 blue, slot 2 orange; blue/red diverging; gray totals.
BLUE, ORANGE, RED, GRAY = "#2a78d6", "#eb6834", "#e34948", "#898781"
GOOD, CRITICAL = "#0ca30c", "#d03b3b"

st.set_page_config(page_title="GrowthOps OS · ScaleLab", page_icon="📈", layout="wide")
st.markdown("""<style>
  .block-container {max-width: 1280px; padding-top: 1.6rem}
  .scope {font-size: .78rem; font-weight: 700; letter-spacing: .08em; opacity: .75}
  div[data-testid="stMetric"] {border: 1px solid rgba(137,135,129,.35); border-radius: 8px; padding: 12px 14px}
</style>""", unsafe_allow_html=True)


@st.cache_resource(show_spinner="Generating fifteen months of synthetic ScaleLab data…")
def demo_database() -> str:
    database = str(Path(tempfile.mkdtemp(prefix="growthops-")) / "sample.db")
    seed(database)
    build(database)
    return database


@st.cache_data(show_spinner="Running the analytics…")
def load_case(database: str) -> dict:
    connection = connect(database)
    try:
        episodes = detect(connection)
        brief = period_brief(connection)
        return {
            "summary": executive_brief(connection),
            "brief": brief,
            "narrative": narrate(brief["findings"]),
            "truth": four_numbers(connection),
            "platform_bridge": platform_bridge(connection),
            "crm_bridge": crm_bridge(connection),
            "platforms": platform_comparison(connection),
            "campaigns": campaign_performance(connection),
            "attribution": {model: attribution_summary(connection, model) for model in MODELS},
            "funnel": funnel(connection),
            "funnel_by_campaign": funnel_by_campaign(connection),
            "content": [dict(row) for row in connection.execute(
                "SELECT * FROM mart_content_performance ORDER BY influenced_net_cash_cents DESC")],
            "episodes": episodes,
            "recall": incident_recall(connection, episodes),
            "series": {metric: metric_series(connection, metric)
                       for metric in ("mql_rate", "utm_completeness", "leads", "spend", "net_cash")},
            "experiment": experiment_analysis(connection, "cta_growth_plan"),
            "ops": workflow_health(connection),
            "stuck": [dict(row) for row in connection.execute(
                """SELECT e.event_id, e.customer_id, p.amount_cents, p.product_id, e.received_at, e.attempts,
                          e.last_error, a.customer_id IS NOT NULL has_access
                   FROM processed_events e JOIN payments p ON p.payment_id=e.payment_id
                   LEFT JOIN access_entitlements a ON a.customer_id=e.customer_id
                   WHERE e.status='dead_letter' ORDER BY e.received_at""")],
            "traces": {row[0]: workflow_trace(connection, row[0]) for row in connection.execute(
                """SELECT event_id FROM processed_events WHERE attempts > 1 OR status <> 'completed'
                   ORDER BY status <> 'dead_letter', received_at""")},
            "renewals": renewal_monitor(connection),
            "migration": migration_audit(connection),
        }
    finally:
        connection.close()


def usd(cents: float | None, digits: int = 0) -> str:
    if cents is None:
        return "—"
    return f"{'-' if cents < 0 else ''}${abs(cents) / 100:,.{digits}f}"


def pct(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}%}"


def waterfall(steps: list[dict]) -> alt.LayerChart:
    """Horizontal bridge: totals in gray, additions in blue, subtractions in red."""
    rows, running = [], 0
    for order, step in enumerate(steps):
        if step["kind"] == "total":
            start, end, role = 0, step["cents"], "Total"
        else:
            start, end = running, running + step["cents"]
            role = "Adds" if step["cents"] >= 0 else "Subtracts"
        running = end
        rows.append({"order": order, "step": step["label"], "start": start / 100, "end": end / 100,
                     "label_x": max(start, end) / 100, "amount": usd(step["cents"]), "role": role})
    frame = pd.DataFrame(rows)
    base = alt.Chart(frame).encode(y=alt.Y("step:N", sort=alt.SortField("order"), title=None,
                                           axis=alt.Axis(labelLimit=440)))
    upper = frame[["start", "end"]].to_numpy().max() * 1.16  # room for the value labels
    bars = base.mark_bar(cornerRadius=3, height=18).encode(
        x=alt.X("start:Q", title="USD", axis=alt.Axis(format="$,.2s"), scale=alt.Scale(domain=[0, upper])), x2="end:Q",
        color=alt.Color("role:N", scale=alt.Scale(domain=["Total", "Adds", "Subtracts"], range=[GRAY, BLUE, RED]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("step:N", title="Step"), alt.Tooltip("amount:N", title="Amount")])
    labels = base.mark_text(align="left", dx=6, fontSize=11, color=GRAY).encode(x="label_x:Q", text="amount:N")
    return (bars + labels).properties(height=34 * len(frame) + 40)


def trend(points: list[dict], label: str, fmt: str) -> alt.LayerChart:
    """Rolling 7-day value against its 56-day baseline; flagged days marked in red."""
    cutoff = (date.fromisoformat(points[-1]["day"]) - timedelta(days=180)).isoformat()
    frame = pd.DataFrame([point for point in points if point["day"] >= cutoff])  # last six months
    frame["day"] = pd.to_datetime(frame["day"])
    long = frame.melt(id_vars=["day"], value_vars=["value", "baseline"], var_name="series", value_name="v")
    names = {"value": f"{label} (7-day)", "baseline": "56-day baseline"}
    long["series"] = long["series"].map(names)
    lines = alt.Chart(long).mark_line(strokeWidth=2).encode(
        x=alt.X("day:T", title=None),
        y=alt.Y("v:Q", title=None, axis=alt.Axis(format=fmt), scale=alt.Scale(zero=False)),
        color=alt.Color("series:N", scale=alt.Scale(domain=list(names.values()), range=[BLUE, GRAY]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("day:T"), alt.Tooltip("series:N"), alt.Tooltip("v:Q", format=fmt)])
    flagged = alt.Chart(frame[frame["flag"]]).mark_point(size=16, filled=True, color=RED).encode(
        x="day:T", y="value:Q", tooltip=[alt.Tooltip("day:T", title="Flagged day"),
                                         alt.Tooltip("value:Q", format=fmt), alt.Tooltip("z:Q", format=".1f")])
    return (lines + flagged).properties(height=240)


database = demo_database()
case = load_case(database)
kpis, quality = case["summary"]["metrics"], case["summary"]["measurement_health"]

st.markdown(f'<span class="scope">SYNTHETIC PORTFOLIO CASE · SCALELAB · DATA THROUGH {AS_OF:%d %b %Y}</span>',
            unsafe_allow_html=True)
st.title("GrowthOps OS")
st.caption("Acquisition → CRM → cash → access → renewal for a fictional creator-led B2B education company. "
           "Fifteen months of generated data with planted incidents; the analytics have to find them.")

tabs = st.tabs(["Morning brief", "Which number is right?", "Acquisition", "Funnel & content",
                "Diagnostics", "Experiment", "Automation & renewals", "Data quality", "Ask your data"])

with tabs[0]:
    brief = case["brief"]
    current, change = brief["current"], brief["change_pct"]
    st.subheader(f"Week of {current['start']} to {current['end']} vs the prior week")
    cols = st.columns(6)
    for col, (label, key, formatter) in zip(cols, (  # spend moving is neither good nor bad by itself
            ("Paid spend", "spend_cents", usd), ("Leads", "leads", "{:,}".format),
            ("MQLs", "mqls", "{:,}".format), ("Calls booked", "calls_booked", "{:,}".format),
            ("Deals won", "closed_won_deals", "{:,}".format), ("Net cash", "net_cash_cents", usd))):
        col.metric(label, formatter(current[key]), f"{change[key]:+.0%}" if change[key] is not None else None,
                   delta_color="off" if key == "spend_cents" else "normal")
    st.caption("Cash is dated by payment and refund. The prior week contained the enrollment-deadline launch, "
               "so week-over-week cash is expected to fall.")
    st.markdown("### What needs attention")
    for item in brief["findings"][:5]:
        with st.container(border=True):
            st.markdown(f"**{item['finding']}**".replace("$", r"\$"))
            st.markdown(f"**Why:** {item['why']}  \n**Next:** {item['investigation']}".replace("$", r"\$"))
            st.caption(f"Evidence: {item['evidence']} · Confidence: {item['confidence']} · `{item['id']}`"
                       .replace("$", r"\$"))
    with st.expander(f"{len(brief['findings']) - 5} lower-priority findings"):
        for item in brief["findings"][5:]:
            st.markdown(f"- **{item['finding']}** {item['evidence']} *Next:* {item['investigation']}".replace("$", r"\$"))
    st.caption("Every sentence is assembled from computed evidence. An optional LLM may rewrite it only if the result "
               "passes a claim validator (no new numbers, dates or causal claims); this public app shows the "
               "deterministic narrative.")

with tabs[1]:
    truth = case["truth"]
    st.subheader("Five systems, five revenue numbers")
    cols = st.columns(5)
    for col, (label, value) in zip(cols, (
            ("Meta says", truth["platform_reported_cents"]["meta"]),
            ("Google says", truth["platform_reported_cents"]["google"]),
            ("LinkedIn says", truth["platform_reported_cents"]["linkedin"]),
            ("CRM closed-won", truth["crm_booked_cents"]),
            ("Net cash collected", truth["net_collected_cents"]))):
        col.metric(label, usd(value))
    st.info(f"**{truth['answer']}** Paid media is credited with {usd(truth['paid_media_net_cash_cents'])} of it "
            "under the governed lead-creation model.".replace("$", r"\$"))
    st.markdown("#### Why the ad platforms' total differs from the warehouse")
    st.altair_chart(waterfall(case["platform_bridge"]["steps"]), width="stretch")
    st.caption(f"Exact bridge: each step is computed independently; residual "
               f"{usd(case['platform_bridge']['residual_cents'], 2)}.".replace("$", r"\$"))
    st.markdown("#### Why CRM bookings differ from cash")
    st.altair_chart(waterfall(case["crm_bridge"]["steps"]), width="stretch")
    st.markdown("#### Self-reported vs warehouse return on ad spend")
    platforms = pd.DataFrame(case["platforms"])
    roas = platforms.melt(id_vars=["platform"], value_vars=["platform_roas", "warehouse_roas"],
                          var_name="measure", value_name="roas")
    roas["measure"] = roas["measure"].map({"platform_roas": "Platform-reported", "warehouse_roas": "Warehouse net cash"})
    bars = alt.Chart(roas).mark_bar(cornerRadius=3).encode(
        x=alt.X("platform:N", title=None, axis=alt.Axis(labelAngle=0)), xOffset="measure:N",
        y=alt.Y("roas:Q", title="ROAS (×)"),
        color=alt.Color("measure:N", scale=alt.Scale(range=[ORANGE, BLUE]), legend=alt.Legend(title=None, orient="top")),
        tooltip=["platform:N", "measure:N", alt.Tooltip("roas:Q", format=".2f")])
    text = alt.Chart(roas).mark_text(dy=-7, fontSize=11, color=GRAY).encode(
        x="platform:N", xOffset="measure:N", y="roas:Q", text=alt.Text("roas:Q", format=".1f"))
    st.altair_chart((bars + text).properties(height=280), width="stretch")
    table = platforms.assign(spend=platforms["spend_cents"].map(usd), reported=platforms["reported_value_cents"].map(usd),
                             warehouse=platforms["warehouse_net_cash_cents"].map(usd))
    st.dataframe(table[["platform", "spend", "reported_conversions", "reported", "warehouse", "platform_roas",
                        "warehouse_roas", "overstatement_ratio"]], hide_index=True, width="stretch")

with tabs[2]:
    cols = st.columns(5)
    cols[0].metric("Paid spend", usd(kpis["spend_cents"]))
    cols[1].metric("Cost per paid lead", usd(kpis["cost_per_lead_cents"]))
    cols[2].metric("Cost per paid MQL", usd(kpis["cost_per_mql_cents"]))
    cols[3].metric("Net cash ROAS", f"{kpis['net_cash_roas']:.2f}×")
    cols[4].metric("Untracked net cash", usd(quality["unassigned_net_cash_cents"]))
    campaigns = pd.DataFrame(case["campaigns"])
    campaigns["spend"] = campaigns["spend_cents"] / 100
    campaigns["net_cash"] = campaigns["net_cash_cents"] / 100
    campaigns["mql_rate"] = campaigns["mqls"] / campaigns["leads"].where(campaigns["leads"] > 0)
    campaigns["cost_per_mql"] = campaigns["spend"] / campaigns["mqls"].where((campaigns["mqls"] > 0) & (campaigns["spend"] > 0))
    campaigns["roas"] = campaigns["net_cash"] / campaigns["spend"].where(campaigns["spend"] > 0)
    st.subheader("Campaign performance (lead-creation net cash)")
    st.dataframe(campaigns.sort_values("net_cash", ascending=False)[
        ["campaign_id", "medium", "spend", "leads", "mql_rate", "cost_per_mql", "customers", "net_cash", "roas"]],
        hide_index=True, width="stretch", column_config={
            "spend": st.column_config.NumberColumn("Spend ($)", format="localized", step=1),
            "net_cash": st.column_config.NumberColumn("Net cash ($)", format="localized", step=1),
            "mql_rate": st.column_config.NumberColumn("MQL rate", format="percent"),
            "cost_per_mql": st.column_config.NumberColumn("Cost/MQL ($)", format="localized", step=1),
            "roas": st.column_config.NumberColumn("ROAS", format="%.2f×")})
    model = st.selectbox("Attribution model", MODELS, index=1, format_func=lambda m: m.replace("_", " ").title())
    credit = pd.DataFrame(case["attribution"][model]).fillna({"campaign_id": "(untracked)"})
    credit["net_cash"] = credit["net_cash_cents"] / 100
    st.altair_chart(alt.Chart(credit).mark_bar(cornerRadius=3, color=BLUE).encode(
        y=alt.Y("campaign_id:N", sort="-x", title=None),
        x=alt.X("net_cash:Q", title="Net cash credited", axis=alt.Axis(format="$,.2s")),
        tooltip=["campaign_id:N", alt.Tooltip("net_cash:Q", format="$,.0f")]).properties(height=360),
        width="stretch")
    compare = pd.DataFrame({m: {row["campaign_id"] or "(untracked)": row["net_cash_cents"] / 100
                                for row in case["attribution"][m]} for m in MODELS}).fillna(0)
    with st.expander("All five models side by side (every column sums to the same net cash)"):
        st.dataframe(compare.style.format("${:,.0f}"), width="stretch")

with tabs[3]:
    stages = pd.DataFrame(case["funnel"])
    st.subheader("Lead to renewal")
    st.altair_chart(alt.Chart(stages).mark_bar(cornerRadius=3, color=BLUE).encode(
        y=alt.Y("stage:N", sort=None, title=None), x=alt.X("people:Q", title="People (square-root scale)",
                                                          scale=alt.Scale(type="sqrt")),
        tooltip=["stage:N", "people:Q", alt.Tooltip("from_previous_rate:Q", format=".1%", title="From previous"),
                 alt.Tooltip("median_days_from_previous:Q", title="Median days")]).properties(height=300),
        width="stretch")
    st.dataframe(stages, hide_index=True, width="stretch", column_config={
        "from_previous_rate": st.column_config.NumberColumn("From previous", format="percent")})
    st.subheader("Conversion by the campaign that created the lead")
    st.dataframe(pd.DataFrame(case["funnel_by_campaign"]), hide_index=True, width="stretch", column_config={
        key: st.column_config.NumberColumn(format="percent")
        for key in ("lead_to_mql", "mql_to_call", "call_to_won", "lead_to_customer")})
    st.subheader("Which content produces buyers, not just views?")
    content = pd.DataFrame(case["content"])
    content["cash"] = content["influenced_net_cash_cents"] / 100
    st.altair_chart(alt.Chart(content).mark_circle(size=90, color=BLUE, opacity=.8).encode(
        x=alt.X("views:Q", title="YouTube views"),
        y=alt.Y("cash:Q", title="Influenced net cash", axis=alt.Axis(format="$,.2s")),
        tooltip=["title:N", "topic:N", "views:Q", "engaged_leads:Q", "customers:Q",
                 alt.Tooltip("cash:Q", format="$,.0f")]).properties(height=300), width="stretch")
    by_topic = content.groupby("topic")[["views", "engaged_leads", "customers", "cash"]].sum() \
        .sort_values("cash", ascending=False)
    by_topic["cash_per_1k_views"] = by_topic["cash"] / by_topic["views"] * 1000
    st.dataframe(by_topic, width="stretch", column_config={
        "cash": st.column_config.NumberColumn("Influenced cash ($)", format="localized", step=1),
        "cash_per_1k_views": st.column_config.NumberColumn("Cash per 1k views ($)", format="localized", step=1)})
    st.caption("First identified content touch: descriptive influence, not incrementality.")

with tabs[4]:
    st.subheader("What changed, and which segment explains it")
    recall = pd.DataFrame(case["recall"])
    st.success(f"Ground-truth check: {int(recall['root_cause_correct'].sum())} of {len(recall)} planted incidents "
               "detected with the correct root cause.")
    st.dataframe(recall[["incident_id", "expected", "detected", "root_cause_correct", "days_to_detect"]],
                 hide_index=True, width="stretch")
    episodes = case["episodes"]
    incident_ids = [r["episode_id"] for r in case["recall"] if r["root_cause_correct"]]
    default = next((i for i, e in enumerate(episodes) if e["episode_id"] in incident_ids
                    and e["metric_id"] == "mql_rate"), 0)
    choice = st.selectbox("Anomaly episode", range(len(episodes)), index=default, format_func=lambda i: (
        f"{episodes[i]['label']} {episodes[i]['direction']} · {episodes[i]['window_start']} to "
        f"{episodes[i]['window_end']} · z {episodes[i]['peak_z']}"))
    episode = episodes[choice]
    cols = st.columns(3)
    cols[0].metric("Baseline", episode["baseline_text"])
    cols[1].metric("During episode", episode["current_text"], episode["change_text"])
    cols[2].metric("Peak z-score", episode["peak_z"])
    rate = episode["metric_id"] in ("mql_rate", "utm_completeness")
    fmt = ".0%" if rate else "$,.0f" if episode["metric_id"] in ("spend", "net_cash") else ",.0f"
    st.altair_chart(trend(case["series"][episode["metric_id"]], episode["label"], fmt), width="stretch")
    drivers = pd.DataFrame(episode["drivers"]).head(8)
    drivers["contribution_display"] = drivers["contribution"] * (100 if rate else 1)
    drivers["direction"] = drivers["contribution"].map(lambda v: "Raised the metric" if v >= 0 else "Lowered the metric")
    st.altair_chart(alt.Chart(drivers).mark_bar(cornerRadius=3).encode(
        y=alt.Y("segment:N", sort=alt.EncodingSortField("contribution_display", op="min"), title=None,
                axis=alt.Axis(labelLimit=260)),
        x=alt.X("contribution_display:Q",
                title=f"Contribution to the change ({'percentage points' if rate else 'per day'})"),
        color=alt.Color("direction:N", scale=alt.Scale(domain=["Raised the metric", "Lowered the metric"],
                                                       range=[BLUE, RED]), legend=alt.Legend(title=None, orient="top")),
        tooltip=["segment:N", alt.Tooltip("contribution_display:Q", format=".2f"),
                 alt.Tooltip("share_of_change:Q", format=".0%")]).properties(height=260), width="stretch")
    st.caption("Exact shift-share decomposition: segment contributions (mix + rate effects) sum to the total change.")

with tabs[5]:
    experiment = case["experiment"]
    comparison = experiment["comparison"]
    st.subheader("CTA test: more leads, but more money?")
    st.caption(f"{experiment['hypothesis']} Randomized by visitor; sample-ratio check p = "
               f"{comparison['sample_ratio_p_value']}.")
    variants = pd.DataFrame(experiment["variants"])
    variants["cash_per_visitor"] = variants["net_cash_per_visitor_cents"] / 100
    st.dataframe(variants[["label", "visitors", "leads", "lead_rate", "mqls", "mql_per_lead", "customers",
                           "cash_per_visitor"]], hide_index=True, width="stretch", column_config={
        "lead_rate": st.column_config.NumberColumn("Lead rate", format="percent"),
        "mql_per_lead": st.column_config.NumberColumn("MQL per lead", format="percent"),
        "cash_per_visitor": st.column_config.NumberColumn("Net cash / visitor ($)", format="%.2f")})
    effects = [
        ("Lead rate (pp)", comparison["variant_b_minus_a_lead_rate"] * 100,
         *[v * 100 for v in comparison["lead_rate_difference_95_ci"]]),
        ("MQL per lead (pp)", comparison["variant_b_minus_a_mql_per_lead"] * 100,
         *[v * 100 for v in comparison["mql_per_lead_difference_95_ci"]]),
        ("Cash per visitor ($)", comparison["variant_b_minus_a_cash_per_visitor_cents"] / 100,
         *[v / 100 for v in comparison["cash_per_visitor_bootstrap_95_ci_cents"]]),
    ]
    charts = []
    for metric, estimate, low, high in effects:
        frame = pd.DataFrame([{"est": estimate, "lo": low, "hi": high, "zero": 0.0}])
        zero = alt.Chart(frame).mark_rule(color=GRAY).encode(x="zero:Q")
        interval = alt.Chart(frame).mark_rule(strokeWidth=2, color=BLUE).encode(x=alt.X("lo:Q", title=None), x2="hi:Q")
        dot = alt.Chart(frame).mark_point(filled=True, size=90, color=BLUE).encode(
            x="est:Q", tooltip=[alt.Tooltip("est:Q", format=".2f", title="B − A"),
                                alt.Tooltip("lo:Q", format=".2f", title="95% low"),
                                alt.Tooltip("hi:Q", format=".2f", title="95% high")])
        charts.append((zero + interval + dot).properties(height=50, width=250, title=f"B − A: {metric}"))
    st.altair_chart(alt.hconcat(*charts))
    st.info(f"**Decision:** {comparison['decision']}")

with tabs[6]:
    ops = case["ops"]
    st.subheader("Payment → CRM → community access (last 30 days of webhooks)")
    cols = st.columns(5)
    cols[0].metric("Payment events", ops["events"])
    cols[1].metric("Completed", pct(ops["success_rate"]))
    cols[2].metric("Dead-letter queue", ops["dead_letter"])
    cols[3].metric("Access within 5 min", pct(ops["within_5_minutes"]))
    cols[4].metric("Duplicates absorbed", ops["duplicate_deliveries_absorbed"])
    st.markdown("#### Needs a person: dead-lettered events")
    stuck = pd.DataFrame(case["stuck"])
    if not stuck.empty:
        stuck["amount"] = stuck["amount_cents"].map(usd)
        stuck["impact"] = stuck["has_access"].map({0: "Paid, no access", 1: "Access granted by a later event"})
        stuck["received_at"] = stuck["received_at"].str[:16].str.replace("T", " ")
        st.dataframe(stuck[["event_id", "customer_id", "product_id", "amount", "impact", "received_at", "attempts",
                            "last_error"]], hide_index=True, width="stretch")
    st.caption("Replay is a role-gated operator action on the API (`POST /ops/events/{id}/replay`); this view is read-only.")
    st.markdown("#### Trace a retried event")
    traces = case["traces"]
    event_id = st.selectbox("Event", list(traces), format_func=lambda e: f"{e} · {traces[e]['status']}")
    log = pd.DataFrame(traces[event_id]["attempts_log"])
    log["start"] = pd.to_datetime(log["started_at"])
    log["step"] = log["step_name"] + " #" + log["attempt"].astype(str)
    log["outcome"] = log["status"].map({"succeeded": "Succeeded", "failed": "Failed"})
    st.altair_chart(alt.Chart(log).mark_point(filled=True, size=120).encode(
        x=alt.X("start:T", title="Attempt time (UTC)"), y=alt.Y("step:N", sort=None, title=None),
        color=alt.Color("outcome:N", scale=alt.Scale(domain=["Succeeded", "Failed"], range=[GOOD, CRITICAL]),
                        legend=alt.Legend(title=None, orient="top")),
        shape=alt.Shape("outcome:N", scale=alt.Scale(domain=["Succeeded", "Failed"], range=["circle", "cross"]),
                        legend=None),
        tooltip=["step:N", "outcome:N", "start:T", "duration_ms:Q", "error:N"]).properties(height=40 + 26 * len(log)),
        width="stretch")
    st.caption(f"Trace `{traces[event_id]['trace_id']}` · failed steps retry after 5, 10, 20 and 40 minutes, then "
               "park in the dead-letter queue.")
    renewals = case["renewals"]
    st.markdown("#### Renewal risk")
    cols = st.columns(4)
    cols[0].metric("Active subscriptions", renewals["active_subscriptions"])
    cols[1].metric("Overdue or failed", renewals["high_risk"])
    cols[2].metric(f"Due in {renewals['due_soon_days']} days", renewals["due_soon"])
    cols[3].metric("Canceled", renewals["canceled"])
    if renewals["issues"]:
        st.dataframe(pd.DataFrame(renewals["issues"]), hide_index=True, width="stretch")

with tabs[7]:
    st.subheader("Can we trust the numbers?")
    health = pd.DataFrame([
        ("UTM completeness", quality["utm_completeness"], TARGETS["utm_completeness"]),
        ("Campaign registry match", quality["campaign_registry_match"], TARGETS["campaign_registry_match"]),
        ("CRM owner coverage", quality["crm_owner_completeness"], TARGETS["crm_owner_completeness"]),
        ("Lifecycle integrity (paid journeys)", quality["lifecycle_integrity"], TARGETS["lifecycle_integrity"]),
        ("Payment-to-deal match", quality["deal_payment_reconciliation"], TARGETS["deal_payment_reconciliation"]),
    ], columns=["Check", "Actual", "Target"])
    health["Status"] = health.apply(lambda r: "✓ Pass" if r.Actual >= r.Target else "⚠ Below target", axis=1)
    st.dataframe(health, hide_index=True, width="stretch", column_config={
        "Actual": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
        "Target": st.column_config.NumberColumn(format="percent")})
    cols = st.columns(3)
    cols[0].metric("Duplicate contact rows", quality["duplicate_contact_rows"])
    cols[1].metric("Payments with no deal", quality["unmatched_payment_count"])
    cols[2].metric("Net cash with no campaign", usd(quality["unassigned_net_cash_cents"]))
    st.markdown("#### UTM completeness (rolling 7 days)")
    st.altair_chart(trend(case["series"]["utm_completeness"], "UTM completeness", ".0%"), width="stretch")
    migration = case["migration"]
    st.markdown("#### Legacy CRM → current CRM reconciliation")
    cols = st.columns(5)
    cols[0].metric("Legacy contacts", f"{migration['legacy_contacts']:,}")
    cols[1].metric("Missing after migration", migration["missing_contacts"])
    cols[2].metric("Owner match", pct(migration["owner_match_rate"]))
    cols[3].metric("Source match", pct(migration["source_match_rate"]))
    cols[4].metric("No stage regression", pct(migration["stage_match_rate"]))
    st.caption("The CLI's safe-repair command restores blank owners and placeholder sources from unambiguous legacy "
               "matches and logs every change; duplicates and stage regressions are left for a person.")

with tabs[8]:
    st.subheader("Ask your data")
    st.caption("Questions map to allowlisted, tested metric functions; nothing typed here is executed as SQL. "
               "Topics: " + ", ".join(TOPICS) + ".")
    question = st.text_input("Question", placeholder="Which revenue number is right?")
    if question:
        connection = connect(database)
        try:
            response = ask_data(connection, question)
        finally:
            connection.close()
        st.markdown(response["answer"].replace("$", r"\$"))
        st.caption(f"Metric: {response['metric_id'] or 'none'} · Evidence: {response['source']}")

st.divider()
st.caption("All business data is synthetic and generated for this demonstration. No HubSpot, Stripe, ad account or "
           "live CRM is connected. Source: github.com/KushPatel29/GrowthOps-OS")
