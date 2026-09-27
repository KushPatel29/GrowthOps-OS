"""Fixed calendar and campaign plan for the synthetic ScaleLab scenario.

Everything here is fictional. The plan is data, not code, so a reviewer can see
exactly which behaviours were planted and the tests can check the analytics
recover them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

START = date(2025, 7, 1)
AS_OF = date(2026, 9, 25)  # Last complete day of data; "this morning" is the next day.
MIGRATION_DATE = date(2026, 3, 15)  # Legacy CRM -> HubSpot-style CRM cut-over.
ACCESS_OUTAGE = (
    datetime.combine(date(2026, 9, 18), time(10, 0), timezone.utc),
    datetime.combine(date(2026, 9, 18), time(16, 0), timezone.utc),
)
UTM_BREAK = (date(2026, 9, 3), date(2026, 9, 14))  # Landing-page release dropped UTMs.
UTM_BREAK_PAGE = "/webinar"
BROAD_LAUNCH = date(2026, 8, 10)
EXPERIMENT_WINDOW = (date(2026, 5, 1), date(2026, 6, 30))
EXPERIMENT_PAGE = "/growth-guide"
WORKFLOW_REPLAY_DAYS = 30  # Payments in this window flow through the lifecycle engine.
ENROLLMENT_DEADLINE = date(2026, 9, 18)
SITE = "https://scalelab.example"
EMAIL_DOMAIN = "mail.scalelab.example"
# Bulk sends moved to a new, unwarmed subdomain without DKIM alignment.
NEW_EMAIL_DOMAIN = "news.scalelab.example"
EMAIL_DOMAIN_SWITCH = date(2026, 9, 1)
PROMO_DATES = (date(2026, 9, 11), date(2026, 9, 15), date(2026, 9, 17), date(2026, 9, 18))

PAID_MEDIA = ("paid_social", "paid_search")
OWNERS = ("owner-ava", "owner-ben", "owner-chloe", "owner-dev", "owner-emma", "owner-farid")


@dataclass(frozen=True)
class Campaign:
    campaign_id: str
    source: str
    medium: str
    platform: str
    landing_page: str
    start: date
    end: date | None
    daily_budget: float  # USD; zero for organic
    cpl: float  # USD cost per lead for paid; leads/day for organic
    mql_rate: float
    win_multiplier: float = 1.0
    executive_share: float = 0.12
    registry_valid: int = 1


CAMPAIGNS: tuple[Campaign, ...] = (
    Campaign("meta_prospecting_founder", "meta", "paid_social", "meta", "/growth-guide",
             START, None, 330, 41, 0.24),
    Campaign("meta_retargeting_webinar", "meta", "paid_social", "meta", "/webinar",
             START, None, 95, 31, 0.36, 1.05),
    Campaign("meta_broad_v17", "meta", "paid_social", "meta", "/growth-guide",
             BROAD_LAUNCH, None, 480, 19, 0.075, 0.6),
    Campaign("google_brand_search", "google", "paid_search", "google", "/apply",
             START, None, 70, 24, 0.52, 1.3, 0.25),
    Campaign("google_nonbrand_growth", "google", "paid_search", "google", "/growth-guide",
             START, None, 230, 68, 0.27),
    Campaign("linkedin_ceo_abm", "linkedin", "paid_social", "linkedin", "/executive",
             date(2025, 10, 1), None, 260, 155, 0.48, 1.2, 0.6),
    # Pre-migration naming that fails the taxonomy; spend is still real.
    Campaign("FB-Broad", "FB", "paid_social", "meta", "/growth-guide",
             START, MIGRATION_DATE, 170, 36, 0.19, 0.9, 0.08, 0),
    Campaign("youtube_founder_guide", "youtube", "organic_video", "youtube", "/growth-guide",
             START, None, 0, 4.2, 0.28),
    Campaign("newsletter_weekly", "newsletter", "owned_email", "email", "/growth-guide",
             START, None, 0, 2.4, 0.33),
    Campaign("webinar_growth_os", "webinar", "event", "zoom", "/webinar",
             START, None, 0, 1.1, 0.44, 1.1),
    Campaign("partner_podcast", "partner", "referral", "partner", "/apply",
             START, None, 0, 0.8, 0.41, 1.15, 0.25),
    Campaign("direct", "direct", "none", "direct", "/",
             START, None, 0, 0.9, 0.3),
)
CAMPAIGN_BY_ID = {campaign.campaign_id: campaign for campaign in CAMPAIGNS}

PRODUCTS = (
    # product_id, name, list price cents, billing
    ("accelerator", "Founder Accelerator", 480000, "one_time"),
    ("accelerator_plan", "Founder Accelerator (3 payments)", 510000, "plan"),
    ("executive_coaching", "Executive Coaching", 1500000, "one_time"),
    ("executive_plan", "Executive Coaching (2 payments)", 1500000, "plan"),
    ("community", "Private Founder Community (annual)", 150000, "annual"),
)
INSTALLMENTS = {"accelerator_plan": (3, 170000), "executive_plan": (2, 750000)}

CONTENT_TOPICS = {
    # topic: (intent multiplier on MQL rate, audience reach multiplier)
    "pricing": (1.45, 0.7),
    "sales": (1.3, 0.8),
    "systems": (1.1, 1.0),
    "hiring": (0.95, 0.9),
    "mindset": (0.55, 1.9),
}

SHORT_LINKS = (
    # link_id, channel, path, (utm_source, utm_medium, utm_campaign) or None, created, owner, clicks/day
    ("nl-guide", "newsletter", "/growth-guide", ("newsletter", "owned_email", "newsletter_weekly"),
     date(2025, 7, 1), "owner-emma", 14),
    ("yt-guide", "youtube", "/growth-guide", ("youtube", "organic_video", "youtube_founder_guide"),
     date(2025, 7, 1), "owner-dev", 22),
    ("web-invite", "email", "/webinar", ("webinar", "event", "webinar_growth_os"),
     date(2025, 7, 1), "owner-emma", 9),
    ("pod-apply", "podcast", "/apply", ("partner", "referral", "partner_podcast"),
     date(2025, 9, 1), "owner-ben", 3),
    ("nl-deadline", "newsletter", "/apply", ("newsletter", "owned_email", "newsletter_weekly"),
     date(2026, 9, 10), "owner-emma", 18),
    # Planted hygiene defects: the audit must find exactly these four.
    ("ig-bio", "instagram", "/growth-guide", None, date(2026, 6, 1), "owner-chloe", 31),
    ("pod-ep41", "podcast", "/apply", ("Podcast", "referral", "partner_podcast"),
     date(2026, 8, 20), "owner-ben", 6),
    ("yt-q3-guide", "youtube", "/growth-guide", ("youtube", "organic_video", "youtube_q3_guide"),
     date(2026, 7, 15), "owner-dev", 12),
    ("li-launch", "linkedin", "/executive", ("linkedin", "social", "linkedin_ceo_abm"),
     date(2026, 9, 8), "owner-farid", 8),
)

INCIDENTS = (
    ("inc_meta_broad_quality", "lead_quality", BROAD_LAUNCH.isoformat(), None,
     "campaign:meta_broad_v17", "mql_rate_down",
     "Broad-audience Meta campaign launched; lead volume rose while qualification fell."),
    ("inc_webinar_utm_break", "tracking", UTM_BREAK[0].isoformat(), UTM_BREAK[1].isoformat(),
     f"landing_page:{UTM_BREAK_PAGE}", "utm_completeness_down",
     "A landing-page release stripped UTM parameters from the webinar registration form."),
    ("inc_access_outage", "automation", ACCESS_OUTAGE[0].isoformat(), ACCESS_OUTAGE[1].isoformat(),
     "provider:community_access", "workflow_failures_up",
     "Community-access provider returned HTTP 503 for six hours."),
    ("inc_crm_migration", "migration", MIGRATION_DATE.isoformat(), None,
     "system:crm", "migration_defects",
     "Legacy CRM cut-over created duplicates, blank owners, overwritten sources and lost deal links."),
    ("inc_email_domain_switch", "deliverability", EMAIL_DOMAIN_SWITCH.isoformat(), None,
     f"sending_domain:{NEW_EMAIL_DOMAIN}", "email_bounce_rate_up",
     ("Bulk email moved to a new sending subdomain without warm-up or DKIM alignment; "
     "bounces and complaints rose and inbox placement fell before the enrollment deadline.")),
    ("inc_untagged_links", "tracking", "2026-06-01", None, "short_links", "link_utm_defects",
     ("Four short links (Instagram bio, a podcast episode, a YouTube guide and a LinkedIn launch post) "
     "carry missing, unregistered or off-taxonomy UTM parameters.")),
)


def as_of_datetime() -> datetime:
    return datetime.combine(AS_OF, time(23, 59, 59), timezone.utc)
