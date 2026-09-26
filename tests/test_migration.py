from growthops.migration import apply_safe_repairs, audit


def test_migration_audit_and_safe_repairs(connection):
    before = audit(connection)
    assert before["missing_contacts"] > 0 and before["duplicate_crm_rows"] > 0
    assert before["owner_match_rate"] < 1 and before["source_match_rate"] < 1 and before["stage_match_rate"] < 1
    assert before["legacy_contacts"] == before["migrated_contacts"] + before["missing_contacts"]
    repaired = apply_safe_repairs(connection)
    after = audit(connection)
    assert repaired > 0
    assert after["owner_match_rate"] == 1 and after["source_match_rate"] == 1
    # Ambiguous problems are left for a person: duplicates, missing contacts, stage regressions.
    assert after["duplicate_crm_rows"] == before["duplicate_crm_rows"]
    assert after["missing_contacts"] == before["missing_contacts"]
    assert after["stage_match_rate"] == before["stage_match_rate"]
    assert apply_safe_repairs(connection) == 0
    logged = connection.execute("SELECT COUNT(*), SUM(old_value='offline_import') FROM migration_repairs").fetchone()
    assert logged[0] == repaired and logged[1] > 0
