"""Versioned, keyless classification and evidence-only sales assistance.

The conversations are deterministic synthetic fixtures. Raw text stays in the
operational database; public responses contain only labels and short evidence
spans. No claim here represents a real sales conversation or an LLM judgment.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime

from growthops.scenario import AS_OF

MODEL_VERSION = "rules-1"
UNKNOWN = "unknown"

PATTERNS: dict[str, tuple[tuple[str, str], ...]] = {
    "pain": (
        ("founder_bottleneck", r"\bfounder (?:is )?(?:the )?bottleneck\b|\beverything depends on me\b"),
        ("team_scaling", r"\b(?:scale|grow) (?:the |our )?team\b|\bhiring managers\b"),
        ("attribution", r"\bcan't (?:trace|attribute) revenue\b|\bwhich campaigns (?:work|produce sales)\b"),
    ),
    "objection": (
        ("budget", r"\b(?:budget|price|too expensive)\b"),
        ("capacity", r"\b(?:implementation capacity|no time to implement|team is stretched)\b"),
        ("timing", r"\b(?:bad timing|wait until next quarter|not this quarter)\b"),
    ),
    "intent": (
        ("high", r"\b(?:ready to start|want to enroll|send the agreement)\b"),
        ("low", r"\b(?:just browsing|not a priority|only researching)\b"),
        ("medium", r"\b(?:exploring options|comparing options|interested in learning)\b"),
    ),
    "timeline": (
        ("this_month", r"\b(?:this month|within 30 days|in the next few weeks)\b"),
        ("this_quarter", r"\b(?:this quarter|within 90 days|before quarter end)\b"),
        ("later", r"\b(?:later this year|next year|after six months)\b"),
    ),
    "authority": (
        ("decision_maker", r"\b(?:i make the decision|i approve the budget|i authorize this purchase|my decision)\b"),
        ("needs_approval", r"\b(?:need board approval|ask my cofounder|manager must approve)\b"),
    ),
    "outcome": (
        ("won", r"\b(?:signed the agreement|completed the purchase|deal is won)\b"),
        ("lost", r"\b(?:chose another vendor|deal is lost|declined the offer)\b"),
        ("open", r"\b(?:decision pending|still evaluating|follow up next week)\b"),
    ),
}


def classify(text: str) -> dict:
    """Label only explicit phrases and return offsets into the source text."""
    labels: dict[str, str] = {}
    evidence: dict[str, dict | None] = {}
    for field, choices in PATTERNS.items():
        matches = []
        for value, pattern in choices:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                matches.append((value, match))
        if len(matches) != 1:
            labels[field] = UNKNOWN
            evidence[field] = None
        else:
            value, match = matches[0]
            labels[field] = value
            evidence[field] = {"start": match.start(), "end": match.end(),
                               "quote": text[match.start():match.end()]}
    return {"model_version": MODEL_VERSION, "labels": labels, "evidence": evidence,
            "mode": "deterministic_rules"}


def seed_conversations(connection: sqlite3.Connection, limit: int = 180) -> int:
    """Plant a separate synthetic conversation stream without changing deal facts."""
    deals = connection.execute(
        """SELECT deal_id, contact_id, stage, product_id, created_at FROM deals
           WHERE product_id != 'community' ORDER BY deal_id LIMIT ?""", (limit,),
    ).fetchall()
    pains = ("The founder is the bottleneck.", "We need to scale our team.",
             "We can't trace revenue to campaigns.")
    objections = ("Budget is tight.", "Implementation capacity is limited.",
                  "This is bad timing.")
    intents = ("We are ready to start.", "We are exploring options.",
               "We are just browsing.")
    timelines = ("We need this within 30 days.", "We need this quarter.",
                 "We may revisit later this year.")
    authorities = ("I make the decision.", "I need board approval.")
    outcomes = {"closed_won": "We signed the agreement.",
                "closed_lost": "We chose another vendor.", "open": "The decision is pending."}
    rows = []
    for index, deal in enumerate(deals):
        transcript = " ".join((pains[index % 3], objections[(index // 3) % 3],
                               intents[(index // 9) % 3], timelines[(index // 27) % 3],
                               authorities[index % 2], outcomes[deal["stage"]]))
        rows.append((f"sc-{deal['deal_id']}", deal["deal_id"], deal["contact_id"],
                     deal["created_at"], transcript, "synthetic_fixture"))
    connection.executemany(
        "INSERT OR IGNORE INTO sales_conversations VALUES (?, ?, ?, ?, ?, ?)", rows,
    )
    return len(rows)


def batch_classify(connection: sqlite3.Connection) -> dict:
    """Persist versioned results; a repeated batch has no duplicate effect."""
    rows = connection.execute(
        """SELECT c.conversation_id, c.transcript FROM sales_conversations c
           LEFT JOIN conversation_classifications x
             ON x.conversation_id=c.conversation_id AND x.model_version=?
           WHERE x.conversation_id IS NULL ORDER BY c.conversation_id""", (MODEL_VERSION,),
    ).fetchall()
    at = datetime.combine(AS_OF, datetime.min.time(), UTC).isoformat()
    results = []
    for row in rows:
        output = classify(row["transcript"])
        results.append((row["conversation_id"], MODEL_VERSION,
                        json.dumps(output["labels"], sort_keys=True),
                        json.dumps(output["evidence"], sort_keys=True), at))
    connection.executemany(
        "INSERT INTO conversation_classifications VALUES (?, ?, ?, ?, ?)", results,
    )
    return {"model_version": MODEL_VERSION, "classified": len(results),
            "total": connection.execute(
                "SELECT COUNT(*) FROM conversation_classifications WHERE model_version=?",
                (MODEL_VERSION,),
            ).fetchone()[0]}


def classification_summary(connection: sqlite3.Connection) -> dict:
    rows = connection.execute(
        "SELECT labels_json FROM conversation_classifications WHERE model_version=?",
        (MODEL_VERSION,),
    ).fetchall()
    counts: dict[str, Counter[str]] = {field: Counter() for field in PATTERNS}
    for row in rows:
        for field, value in json.loads(row["labels_json"]).items():
            counts[field][value] += 1
    return {"mode": "deterministic_rules", "synthetic": True,
            "model_version": MODEL_VERSION, "conversations": len(rows),
            "labels": {field: dict(items) for field, items in counts.items()},
            "raw_transcripts_in_response": False}


def sales_copilot(connection: sqlite3.Connection, person_key: str) -> dict | None:
    """Return grounded prospect context and similar won cases, without invented advice."""
    contact = connection.execute(
        "SELECT contact_id, current_stage, original_source, owner_id FROM contacts WHERE contact_id=?",
        (person_key,),
    ).fetchone()
    if contact is None:
        return None
    conversations = connection.execute(
        """SELECT c.conversation_id, c.deal_id, d.stage, d.product_id,
                  x.labels_json, x.evidence_json
           FROM sales_conversations c JOIN deals d ON d.deal_id=c.deal_id
           LEFT JOIN conversation_classifications x
             ON x.conversation_id=c.conversation_id AND x.model_version=?
           WHERE c.person_key=? ORDER BY c.occurred_at DESC, c.conversation_id""",
        (MODEL_VERSION, person_key),
    ).fetchall()
    current = conversations[0] if conversations else None
    labels = json.loads(current["labels_json"]) if current and current["labels_json"] else None
    evidence = json.loads(current["evidence_json"]) if current and current["evidence_json"] else None
    similar = []
    if current:
        for row in connection.execute(
            """SELECT c.conversation_id, d.deal_id, x.labels_json, x.evidence_json
               FROM sales_conversations c JOIN deals d ON d.deal_id=c.deal_id
               JOIN conversation_classifications x ON x.conversation_id=c.conversation_id
               WHERE d.stage='closed_won' AND d.product_id=? AND c.person_key!=?
                 AND x.model_version=? ORDER BY d.deal_id LIMIT 200""",
            (current["product_id"], person_key, MODEL_VERSION),
        ):
            case_labels = json.loads(row["labels_json"])
            if labels and case_labels["pain"] == labels["pain"] and labels["pain"] != UNKNOWN:
                case_evidence = json.loads(row["evidence_json"])
                similar.append({"deal_id": row["deal_id"],
                                "conversation_id": row["conversation_id"],
                                "matched_pain": labels["pain"],
                                "evidence": case_evidence["pain"]})
            if len(similar) == 3:
                break
    content = [dict(row) for row in connection.execute(
        """SELECT e.content_id, i.title, i.platform, e.occurred_at
           FROM content_engagements e JOIN content_items i ON i.content_id=e.content_id
           WHERE e.contact_id=? ORDER BY e.occurred_at DESC LIMIT 5""", (person_key,),
    )]
    return {"person_key": person_key, "synthetic": True,
            "crm": dict(contact), "conversation_id": current["conversation_id"] if current else None,
            "labels": labels, "evidence": evidence, "recent_content": content,
            "similar_won_cases": similar, "recommendation":
            "Review the cited cases and verify fit with the prospect; no automated sales decision."
            if similar else "No sufficiently similar won-case evidence; review the account directly.",
            "raw_transcript_in_response": False}


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify synthetic conversations with a versioned local rule set")
    parser.add_argument("--database", default="data/growthops-sample.db")
    args = parser.parse_args()
    from growthops.db import connect, initialize

    connection = connect(args.database)
    try:
        initialize(connection)
        print(json.dumps(batch_classify(connection), indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
