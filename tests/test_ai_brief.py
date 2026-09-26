from growthops.ai_brief import generate, select_ids
from growthops.db import connect
from growthops.seed import seed
from growthops.warehouse import build


def test_ai_brief_rejects_unsupported_claims_and_renders_source_facts(tmp_path):
    database = tmp_path / "brief.db"
    seed(str(database))
    build(str(database))
    connection = connect(database)
    generated = generate(connection, llm_text='{"finding_ids":["invented_claim"]}')
    assert generated["mode"] == "deterministic_fallback"
    assert all(item["source"] and item["evidence"] for item in generated["findings"])
    selected, mode = select_ids([{"id": "valid"}], '{"finding_ids":["valid"]}')
    assert selected == ["valid"] and mode == "llm_ranked"
    connection.close()
