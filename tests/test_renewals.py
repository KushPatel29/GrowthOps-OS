from datetime import date

from growthops.renewals import monitor


def test_renewal_monitor_prioritizes_overdue_and_due_soon(connection):
    result = monitor(connection)
    assert result["as_of"] == "2026-09-25"
    assert result["active_subscriptions"] > 100
    assert result["high_risk"] + result["due_soon"] == len(result["issues"])
    assert all(i["severity"] == "high" for i in result["issues"] if i["days_to_due"] <= 0 or i["failed_attempts"])
    later = monitor(connection, date(2026, 12, 31), due_soon_days=0)
    assert later["high_risk"] >= result["high_risk"]
