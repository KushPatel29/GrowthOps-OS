"""Signed payment webhook and read-only operations lookup."""

from __future__ import annotations

import hashlib
import hmac
import os
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from growthops.db import connect, initialize
from growthops.campaign_links import LinkRequest, build_link
from growthops.attribution import summary as attribution_summary
from growthops.funnel import funnel
from growthops.report import executive_brief
from growthops.brief import daily_series, period_brief
from growthops.warehouse import build as build_warehouse
from growthops.migration import audit as migration_audit
from growthops.experiments import analyze as experiment_analysis
from growthops.renewals import monitor as renewal_monitor
from growthops.ai_brief import generate as ai_brief
from growthops.workflow import (EventConflict, PaymentEvent, health as workflow_health, process_payment,
                                replay_dead_letter, trace as workflow_trace)
from growthops.attribution import MODELS
from growthops.diagnostics import detect, incident_recall
from growthops.narrator import narrate
from growthops.brief import findings as brief_findings
from growthops.reconciliation import crm_bridge, four_numbers, platform_bridge, platform_comparison


def database_path() -> str:
    return os.getenv("GROWTHOPS_DATABASE", "data/growthops-sample.db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    build_warehouse(database_path())
    yield


app = FastAPI(title="GrowthOps OS", version="0.2.0", lifespan=lifespan)
assert set(MODELS) == {"first_touch", "lead_creation", "last_non_direct", "u_shaped", "linear"}


def _read(function, *args):
    connection = connect(database_path())
    try:
        return function(connection, *args)
    finally:
        connection.close()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> HTMLResponse:
    html = files("growthops").joinpath("static/dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.post("/campaign-links")
def campaign_link(request: LinkRequest) -> dict:
    connection = connect(database_path())
    try:
        initialize(connection)
        return build_link(connection, request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        connection.close()


@app.get("/metrics/executive")
def executive_metrics() -> dict:
    connection = connect(database_path())
    try:
        initialize(connection)
        return {"period": "all_time_synthetic", **executive_brief(connection)}
    finally:
        connection.close()


@app.get("/metrics/funnel")
def funnel_metrics() -> list[dict]:
    connection = connect(database_path())
    try:
        initialize(connection)
        return funnel(connection)
    finally:
        connection.close()


@app.get("/metrics/attribution/{model}")
def attribution_metrics(model: Literal["first_touch", "lead_creation", "last_non_direct", "u_shaped", "linear"]) -> list[dict]:
    connection = connect(database_path())
    try:
        initialize(connection)
        return attribution_summary(connection, model)
    finally:
        connection.close()


@app.get("/metrics/daily")
def daily_metrics(days: int = Query(default=90, ge=1, le=500)) -> list[dict]:
    connection = connect(database_path())
    try:
        return daily_series(connection, days)
    finally:
        connection.close()


@app.get("/metrics/content")
def content_metrics() -> list[dict]:
    connection = connect(database_path())
    try:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM mart_content_performance ORDER BY influenced_net_cash_cents DESC, content_id"
        ).fetchall()]
    finally:
        connection.close()


@app.get("/metrics/experiments/{experiment_id}")
def experiment_metrics(experiment_id: str) -> dict:
    connection = connect(database_path())
    try:
        return experiment_analysis(connection, experiment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        connection.close()


@app.get("/ops/renewals")
def renewal_risk() -> dict:
    connection = connect(database_path())
    try:
        return renewal_monitor(connection)
    finally:
        connection.close()


@app.get("/metrics/ai-brief")
def evidence_brief() -> dict:
    connection = connect(database_path())
    try:
        return ai_brief(connection)
    finally:
        connection.close()


@app.get("/metrics/brief")
def brief_metrics(days: int = Query(default=7, ge=1, le=30)) -> dict:
    connection = connect(database_path())
    try:
        return period_brief(connection, days)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        connection.close()


@app.post("/webhooks/payments", status_code=202)
async def payment_webhook(request: Request, x_growthops_signature: str = Header(default="")) -> dict:
    body = await request.body()
    secret = os.getenv("GROWTHOPS_WEBHOOK_SECRET", "local-demo-secret").encode()
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_growthops_signature):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        event = PaymentEvent.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    connection = connect(database_path())
    try:
        initialize(connection)
        result = process_payment(connection, event)
    except EventConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        connection.close()
    return result


@app.get("/ops/customers/{customer_id}")
def customer_lookup(customer_id: str) -> dict:
    connection = connect(database_path())
    try:
        initialize(connection)
        contact = connection.execute(
            "SELECT contact_id, current_stage, owner_id FROM contacts WHERE contact_id=?", (customer_id,)
        ).fetchone()
        payments = connection.execute(
            "SELECT payment_id, amount_cents, status FROM payments WHERE customer_id=? ORDER BY paid_at", (customer_id,)
        ).fetchall()
        access = connection.execute(
            "SELECT status, granted_at FROM access_entitlements WHERE customer_id=?", (customer_id,)
        ).fetchone()
        workflows = connection.execute(
            """SELECT event_id, status, attempts, last_error
               FROM processed_events WHERE customer_id=? ORDER BY received_at DESC""",
            (customer_id,),
        ).fetchall()
        paid_new = any(row["status"] == "succeeded" for row in payments)
        stuck = [row["event_id"] for row in workflows if row["status"] == "dead_letter"]
        diagnosis = ("Paid but no community access: replay " + ", ".join(stuck)) if paid_new and not access and stuck \
            else "Paid but no community access: investigate" if paid_new and not access \
            else "OK" if contact else "Unknown customer"
        return {
            "customer_id": customer_id,
            "crm": dict(contact) if contact else None,
            "payments": [dict(row) for row in payments],
            "access": dict(access) if access else None,
            "workflows": [dict(row) for row in workflows],
            "diagnosis": diagnosis,
        }
    finally:
        connection.close()


@app.get("/ops/migration")
def migration_status() -> dict:
    connection = connect(database_path())
    try:
        initialize(connection)
        return migration_audit(connection)
    finally:
        connection.close()


@app.get("/metrics/revenue-truth")
def revenue_truth() -> dict:
    """Which revenue number is right: platform claims, CRM bookings and cash, with exact bridges."""
    return {"summary": _read(four_numbers), "platform_bridge": _read(platform_bridge),
            "crm_bridge": _read(crm_bridge), "by_platform": _read(platform_comparison)}


@app.get("/metrics/anomalies")
def anomalies() -> dict:
    episodes = _read(detect)
    return {"episodes": [{key: value for key, value in episode.items() if key != "drivers"} for episode in episodes],
            "ground_truth_check": _read(incident_recall, episodes)}


@app.get("/metrics/narrative")
def narrative() -> dict:
    """Validated executive narrative (deterministic unless an LLM candidate passes the guardrail)."""
    return narrate(_read(brief_findings))


@app.get("/ops/workflows")
def workflows() -> dict:
    return _read(workflow_health)


@app.get("/ops/events/{event_id}")
def event_trace(event_id: str) -> dict:
    try:
        return _read(workflow_trace, event_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/ops/paid-without-access")
def paid_without_access() -> list[dict]:
    """The support queue: customers who paid for a new product but have no active entitlement."""
    return _read(lambda connection: [dict(row) for row in connection.execute(
        """SELECT p.customer_id, p.payment_id, p.amount_cents, p.paid_at, e.status workflow_status, e.last_error
           FROM payments p LEFT JOIN access_entitlements a ON a.customer_id=p.customer_id
           LEFT JOIN processed_events e ON e.payment_id=p.payment_id
           WHERE p.status='succeeded' AND p.payment_type='new' AND a.customer_id IS NULL
           ORDER BY p.paid_at"""
    ).fetchall()])


@app.post("/ops/events/{event_id}/replay")
def replay_event(event_id: str, x_growthops_ops_token: str = Header(default="")) -> dict:
    """Operator action (role-gated): retry a dead-lettered event with a fresh attempt budget."""
    expected = os.getenv("GROWTHOPS_OPS_TOKEN", "")
    if not expected or not hmac.compare_digest(expected, x_growthops_ops_token):
        raise HTTPException(status_code=403, detail="ops role required")
    connection = connect(database_path())
    try:
        return replay_dead_letter(connection, event_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        connection.close()
