"""Evidence-grounded executive finding selection with optional LLM ranking."""

from __future__ import annotations

import json
import os
import sqlite3
from urllib import request

from growthops.brief import period_brief
from growthops.experiments import analyze as experiment_analysis
from growthops.renewals import monitor as renewal_monitor
from growthops.report import executive_brief


def evidence_pack(connection: sqlite3.Connection) -> list[dict]:
    executive = executive_brief(connection)
    weekly = period_brief(connection)
    renewals = renewal_monitor(connection)
    experiment = experiment_analysis(connection, "cta_growth_plan", bootstrap_draws=300)
    findings = []
    for index, observation in enumerate(executive["observations"], 1):
        findings.append({
            "id": f"quality_{index}", "category": "measurement",
            "finding": observation["finding"], "evidence": observation["evidence"],
            "action": observation["action"], "source": "measurement_health",
        })
    for index, finding in enumerate(weekly["findings"], 1):
        findings.append({
            "id": f"weekly_{index}", "category": "growth",
            "finding": finding["finding"], "evidence": finding["evidence"],
            "action": finding["investigation"], "source": "mart_growth_daily",
        })
    if renewals["high_risk"]:
        findings.append({
            "id": "renewal_risk", "category": "automation",
            "finding": "Renewals need customer-success review.",
            "evidence": f"{renewals['high_risk']} active subscriptions were overdue or had failed attempts as of {renewals['as_of']}.",
            "action": "Review payment methods and retry history before contacting customers.",
            "source": "subscriptions + renewal_attempts",
        })
    comparison = experiment["comparison"]
    if comparison and comparison["variant_b_minus_a_cash_per_visitor_cents"] < 0:
        findings.append({
            "id": "experiment_cash", "category": "experiment",
            "finding": "Lead lift did not translate into observed cash lift.",
            "evidence": f"Variant B minus A cash per visitor was ${comparison['variant_b_minus_a_cash_per_visitor_cents']/100:.2f}; the bootstrap interval includes zero.",
            "action": "Keep A while collecting a larger revenue sample.",
            "source": "experiment_exposures + payments + refunds",
        })
    return findings


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
    return {
        "mode": mode,
        "synthetic": True,
        "findings": [by_id[item] for item in selected],
        "available_evidence_ids": list(by_id),
        "method": "An optional LLM ranks evidence IDs only. Displayed claims and actions come from validated source facts.",
    }
