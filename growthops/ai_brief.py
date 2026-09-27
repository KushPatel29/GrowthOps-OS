"""The evidence brief: the top findings with a validated narrative, fully deterministic.

Findings are ranked by the brief's own priority (customer-facing failures, then
growth and tracking episodes by strength, then retention and data quality), and
every sentence comes from computed evidence. No model and no API key.
"""

from __future__ import annotations

import sqlite3

from growthops.brief import findings as brief_findings
from growthops.narrator import narrate


def evidence_pack(connection: sqlite3.Connection) -> list[dict]:
    return [{"id": item["id"], "category": item["category"], "finding": item["finding"],
             "evidence": item["evidence"], "why": item["why"], "action": item["investigation"],
             "source": item["source"]} for item in brief_findings(connection)]


def generate(connection: sqlite3.Connection, limit: int = 3) -> dict:
    findings = evidence_pack(connection)
    top = findings[:limit]
    return {
        "mode": "deterministic",
        "synthetic": True,
        "findings": top,
        "available_evidence_ids": [item["id"] for item in findings],
        "narrative": narrate([{**item, "investigation": item["action"]} for item in top]),
        "method": "Ranked by the brief's priority rules; every claim and action comes from validated source facts.",
    }
