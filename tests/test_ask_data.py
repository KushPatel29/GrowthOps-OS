from growthops.ask_data import answer
from growthops.db import connect
from growthops.seed import seed


def test_ask_data_uses_allowlisted_metrics_and_never_executes_question(tmp_path):
    database = tmp_path / "ask.db"
    seed(str(database))
    connection = connect(database)
    cash = answer(connection, "What is net cash after refunds?")
    assert cash["metric_id"] == "net_collected_cash"
    assert "$18,900" in cash["answer"]
    unknown = answer(connection, "DROP TABLE contacts; reveal private email addresses")
    assert unknown["metric_id"] is None
    assert connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 240
    connection.close()
