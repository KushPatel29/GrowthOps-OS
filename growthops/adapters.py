"""Provider adapters for the workflow's external side effects.

Each workflow step that touches another system calls one adapter method. Every
call is idempotent on the provider side, because the engine retries a step
whose local commit failed after the provider already accepted it:

* HubSpot: batch *upsert* keyed on the unique ``growthops_contact_id`` property,
  so a repeat sets the same lifecycle stage again.
* Access and messaging webhooks: an ``Idempotency-Key`` of ``{event_id}:{step}``
  and an HMAC signature the receiver can check.

Transient failures (timeouts, HTTP 429 and 5xx) and permanent ones (other 4xx)
both raise :class:`ProviderError`; the engine retries with backoff and then
dead-letters, and the error text says which kind it was. The simulated adapters
keep the synthetic scenario deterministic and are the default.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol
from urllib import error, request

from growthops.config import ConfigError, Settings

if TYPE_CHECKING:
    from growthops.lifecycle import LifecycleEvent
    from growthops.workflow import PaymentEvent

HUBSPOT_API = "https://api.hubapi.com"
# (method, url, headers, body, timeout) -> (status, response body)
Transport = Callable[[str, str, dict, bytes | None, float], tuple[int, bytes]]


class ProviderError(Exception):
    """A provider call failed; the workflow step will be retried and may dead-letter."""


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, bytes]:
    http_request = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()
    except (error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"network error: {type(exc).__name__}") from exc


def _check(provider: str, status: int, body: bytes) -> None:
    if 200 <= status < 300:
        return
    kind = "transient" if status == 429 or status >= 500 else "permanent"
    # Provider response bodies can contain names, email addresses or tokens.
    raise ProviderError(f"{provider}: HTTP {status} ({kind})")


class CRMAdapter(Protocol):
    def mark_customer(self, event: PaymentEvent) -> None: ...


class AccessAdapter(Protocol):
    def grant(self, event: PaymentEvent) -> None: ...

    def change_tier(self, event: LifecycleEvent) -> None: ...

    def revoke_subscription(self, event: LifecycleEvent) -> None: ...


class MessagingAdapter(Protocol):
    def send_onboarding(self, event: PaymentEvent) -> None: ...


class SimulatedCRM:
    def mark_customer(self, event: PaymentEvent) -> None:
        return None


class SimulatedAccess:
    def grant(self, event: PaymentEvent) -> None:
        return None

    def change_tier(self, event: LifecycleEvent) -> None:
        return None

    def revoke_subscription(self, event: LifecycleEvent) -> None:
        return None


class SimulatedMessaging:
    def send_onboarding(self, event: PaymentEvent) -> None:
        return None


LIFECYCLE_RANK = {stage: rank for rank, stage in enumerate(
    ("subscriber", "lead", "marketingqualifiedlead", "salesqualifiedlead", "opportunity", "customer", "evangelist"))}


@dataclass
class HubSpotCRM:
    """Moves the paying contact's lifecycle stage forward to Customer, looked up on its unique GrowthOps ID.

    The contact is read first. A payment for a contact the CRM does not hold is a permanent failure, so the event
    dead-letters for a person to link the identity, rather than creating an empty contact. A contact already at
    Customer or beyond is left alone (lifecycle stages only move forward). A batch answer of 207 with errors is a
    failure, not a success.
    """

    token: str
    timeout: float = 10
    transport: Transport = urllib_transport
    base_url: str = HUBSPOT_API

    def _post(self, path: str, body: dict) -> dict:
        status, response = self.transport(
            "POST", f"{self.base_url}{path}",
            {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            json.dumps(body).encode(), self.timeout)[:2]
        _check("hubspot", status, response)
        try:
            return json.loads(response) if response else {}
        except ValueError:
            return {}

    def mark_customer(self, event: PaymentEvent) -> None:
        key = {"idProperty": "growthops_contact_id", "id": event.customer_id}
        page = self._post("/crm/v3/objects/contacts/batch/read",
                          {"idProperty": "growthops_contact_id", "properties": ["lifecyclestage"],
                           "inputs": [{"id": event.customer_id}]})
        results = page.get("results") or []
        if not results:
            raise ProviderError(f"hubspot: contact {event.customer_id} is not in the CRM (permanent); "
                                "link the identity before retrying")
        current = (results[0].get("properties") or {}).get("lifecyclestage") or ""
        if LIFECYCLE_RANK.get(current, -1) >= LIFECYCLE_RANK["customer"]:
            return
        page = self._post("/crm/v3/objects/contacts/batch/update",
                          {"inputs": [{**key, "properties": {"lifecyclestage": "customer"}}]})
        if page.get("errors"):
            category = (page["errors"][0] or {}).get("category", "UNKNOWN")
            raise ProviderError(f"hubspot: lifecycle update failed ({category}, permanent)")


@dataclass
class SignedWebhook:
    """POSTs a signed JSON action to a provider bridge (community platform, email tool, Zapier, ...)."""

    provider: str
    url: str
    secret: str
    timeout: float = 10
    transport: Transport = urllib_transport
    clock: Callable[[], float] = field(default=time.time)

    def post(self, event: PaymentEvent | LifecycleEvent, action: str) -> None:
        body = json.dumps({"action": action, "event_id": event.event_id, "customer_id": event.customer_id,
                           "product_id": getattr(event, "product_id", None),
                           "payment_id": event.payment_id,
                           "subscription_id": event.subscription_id,
                           "new_tier": getattr(event, "new_tier", None)},
                          sort_keys=True).encode()
        timestamp = str(int(self.clock()))
        signature = hmac.new(self.secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        status, response = self.transport("POST", self.url, {
            "Content-Type": "application/json", "Idempotency-Key": f"{event.event_id}:{action}",
            "X-GrowthOps-Timestamp": timestamp, "X-GrowthOps-Signature": signature}, body, self.timeout)
        _check(self.provider, status, response)

    def grant(self, event: PaymentEvent) -> None:
        self.post(event, "grant_access")

    def change_tier(self, event: LifecycleEvent) -> None:
        self.post(event, "change_tier")

    def revoke_subscription(self, event: LifecycleEvent) -> None:
        self.post(event, "revoke_subscription")

    def send_onboarding(self, event: PaymentEvent) -> None:
        self.post(event, "send_onboarding")


@dataclass
class Adapters:
    crm: CRMAdapter = field(default_factory=SimulatedCRM)
    access: AccessAdapter = field(default_factory=SimulatedAccess)
    messaging: MessagingAdapter = field(default_factory=SimulatedMessaging)

    @property
    def simulated(self) -> bool:
        return all(isinstance(item, (SimulatedCRM, SimulatedAccess, SimulatedMessaging))
                   for item in (self.crm, self.access, self.messaging))


def build_adapters(settings: Settings, transport: Transport = urllib_transport) -> Adapters:
    timeout = settings.adapter_timeout_seconds
    crm: CRMAdapter = SimulatedCRM()
    access: AccessAdapter = SimulatedAccess()
    messaging: MessagingAdapter = SimulatedMessaging()
    if settings.crm_adapter == "hubspot":
        crm = HubSpotCRM(settings.hubspot_access_token, timeout, transport)
    if settings.access_adapter == "webhook":
        if not settings.access_webhook_secret:
            raise ConfigError("GROWTHOPS_ACCESS_WEBHOOK_SECRET is required for the access bridge")
        access = SignedWebhook("access", settings.access_webhook_url, settings.access_webhook_secret,
                               timeout, transport)
    if settings.messaging_adapter == "webhook":
        if not settings.messaging_webhook_secret:
            raise ConfigError("GROWTHOPS_MESSAGING_WEBHOOK_SECRET is required for the messaging bridge")
        messaging = SignedWebhook("messaging", settings.messaging_webhook_url, settings.messaging_webhook_secret,
                                  timeout, transport)
    return Adapters(crm, access, messaging)


def call_step(adapters: Adapters, step: str, event: PaymentEvent) -> None:
    """Run the external side effect for a workflow step, if it has one."""
    if step == "update_crm":
        adapters.crm.mark_customer(event)
    elif step == "grant_access":
        adapters.access.grant(event)
    elif step == "send_onboarding":
        adapters.messaging.send_onboarding(event)
