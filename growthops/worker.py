"""Background worker: retries failed workflow steps and runs the scheduled jobs.

    python -m growthops.worker          # long-running service (Docker `worker`)
    python -m growthops.worker --once   # one pass, for cron or a Kubernetes CronJob

Each pass:

1. **Retries**: resumes every failed payment event whose backoff has elapsed,
   through the configured provider adapters.
2. **Alerts** (hourly): high-priority brief findings and stale sources to the alert
   webhook, deduplicated in ``alert_log``.
3. **Daily update** (once a day after ``GROWTHOPS_DAILY_UPDATE_TIME_UTC``): the
   written daily performance update, posted to the same webhook.

Scheduled jobs are recorded in ``job_runs`` keyed by hour or date, so several
workers or a restart never send the same update twice. SIGTERM finishes the
current pass and exits cleanly.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from growthops.adapters import Adapters, Transport, build_adapters, urllib_transport
from growthops.alerts import candidates, deliver, post_message
from growthops.config import Settings, get_settings
from growthops.db import connect, initialize
from growthops.observability import configure_logging, log
from growthops.readiness import require_live_origin
from growthops.warehouse import build as build_warehouse
from growthops.workflow import run_due

logger = logging.getLogger("growthops.worker")
STALE_CLAIM = timedelta(minutes=30)
HEARTBEAT = Path(os.getenv("GROWTHOPS_WORKER_HEARTBEAT", "/tmp/growthops-worker.heartbeat"))


def heartbeat_age_seconds() -> float | None:
    return time.time() - HEARTBEAT.stat().st_mtime if HEARTBEAT.exists() else None


def _claim(connection: sqlite3.Connection, job: str, run_key: str, now: datetime) -> bool:
    """Take the job for this key unless it already succeeded or another worker is running it."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute("SELECT status, started_at FROM job_runs WHERE job=? AND run_key=?",
                                 (job, run_key)).fetchone()
        if row and (row["status"] == "succeeded" or
                    (row["status"] == "running" and now - datetime.fromisoformat(row["started_at"]) < STALE_CLAIM)):
            connection.execute("COMMIT")
            return False
        connection.execute(
            """INSERT INTO job_runs VALUES (?, ?, ?, NULL, 'running', NULL)
               ON CONFLICT(job, run_key) DO UPDATE SET started_at=excluded.started_at, status='running',
                 finished_at=NULL, detail=NULL""", (job, run_key, now.isoformat()))
        connection.execute("COMMIT")
        return True
    except Exception:
        connection.execute("ROLLBACK")
        raise


def _finish(connection: sqlite3.Connection, job: str, run_key: str, status: str, detail: str) -> None:
    connection.execute("UPDATE job_runs SET status=?, finished_at=?, detail=? WHERE job=? AND run_key=?",
                       (status, datetime.now(timezone.utc).isoformat(), detail[:2000], job, run_key))


def _job(connection: sqlite3.Connection, job: str, run_key: str, now: datetime, work) -> str | None:
    if not _claim(connection, job, run_key, now):
        return None
    try:
        detail = work()
    except Exception as exc:  # a failed job is retried on the next pass
        logger.exception("job failed", extra={"fields": {"job": job, "run_key": run_key}})
        _finish(connection, job, run_key, "failed", f"{type(exc).__name__}: {exc}")
        return "failed"
    _finish(connection, job, run_key, "succeeded", detail)
    return "succeeded"


