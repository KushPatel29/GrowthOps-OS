"""The HubSpot API client every GrowthOps module uses: paced, retried, budgeted, and safe to log.

Production behaviour, each one pinned by a test:

* **Pacing.** Requests are spaced under the lowest private-app limit (100 per 10 seconds), and CRM search, which
  has its own lower limit, more widely.
* **Retries.** 429 and 5xx responses and network errors are retried with exponential backoff, waiting at least as
  long as a ``Retry-After`` header asks.
* **Daily budget.** HubSpot reports the day's remaining allowance in ``X-HubSpot-RateLimit-Daily-Remaining``. With
  ``daily_floor`` set, the client stops before the allowance falls under it, so a sync never starves the other
  integrations sharing the portal's limit.
* **Circuit breaker.** After ``breaker_threshold`` calls in a row exhaust their retries, the client refuses calls for
  ``breaker_cooldown`` seconds instead of hammering a provider that is down.
* **Redacted errors.** A HubSpot error body can echo the values that failed validation, including email addresses.
  Errors carry HubSpot's category, the property names involved and the correlation ID (what HubSpot support asks
  for), never the response text and never the token.
* **Partial batches.** Batch endpoints answer 207 when some inputs fail; :func:`batch_outcome` splits the results
  from the failures and names the IDs that failed.

The token comes from ``HUBSPOT_ACCESS_TOKEN`` or the git-ignored ``.env`` file and is never printed or stored.
:func:`account` refuses any portal that is not a developer test account or sandbox unless its ID is named, and a
deployment can pin the one portal it may touch with ``GROWTHOPS_HUBSPOT_PORTAL_ID``.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib import error, request

from growthops.adapters import HUBSPOT_API, ProviderError

TEST_ACCOUNT_TYPES = frozenset({"DEVELOPER_TEST", "SANDBOX"})
READ_SUFFIXES = ("/batch/read", "/search", "/lists/search")
# (method, url, headers, body, timeout) -> (status, body) or (status, body, headers)
ClientTransport = Callable[..., tuple]


class HubSpotError(ProviderError):
    """A HubSpot call that failed. Safe to log: category, property names and correlation ID, never values."""

    def __init__(self, status: int, method: str, path: str, *, category: str = "", correlation_id: str = "",
                 properties: tuple[str, ...] = (), note: str = "") -> None:
        self.status, self.method, self.path = status, method, path
        self.category, self.correlation_id, self.properties = category, correlation_id, properties
        kind = "transient" if self.transient else "permanent"
        detail = "; ".join(part for part in (
            category, f"properties {', '.join(properties)}" if properties else "",
            f"correlation {correlation_id}" if correlation_id else "", note) if part)
        super().__init__(f"hubspot: HTTP {status} ({kind}) {method} {path}" + (f": {detail}" if detail else ""))

    @property
    def transient(self) -> bool:
        return self.status == 429 or self.status >= 500 or self.status == 0


class PortalRefused(RuntimeError):
    """The token reaches a portal this run must not touch (not a test account, or not the pinned portal)."""


class BudgetExhausted(ProviderError):
    """HubSpot's daily allowance for this portal is at the configured floor; resume after it resets."""


class CircuitOpen(ProviderError):
    """Recent calls kept failing after retries; the client is cooling down before trying HubSpot again."""


def header_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple:
    """urllib, returning the response headers too (rate-limit counters, Retry-After)."""
    http_request = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers.items())
    except error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items()) if exc.headers else {}
    except (error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"network error: {type(exc).__name__}") from exc


def _int(value: str | None) -> int | None:
    try:
        return int(str(value).strip()) if value is not None else None
    except ValueError:
        return None


