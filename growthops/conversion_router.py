"""Consent-gated conversion preview and local outbox; no ad network delivery."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime

from growthops.communications import latest_consent


def preview_conversion(connection: sqlite3.Connection, payment_id: str) -> dict | None:
    """Use settled cash, original lead campaign and latest explicit ads consent."""
    row = connection.execute(
        """WITH lead AS (
             SELECT contact_id, campaign_id, ROW_NUMBER() OVER (
               PARTITION BY contact_id ORDER BY occurred_at, touch_id) rn
             FROM touches WHERE touch_type='lead_creation'
           ), refunded AS (
             SELECT payment_id, SUM(amount_cents) amount_minor FROM refunds GROUP BY payment_id
           )
           SELECT p.payment_id, p.customer_id person_key, p.status, p.amount_cents,
                  p.paid_at, COALESCE(r.amount_minor,0) refunded_minor,
                  l.campaign_id, c.platform, c.registry_valid, c.medium
           FROM payments p
           LEFT JOIN refunded r ON r.payment_id=p.payment_id
           LEFT JOIN lead l ON l.contact_id=p.customer_id AND l.rn=1
           LEFT JOIN campaigns c ON c.campaign_id=l.campaign_id
           WHERE p.payment_id=?""",
        (payment_id,),
    ).fetchone()
    if row is None:
        return None
    consent = latest_consent(connection, row["person_key"], "ads")
    consent_at_purchase = latest_consent(connection, row["person_key"], "ads", row["paid_at"])
    net = row["amount_cents"] - row["refunded_minor"]
    reasons = []
    if row["status"] != "succeeded":
        reasons.append("payment_not_succeeded")
    if net <= 0:
        reasons.append("no_net_cash")
    if row["platform"] not in ("meta", "google", "linkedin") or not row["registry_valid"]:
        reasons.append("no_registered_paid_campaign")
    if consent is None or consent["status"] != "granted":
        reasons.append("ads_consent_not_granted")
    if consent_at_purchase is None or consent_at_purchase["status"] != "granted":
        reasons.append("ads_consent_missing_at_purchase")
    return {"payment_id": payment_id, "person_key": row["person_key"],
            "platform": row["platform"], "campaign_id": row["campaign_id"],
            "conversion_time": row["paid_at"], "amount_minor": max(net, 0),
            "currency": "USD", "consent": consent, "consent_at_purchase": consent_at_purchase,
            "eligible": not reasons, "reasons": reasons,
            "mode": "local_preview_only", "provider_delivered": False}


def queue_conversion(connection: sqlite3.Connection, payment_id: str) -> dict:
    """Create one local intent per payment; never call a provider."""
    preview = preview_conversion(connection, payment_id)
    if preview is None:
        raise LookupError("payment not found")
    if not preview["eligible"]:
        raise ValueError(", ".join(preview["reasons"]))
    conversion_id = "cv_" + hashlib.sha256(
        f"{preview['platform']}:{payment_id}".encode(),
    ).hexdigest()[:24]
    cursor = connection.execute(
        """INSERT OR IGNORE INTO conversion_outbox
           (conversion_id, payment_id, person_key, platform, campaign_id,
            amount_minor, consent_ref, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
        (conversion_id, payment_id, preview["person_key"], preview["platform"],
         preview["campaign_id"], preview["amount_minor"],
         preview["consent"]["consent_id"], datetime.now(UTC).isoformat()),
    )
    return {"conversion_id": conversion_id, "duplicate": cursor.rowcount == 0,
            "status": "queued", "provider_delivered": False}


def conversion_health(connection: sqlite3.Connection) -> dict:
    counts = {row["status"]: row["records"] for row in connection.execute(
        "SELECT status, COUNT(*) records FROM conversion_outbox GROUP BY status",
    )}
    queued_ids = [row[0] for row in connection.execute(
        "SELECT payment_id FROM conversion_outbox WHERE status='queued'",
    )]
    still_eligible = sum(bool((preview_conversion(connection, payment_id) or {})
                              .get("eligible")) for payment_id in queued_ids)
    return {"scope": "local_synthetic_intent", "queued": counts.get("queued", 0),
            "queued_still_eligible": still_eligible,
            "queued_blocked_by_current_evidence": len(queued_ids) - still_eligible,
            "simulated_delivered": counts.get("simulated_delivered", 0),
            "provider_connected": False, "provider_verified_deliveries": 0,
            "policy": "Only positive net settled cash with a registered paid campaign and explicit ads consent at purchase and now can be queued. Recheck eligibility before any future provider dispatch."}
