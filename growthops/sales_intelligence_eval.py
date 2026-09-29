"""Held-out synthetic classifier cases and grounding checks for the sales assistant."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from growthops.sales_intelligence import PATTERNS, UNKNOWN, classify, sales_copilot

CASES = Path(__file__).resolve().parent.parent / "evals" / "sales_classifier_cases.json"
CONTEXTS = (
    ("", ""),
    ("At the discovery call, the prospect said: ", ""),
    ("Sales note: ", " Their current process is documented."),
    ("On Tuesday they explained, ", " We will check back later."),
)


def run_eval(connection: sqlite3.Connection, path: Path = CASES) -> dict:
    cases = json.loads(path.read_text(encoding="utf-8"))
    expected: list[tuple[str, str]] = []
    observed: list[tuple[str, str]] = []
    failures = []
    for index, case in enumerate(cases):
        field, label = case["field"], case["label"]
        if field not in PATTERNS:
            raise ValueError(f"unknown eval field: {field}")
        for variant, (prefix, suffix) in enumerate(CONTEXTS):
            result = classify(prefix + case["text"] + suffix)
            value = result["labels"][field]
            expected.append((field, label))
            observed.append((field, value))
            if value != label:
                failures.append({"case": index, "variant": variant,
                                 "field": field, "expected": label, "observed": value})
            for other in PATTERNS:
                if other != field and result["labels"][other] != UNKNOWN:
                    failures.append({"case": index, "variant": variant,
                                     "field": other, "expected": UNKNOWN,
                                     "observed": result["labels"][other]})
    confusion: dict[str, dict[str, int]] = defaultdict(dict)
    for actual, predicted in zip(expected, observed):
        key = f"{actual[0]}:{actual[1]}"
        guess = predicted[1]
        confusion[key][guess] = confusion[key].get(guess, 0) + 1
    labels = sorted(set(expected) | set(observed))
    f1s = []
    for label in labels:
        tp = sum(a == label and b == label for a, b in zip(expected, observed))
        fp = sum(a != label and b == label for a, b in zip(expected, observed))
        fn = sum(a == label and b != label for a, b in zip(expected, observed))
        denominator = 2 * tp + fp + fn
        f1s.append(2 * tp / denominator if denominator else 0)
    # The assistant is evaluated for grounding and data minimization on a
    # fixed sample of actual seeded synthetic conversations.
    people = [row[0] for row in connection.execute(
        "SELECT DISTINCT person_key FROM sales_conversations ORDER BY person_key LIMIT 20"
    )]
    copilot_checks = 0
    for person_key in people:
        answer = sales_copilot(connection, person_key)
        if answer is None or answer["raw_transcript_in_response"]:
            failures.append({"copilot": person_key, "issue": "missing_or_raw_transcript"})
            continue
        for case in answer["similar_won_cases"]:
            row = connection.execute(
                """SELECT d.stage, c.transcript FROM sales_conversations c
                   JOIN deals d ON d.deal_id=c.deal_id WHERE c.conversation_id=?""",
                (case["conversation_id"],),
            ).fetchone()
            if row is None or row["stage"] != "closed_won" or \
                    case["evidence"]["quote"] not in row["transcript"]:
                failures.append({"copilot": person_key, "issue": "ungrounded_case"})
        copilot_checks += 1
    return {"suite": "sales-intelligence-1", "synthetic": True,
            "base_phrases": len(cases), "classification_cases": len(expected),
            "copilot_checks": copilot_checks, "macro_f1": round(sum(f1s) / len(f1s), 4),
            "failures": failures, "confusion": dict(confusion)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/growthops-sample.db")
    args = parser.parse_args()
    from growthops.db import connect

    connection = connect(args.database)
    try:
        result = run_eval(connection)
        print(json.dumps(result, indent=2))
        if result["failures"] or result["macro_f1"] < 0.85:
            raise SystemExit(1)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
