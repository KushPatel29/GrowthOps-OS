from growthops.db import connect
from growthops.migration import apply_safe_repairs, audit
from growthops.seed import seed


def test_migration_audit_and_safe_repairs(tmp_path):
    database = tmp_path / "migration.db"
    seed(str(database))
    connection = connect(database)
    before = audit(connection)
    assert before["legacy_contacts"] == 243
    assert before["missing_contacts"] == 3
    assert before["duplicate_crm_rows"] == 10
    assert before["owner_match_rate"] < 1
    assert before["source_match_rate"] < 1

    repaired = apply_safe_repairs(connection)
    after = audit(connection)
    assert repaired > 0
    assert after["owner_match_rate"] == 1
    assert after["source_match_rate"] == 1
    assert after["missing_contacts"] == 3
    assert after["duplicate_crm_rows"] == 10
    assert apply_safe_repairs(connection) == 0
    assert connection.execute("SELECT COUNT(*) FROM migration_repairs").fetchone()[0] == repaired
    connection.close()
