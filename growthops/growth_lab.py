"""Cohort economics, transparent planning and trust checks for synthetic data."""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel, Field, model_validator

from growthops.db import SCHEMA_VERSION, schema_version
from growthops.freshness import check as freshness_check
from growthops.report import metrics
from growthops.scenario import AS_OF


def customer_economics(connection: sqlite3.Connection) -> dict:
    """Observed cash economics, with no unearned lifetime or causal claim."""
    base = metrics(connection)
    paid_cohort = connection.execute(
        """WITH lead_campaign AS (
             SELECT contact_id, campaign_id, ROW_NUMBER() OVER
               (PARTITION BY contact_id ORDER BY occurred_at, touch_id) rn
             FROM touches WHERE touch_type='lead_creation'
           ), refunds_by_payment AS (
             SELECT payment_id, SUM(amount_cents) amount_cents FROM refunds GROUP BY payment_id
           )
           SELECT COUNT(DISTINCT p.customer_id) buyers,
                  SUM(p.amount_cents-COALESCE(r.amount_cents,0)) net_minor
           FROM payments p
           JOIN lead_campaign l ON l.contact_id=p.customer_id AND l.rn=1
           JOIN campaigns c ON c.campaign_id=l.campaign_id
           LEFT JOIN refunds_by_payment r ON r.payment_id=p.payment_id
           WHERE p.status='succeeded' AND c.medium IN ('paid_social','paid_search')"""
    ).fetchone()
    paid_buyers = paid_cohort["buyers"]
    active_subscriptions = connection.execute(
        "SELECT COUNT(*) FROM subscriptions WHERE status='active'"
    ).fetchone()[0]
    annual_price = connection.execute(
        "SELECT list_price_cents FROM products WHERE product_id='community'"
    ).fetchone()[0]
    due = connection.execute(
        "SELECT COUNT(*) FROM subscriptions WHERE DATE(started_at)<=DATE(?,'-365 days')",
        (AS_OF.isoformat(),),
    ).fetchone()[0]
    renewed = connection.execute(
        """SELECT COUNT(DISTINCT s.subscription_id) FROM subscriptions s
           JOIN renewal_attempts a ON a.subscription_id=s.subscription_id
           WHERE a.outcome='succeeded' AND DATE(a.attempted_at)<=?
             AND DATE(s.started_at)<=DATE(?,'-365 days')""",
        (AS_OF.isoformat(), AS_OF.isoformat()),
    ).fetchone()[0]
    customers = base["customers"]
    return {
        "scope": "observed_full_synthetic_scenario", "as_of": AS_OF.isoformat(),
        "currency": "USD", "paid_spend_minor": base["spend_cents"],
        "paid_acquired_buyers": paid_buyers,
        "paid_cohort_net_cash_minor": paid_cohort["net_minor"] or 0,
        "paid_cac_minor": round(base["spend_cents"] / paid_buyers) if paid_buyers else None,
        "customers": customers, "net_collected_minor": base["net_collected_cents"],
        "observed_net_cash_per_customer_minor": round(base["net_collected_cents"] / customers)
        if customers else None,
        "paid_cohort_observed_cash_to_cac_ratio": round(
            (paid_cohort["net_minor"] or 0) / base["spend_cents"], 2)
        if paid_buyers and base["spend_cents"] else None,
        "active_annual_subscriptions": active_subscriptions,
        "contracted_arr_minor": active_subscriptions * annual_price,
        "contracted_mrr_minor": round(active_subscriptions * annual_price / 12),
        "renewal_cohort_due": due, "renewal_cohort_succeeded": renewed,
        "observed_renewal_rate": round(renewed / due, 4) if due else None,
        "unsupported_metrics": ["projected_ltv", "cac_payback", "nrr", "grr", "arpu"],
        "caveat": "ARR/MRR are contracted annual subscription run rate, not cash. The net-cash ratio is observed, not lifetime LTV:CAC."
    }


COHORT_DIMENSIONS = {
    "acquisition_month": "substr(c.created_at,1,7)",
    "source": "COALESCE(camp.source,'(unknown)')",
    "campaign": "COALESCE(lead.campaign_id,'(unattributed)')",
    "owner": "COALESCE(c.owner_id,'(unassigned)')",
}


