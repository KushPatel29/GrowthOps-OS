from growthops.case_study import OUTPUT, render


def test_committed_case_study_matches_the_code(connection):
    assert OUTPUT.read_text(encoding="utf-8") == render(connection), \
        "docs/case-study.md is stale: run python -m growthops.case_study"
