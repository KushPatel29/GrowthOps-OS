"""Public, read-only ScaleLab portfolio dashboard on synthetic data."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from growthops.attribution import summary as attribution_summary
from growthops.brief import daily_series, period_brief
from growthops.db import connect
from growthops.experiments import analyze as experiment_analysis
from growthops.funnel import funnel
from growthops.migration import audit as migration_audit
from growthops.report import executive_brief
from growthops.renewals import monitor as renewal_monitor
from growthops.ai_brief import generate as ai_brief
from growthops.ask_data import answer as ask_data
from growthops.seed import seed
from growthops.warehouse import build


st.set_page_config(page_title="ScaleLab | GrowthOps OS", page_icon="📈", layout="wide")
st.markdown("""<style>
  .block-container {max-width: 1250px; padding-top: 2rem}
  h1,h2,h3 {color:#102a43}
  div[data-testid="stMetric"] {background:#f2f6f9; border:1px solid #cddbe5;
    border-radius:8px; padding:16px}
  .scope {color:#087f78; font-weight:700; letter-spacing:.04em}
</style>""", unsafe_allow_html=True)


@st.cache_resource(show_spinner="Preparing the synthetic case…")
def demo_database() -> str:
    directory = Path(tempfile.mkdtemp(prefix="growthops-streamlit-"))
    database = str(directory / "sample.db")
    seed(database)
    build(database)
    return database


@st.cache_data(ttl=3600)
def load_case(database: str) -> dict:
    connection = connect(database)
    try:
        return {
            "summary": executive_brief(connection),
            "daily": daily_series(connection, 120),
            "brief": period_brief(connection),
            "funnel": funnel(connection),
            "content": [dict(row) for row in connection.execute(
                "SELECT * FROM mart_content_performance ORDER BY influenced_net_cash_cents DESC"
            )],
            "campaigns": [dict(row) for row in connection.execute(
                "SELECT * FROM mart_campaign_performance ORDER BY spend_cents DESC"
            )],
            "migration": migration_audit(connection),
            "renewals": renewal_monitor(connection),
            "ai_brief": ai_brief(connection),
            "experiment": experiment_analysis(connection, "cta_growth_plan"),
            "attribution": {
                model: attribution_summary(connection, model)
                for model in ("lead_creation", "first_touch", "last_non_direct", "u_shaped")
            },
        }
    finally:
        connection.close()


def usd(cents: int | float | None, digits: int = 0) -> str:
    return "—" if cents is None else f"${cents / 100:,.{digits}f}"


case = load_case(demo_database())
metrics = case["summary"]["metrics"]
quality = case["summary"]["measurement_health"]
st.markdown('<span class="scope">SYNTHETIC PORTFOLIO CASE</span>', unsafe_allow_html=True)
st.title("ScaleLab GrowthOps OS")
st.caption("Acquisition → CRM → cash → customer lifecycle. All people, transactions and media records are generated for this demonstration.")

tabs = st.tabs(["Executive Pulse", "Acquisition & attribution", "Funnel & content", "Experiments", "Data & automation health", "Ask your data"])

with tabs[0]:
    st.subheader("What made it to cash?")
    cols = st.columns(4)
    for col, label, value in zip(cols, ("Booked deal value", "Gross collected", "Refunds", "Net collected cash"),
                                 (metrics["booked_revenue_cents"], metrics["gross_collected_cents"],
                                  metrics["refunds_cents"], metrics["net_collected_cents"])):
        col.metric(label, usd(value))
    st.caption("Booked CRM value and captured payment cash are distinct definitions. Refunds reduce net cash.")
    brief = case["brief"]
    st.subheader("Latest paid-spend week")
    st.caption(f"{brief['current']['start']} to {brief['current']['end']} vs {brief['previous']['start']} to {brief['previous']['end']}")
    cols = st.columns(3)
    for col, label, key in zip(cols, ("Spend", "Recorded leads", "Net cash by event date"),
                               ("spend_cents", "leads", "net_cash_cents")):
        formatter = (lambda v: f"{v:,}") if key == "leads" else usd
        col.metric(label, formatter(brief["current"][key]),
                   f"{brief['change_pct'][key]:+.1%}" if brief["change_pct"][key] is not None else None)
    for finding in brief["findings"]:
        st.warning(f"{finding['finding']} {finding['evidence']} {finding['investigation']} Cause remains unconfirmed.")
    daily = pd.DataFrame(case["daily"])
    daily["day"] = pd.to_datetime(daily["day"])
    daily["net_cash_usd"] = daily["net_cash_cents"] / 100
    daily["spend_usd"] = daily["spend_cents"] / 100
    st.line_chart(daily.set_index("day")[["net_cash_usd", "spend_usd"]],
                  color=["#087f78", "#a96612"], y_label="USD")
    st.caption("Daily cash is dated by payments/refunds and is not a same-week acquisition cohort return.")
    st.subheader("Evidence brief")
    for item in case["ai_brief"]["findings"]:
        st.markdown("  \n".join((
            f"**{item['finding']}**",
            item["evidence"],
            f"Action: {item['action']}",
            f"Evidence: `{item['source']}` · `{item['id']}`",
        )))
    st.caption("This public view uses deterministic evidence selection. Optional LLM ranking is available through the backend when configured; it cannot author unsupported claims.")

with tabs[1]:
    cols = st.columns(4)
    cols[0].metric("Paid spend", usd(metrics["spend_cents"]))
    cols[1].metric("Paid leads", f"{metrics['paid_leads']:,}")
    cols[2].metric("Cost per paid lead", usd(metrics["cost_per_lead_cents"]))
    cols[3].metric("Net cash ROAS", f"{metrics['net_cash_roas']:.2f}×")
    st.subheader("Campaign performance")
    campaigns = pd.DataFrame(case["campaigns"])
    for col in ("spend_cents", "net_cash_cents"):
        campaigns[col.replace("_cents", "_usd")] = campaigns[col] / 100
    st.dataframe(campaigns[["campaign_id", "source", "medium", "spend_usd", "leads", "mqls", "net_cash_usd"]],
                 hide_index=True, width="stretch")
    model = st.selectbox("Net cash attribution model", list(case["attribution"]),
                         format_func=lambda value: value.replace("_", " ").title())
    credit = pd.DataFrame(case["attribution"][model])
    credit["net_cash_usd"] = credit["net_cash_cents"] / 100
    st.bar_chart(credit.set_index("campaign_id")["net_cash_usd"], color="#173a5c", y_label="Net cash, USD")
    st.caption("Every model allocates the same total net collected cash; the source credit changes with the rule.")

with tabs[2]:
    st.subheader("Customer journey")
    stages = pd.DataFrame(case["funnel"])
    st.bar_chart(stages.set_index("stage")["people"], color="#087f78", y_label="People")
    st.dataframe(stages, hide_index=True, width="stretch")
    st.subheader("Content to pipeline")
    content = pd.DataFrame(case["content"])
    content["influenced_net_cash_usd"] = content["influenced_net_cash_cents"] / 100
    st.dataframe(content[["title", "views", "clicks", "engaged_leads", "mqls", "calls_booked", "customers", "influenced_net_cash_usd"]],
                 hide_index=True, width="stretch")
    st.caption("First identified content influence is descriptive; views and clicks alone do not prove incrementality.")

with tabs[3]:
    experiment = case["experiment"]
    st.subheader("CTA experiment: lead lift versus cash")
    st.caption(experiment["hypothesis"] + " Balanced synthetic visitor assignments; no live randomization claim.")
    variants = pd.DataFrame(experiment["variants"])
    variants["net_cash_per_visitor_usd"] = variants["net_cash_per_visitor_cents"] / 100
    st.dataframe(variants[["label", "visitors", "leads", "lead_rate", "mqls", "mql_per_lead", "customers", "net_cash_per_visitor_usd"]],
                 hide_index=True, width="stretch")
    st.bar_chart(variants.set_index("label")[["net_cash_per_visitor_usd"]], color="#087f78")
    comparison = experiment["comparison"]
    st.info(comparison["decision"])
    lo, hi = comparison["cash_per_visitor_bootstrap_95_ci_cents"]
    st.caption(f"B − A cash/visitor: {usd(comparison['variant_b_minus_a_cash_per_visitor_cents'], 2)}; bootstrap 95% interval {usd(lo, 2)} to {usd(hi, 2)}. This interval includes zero.")

with tabs[4]:
    st.subheader("Measurement health")
    health = pd.DataFrame([
        ("UTM completeness", quality["utm_completeness"], .95),
        ("Campaign registry", quality["campaign_registry_match"], .98),
        ("CRM owner coverage", quality["crm_owner_completeness"], .99),
        ("Lifecycle integrity", quality["lifecycle_integrity"], .99),
        ("Payment–deal match", quality["deal_payment_reconciliation"], .99),
    ], columns=["Check", "Actual", "Target"])
    health["Status"] = health.apply(lambda row: "Pass" if row.Actual >= row.Target else "Investigate", axis=1)
    st.dataframe(health, hide_index=True, width="stretch",
                 column_config={"Actual": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=1),
                                "Target": st.column_config.NumberColumn(format="%.0f%%")})
    migration = case["migration"]
    st.subheader("Post-migration reconciliation")
    cols = st.columns(4)
    for col, label, value in zip(cols, ("Legacy contacts", "Matched", "Missing", "Duplicate CRM rows"),
                                 (migration["legacy_contacts"], migration["migrated_contacts"],
                                  migration["missing_contacts"], migration["duplicate_crm_rows"])):
        col.metric(label, f"{value:,}")
    st.caption("Safe repairs exist in the local CLI. This public app is read-only and does not modify records.")
    renewals = case["renewals"]
    st.subheader("Renewal risk monitor")
    st.caption(f"As of {renewals['as_of']} · scheduled synthetic community renewals; no payment-provider calls")
    cols = st.columns(3)
    cols[0].metric("Active subscriptions", renewals["active_subscriptions"])
    cols[1].metric("High risk", renewals["high_risk"])
    cols[2].metric("Due in seven days", renewals["due_soon"])
    st.dataframe(pd.DataFrame(renewals["issues"]), hide_index=True, width="stretch")

with tabs[5]:
    st.subheader("Ask your data")
    st.caption("Questions run against allowlisted, governed metric queries. This read-only interface never executes generated SQL.")
    question = st.text_input("Question", placeholder="What is net collected cash after refunds?")
    if question:
        connection = connect(demo_database())
        try:
            response = ask_data(connection, question)
        finally:
            connection.close()
        st.write(response["answer"])
        st.caption(f"Metric: {response['metric_id'] or 'none'} · Evidence: {response['source']}")

st.divider()
st.caption("All business data is synthetic. This public view does not connect to HubSpot, Stripe, ad accounts or a live CRM.")

