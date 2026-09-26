from datetime import date

from growthops.db import connect
from growthops.renewals import monitor
from growthops.seed import seed


def test_renewal_monitor_prioritizes_overdue_and_due_soon(tmp_path):
    database = tmp_path / "renewals.db"
    seed(str(database))
    connection = connect(database)
    result = monitor(connection, date(2026, 9, 26))
    assert result["active_subscriptions"] == 15
    assert result["high_risk"] > 0
    assert result["due_soon"] > 0
    assert result["high_risk"] + result["due_soon"] == len(result["issues"])
    assert any(issue["failed_attempts"] for issue in result["issues"])
    connection.close()
