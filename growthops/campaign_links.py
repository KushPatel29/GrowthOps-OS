"""Build registered, canonical campaign links."""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field, HttpUrl

SOURCE_MEDIUM = {
    "meta": "paid_social",
    "google": "paid_search",
    "linkedin": "paid_social",
    "youtube": "organic_video",
    "newsletter": "owned_email",
    "partner": "referral",
}
SLUG = re.compile(r"^[a-z][a-z0-9_]*$")


class LinkRequest(BaseModel):
    campaign_id: str = Field(min_length=1)
    destination_url: HttpUrl
    content: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


def build_link(connection: sqlite3.Connection, request: LinkRequest) -> dict:
    campaign = connection.execute(
        "SELECT campaign_id, source, medium, campaign_name, registry_valid FROM campaigns WHERE campaign_id=?",
        (request.campaign_id,),
    ).fetchone()
    if campaign is None:
        raise LookupError("campaign is not registered")
    if not campaign["registry_valid"] or campaign["source"] not in SOURCE_MEDIUM:
        raise ValueError("campaign is invalid in the registry")
    if campaign["medium"] != SOURCE_MEDIUM[campaign["source"]] or not SLUG.fullmatch(campaign["campaign_name"]):
        raise ValueError("campaign source, medium, or name fails taxonomy")
    parts = urlsplit(str(request.destination_url))
    existing = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if not key.lower().startswith("utm_")]
    params = {
        "utm_source": campaign["source"],
        "utm_medium": campaign["medium"],
        "utm_campaign": campaign["campaign_name"],
        "utm_content": request.content,
    }
    url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                      urlencode([*existing, *params.items()]), parts.fragment))
    return {"campaign_id": campaign["campaign_id"], "url": url, "utm": params}
