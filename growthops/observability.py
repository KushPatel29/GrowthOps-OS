"""Structured logs, request IDs and Prometheus-format metrics without extra dependencies."""

from __future__ import annotations

import contextvars
import json
import logging
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)


class JsonFormatter(logging.Formatter):
    """One JSON object per line: what log shippers (CloudWatch, Datadog, Loki) ingest without parsing rules."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id.get(),
        }
        entry.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.handlers[:] = [handler]
    root.setLevel(level)


def log(logger: logging.Logger, level: int, message: str, **fields) -> None:
    logger.log(level, message, extra={"fields": fields})


class Metrics:
    """Thread-safe counters and latency histograms for the API process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self.latency_counts: dict[tuple[str, float], int] = defaultdict(int)
        self.latency_sum: dict[str, float] = defaultdict(float)
        self.latency_total: dict[str, int] = defaultdict(int)
        self.counters: dict[str, int] = defaultdict(int)
        self.started = time.time()

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self.requests[(method, route, status)] += 1
            self.latency_sum[route] += seconds
            self.latency_total[route] += 1
            for bucket in LATENCY_BUCKETS:
                if seconds <= bucket:
                    self.latency_counts[(route, bucket)] += 1

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[name] += amount

    def render(self, gauges: dict[str, tuple[str, list[tuple[dict, float]]]]) -> str:
        lines = ["# HELP growthops_http_requests_total HTTP requests by route and status.",
                 "# TYPE growthops_http_requests_total counter"]
        with self._lock:
            for (method, route, status), count in sorted(self.requests.items()):
                lines.append(f'growthops_http_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}')
            lines += ["# HELP growthops_http_request_seconds Request latency.",
                      "# TYPE growthops_http_request_seconds histogram"]
            for route in sorted(self.latency_total):
                for bucket in LATENCY_BUCKETS:
                    lines.append(f'growthops_http_request_seconds_bucket{{route="{route}",le="{bucket}"}} '
                                 f'{self.latency_counts[(route, bucket)]}')
                lines.append(f'growthops_http_request_seconds_bucket{{route="{route}",le="+Inf"}} {self.latency_total[route]}')
                lines.append(f'growthops_http_request_seconds_sum{{route="{route}"}} {self.latency_sum[route]:.6f}')
                lines.append(f'growthops_http_request_seconds_count{{route="{route}"}} {self.latency_total[route]}')
            for name, value in sorted(self.counters.items()):
                lines += [f"# TYPE growthops_{name}_total counter", f"growthops_{name}_total {value}"]
        lines += ["# TYPE growthops_process_uptime_seconds gauge",
                  f"growthops_process_uptime_seconds {time.time() - self.started:.0f}"]
        for name, (help_text, samples) in gauges.items():
            lines += [f"# HELP growthops_{name} {help_text}", f"# TYPE growthops_{name} gauge"]
            for labels, sample in samples:
                label = ",".join(f'{key}="{val}"' for key, val in labels.items())
                lines.append(f"growthops_{name}{{{label}}} {sample}" if label else f"growthops_{name} {sample}")
        return "\n".join(lines) + "\n"


METRICS = Metrics()


def business_gauges(connection: sqlite3.Connection, freshness: list[dict]) -> dict:
    """Operational state worth alerting on, computed at scrape time."""
    by_status = connection.execute("SELECT status, COUNT(*) FROM processed_events GROUP BY status").fetchall()
    paid_without_access = connection.execute(
        """SELECT COUNT(DISTINCT p.customer_id) FROM payments p
           LEFT JOIN access_entitlements a ON a.customer_id=p.customer_id
           WHERE p.status='succeeded' AND p.payment_type='new' AND a.customer_id IS NULL""").fetchone()[0]
    return {
        "workflow_events": ("Payment workflow events by status.",
                            [({"status": status}, count) for status, count in by_status]),
        "paid_without_access_customers": ("Customers with a successful new payment and no entitlement.",
                                          [({}, paid_without_access)]),
        "source_age_hours": ("Hours since the latest record per source.",
                             [({"source": item["source"]}, item["age_hours"]) for item in freshness
                              if item["age_hours"] is not None]),
        "source_stale": ("1 when a source is older than its freshness SLA.",
                         [({"source": item["source"]}, int(item["status"] != "fresh")) for item in freshness]),
        **_hubspot_gauges(connection),
    }


def _hubspot_gauges(connection: sqlite3.Connection) -> dict:
    """HubSpot sync backlog: webhook events by status, change sets by status, and items that hit a conflict."""
    try:
        events = connection.execute("SELECT status, COUNT(*) FROM hubspot_webhook_events GROUP BY status").fetchall()
        changesets = connection.execute("SELECT status, COUNT(*) FROM hubspot_changesets GROUP BY status").fetchall()
        conflicts = connection.execute(
            "SELECT COUNT(*) FROM hubspot_changeset_items WHERE status IN ('conflict','failed')").fetchone()[0]
    except sqlite3.OperationalError:  # a database from before the sync tables
        return {}
    return {
        "hubspot_webhook_events": ("HubSpot webhook events by status.",
                                   [({"status": status}, count) for status, count in events]),
        "hubspot_changesets": ("HubSpot change sets by status (planned ones wait for approval).",
                               [({"status": status}, count) for status, count in changesets]),
        "hubspot_write_problems": ("HubSpot change set items that failed or hit a conflict.", [({}, conflicts)]),
    }
