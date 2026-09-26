"""Audit legacy-to-CRM migration and repair only empty, unambiguous fields."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone

from growthops.db import connect, initialize

# Contacts keep progressing after cut-over, so only a stage *regression* is a defect.
STAGE_RANK = {"lead": 0, "mql": 1, "opportunity": 2, "customer": 3}


def audit(connection: sqlite3.Connection) -> dict:
    rows = connection.execute(
        """SELECT l.legacy_id, l.email legacy_email, l.owner_id legacy_owner,
                  l.original_source legacy_source, l.lifecycle_stage legacy_stage,
                  c.contact_id, c.email crm_email, c.owner_id crm_owner,
                  c.original_source crm_source, c.current_stage crm_stage
           FROM legacy_contacts l LEFT JOIN contacts c ON c.legacy_id=l.legacy_id
           ORDER BY l.legacy_id"""
    ).fetchall()
    issues = []
    matched = owner_match = source_match = stage_match = 0
    for row in rows:
        if row["contact_id"] is None:
            issues.append({"legacy_id": row["legacy_id"], "contact_id": None, "issues": ["missing_contact"]})
            continue
        matched += 1
        flags = []
        if row["crm_owner"] == row["legacy_owner"]:
            owner_match += 1
        else:
            flags.append("owner_mismatch")
        if row["crm_source"] == row["legacy_source"]:
            source_match += 1
        else:
            flags.append("source_mismatch")
        if STAGE_RANK.get(row["crm_stage"], 0) >= STAGE_RANK.get(row["legacy_stage"], 0):
            stage_match += 1
        else:
            flags.append("stage_regressed")
        if row["crm_email"].strip().lower() != row["legacy_email"].strip().lower():
            flags.append("email_mismatch")
        if flags:
            issues.append({"legacy_id": row["legacy_id"], "contact_id": row["contact_id"], "issues": flags})
    duplicate_rows = connection.execute(
        "SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM contacts GROUP BY LOWER(TRIM(email)) HAVING COUNT(*)>1)"
    ).fetchone()[0]
    return {
        "legacy_contacts": len(rows),
        "migrated_contacts": matched,
        "missing_contacts": len(rows) - matched,
        "owner_match_rate": round(owner_match / matched, 4) if matched else None,
        "source_match_rate": round(source_match / matched, 4) if matched else None,
        "stage_match_rate": round(stage_match / matched, 4) if matched else None,
        "duplicate_crm_rows": duplicate_rows,
        "issue_count": len(issues),
        "issues": issues,
    }


PLACEHOLDER_SOURCES = ("offline_import",)


def apply_safe_repairs(connection: sqlite3.Connection) -> int:
    """Restore owner/source from the matching legacy row when the CRM value is empty or a
    known import placeholder. Never touches duplicates, stages or anything ambiguous."""
    placeholders = ",".join("?" * len(PLACEHOLDER_SOURCES))
    connection.execute("BEGIN IMMEDIATE")
    try:
        candidates = connection.execute(
            f"""SELECT c.contact_id, c.legacy_id, c.owner_id crm_owner,
                       c.original_source crm_source, l.owner_id legacy_owner,
                       l.original_source legacy_source
                FROM contacts c JOIN legacy_contacts l ON l.legacy_id=c.legacy_id
                WHERE (c.owner_id IS NULL AND l.owner_id IS NOT NULL)
                   OR ((c.original_source IS NULL OR c.original_source IN ({placeholders}))
                       AND l.original_source IS NOT NULL)""",
            PLACEHOLDER_SOURCES,
        ).fetchall()
        count = 0
        for row in candidates:
            for field, current, legacy in (
                ("owner_id", row["crm_owner"], row["legacy_owner"]),
                ("original_source", row["crm_source"], row["legacy_source"]),
            ):
                repairable = current is None or (field == "original_source" and current in PLACEHOLDER_SOURCES)
                if not repairable or legacy is None or legacy == current:
                    continue
                changed = connection.execute(
                    f"UPDATE contacts SET {field}=? WHERE contact_id=? AND {field} IS ?",
                    (legacy, row["contact_id"], current),
                ).rowcount
                if changed:
                    connection.execute(
                        "INSERT INTO migration_repairs VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (f"{row['contact_id']}:{field}", row["contact_id"], row["legacy_id"],
                         field, current, legacy, datetime.now(timezone.utc).isoformat()),
                    )
                    count += 1
        connection.commit()
        return count
    except Exception:
        connection.rollback()
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--apply-safe-repairs", action="store_true")
    args = parser.parse_args()
    connection = connect(args.database)
    initialize(connection)
    repaired = apply_safe_repairs(connection) if args.apply_safe_repairs else 0
    print(json.dumps({"safe_repairs_applied": repaired, **audit(connection)}, indent=2))
    connection.close()


if __name__ == "__main__":
    main()
