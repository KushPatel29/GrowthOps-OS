"""Both CRM read paths fail before replaying a cyclic continuation."""

import json

import pytest

from growthops.adapters import ProviderError
from growthops.hubspot_client import HubSpotClient


@pytest.mark.parametrize("mode", ["paged", "search"])
@pytest.mark.parametrize("tokens", [("a",), ("a", "b")])
def test_cyclic_continuations_fail_before_replaying_a_request(mode, tokens):
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((url, body))
        if len(calls) > len(tokens) + 1:
            raise RuntimeError("test guard: unbounded pagination")
        token = tokens[(len(calls) - 1) % len(tokens)]
        page = {"results": [{"id": str(len(calls))}], "paging": {"next": {"after": token}}}
        return 200, json.dumps(page).encode(), {}

    client = HubSpotClient("fixture-token", transport=transport, min_interval=0, search_interval=0)
    with pytest.raises(ProviderError, match="repeated pagination"):
        if mode == "paged":
            client.paged("/crm/v3/objects/contacts?limit=100")
        else:
            client.search("contacts", [], ["email"])
    assert len(calls) == len(tokens) + 1
    assert len(calls) == len(set(calls))