def _error_fields(payload: bytes) -> tuple[str, str, tuple[str, ...]]:
    """HubSpot's category, correlation ID and the property names an error names, from its JSON body."""
    try:
        body = json.loads(payload) if payload else {}
    except (ValueError, UnicodeDecodeError):
        return "", "", ()
    if not isinstance(body, dict):
        return "", "", ()
    names: set[str] = set()
    for item in [body, *(body.get("errors") or [])]:
        context = item.get("context") if isinstance(item, dict) else None
        if isinstance(context, dict):
            for key in ("propertyName", "properties", "property"):
                value = context.get(key)
                names.update(value if isinstance(value, list) else [value] if isinstance(value, str) else [])
    return str(body.get("category") or ""), str(body.get("correlationId") or ""), tuple(sorted(names))


def batch_outcome(page: dict) -> tuple[list[dict], list[dict]]:
    """A batch response split into its results and its failures, each failure with a category and the IDs it names."""
    failures = []
    for item in page.get("errors") or []:
        context = item.get("context") or {}
        ids = context.get("ids") or context.get("id") or []
        failures.append({"category": item.get("category", "UNKNOWN"),
                         "ids": [str(i) for i in (ids if isinstance(ids, list) else [ids])]})
    return list(page.get("results") or []), failures


@dataclass
class HubSpotClient:
    """Bearer-token client for HubSpot's public APIs (CRM v3/v4, Properties, Pipelines, Lists, Automation)."""

    token: str
    transport: ClientTransport = header_transport
    base_url: str = HUBSPOT_API
    timeout: float = 60
    min_interval: float = 0.12  # under 100 requests per 10 seconds, the lowest private-app limit
    search_interval: float = 0.3  # CRM search has its own, lower limit
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    max_attempts: int = 6
    daily_floor: int = 0
    breaker_threshold: int = 3
    breaker_cooldown: float = 60
    calls: list[tuple[str, str, int]] = field(default_factory=list)
    rate_limit: dict[str, int] = field(default_factory=dict)
    _last: float = field(default=0.0, repr=False)
    _failures: int = field(default=0, repr=False)
    _open_until: float = field(default=0.0, repr=False)

    @property
    def writes(self) -> int:
        return sum(1 for method, path, _ in self.calls
                   if method in {"POST", "PATCH", "PUT", "DELETE"} and not path.endswith(READ_SUFFIXES))

    def _backoff(self, attempt: int, headers: dict[str, str]) -> float:
        wanted = _int(headers.get("retry-after"))
        return max(float(wanted or 0), float(min(2 ** attempt, 20)))

    def _track(self, headers: dict[str, str]) -> None:
        for header, key in (("x-hubspot-ratelimit-daily-remaining", "daily_remaining"),
                            ("x-hubspot-ratelimit-daily", "daily_max"),
                            ("x-hubspot-ratelimit-remaining", "interval_remaining"),
                            ("x-hubspot-ratelimit-max", "interval_max")):
            value = _int(headers.get(header))
            if value is not None:
                self.rate_limit[key] = value

    def request(self, method: str, path: str, body: dict | None = None, *, raw: bytes | None = None,
                content_type: str = "application/json", missing_ok: bool = False) -> dict | None:
        route = path.split("?")[0]
        if self._open_until > self.clock():
            raise CircuitOpen(f"hubspot: circuit open after {self._failures} failing calls; {method} {route} "
                              "not sent")
        remaining = self.rate_limit.get("daily_remaining")
        if self.daily_floor and remaining is not None and remaining <= self.daily_floor:
            raise BudgetExhausted(f"hubspot: {remaining} daily calls left, at the floor of {self.daily_floor}; "
                                  f"{method} {route} not sent")
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": content_type, "Accept": "application/json"}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        interval = self.search_interval if route.endswith("/search") else self.min_interval
        status = 0
        for attempt in range(self.max_attempts):
            wait = self._last + interval - self.clock()
            if wait > 0:
                self.sleep(wait)
            try:
                result = self.transport(method, self.base_url + path, headers, data, self.timeout)
            except ProviderError:  # a network error: retry like a 5xx
                self._last = self.clock()
                self.calls.append((method, route, 0))
                self.sleep(self._backoff(attempt, {}))
                continue
            status, payload = result[0], result[1]
            response_headers = {str(k).lower(): str(v) for k, v in (result[2] if len(result) > 2 else {}).items()}
            self._last = self.clock()
            self.calls.append((method, route, status))
            self._track(response_headers)
            if status == 429 or status >= 500:
                self.sleep(self._backoff(attempt, response_headers))
                continue
            self._failures = 0
            if status == 404 and missing_ok:
                return None
            if not 200 <= status < 300:
                category, correlation, names = _error_fields(payload)
                raise HubSpotError(status, method, route, category=category, correlation_id=correlation,
                                   properties=names)
            return json.loads(payload) if payload else {}
        self._failures += 1
        if self._failures >= self.breaker_threshold:
            self._open_until = self.clock() + self.breaker_cooldown
        raise HubSpotError(status, method, route, note="still failing after retries")

    def get(self, path: str, **kwargs) -> dict:
        return self.request("GET", path, **kwargs) or {}

    def post(self, path: str, body: dict) -> dict:
        return self.request("POST", path, body) or {}

    def paged(self, path: str, key: str = "results") -> list[dict]:
        items, after = [], None
        while True:
            sep = "&" if "?" in path else "?"
            page = self.get(path + (f"{sep}after={after}" if after else ""))
            items += page.get(key, [])
            after = page.get("paging", {}).get("next", {}).get("after")
            if not after:
                return items

    def search(self, object_type: str, filters: list[dict], properties: list[str], *,
               sorts: list[dict] | None = None, limit: int | None = None) -> list[dict]:
        """Every match of a CRM search, or the first ``limit``. HubSpot stops a search at 10,000 results, so a
        caller that may pass that keeps its filters narrow (the sync advances a watermark instead)."""
        results, after = [], None
        while True:
            body: dict = {"filterGroups": [{"filters": filters}] if filters else [], "properties": properties,
                          "limit": 100,
                          "sorts": sorts or [{"propertyName": "hs_object_id", "direction": "ASCENDING"}]}
            if after:
                body["after"] = after
            page = self.post(f"/crm/v3/objects/{object_type}/search", body)
            results += page.get("results", [])
            after = page.get("paging", {}).get("next", {}).get("after")
            if not after or (limit is not None and len(results) >= limit):
                return results[:limit] if limit is not None else results

    def count(self, object_type: str, filters: list[dict]) -> int:
        body = {"filterGroups": [{"filters": filters}] if filters else [], "properties": ["hs_object_id"], "limit": 1}
        return int(self.post(f"/crm/v3/objects/{object_type}/search", body).get("total", 0))


