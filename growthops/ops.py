"""Operator commands: configuration check, online backup, restore and integrity verification.

    python -m growthops.ops check-config
    python -m growthops.ops backup --output backups/
    python -m growthops.ops verify --database backups/growthops-20260926T070000Z.db
    python -m growthops.ops restore --from backups/growthops-20260926T070000Z.db

Backups use SQLite's online backup API, so they are consistent while the API and
worker keep writing, and each one is verified before it is kept. Restore only
accepts a backup that passes verification, refuses to replace an existing database
without ``--force``, and keeps the replaced file as ``*.pre-restore.db``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from growthops.config import get_settings
from growthops.db import SCHEMA_VERSION, connect, schema_version


def backup(database: str, output_dir: str, keep: int = 14) -> Path:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = destination / f"growthops-{stamp}.db"
    source = connect(database)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    problems = verify(str(target))
    if problems:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"backup failed verification: {problems}")
    for old in sorted(destination.glob("growthops-*.db"))[:-keep]:
        old.unlink()
    return target


def verify(database: str) -> list[str]:
    """Integrity, foreign keys and schema version; an empty list means healthy."""
    if not Path(database).exists():
        return [f"{database} does not exist"]
    connection = sqlite3.connect(database)
    try:
        return _verify(connection)
    except sqlite3.DatabaseError as exc:
        return [f"not a readable SQLite database: {exc}"]
    finally:
        connection.close()


def _verify(connection: sqlite3.Connection) -> list[str]:
    problems = []
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        problems.append(f"integrity_check: {integrity}")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        problems.append("foreign_key_check found orphaned rows")
    version = schema_version(connection)
    if version != SCHEMA_VERSION:
        problems.append(f"schema version {version}, expected {SCHEMA_VERSION}")
    return problems


def restore(backup_file: str, database: str, force: bool = False) -> Path:
    problems = verify(backup_file)
    if problems:
        raise RuntimeError(f"refusing to restore an unhealthy backup: {problems}")
    target = Path(database)
    if target.exists() and not force:
        raise RuntimeError(f"{database} exists; stop the API and worker, then pass --force")
    if target.exists():
        shutil.copy2(target, target.with_suffix(".pre-restore.db"))
    for suffix in ("-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    shutil.copy2(backup_file, target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config")
    run = sub.add_parser("backup")
    run.add_argument("--output", default="backups")
    run.add_argument("--keep", type=int, default=14)
    check = sub.add_parser("verify")
    check.add_argument("--database")
    back = sub.add_parser("restore")
    back.add_argument("--from", dest="source", required=True)
    back.add_argument("--force", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "check-config":
        problems = settings.problems()
        print(json.dumps({"settings": settings.redacted(), "production_problems": problems}, indent=2))
        sys.exit(1 if settings.production and problems else 0)
    if args.command == "backup":
        print(backup(settings.database, args.output, args.keep))
    elif args.command == "verify":
        problems = verify(args.database or settings.database)
        print(json.dumps({"healthy": not problems, "problems": problems}))
        sys.exit(1 if problems else 0)
    elif args.command == "restore":
        print(restore(args.source, settings.database, args.force))


if __name__ == "__main__":
    main()
