import json

from growthops.narrator import EVAL_PATH, narrate, run_evals, validate


def test_guardrail_eval_suite_passes_every_case():
    report = run_evals()
    failures = [item for item in report["results"] if not item["passed"]]
    assert report["cases"] >= 30 and not failures, failures
    labels = {case["expected"] for case in json.loads(EVAL_PATH.read_text())["cases"]}
    assert labels == {"accept", "reject"}


def test_invalid_draft_is_replaced_by_the_deterministic_narrative():
    suite = json.loads(EVAL_PATH.read_text())
    bad = next(case["narrative"] for case in suite["cases"] if case["id"] == "causal_caused")
    result = narrate(suite["findings"], candidate=bad)
    assert result["mode"] == "deterministic_fallback" and result["violations"]
    assert validate(result["narrative"], suite["findings"]) == []
    good = next(case["narrative"] for case in suite["cases"] if case["id"] == "paraphrase_same_numbers")
    assert narrate(suite["findings"], candidate=good)["mode"] == "candidate_validated"


def test_default_narrative_is_deterministic_and_valid():
    suite = json.loads(EVAL_PATH.read_text())
    first, second = narrate(suite["findings"]), narrate(suite["findings"])
    assert first == second and first["mode"] == "deterministic"
    assert validate(first["narrative"], suite["findings"]) == []
