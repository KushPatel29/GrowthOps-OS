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


def audit_short_links(connection: sqlite3.Connection, recent_days: int = 30) -> dict:
    """Check every short link's destination against the campaign registry.

    A clean link carries utm_source, utm_medium and utm_campaign, the campaign is
    registered and valid, and source and medium match the registry exactly
    (case included: 'Podcast' and 'partner' land in different CRM buckets).
    """
    registry = {row["campaign_name"]: dict(row) for row in connection.execute(
        "SELECT campaign_id, campaign_name, source, medium, registry_valid FROM campaigns")}
    latest = connection.execute("SELECT MAX(click_date) FROM short_link_clicks").fetchone()[0]
    rows = connection.execute(
        """SELECT l.link_id, l.channel, l.destination_url, l.created_at, l.owner,
                  COALESCE(SUM(k.clicks),0) clicks,
                  COALESCE(SUM(CASE WHEN k.click_date > DATE(?, ?) THEN k.clicks END),0) recent_clicks
           FROM short_links l LEFT JOIN short_link_clicks k ON k.link_id=l.link_id
           GROUP BY l.link_id ORDER BY l.link_id""",
        (latest, f"-{recent_days} days"),
    ).fetchall()
    links = []
    for row in rows:
        parts = urlsplit(row["destination_url"])
        utm = {key[4:]: value for key, value in parse_qsl(parts.query) if key.startswith("utm_")}
        issues = []
        missing = [key for key in ("source", "medium", "campaign") if not utm.get(key)]
        if missing:
            issues.append("missing utm_" + ", utm_".join(missing))
        campaign = registry.get(utm.get("campaign", ""))
        if utm.get("campaign") and campaign is None:
            issues.append(f"campaign '{utm['campaign']}' is not registered")
        elif campaign and not campaign["registry_valid"]:
            issues.append(f"campaign '{utm['campaign']}' is invalid in the registry")
        if campaign:
            for key in ("source", "medium"):
                if utm.get(key) and utm[key] != campaign[key]:
                    issues.append(f"utm_{key} '{utm[key]}' should be '{campaign[key]}'")
        links.append({**dict(row), "path": parts.path, "utm_source": utm.get("source"),
                      "utm_medium": utm.get("medium"), "utm_campaign": utm.get("campaign"),
                      "issues": issues, "status": "fix" if issues else "ok"})
    broken = [link for link in links if link["issues"]]
    recent_total = sum(link["recent_clicks"] for link in links)
    return {
        "as_of": latest,
        "recent_days": recent_days,
        "links": links,
        "links_with_issues": len(broken),
        "recent_clicks": recent_total,
        "recent_clicks_on_broken_links": sum(link["recent_clicks"] for link in broken),
        "share_of_recent_clicks_broken": round(sum(link["recent_clicks"] for link in broken) / recent_total, 4)
        if recent_total else None,
    }
