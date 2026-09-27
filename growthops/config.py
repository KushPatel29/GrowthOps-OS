"""Runtime settings from the environment, validated once at startup.

Development runs with safe local defaults. ``GROWTHOPS_ENV=production`` turns
every default that would be unsafe on a network (the demo webhook secret, open
read endpoints, an unset operator token) into a startup error, so a
misconfigured deployment fails before it serves a request.

Settings are read on each call rather than cached, so tests and operators can
change the environment without restarting a Python process.
"""

from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

DEMO_WEBHOOK_SECRET = "local-demo-secret"


def _list(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and bool(parsed.hostname) and parsed.port != 0
                and not parsed.username and not parsed.password and not parsed.fragment)
    except ValueError:
        return False


class ConfigError(RuntimeError):
    """The environment does not describe a safe deployment."""


class Settings(BaseModel):
    env: Literal["development", "production"] = "development"
    data_mode: Literal["synthetic", "live"] = "synthetic"
    database: str = "data/growthops-sample.db"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    webhook_secret: str = DEMO_WEBHOOK_SECRET
    webhook_tolerance_seconds: int = Field(default=300, ge=30, le=3600)
    api_keys: list[str] = []
    ops_token: str = ""
    cors_origins: list[str] = []

    crm_adapter: Literal["simulated", "hubspot"] = "simulated"
    hubspot_access_token: str = ""
    access_adapter: Literal["simulated", "webhook"] = "simulated"
    access_webhook_url: str = ""
    access_webhook_secret: str = ""
    messaging_adapter: Literal["simulated", "webhook"] = "simulated"
    messaging_webhook_url: str = ""
    messaging_webhook_secret: str = ""
    adapter_timeout_seconds: float = Field(default=10, gt=0, le=60)

    alert_webhook_url: str = ""
    alert_min_priority: int = 45
    worker_poll_seconds: int = Field(default=30, ge=5, le=3600)
    daily_update_time_utc: time = time(14, 0)  # 07:00 in Kelowna/Vancouver during daylight time
    freshness_sla_hours: int = Field(default=36, ge=1)

    retrieval_mode: Literal["auto", "keyword", "hybrid"] = "auto"

    @property
    def production(self) -> bool:
        return self.env == "production"

    def reference_time(self) -> datetime:
        """'Now' for freshness: the morning after the scenario cut-off for synthetic data."""
        if self.data_mode == "synthetic":
            from growthops.scenario import AS_OF

            return datetime.combine(AS_OF + timedelta(days=1), time(7), timezone.utc)
        return datetime.now(timezone.utc)

    def problems(self) -> list[str]:
        """Everything that would make this configuration unsafe in production."""
        issues = []
        if self.webhook_secret == DEMO_WEBHOOK_SECRET or len(self.webhook_secret) < 32:
            issues.append("GROWTHOPS_WEBHOOK_SECRET must be set to a random value of at least 32 characters")
        if not self.api_keys or any(len(key) < 24 for key in self.api_keys):
            issues.append("GROWTHOPS_API_KEYS must list at least one key of 24+ characters")
        if len(self.ops_token) < 24:
            issues.append("GROWTHOPS_OPS_TOKEN must be set (24+ characters) to allow operator replay")
        # A simulated adapter reports success without touching a provider.
        # Never acknowledge a live payment as fulfilled under that setup.
        if self.crm_adapter == "simulated":
            issues.append("GROWTHOPS_CRM_ADAPTER=hubspot is required in production")
        if self.access_adapter == "simulated":
            issues.append("GROWTHOPS_ACCESS_ADAPTER=webhook is required in production")
        if self.messaging_adapter == "simulated":
            issues.append("GROWTHOPS_MESSAGING_ADAPTER=webhook is required in production")
        if self.crm_adapter == "hubspot" and not self.hubspot_access_token:
            issues.append("HUBSPOT_ACCESS_TOKEN is required when GROWTHOPS_CRM_ADAPTER=hubspot")
        if self.access_adapter == "webhook" and not _https_url(self.access_webhook_url):
            issues.append("GROWTHOPS_ACCESS_WEBHOOK_URL must be an https URL when GROWTHOPS_ACCESS_ADAPTER=webhook")
        if self.access_adapter == "webhook" and (
            len(self.access_webhook_secret) < 32 or self.access_webhook_secret == self.webhook_secret
        ):
            issues.append("GROWTHOPS_ACCESS_WEBHOOK_SECRET must be 32+ characters and distinct from the payment secret")
        if self.messaging_adapter == "webhook" and not _https_url(self.messaging_webhook_url):
            issues.append("GROWTHOPS_MESSAGING_WEBHOOK_URL must be an https URL when GROWTHOPS_MESSAGING_ADAPTER=webhook")
        if self.messaging_adapter == "webhook" and (
            len(self.messaging_webhook_secret) < 32 or self.messaging_webhook_secret in
            (self.webhook_secret, self.access_webhook_secret)
        ):
            issues.append("GROWTHOPS_MESSAGING_WEBHOOK_SECRET must be 32+ characters and distinct from other secrets")
        if self.alert_webhook_url and not _https_url(self.alert_webhook_url):
            issues.append("GROWTHOPS_ALERT_WEBHOOK_URL must be an https URL")
        if self.data_mode == "synthetic":
            issues.append("GROWTHOPS_DATA_MODE=synthetic: production must run on live sources")
        return issues

    def require_safe(self) -> None:
        if self.production and (issues := self.problems()):
            raise ConfigError("Unsafe production configuration:\n- " + "\n- ".join(issues))

    def redacted(self) -> dict:
        """Settings for logs and `ops check-config`, with secrets masked."""
        secret = {"webhook_secret", "api_keys", "ops_token", "hubspot_access_token",
                  "access_webhook_secret", "messaging_webhook_secret"}
        data = self.model_dump(mode="json")
        for key in secret:
            if data.get(key):
                data[key] = "***" if isinstance(data[key], str) else [f"***{len(data[key])} keys"]
        for key in ("alert_webhook_url", "access_webhook_url", "messaging_webhook_url"):
            if data.get(key):
                # Webhook paths and even URL userinfo can contain credentials.
                try:
                    host = urlsplit(data[key]).hostname or "<invalid-host>"
                except ValueError:
                    host = "<invalid-host>"
                data[key] = host + "/***"
        return data