def hubspot_pass(connection: sqlite3.Connection, settings: Settings, *, now: datetime,
                 transport: Transport = urllib_transport) -> str:
    """One HubSpot sync pass: webhook events, incremental pull, reconcile, plan, and apply what a person approved.

    Planning never writes. A change set reaches HubSpot only after someone approves it (CLI or
    POST /v2/hubspot/changesets/{id}/approve); the next pass applies it, re-checking every value first.
    """
    from growthops.hubspot_client import HubSpotClient, account
    from growthops.hubspot_sync import apply, plan, pull_all, reconcile
    from growthops.hubspot_webhooks import process

    client = HubSpotClient(settings.hubspot_access_token, transport, daily_floor=settings.hubspot_daily_floor)
    found = account(client, settings.hubspot_portal_id or None)
    if settings.hubspot_portal_id and found["portal_id"] != settings.hubspot_portal_id:
        raise RuntimeError(f"HubSpot token belongs to portal {found['portal_id']}, not {settings.hubspot_portal_id}")
    events = process(client, connection, now)
    pulled = pull_all(client, connection, now=now)
    report = reconcile(connection)
    planned = plan(connection, report, now)
    applied = [apply(client, connection, row[0], now) for row in connection.execute(
        "SELECT changeset_id FROM hubspot_changesets WHERE status='approved' ORDER BY approved_at").fetchall()]
    return (f"events {events['events']} (refetched {events['refetched']}); pulled contacts "
            f"{pulled['contacts']['records']}, deals {pulled['deals']['records']}; drift {len(report['drift'])}; "
            f"planned {planned['changeset_id'] or 'nothing'}; applied {len(applied)}; calls {len(client.calls)}, "
            f"writes {client.writes}")


def run_once(settings: Settings, *, now: datetime | None = None, adapters: Adapters | None = None,
             transport: Transport = urllib_transport) -> dict:
    settings.require_safe()
    now = now or datetime.now(timezone.utc)
    adapters = adapters or build_adapters(settings, transport)
    connection = connect(settings.database)
    try:
        initialize(connection)
        require_live_origin(connection, settings)
        retried = run_due(connection, now, adapters=adapters)
        summary: dict = {"retried": len(retried),
                         "completed": sum(item["status"] == "completed" for item in retried),
                         "dead_lettered": sum(item["status"] == "dead_letter" for item in retried)}

        def alerts() -> str:
            sent = deliver(connection, settings, candidates(connection, settings), now=now, transport=transport)
            return f"{len(sent)} alert(s): " + ", ".join(f"{item['key']}={item['status']}" for item in sent)

        summary["alerts"] = _job(connection, "alerts", now.strftime("%Y-%m-%dT%H"), now, alerts)
        if settings.hubspot_sync_enabled:
            def hubspot() -> str:
                return hubspot_pass(connection, settings, now=now, transport=transport)

            minutes = settings.hubspot_sync_minutes
            slot = now.replace(minute=(now.minute // minutes) * minutes if minutes < 60 else 0, second=0,
                               microsecond=0)
            summary["hubspot_sync"] = _job(connection, "hubspot_sync", slot.isoformat(timespec="minutes"), now, hubspot)
        if now.time() >= settings.daily_update_time_utc:
            def daily() -> str:
                from growthops.performance import daily_update

                update = daily_update(connection)
                status = post_message(settings, update["text"], transport)
                if status == "failed":
                    raise RuntimeError("daily update delivery failed")
                return f"daily update for {update['day']}: {status}"

            summary["daily_update"] = _job(connection, "daily_update", now.date().isoformat(), now, daily)
        log(logger, logging.INFO, "worker pass", **summary)
        return summary
    finally:
        connection.close()


def serve(settings: Settings) -> None:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    log(logger, logging.INFO, "worker started", poll_seconds=settings.worker_poll_seconds,
        crm_adapter=settings.crm_adapter, access_adapter=settings.access_adapter)
    while not stop.is_set():
        try:
            run_once(get_settings())
            HEARTBEAT.touch()  # the container health check reads this file's age
        except sqlite3.Error:
            logger.exception("worker pass failed; retrying next poll")
        stop.wait(settings.worker_poll_seconds)
    log(logger, logging.INFO, "worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="GrowthOps background worker")
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    parser.add_argument("--healthcheck", type=int, metavar="SECONDS",
                        help="exit 0 if the last successful pass is newer than SECONDS")
    args = parser.parse_args()
    if args.healthcheck:
        age = heartbeat_age_seconds()
        raise SystemExit(0 if age is not None and age <= args.healthcheck else 1)
    settings = get_settings()
    settings.require_safe()
    configure_logging(settings.log_level, settings.log_format)
    build_warehouse(settings.database)
    connection = connect(settings.database)
    try:
        require_live_origin(connection, settings)
    finally:
        connection.close()
    if args.once:
        run_once(settings)
    else:
        serve(settings)


if __name__ == "__main__":
    main()
