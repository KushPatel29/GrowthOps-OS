"""Signed payment webhook and read-only operations lookup."""

from __future__ import annotations

import hashlib
import hmac
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import ValidationError

from growthops.db import connect, initialize
from growthops.campaign_links import LinkRequest, build_link
from growthops.workflow import EventConflict, PaymentEvent, process_payment


def database_path() -> str:
    return os.getenv("GROWTHOPS_DATABASE", "data/growthops-sample.db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    connection = connect(database_path())
    initialize(connection)
    connection.close()
    yield


app = FastAPI(title="GrowthOps OS", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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
        return {
            "customer_id": customer_id,
            "crm": dict(contact) if contact else None,
            "payments": [dict(row) for row in payments],
            "access": dict(access) if access else None,
            "workflows": [dict(row) for row in workflows],
        }
    finally:
        connection.close()
