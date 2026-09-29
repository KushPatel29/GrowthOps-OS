"""Consent and delivery diagnostics; no messages are sent by this module."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from growthops.email_analytics import deliverability
from growthops.scenario import AS_OF, EMAIL_DOMAIN, NEW_EMAIL_DOMAIN


def seed_consent(connection: sqlite3.Connection) -> int:
    """Add explicit, clearly synthetic decisions for a small buyer sample."""
    buyers = connection.execute(
        """SELECT DISTINCT p.customer_id, c.created_at FROM payments p
           JOIN contacts c ON c.contact_id=p.customer_id
           WHERE p.status='succeeded' ORDER BY p.customer_id LIMIT 180"""
    ).fetchall()
    rows = []
    for index, buyer in enumerate(buyers):
        person = buyer["customer_id"]
        at = buyer["created_at"] or datetime.combine(AS_OF, datetime.min.time(), UTC).isoformat()
        status = ("granted", "denied", "revoked")[index % 3]
        for channel in ("email", "ads", "sms"):
            rows.append((f"consent:{channel}:{person}", person, channel, status,
                         "synthetic_form_fixture", at,
                         f"fixture:synthetic_{channel}_choice:{person}"))
    connection.executemany(
        "INSERT OR IGNORE INTO consent_ledger VALUES (?, ?, ?, ?, ?, ?, ?)", rows,
    )
    return len(rows)


def latest_consent(connection: sqlite3.Connection, person_key: str, channel: str,
                   at: str | None = None) -> dict | None:
    row = connection.execute(
        """SELECT consent_id, status, source, recorded_at, evidence_ref FROM consent_ledger
           WHERE person_key=? AND channel=? AND (? IS NULL OR recorded_at<=?)
           ORDER BY recorded_at DESC, consent_id DESC LIMIT 1""",
        (person_key, channel, at, at),
    ).fetchone()
    return dict(row) if row else None


def communication_health(connection: sqlite3.Connection) -> dict:
    """Separate observed synthetic send outcomes from unmeasured DNS/SMS state."""
    consent = [dict(row) for row in connection.execute(
        """SELECT channel, status, COUNT(*) records FROM (
             SELECT channel, status, ROW_NUMBER() OVER
               (PARTITION BY person_key, channel ORDER BY recorded_at DESC, consent_id DESC) rn
             FROM consent_ledger
           ) WHERE rn=1 GROUP BY channel, status ORDER BY channel, status"""
    )]
    domains = deliverability(connection)
    return {"scope": "synthetic_communication_evidence", "as_of": AS_OF.isoformat(),
            "consent": consent, "email_delivery": domains,
            "domain_configuration": [
                {"domain": EMAIL_DOMAIN, "alignment": "scenario_baseline",
                 "dns_verified": False},
                {"domain": NEW_EMAIL_DOMAIN, "alignment": "scenario_planted_dkim_defect",
                 "dns_verified": False},
            ],
            "sms_delivery": {"status": "unmeasured", "provider_connected": False},
            "policy": "Unknown, denied and revoked decisions never authorize a marketing or ad conversion action."}
