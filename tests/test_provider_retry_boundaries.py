"""A bounded CRM read must respect its budget across retries and zero-sized reads."""
import json

import pytest

from growthops.hubspot_client import BudgetExhausted, HubSpotClient


@pytest.mark.parametrize("status", [429, 500, 503])
@pytest.mark.parametrize("remaining", [0, 2, 3])
def test_retry_stops_at_daily_floor_without_another_request_or_backoff(status, remaining):
    calls, sleeps = [], []

    def transport(*args):
        calls.append(args)
        return status, b'{}', {'X-HubSpot-RateLimit-Daily-Remaining': str(remaining)}

    client = HubSpotClient("fixture-token", transport=transport, min_interval=0,
                           daily_floor=3, max_attempts=3, sleep=sleeps.append)
    with pytest.raises(BudgetExhausted):
        client.get("/crm/v3/objects/contacts")
    assert len(calls) == 1
    assert not sleeps


def test_budget_is_rechecked_after_later_retry_responses():
    calls, sleeps = [], []

    def transport(*args):
        calls.append(args)
        remaining = 5 if len(calls) == 1 else 2
        return 429, b'{}', {'X-HubSpot-RateLimit-Daily-Remaining': str(remaining)}

    client = HubSpotClient("fixture-token", transport=transport, min_interval=0,
                           daily_floor=3, max_attempts=4, sleep=sleeps.append)
    with pytest.raises(BudgetExhausted):
        client.get("/crm/v3/objects/contacts")
    assert len(calls) == 2 and len(sleeps) == 1


@pytest.mark.parametrize("remaining", [0, 3])
def test_success_that_reaches_the_floor_is_returned_before_future_calls_are_blocked(remaining):
    calls = []

    def transport(*args):
        calls.append(args)
        return 200, b'{"results": [{"id": "one"}]}', {
            'X-HubSpot-RateLimit-Daily-Remaining': str(remaining),
        }

    client = HubSpotClient("fixture-token", transport=transport, min_interval=0, daily_floor=3)
    assert client.get("/crm/v3/objects/contacts")["results"] == [{"id": "one"}]
    with pytest.raises(BudgetExhausted):
        client.get("/crm/v3/objects/contacts")
    assert len(calls) == 1


@pytest.mark.parametrize("limit", [-1, True, False, 1.5, "3", float("nan")])
def test_invalid_search_limit_fails_without_provider_access(limit):
    calls = []
    client = HubSpotClient("fixture-token", transport=lambda *args: calls.append(args) or (200, b'{}'),
                           min_interval=0, search_interval=0)
    with pytest.raises(ValueError, match="limit"):
        client.search("contacts", [], ["email"], limit=limit)
    assert not calls


def test_zero_search_limit_performs_no_provider_request():
    calls = []
    client = HubSpotClient("fixture-token", transport=lambda *args: calls.append(args) or (200, b'{}'),
                           min_interval=0, search_interval=0)
    assert client.search("contacts", [], ["email"], limit=0) == []
    assert not calls


@pytest.mark.parametrize("limit", [1, 2, None])
def test_positive_and_unlimited_searches_preserve_results(limit):
    rows = [{"id": str(index)} for index in range(3)]
    client = HubSpotClient("fixture-token", transport=lambda *args: (200, json.dumps({'results': rows}).encode()),
                           min_interval=0, search_interval=0)
    assert client.search("contacts", [], ["email"], limit=limit) == (rows[:limit] if limit else rows)