def get_settings() -> Settings:
    env = os.environ
    raw = {
        "env": env.get("GROWTHOPS_ENV", "development"),
        "data_mode": env.get("GROWTHOPS_DATA_MODE", "synthetic"),
        "database": env.get("GROWTHOPS_DATABASE", "data/growthops-sample.db"),
        "log_level": env.get("GROWTHOPS_LOG_LEVEL", "INFO").upper(),
        "log_format": env.get("GROWTHOPS_LOG_FORMAT", "json"),
        "webhook_secret": env.get("GROWTHOPS_WEBHOOK_SECRET", DEMO_WEBHOOK_SECRET),
        "webhook_tolerance_seconds": env.get("GROWTHOPS_WEBHOOK_TOLERANCE_SECONDS", 300),
        "api_keys": _list("GROWTHOPS_API_KEYS"),
        "ops_token": env.get("GROWTHOPS_OPS_TOKEN", ""),
        "cors_origins": _list("GROWTHOPS_CORS_ORIGINS"),
        "crm_adapter": env.get("GROWTHOPS_CRM_ADAPTER", "simulated"),
        "hubspot_access_token": env.get("HUBSPOT_ACCESS_TOKEN", ""),
        "access_adapter": env.get("GROWTHOPS_ACCESS_ADAPTER", "simulated"),
        "access_webhook_url": env.get("GROWTHOPS_ACCESS_WEBHOOK_URL", ""),
        "access_webhook_secret": env.get("GROWTHOPS_ACCESS_WEBHOOK_SECRET", ""),
        "messaging_adapter": env.get("GROWTHOPS_MESSAGING_ADAPTER", "simulated"),
        "messaging_webhook_url": env.get("GROWTHOPS_MESSAGING_WEBHOOK_URL", ""),
        "messaging_webhook_secret": env.get("GROWTHOPS_MESSAGING_WEBHOOK_SECRET", ""),
        "adapter_timeout_seconds": env.get("GROWTHOPS_ADAPTER_TIMEOUT_SECONDS", 10),
        "alert_webhook_url": env.get("GROWTHOPS_ALERT_WEBHOOK_URL", ""),
        "alert_min_priority": env.get("GROWTHOPS_ALERT_MIN_PRIORITY", 45),
        "worker_poll_seconds": env.get("GROWTHOPS_WORKER_POLL_SECONDS", 30),
        "daily_update_time_utc": env.get("GROWTHOPS_DAILY_UPDATE_TIME_UTC", "14:00"),
        "freshness_sla_hours": env.get("GROWTHOPS_FRESHNESS_SLA_HOURS", 36),
        "retrieval_mode": env.get("GROWTHOPS_RETRIEVAL_MODE", "auto"),
    }
    return Settings.model_validate(raw)
