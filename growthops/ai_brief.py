"""Evidence-grounded executive finding selection with optional LLM ranking."""

from __future__ import annotations

import json
import os
import sqlite3
from urllib import request

from growthops.brief import findings as brief_findings
from growthops.narrator import narrate


def evidence_pack(connection: sqlite3.Connection) -> list[dict]:
    """Findings from the Morning Brief, reshaped as the evidence an LLM may rank."""
    return [{"id": item["id"], "category": item["category"], "finding": item["finding"],
             "evidence": item["evidence"], "why": item["why"], "action": item["investigation"],
             "source": item["source"]} for item in brief_findings(connection)]


def select_ids(findings: list[dict], llm_text: str | None = None, limit: int = 3) -> tuple[list[str], str]:
    fallback = [finding["id"] for finding in findings[:limit]]
    if llm_text is None:
        return fallback, "deterministic"
    try:
        parsed = json.loads(llm_text)
        selected = parsed["finding_ids"]
        allowed = {finding["id"] for finding in findings}
        if not isinstance(selected, list) or not 1 <= len(selected) <= limit:
            raise ValueError("invalid selection length")
        if len(set(selected)) != len(selected) or any(item not in allowed for item in selected):
            raise ValueError("unsupported or duplicate evidence ID")
        return selected, "llm_ranked"
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return fallback, "deterministic_fallback"


def _llm_rank(findings: list[dict]) -> str | None:
    url = os.getenv("GROWTHOPS_LLM_URL")
    key = os.getenv("GROWTHOPS_LLM_API_KEY")
    model = os.getenv("GROWTHOPS_LLM_MODEL")
    if not all((url, key, model)):
        return None
    compact = [{"id": item["id"], "category": item["category"], "finding": item["finding"],
                "evidence": item["evidence"]} for item in findings]
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "Select up to three evidence IDs for a marketing executive. Return only JSON: {\"finding_ids\":[\"id\"]}. Never add claims or numbers."},
            {"role": "user", "content": json.dumps(compact)},
        ],
    }).encode()
    http_request = request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
    })
    try:
        with request.urlopen(http_request, timeout=15) as response:
            payload = json.load(response)
        return payload["choices"][0]["message"]["content"]
    except (OSError, KeyError, IndexError, TypeError, ValueError):
        return None


def generate(connection: sqlite3.Connection, *, llm_text: str | None = None,
             use_provider: bool = False) -> dict:
    findings = evidence_pack(connection)
    provider_text = _llm_rank(findings) if use_provider else None
    selected, mode = select_ids(findings, llm_text if llm_text is not None else provider_text)
    if use_provider and provider_text is None and llm_text is None:
        mode = "deterministic_provider_unavailable"
    by_id = {finding["id"]: finding for finding in findings}
    narrative = narrate([{**item, "investigation": item["action"]} for item in (by_id[i] for i in selected)])
    return {
        "mode": mode,
        "synthetic": True,
        "findings": [by_id[item] for item in selected],
        "available_evidence_ids": list(by_id),
        "narrative": narrative,
        "method": "An optional LLM ranks evidence IDs only. Displayed claims and actions come from validated source facts.",
    }
