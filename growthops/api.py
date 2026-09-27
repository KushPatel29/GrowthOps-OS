"""Signed payment webhook and read-only operations lookup."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from importlib.resources import files
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from pydantic import ValidationError

from growthops.adapters import build_adapters
from growthops.ai_brief import generate as ai_brief
from growthops.ask_data import answer as ask_data
from growthops.ask_data import suggestions as ask_suggestions
from growthops.ask_data import usage as ask_usage_summary
from growthops.attribution import MODELS
from growthops.attribution import summary as attribution_summary
from growthops.brief import daily_series, period_brief
from growthops.brief import findings as brief_findings
from growthops.campaign_links import LinkRequest, audit_short_links, build_link
from growthops.config import get_settings
from growthops.db import SCHEMA_VERSION, connect, initialize, schema_version
from growthops.diagnostics import detect, incident_recall
from growthops.email_analytics import (
    deliverability,
    email_performance,
    list_source_mix,
    newsletter_pipeline,
    type_summary,
)
from growthops.experiments import analyze as experiment_analysis
from growthops.freshness import check as freshness_check
from growthops.funnel import funnel
from growthops.hubspot import audit as hubspot_audit
from growthops.hubspot import property_definitions
from growthops.migration import audit as migration_audit
from growthops.narrator import narrate
from growthops.observability import (
    METRICS,
    business_gauges,
    configure_logging,
    log,
    request_id,
)
from growthops.performance import daily_update, paid_efficiency
from growthops.reconciliation import (
    crm_bridge,
    four_numbers,
    platform_bridge,
    platform_comparison,
)
from growthops.renewals import monitor as renewal_monitor
from growthops.report import executive_brief
from growthops.warehouse import build as build_warehouse
from growthops.workflow import (
    EventConflict,
    PaymentEvent,
    process_payment,
    replay_dead_letter,
)
from growthops.workflow import health as workflow_health
from growthops.workflow import trace as workflow_trace

logger = logging.getLogger("growthops.api")
PROTECTED_PREFIXES = ("/metrics", "/ops", "/crm", "/campaign-links", "/ask", "/docs", "/redoc", "/openapi.json")
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                    "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}
MAX_WEBHOOK_BYTES = 128 * 1024


def database_path() -> str:
    return get_settings().database


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.require_safe()  # a misconfigured production deployment stops here
    configure_logging(settings.log_level, settings.log_format)
    build_warehouse(settings.database)
    log(logger, logging.INFO, "api started", env=settings.env, data_mode=settings.data_mode,
        schema_version=SCHEMA_VERSION, crm_adapter=settings.crm_adapter)
    yield


app = FastAPI(title="GrowthOps OS", version="0.3.0", lifespan=lifespan)
if _origins := get_settings().cors_origins:  # browsers only; server-to-server callers use API keys
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET"],
                       allow_headers=["X-API-Key", "Authorization", "X-Request-ID"])
assert set(MODELS) == {"first_touch", "lead_creation", "last_non_direct", "u_shaped", "linear"}


def _authorized(request: Request, keys: list[str]) -> bool:
    supplied = request.headers.get("x-api-key", "")
    bearer = request.headers.get("authorization", "")
    if bearer.lower().startswith("bearer "):
        supplied = supplied or bearer[7:]
    return bool(supplied) and any(hmac.compare_digest(supplied, key) for key in keys)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Request ID, API-key check, access log, latency metrics and security headers for every request."""
    candidate = request.headers.get("x-request-id", "")
    rid = candidate if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", candidate) else uuid.uuid4().hex[:16]
    token = request_id.set(rid)
    started = time.perf_counter()
    settings = get_settings()
    try:
        protected = request.url.path.startswith(PROTECTED_PREFIXES)
        # Lifespan normally blocks an unsafe production start. Keep the same
        # fail-closed behavior if a server disables ASGI lifespan or settings
        # change while the process is running.
        if settings.production and settings.problems():
            response = JSONResponse({"detail": "server configuration unavailable"}, status_code=503)
        elif protected and (settings.production or settings.api_keys) and not _authorized(request, settings.api_keys):
            response = JSONResponse({"detail": "missing or invalid API key"}, status_code=401)
        else:
            try:
                response = await call_next(request)
            except Exception:  # never leak internals; the log keeps the traceback
                logger.exception("unhandled error", extra={"fields": {"path": request.url.path}})
                response = JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        elapsed = time.perf_counter() - started
        METRICS.observe(request.method, route, response.status_code, elapsed)
        response.headers["X-Request-ID"] = rid
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        log(logger, logging.INFO, "request", method=request.method, route=route, status=response.status_code,
            duration_ms=round(elapsed * 1000, 1))
        return response
    finally:
        request_id.reset(token)


def _read(function, *args):
    connection = connect(database_path())
    try:
        return function(connection, *args)
    finally:
        connection.close()


@app.get("/health")
def health() -> dict:
    """Liveness: the process is up. Use /ready for dependencies."""
    return {"status": "ok"}