def funnel_cohorts(connection: sqlite3.Connection, dimension: str = "acquisition_month") -> dict:
    """Person-grain cohort funnel; cash stays on the original acquisition cohort."""
    if dimension not in COHORT_DIMENSIONS:
        raise ValueError("unsupported cohort dimension")
    expression = COHORT_DIMENSIONS[dimension]
    rows = connection.execute(
        f"""WITH lead AS (
             SELECT contact_id, campaign_id,
                    ROW_NUMBER() OVER (PARTITION BY contact_id ORDER BY occurred_at, touch_id) rn
             FROM touches WHERE touch_type='lead_creation'
           ), stage AS (
             SELECT contact_id, MAX(stage='mql') mql, MAX(stage='call_booked') meeting,
                    MAX(stage='opportunity') opportunity, MAX(stage='closed_won') won
             FROM lifecycle_events GROUP BY contact_id
           ), qualified AS (
             SELECT d.contact_id, MAX(q.status='qualified') qualified
             FROM deals d JOIN deal_qualification q ON q.deal_id=d.deal_id
             GROUP BY d.contact_id
           ), refunds_by_payment AS (
             SELECT payment_id, SUM(amount_cents) amount_cents FROM refunds GROUP BY payment_id
           ), cash AS (
             SELECT p.customer_id, SUM(p.amount_cents-COALESCE(r.amount_cents,0)) net_minor
             FROM payments p LEFT JOIN refunds_by_payment r ON r.payment_id=p.payment_id
             WHERE p.status='succeeded' GROUP BY p.customer_id
           )
           SELECT {expression} cohort, COUNT(*) leads,
                  SUM(COALESCE(stage.mql,0)) mqls,
                  SUM(COALESCE(stage.meeting,0)) meetings,
                  SUM(COALESCE(qualified.qualified,0)) qualified_people,
                  SUM(COALESCE(stage.opportunity,0)) opportunities,
                  SUM(COALESCE(stage.won,0)) won_people,
                  SUM(cash.net_minor IS NOT NULL) customers,
                  COALESCE(SUM(cash.net_minor),0) net_cash_minor
           FROM contacts c LEFT JOIN lead ON lead.contact_id=c.contact_id AND lead.rn=1
           LEFT JOIN campaigns camp ON camp.campaign_id=lead.campaign_id
           LEFT JOIN stage ON stage.contact_id=c.contact_id
           LEFT JOIN qualified ON qualified.contact_id=c.contact_id
           LEFT JOIN cash ON cash.customer_id=c.contact_id
           GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    return {"dimension": dimension, "cohort_basis": "contact created/first lead touch",
            "scope": "full_synthetic_scenario", "as_of": AS_OF.isoformat(),
            "rows": [dict(row) for row in rows]}


class ScenarioInputs(BaseModel):
    spend_minor: int = Field(ge=0, le=100_000_000)
    cost_per_lead_minor: int = Field(gt=0, le=10_000_000)
    mql_rate: float = Field(ge=0, le=1)
    qualification_rate: float = Field(ge=0, le=1)
    win_rate: float = Field(ge=0, le=1)
    average_deal_minor: int = Field(ge=0, le=100_000_000)
    collection_rate: float = Field(ge=0, le=1)
    refund_rate: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def sensible_rates(self) -> ScenarioInputs:
        if self.collection_rate + self.refund_rate > 1:
            raise ValueError("collection and refund rates cannot sum above one")
        return self


def scenario_plan(inputs: ScenarioInputs) -> dict:
    """A deterministic sensitivity calculator, never a forecast or causal estimate."""
    leads = inputs.spend_minor / inputs.cost_per_lead_minor
    mqls = leads * inputs.mql_rate
    qualified = mqls * inputs.qualification_rate
    won = qualified * inputs.win_rate
    pipeline = qualified * inputs.average_deal_minor
    bookings = won * inputs.average_deal_minor
    net_cash = bookings * (inputs.collection_rate - inputs.refund_rate)
    return {"mode": "user_assumption_scenario", "currency": "USD", "inputs": inputs.model_dump(),
            "expected_leads": round(leads, 1), "expected_mqls": round(mqls, 1),
            "expected_qualified_opportunities": round(qualified, 1),
            "expected_won_deals": round(won, 1),
            "pipeline_created_minor": round(pipeline),
            "booked_minor": round(bookings), "net_collected_minor": round(net_cash),
            "implied_cac_minor": round(inputs.spend_minor / won) if won else None,
            "implied_net_cash_roas": round(net_cash / inputs.spend_minor, 2)
            if inputs.spend_minor else None,
            "caveat": "Arithmetic on user assumptions. It does not predict performance or measure incremental lift."}


def trust_center(connection: sqlite3.Connection) -> dict:
    """Show data and schema evidence, with unavailable operational signals named."""
    from growthops.control_plane import crm_health, quality_queue

    freshness = freshness_check(connection)
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    marts = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view') AND name LIKE 'mart_%'"
    ).fetchone()[0]
    quality = quality_queue(connection, limit=5)
    return {"scope": "local_synthetic_store", "as_of": AS_OF.isoformat(),
            "schema_version": schema_version(connection), "expected_schema_version": SCHEMA_VERSION,
            "source_freshness": freshness, "foreign_key_violations": len(violations),
            "materialized_marts": marts, "crm_health": crm_health(connection),
            "quality_rules": quality["rule_counts"],
            "unavailable_signals": ["dashboard_usage", "last_certification", "live_dbt_test_status"],
            "note": "Freshness uses the configured reference clock. CI validates dbt and metric parity; this view does not ingest GitHub Actions results."}
