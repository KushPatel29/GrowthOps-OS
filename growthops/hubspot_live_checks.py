"""Live checks of the two HubSpot paths that normally run on events: the webhook refetch and the payment adapter.

Neither writes to HubSpot. ``webhook`` records a delivery for real portal records, signed with a local secret the
way HubSpot signs one, verifies it, and lets the worker's processing refetch those records from the live portal.
``payment`` runs the payment workflow's CRM adapter against an existing customer (a read that must decide no write
is needed) and an unknown contact (which must be refused as permanent rather than created). Results merge into
the sync evidence (``python -m growthops.hubspot_live_checks --evidence``).
"""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
from datetime import UTC, datetime

from growthops.adapters import HubSpotCRM, ProviderError
from growthops.hubspot_client import HubSpotClient, account, load_token
from growthops.hubspot_sync import _merge_evidence
from growthops.hubspot_webhooks import process, record, signature, verify
from growthops.workflow import PaymentEvent


def webhook_check(client: HubSpotClient, connection: sqlite3.Connection, portal_id: str) -> dict:
    """A signed delivery for two real contacts, verified, recorded once, and applied by refetching them."""
    targets = [row[0] for row in connection.execute(
        "SELECT hs_id FROM hubspot_records WHERE object_type='contacts' AND growthops_id IS NOT NULL "
        "AND archived=0 ORDER BY hs_id LIMIT 2")]
    now = datetime.now(UTC)
    run = now.strftime("%Y%m%d%H%M%S")
    events = [{"eventId": f"live-check-{run}-{i}", "portalId": int(portal_id), "subscriptionType":
               "contact.propertyChange", "objectId": int(hs_id), "propertyName": "growthops_tracking_status",
               "propertyValue": "stale value from the event", "occurredAt": int(now.timestamp() * 1000)}
              for i, hs_id in enumerate(targets)]
    body = json.dumps(events).encode()
    secret = secrets.token_hex(24)
    uri = "https://growthops.example.com/v2/webhooks/hubspot"
    timestamp = str(int(now.timestamp() * 1000))
    signed = signature(secret, "POST", uri, body, timestamp)
    accepted = verify(secret, "POST", uri, body, timestamp, signed, now=now)
    tampered = verify(secret, "POST", uri, body.replace(b"stale", b"forged"), timestamp, signed, now=now)
    first = record(connection, events, portal_id, now)
    repeat = record(connection, events, portal_id, now)
    before = client.writes
    processed = process(client, connection, now)
    landed = {hs_id: json.loads(connection.execute(
        "SELECT properties_json FROM hubspot_records WHERE object_type='contacts' AND hs_id=?", (hs_id,)
    ).fetchone()[0]).get("growthops_tracking_status") for hs_id in targets}
    return {"signature_verified": accepted, "tampered_body_rejected": not tampered, "first_delivery": first,
            "repeated_delivery": repeat, "processed": processed,
            "landed_value_is_hubspots_not_the_events": all(v != "stale value from the event" for v in landed.values()),
            "writes": client.writes - before}


def payment_check(token: str, connection: sqlite3.Connection) -> dict:
    """The payment adapter against a real customer (read only, no write needed) and an unknown contact."""
    calls: list[tuple[str, str]] = []

    def recording(method, url, headers, body, timeout):
        from growthops.adapters import urllib_transport

        calls.append((method, url.rsplit("/", 2)[-1]))
        return urllib_transport(method, url, headers, body, timeout)

    customer = connection.execute(
        """SELECT growthops_id FROM hubspot_records WHERE object_type='contacts' AND archived=0
           AND json_extract(properties_json, '$.lifecyclestage')='customer' ORDER BY growthops_id LIMIT 1"""
    ).fetchone()[0]
    adapter = HubSpotCRM(token, 20, recording)
    event = PaymentEvent(event_id="live-check-payment", event_type="payment.succeeded", payment_id="live-check",
                         customer_id=customer, amount_cents=100, paid_at=datetime.now(UTC), product_id="community")
    adapter.mark_customer(event)
    existing_calls = list(calls)
    calls.clear()
    try:
        adapter.mark_customer(event.model_copy(update={"customer_id": "c-not-in-crm"}))
        unknown = "accepted (unexpected)"
    except ProviderError as exc:
        unknown = str(exc)
    return {"existing_customer": {"calls": existing_calls, "wrote": any(c[1] == "update" for c in existing_calls)},
            "unknown_contact": {"calls": list(calls), "outcome": unknown,
                                "wrote": any(c[1] == "update" for c in calls)}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--evidence", action="store_true")
    args = parser.parse_args()
    from growthops.db import connect, initialize

    token = load_token()
    client = HubSpotClient(token)
    connection = connect(args.database)
    try:
        initialize(connection)
        portal_id = account(client)["portal_id"]
        result = {"webhook": webhook_check(client, connection, portal_id),
                  "payment_adapter": payment_check(token, connection),
                  "api": {"calls": len(client.calls), "writes": client.writes}}
    finally:
        connection.close()
    if args.evidence:
        _merge_evidence({"live_checks": result, "live_checks_at": datetime.now(UTC).isoformat(timespec="seconds")})
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
