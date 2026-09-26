"""Evidence-bound executive narrative with a claim validator.

The brief's findings are the only facts a narrative may use. The prose is
written deterministically from those findings; any other draft (for example one
edited by a person) must be structured JSON whose every point cites evidence
IDs, and the validator rejects it outright if it:

* contains a number or date that does not appear in the cited evidence,
* cites an evidence ID that does not exist, or leaves a point uncited,
* asserts causation the evidence cannot support ("caused", "due to", ...),
* promises an outcome ("will increase", "guarantees"), or
* contains an email address.

A rejected draft is never shown: the deterministic narrative is used instead.
The rules are held to a labelled eval set in ``evals/narrative_guardrail_cases.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

MAX_POINTS = 5
NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "evidence_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "points"],
    "additionalProperties": False,
}
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUMBER = re.compile(r"(?<![\w.])[-+−]?\$?\d[\d,]*(?:\.\d+)?(?:%|\s?pp|x|×)?(?![\w])")
CAUSAL = re.compile(
    r"\b(caused|causing|causes|because of|due to|as a result of|led to|resulted in|drove|proves?|proven)\b",
    re.IGNORECASE,
)
PROMISE = re.compile(r"\b(will (increase|decrease|improve|grow|recover|fix|double)|guarantee[sd]?|certainly)\b",
                     re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _numbers(text: str) -> set[str]:
    """Normalized numeric tokens: signs, currency and thousands separators removed; % kept."""
    stripped = DATE.sub(" ", text)
    values = set()
    for token in NUMBER.findall(stripped):
        token = token.replace("−", "-").lstrip("+-").replace("$", "").replace(",", "")
        token = re.sub(r"\s?pp$", "pp", token).replace("×", "x")
        values.add(token)
    return values


def _allowed(text: str) -> set[str]:
    """Evidence numbers, plus each one without its unit (so '9.4 pp' also allows '9.4')."""
    values = _numbers(text)
    return values | {re.sub(r"(%|pp|x)$", "", value) for value in values}


def evidence_text(finding: dict) -> str:
    return " ".join(str(finding.get(key, "")) for key in ("finding", "evidence", "why", "investigation"))


def validate(narrative: dict, findings: list[dict]) -> list[str]:
    """Return a list of violations; an empty list means the narrative may be shown."""
    by_id = {finding["id"]: finding for finding in findings}
    problems: list[str] = []
    points = narrative.get("points")
    if not isinstance(narrative.get("headline"), str) or not isinstance(points, list):
        return ["malformed: headline and points are required"]
    if not 1 <= len(points) <= MAX_POINTS:
        problems.append(f"malformed: {len(points)} points (1-{MAX_POINTS} allowed)")
    all_numbers = set().union(*(_allowed(evidence_text(f)) for f in findings)) if findings else set()
    all_dates = set(DATE.findall(" ".join(evidence_text(f) for f in findings)))
    items = [("headline", narrative["headline"], None)] + [
        (f"point {index}", point.get("text", ""), point.get("evidence_ids")) for index, point in enumerate(points, 1)
    ]
    for label, text, ids in items:
        if ids is not None:
            if not ids:
                problems.append(f"{label}: cites no evidence")
                continue
            unknown = [item for item in ids if item not in by_id]
            if unknown:
                problems.append(f"{label}: unknown evidence id {', '.join(unknown)}")
                continue
            source = " ".join(evidence_text(by_id[item]) for item in ids)
            allowed_numbers, allowed_dates = _allowed(source), set(DATE.findall(source))
        else:
            allowed_numbers, allowed_dates = all_numbers, all_dates
        for number in sorted(_numbers(text) - allowed_numbers):
            problems.append(f"{label}: number {number} is not in the cited evidence")
        for day in sorted(set(DATE.findall(text)) - allowed_dates):
            problems.append(f"{label}: date {day} is not in the cited evidence")
        if match := CAUSAL.search(text):
            problems.append(f"{label}: unsupported causal claim '{match.group(0)}'")
        if match := PROMISE.search(text):
            problems.append(f"{label}: predicts an outcome '{match.group(0)}'")
        if EMAIL.search(text):
            problems.append(f"{label}: contains an email address")
    return problems


def deterministic(findings: list[dict]) -> dict:
    """Template narrative built only from finding text; always passes the validator."""
    top = findings[:MAX_POINTS]
    if not top:
        return {"headline": "No material changes or open issues this morning.",
                "points": [{"text": "All monitored metrics are within their normal range.", "evidence_ids": []}]}
    return {
        "headline": top[0]["finding"],
        "points": [{"text": f"{item['finding']} {item['why']} Next: {item['investigation']}",
                    "evidence_ids": [item["id"]]} for item in top],
    }


def _compact(findings: list[dict]) -> list[dict]:
    keys = ("id", "category", "finding", "evidence", "why", "investigation", "confidence")
    return [{key: item[key] for key in keys if key in item} for item in findings[:8]]


def narrate(findings: list[dict], *, candidate: dict | None = None) -> dict:
    """Pick the narrative to show and report why.

    The deterministic narrative is the default. A ``candidate`` (for example a
    human-edited draft pasted into a tool) is shown only if the validator passes it.
    """
    if candidate is not None:
        violations = validate(candidate, findings)
        if not violations:
            return {"mode": "candidate_validated", "narrative": candidate, "violations": []}
        return {"mode": "deterministic_fallback", "narrative": deterministic(findings), "violations": violations}
    return {"mode": "deterministic", "narrative": deterministic(findings), "violations": []}


EVAL_PATH = Path(__file__).resolve().parent.parent / "evals" / "narrative_guardrail_cases.json"


def run_evals(path: Path = EVAL_PATH) -> dict:
    """Score the validator against labelled cases: each must be accepted or rejected as expected."""
    suite = json.loads(path.read_text(encoding="utf-8"))
    results = []
    for case in suite["cases"]:
        violations = validate(case["narrative"], suite["findings"])
        verdict = "reject" if violations else "accept"
        results.append({"id": case["id"], "expected": case["expected"], "verdict": verdict,
                        "passed": verdict == case["expected"], "violations": violations})
    passed = sum(item["passed"] for item in results)
    return {"cases": len(results), "passed": passed, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--eval", action="store_true", help="score the guardrail eval set")
    args = parser.parse_args()
    if args.eval:
        report = run_evals()
        for item in report["results"]:
            print(f"{'PASS' if item['passed'] else 'FAIL'}  {item['id']:<32} expected {item['expected']:<6} "
                  f"got {item['verdict']}")
        print(f"{report['passed']}/{report['cases']} guardrail cases passed")
        raise SystemExit(0 if report["passed"] == report["cases"] else 1)
    from growthops.brief import findings as brief_findings
    from growthops.db import connect, initialize

    connection: sqlite3.Connection = connect(args.database)
    initialize(connection)
    try:
        print(json.dumps(narrate(brief_findings(connection)), indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