@app.get("/ready")
def ready() -> JSONResponse:
    """Readiness: the database answers, the schema is current, and each source's freshness."""
    settings = get_settings()
    try:
        connection = connect(settings.database)
        try:
            connection.execute("SELECT 1").fetchone()
            version = schema_version(connection)
            sources = freshness_check(connection, settings)
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return JSONResponse({"status": "unavailable", "database": str(exc)}, status_code=503)
    schema_ok = version == SCHEMA_VERSION
    stale = [item["source"] for item in sources if item["status"] != "fresh"]
    body = {"status": "ready" if schema_ok else "schema_mismatch", "schema_version": version,
            "expected_schema_version": SCHEMA_VERSION, "stale_sources": stale, "sources": sources}
    return JSONResponse(body, status_code=200 if schema_ok else 503)


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> PlainTextResponse:
    """Prometheus exposition: HTTP traffic plus workflow, access and freshness gauges."""
    connection = connect(database_path())
    try:
        gauges = business_gauges(connection, freshness_check(connection))
    finally:
        connection.close()
    return PlainTextResponse(METRICS.render(gauges), media_type="text/plain; version=0.0.4")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> HTMLResponse:
    if get_settings().production:  # the static page calls endpoints without an API key
        raise HTTPException(status_code=404, detail="not available in production")
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


def verify_signature(body: bytes, signature: str, timestamp: str, settings) -> bool:
    """HMAC-SHA256 over ``"{timestamp}.{body}"``, rejected outside the tolerance window (replay protection).

    Development also accepts the legacy body-only signature when no timestamp is sent.
    """
    secret = settings.webhook_secret.encode()
    if timestamp:
        try:
            age = abs(time.time() - int(timestamp))
        except ValueError:
            return False
        if age > settings.webhook_tolerance_seconds:
            return False
        signed = timestamp.encode() + b"." + body
    elif settings.production:
        return False
    else:
        signed = body
    return hmac.compare_digest(hmac.new(secret, signed, hashlib.sha256).hexdigest(), signature)


@app.post("/webhooks/payments", status_code=202)
async def payment_webhook(request: Request, x_growthops_signature: str = Header(default=""),
                          x_growthops_timestamp: str = Header(default="")) -> dict:
    # Read in bounded chunks: Content-Length is optional and cannot be trusted
    # as the sole limit. This caps memory and HMAC work before parsing JSON.
    length = request.headers.get("content-length", "")
    if length.isdecimal() and (len(length) > 20 or int(length) > MAX_WEBHOOK_BYTES):
        raise HTTPException(status_code=413, detail="webhook payload too large")
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="webhook payload too large")
        payload.extend(chunk)
    body = bytes(payload)
    settings = get_settings()
    if not verify_signature(body, x_growthops_signature, x_growthops_timestamp, settings):
        METRICS.increment("webhook_rejected")
        raise HTTPException(status_code=401, detail="invalid or expired signature")
    METRICS.increment("webhook_accepted")
    try:
        event = PaymentEvent.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    connection = connect(database_path())
    try:
        initialize(connection)
        result = process_payment(connection, event, adapters=build_adapters(settings))
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


@app.get("/ask")
def ask(q: str = Query(min_length=1, max_length=300)) -> dict:
    """Keyless, retrieval-grounded question answering over the governed metrics (see growthops.ask_data).

    The response says what it understood (period, platform, campaign, measure) and offers follow-up questions.
    """
    return _read(ask_data, q)


@app.get("/ask/suggestions")
def ask_suggestions_route() -> dict:
    """Starter questions by theme; every one is held by a test to reach a governed answer."""
    return {"themes": ask_suggestions()}


@app.get("/ops/ask-usage")
def ask_usage(days: int = Query(default=7, ge=1, le=90)) -> dict:
    """Questions asked by route (answered, defined, refused) and latency, from the audit log."""
    return _read(ask_usage_summary, days)


@app.get("/metrics/daily-update")
def written_daily_update(day: date | None = None) -> dict:
    """Yesterday vs the trailing week, paid efficiency and what needs attention, as copy-ready text."""
    return _read(daily_update, day)


@app.get("/metrics/paid-efficiency")
def paid_media_efficiency(days: int = Query(default=7, ge=1, le=450),
                          by: Literal["campaign", "platform"] = "campaign") -> list[dict]:
    """CPM, CTR, CPC, CPL, cost per MQL, cost per booked call and net-cash ROAS for the last N days."""
    end = date.fromisoformat(_read(lambda c: c.execute("SELECT MAX(spend_date) FROM ad_spend_daily").fetchone()[0]))
    return _read(paid_efficiency, end - timedelta(days=days - 1), end, by)


@app.get("/metrics/email")
def email_metrics() -> dict:
    return {"by_type": _read(type_summary), "sends": _read(email_performance),
            "newsletter_pipeline": _read(newsletter_pipeline), "deliverability": _read(deliverability),
            "list_source_mix": _read(list_source_mix)}


@app.get("/metrics/link-hygiene")
def link_hygiene() -> dict:
    return _read(audit_short_links)


@app.get("/crm/hubspot/audit")
def hubspot_crm_audit() -> dict:
    """CRM hygiene against the HubSpot mapping, plus the custom-property definitions an import needs."""
    return {"audit": _read(hubspot_audit), "property_definitions": _read(property_definitions)}


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
    expected = get_settings().ops_token
    if not expected or not hmac.compare_digest(expected, x_growthops_ops_token):
        raise HTTPException(status_code=403, detail="ops role required")
    connection = connect(database_path())
    try:
        return replay_dead_letter(connection, event_id, adapters=build_adapters(get_settings()))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        connection.close()