def load_token(env_file: str | Path = ".env", name: str = "HUBSPOT_ACCESS_TOKEN") -> str:
    """The access token from the environment, else from a git-ignored .env file. Never logged."""
    token = os.environ.get(name, "").strip()
    path = Path(env_file)
    if not token and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                token = value.strip().strip('"').strip("'")
    if not token:
        raise SystemExit(f"{name} is not set (environment or .env). See docs/hubspot-portal.md.")
    return token


def account(client: HubSpotClient, allow_portal: str | None = None) -> dict:
    """Account details, refusing a portal that is not a developer test account or sandbox unless named, and any
    portal other than ``GROWTHOPS_HUBSPOT_PORTAL_ID`` when a deployment pins one."""
    info = client.get("/account-info/v3/details")
    kind, portal_id = info.get("accountType", "UNKNOWN"), str(info.get("portalId", ""))
    pinned = os.environ.get("GROWTHOPS_HUBSPOT_PORTAL_ID", "").strip()
    if pinned and pinned != portal_id:
        raise PortalRefused(f"Refusing portal {portal_id}: GROWTHOPS_HUBSPOT_PORTAL_ID pins this deployment to "
                            f"{pinned}.")
    if kind not in TEST_ACCOUNT_TYPES and allow_portal != portal_id:
        raise PortalRefused(f"Refusing to write synthetic data to a {kind} portal. Use a developer test account, "
                            f"or pass --allow-portal {portal_id} if this portal is meant for it.")
    return {"account_type": kind, "portal_id": portal_id, "time_zone": info.get("timeZone"),
            "currency": info.get("companyCurrency"), "data_hosting": info.get("dataHostingLocation")}
