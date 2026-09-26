"""Reproducible synthetic ScaleLab scenario; never uses real people.

The generator simulates fifteen months of a creator-led B2B education business:
paid and organic acquisition, content influence, CRM lifecycle, sales, payment
plans, refunds, subscriptions and renewals. Behaviour is probabilistic but
seeded, so every run is identical. Planted incidents (see ``scenario.INCIDENTS``)
give the diagnostics a known ground truth to recover.
"""

from __future__ import annotations

import argparse
import math
import random
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from growthops import scenario as sc
from growthops.db import connect, initialize
from growthops.workflow import PaymentEvent, process_payment, run_due

UTC = timezone.utc
END = sc.as_of_datetime()
STAGE_RANK = {"lead": 0, "mql": 1, "opportunity": 2, "customer": 3}
TABLES = (
    "workflow_step_attempts", "workflow_steps", "processed_events", "access_entitlements",
    "migration_repairs", "platform_conversions", "refunds", "payments", "renewal_attempts",
    "subscriptions", "deals", "experiment_exposures", "experiment_variants", "experiments",
    "content_engagements", "lifecycle_events", "touches", "contacts", "legacy_contacts",
    "content_items", "ad_spend_daily", "campaigns", "products", "incidents",
)
PLATFORM_WINDOWS = {"meta": ("28d_click_1d_view", 28), "google": ("30d_click", 30),
                    "linkedin": ("30d_click_7d_view", 30)}
VIEW_THROUGH = {"meta": 0.24, "linkedin": 0.07}


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class Generator:
    def __init__(self, seed_value: int, scale: float) -> None:
        self.rng = random.Random(seed_value)
        self.scale = scale
        self.rows: dict[str, list[tuple]] = defaultdict(list)
        self.person_count = 0
        self.deal_count = 0
        self.payment_count = 0
        self.touch_count = 0
        self.content: list[dict] = []
        self.replay: list[PaymentEvent] = []
        self.replay_ids: set[str] = set()
        self.replay_refunds: list[tuple] = []
        self.replay_contacts: set[str] = set()
        self.contacts: dict[str, dict] = {}
        self.first_paid_contacts: list[tuple[str, datetime, int]] = []
        self.person_touches: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        self.experiment_leads: dict[str, list[tuple[str, datetime]]] = {"cta_a": [], "cta_b": []}
        self.spend_totals: dict[str, int] = defaultdict(int)

    def add(self, table: str, *values) -> None:
        self.rows[table].append(values)

    # --- random helpers -------------------------------------------------
    def poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        if lam > 40:
            return max(0, round(self.rng.gauss(lam, math.sqrt(lam))))
        threshold, count, product = math.exp(-lam), 0, self.rng.random()
        while product > threshold:
            count += 1
            product *= self.rng.random()
        return count

    def delay(self, median_days: float, sigma: float = 0.6) -> timedelta:
        return timedelta(days=median_days * math.exp(self.rng.gauss(0, sigma)))

    def moment(self, day: date) -> datetime:
        hour = self.rng.choices(range(24), weights=[1, 1, 1, 1, 1, 2, 3, 5, 8, 10, 10, 9,
                                                    8, 9, 10, 10, 9, 8, 7, 7, 6, 4, 3, 2])[0]
        return datetime.combine(day, time(hour, self.rng.randrange(60), self.rng.randrange(60)), UTC)

    # --- calendar -------------------------------------------------------
    @staticmethod
    def days() -> list[date]:
        return [sc.START + timedelta(days=offset) for offset in range((sc.AS_OF - sc.START).days + 1)]

    @staticmethod
    def season(day: date) -> float:
        factor = 1 + 0.1 * math.sin(2 * math.pi * (day.timetuple().tm_yday - 80) / 365)
        if (day.month == 12 and day.day >= 20) or (day.month == 1 and day.day <= 2):
            factor *= 0.7
        elif day.month == 1:
            factor *= 1.12
        return factor

    @staticmethod
    def webinar_day(day: date) -> bool:
        return day.weekday() == 3 and 8 <= day.day <= 14  # second Thursday

    @staticmethod
    def budget_multiplier(campaign: sc.Campaign, day: date) -> float:
        if campaign.campaign_id == "meta_prospecting_founder" and day >= date(2026, 4, 1):
            return 1.2
        if campaign.campaign_id == "google_nonbrand_growth" and day >= date(2026, 2, 1):
            return 0.85
        return 1.0

    # --- static catalogue -----------------------------------------------
    def catalogue(self) -> None:
        for product in sc.PRODUCTS:
            self.add("products", *product)
        for incident in sc.INCIDENTS:
            self.add("incidents", *incident)
        topics = list(sc.CONTENT_TOPICS)
        titles = {
            "pricing": ["Raise prices without losing clients", "Pricing a high-ticket offer", "Value-based pricing teardown"],
            "sales": ["The discovery call script", "Handling the budget objection", "Closing without pressure"],
            "systems": ["Founder operating system", "Weekly metrics that matter", "Delegating the inbox"],
            "hiring": ["Your first operations hire", "Hiring an integrator", "Scorecards for new hires"],
            "mindset": ["Why founders burn out", "Morning routine of a CEO", "Thinking in decades"],
        }
        ctr_by_topic = {"pricing": 0.034, "sales": 0.031, "systems": 0.026, "hiring": 0.022, "mindset": 0.011}
        publish = date(2025, 5, 1)
        for index in range(30):
            topic = topics[index % len(topics)] if index % 7 else self.rng.choice(topics)
            reach = sc.CONTENT_TOPICS[topic][1]
            age_days = (sc.AS_OF - publish).days
            views = int(9000 * reach * math.exp(self.rng.gauss(0, 0.55)) * (0.6 + min(age_days, 300) / 400))
            clicks = int(views * ctr_by_topic[topic] * math.exp(self.rng.gauss(0, 0.2)))
            item = {"content_id": f"video-{index + 1:02d}", "topic": topic, "published": publish, "weight": views}
            self.content.append(item)
            self.add("content_items", item["content_id"], f"{titles[topic][index % 3]} (ep. {index + 1})",
                     "youtube", _iso(datetime.combine(publish, time(15), UTC)), views, clicks,
                     "strategy_call" if topic == "sales" else "founder_guide", topic)
            publish += timedelta(days=16 + self.rng.randrange(-3, 4))
        self.add("experiments", "cta_growth_plan",
                 "A 'Get my growth plan' CTA may lift lead volume; qualified leads and net cash are guardrails.",
                 "net_cash_per_visitor", "visitor", _iso(datetime.combine(sc.EXPERIMENT_WINDOW[0], time(0), UTC)))
        self.add("experiment_variants", "cta_a", "cta_growth_plan", "Control: Book a strategy call",
                 "Book a strategy call")
        self.add("experiment_variants", "cta_b", "cta_growth_plan", "Variant: Get my growth plan",
                 "Get my growth plan")

    def pick_video(self, at: datetime) -> dict | None:
        eligible = [item for item in self.content if item["published"] <= at.date()]
        if not eligible:
            return None
        weights = [item["weight"] * (1.6 if (at.date() - item["published"]).days < 45 else 1) for item in eligible]
        return self.rng.choices(eligible, weights=weights)[0]

    # --- acquisition ----------------------------------------------------
    def acquisition(self) -> None:
        for day in self.days():
            weekday = (1.08, 1.1, 1.08, 1.05, 0.98, 0.72, 0.78)[day.weekday()]
            for campaign in sc.CAMPAIGNS:
                if day < campaign.start or (campaign.end and day >= campaign.end):
                    continue
                if campaign.daily_budget:
                    budget = campaign.daily_budget * self.budget_multiplier(campaign, day)
                    spend = int(budget * 100 * self.rng.uniform(0.88, 1.08))
                    cpm, ctr = {"meta": (14.0, 0.011), "google": (48.0, 0.052),
                                "linkedin": (52.0, 0.0065)}[campaign.platform]
                    impressions = int(spend / 100 / cpm * 1000)
                    clicks = int(impressions * ctr * self.rng.uniform(0.85, 1.15))
                    self.add("ad_spend_daily", campaign.campaign_id, day.isoformat(), spend, impressions, clicks)
                    self.spend_totals[campaign.campaign_id] += spend
                    cpl = campaign.cpl
                    if campaign.campaign_id == "meta_broad_v17":
                        cpl *= 1 + (day - sc.BROAD_LAUNCH).days / 120  # creative fatigue
                    expected = spend / 100 / cpl * weekday * self.season(day)
                else:
                    expected = campaign.cpl * weekday * self.season(day)
                    if campaign.campaign_id == "newsletter_weekly":
                        expected *= 3.4 if day.weekday() == 1 else 0.6
                    elif campaign.campaign_id == "webinar_growth_os":
                        expected *= 14 if self.webinar_day(day) else 0.35
                    elif campaign.campaign_id == "youtube_founder_guide":
                        expected *= 0.8 + 0.5 * (day - sc.START).days / 450
                for _ in range(self.poisson(expected * self.scale)):
                    self.person(campaign, day)

    def touch(self, contact_id: str, campaign: sc.Campaign, at: datetime, touch_type: str) -> tuple[str, bool] | None:
        """Record a touch; returns (touch_id, utm_lost) or None when it falls after the data cut-off."""
        if at > END:
            return None
        self.touch_count += 1
        touch_id = f"t-{self.touch_count:07d}"
        lost = False
        if campaign.campaign_id != "direct":
            broken = (campaign.landing_page == sc.UTM_BREAK_PAGE
                      and sc.UTM_BREAK[0] <= at.date() <= sc.UTM_BREAK[1])
            lost = broken or self.rng.random() < 0.022
        self.add("touches", touch_id, contact_id, None if lost else campaign.campaign_id, _iso(at), touch_type,
                 None if lost or campaign.campaign_id == "direct" else campaign.source,
                 campaign.landing_page, campaign.campaign_id)
        self.person_touches[contact_id].append((at, campaign.campaign_id))
        return touch_id, lost

    def person(self, campaign: sc.Campaign, day: date) -> None:
        rng = self.rng
        self.person_count += 1
        n = self.person_count
        contact_id = f"c-{n:06d}"
        lead_at = self.moment(day)
        events: list[tuple[str, datetime]] = []
        contact = {"contact_id": contact_id, "email": f"person{n}@synthetic.scalelab.test",
                   "owner": rng.choice(sc.OWNERS), "source": campaign.source, "lead_at": lead_at,
                   "events": events, "legacy": lead_at.date() < sc.MIGRATION_DATE, "paid_at": None}
        self.contacts[contact_id] = contact

        intent = 1.0
        if campaign.campaign_id != "direct" and campaign.source != "youtube" and rng.random() < 0.3:
            seen = lead_at - timedelta(days=rng.uniform(3, 40), hours=rng.uniform(0, 12))
            video = self.pick_video(seen)
            if video:
                touch_id, _ = self.touch(contact_id, sc.CAMPAIGN_BY_ID["youtube_founder_guide"], seen, "content_visit")
                self.add("content_engagements", f"ce-{n:06d}-d", contact_id, video["content_id"], touch_id,
                         _iso(seen), int(rng.uniform(60, 900)))
                intent *= 1 + (sc.CONTENT_TOPICS[video["topic"]][0] - 1) * 0.5
        lead_touch, lost = self.touch(contact_id, campaign, lead_at, "lead_creation")
        if lost:
            contact["source"] = None
        if campaign.source == "youtube":
            video = self.pick_video(lead_at)
            if video:
                self.add("content_engagements", f"ce-{n:06d}-l", contact_id, video["content_id"], lead_touch,
                         _iso(lead_at - timedelta(minutes=rng.randrange(2, 30))), int(rng.uniform(120, 1500)))
                intent *= sc.CONTENT_TOPICS[video["topic"]][0]
        events.append(("lead", lead_at))

        if (campaign.landing_page == sc.EXPERIMENT_PAGE and campaign.campaign_id != "direct"
                and sc.EXPERIMENT_WINDOW[0] <= day <= sc.EXPERIMENT_WINDOW[1]):
            variant = "cta_b" if rng.random() < 1.2 / 2.2 else "cta_a"
            self.experiment_leads[variant].append((contact_id, lead_at))
            if variant == "cta_b":
                intent *= 0.68

        quality = campaign.mql_rate * intent * math.exp(rng.gauss(0, 0.15))
        if rng.random() < 0.45:
            self.touch(contact_id, sc.CAMPAIGN_BY_ID["direct"], lead_at + self.delay(2.5), "site_visit")
        if rng.random() >= min(quality, 0.92):
            return
        mql_at = lead_at + self.delay(1.6, 0.7)
        events.append(("mql", mql_at))
        if rng.random() < 0.3:
            self.touch(contact_id, sc.CAMPAIGN_BY_ID["meta_retargeting_webinar"], mql_at + self.delay(3), "site_visit")
        if rng.random() < 0.18:
            self.touch(contact_id, sc.CAMPAIGN_BY_ID["google_brand_search"], mql_at + self.delay(2), "site_visit")
        book_rate = {"linkedin_ceo_abm": 0.62, "meta_broad_v17": 0.33}.get(campaign.campaign_id, 0.46)
        if rng.random() >= book_rate:
            return
        booked_at = mql_at + self.delay(2.8)
        events.append(("call_booked", booked_at))
        if rng.random() >= 0.74:
            return
        attended_at = booked_at + self.delay(3.5, 0.4)
        events.append(("call_attended", attended_at))
        if rng.random() >= 0.86:
            return
        opportunity_at = attended_at + timedelta(hours=rng.uniform(1, 30))
        events.append(("opportunity", opportunity_at))
        executive = rng.random() < campaign.executive_share
        product = ("executive_plan" if rng.random() < 0.4 else "executive_coaching") if executive else \
            ("accelerator_plan" if rng.random() < 0.35 else "accelerator")
        amount = next(price for pid, _, price, _ in sc.PRODUCTS if pid == product)
        won = rng.random() < min(0.34 * campaign.win_multiplier * (1.08 if intent > 1.05 else 1.0), 0.8)
        decided_at = opportunity_at + self.delay(8, 0.55)
        deadline = datetime(2026, 9, 18, 9, tzinfo=UTC)
        checkout_delay = timedelta(hours=rng.uniform(0.2, 40))
        if opportunity_at < deadline < decided_at and rng.random() < 0.8:
            # Enrollment deadline: undecided opportunities decide on launch day and check out at once.
            won = rng.random() < 0.65
            decided_at = deadline + timedelta(minutes=rng.randrange(0, 9 * 60))
            checkout_delay = timedelta(minutes=rng.uniform(2, 25))
        if opportunity_at > END:
            return
        self.deal_count += 1
        deal_id = f"d-{self.deal_count:06d}"
        if decided_at > END:
            self.add("deals", deal_id, contact_id, amount, "open", None, product, _iso(opportunity_at))
            return
        self.add("deals", deal_id, contact_id, amount, "closed_won" if won else "closed_lost",
                 _iso(decided_at), product, _iso(opportunity_at))
        if not won:
            return
        events.append(("closed_won", decided_at))
        contact["deal"] = (deal_id, amount)
        if rng.random() < 0.03:
            return  # Won but never paid.
        self.collect(contact, deal_id, product, amount, decided_at + checkout_delay)

    # --- revenue --------------------------------------------------------
    def payment(self, contact: dict, deal_id: str | None, amount: int, at: datetime, payment_type: str,
                product: str, subscription_id: str | None = None, status: str = "succeeded") -> str | None:
        if at > END:
            return None
        self.payment_count += 1
        payment_id = f"p-{self.payment_count:06d}"
        if status == "succeeded" and at >= END - timedelta(days=sc.WORKFLOW_REPLAY_DAYS):
            self.replay.append(PaymentEvent(
                event_id=f"evt-{payment_id}", event_type="payment.succeeded", payment_id=payment_id,
                customer_id=contact["contact_id"], deal_id=deal_id, amount_cents=amount, paid_at=at,
                payment_type=payment_type, subscription_id=subscription_id, product_id=product))
            self.replay_ids.add(payment_id)
            if payment_type == "new" and product != "community":
                self.replay_contacts.add(contact["contact_id"])
        else:
            self.add("payments", payment_id, deal_id, contact["contact_id"], amount, status, _iso(at),
                     payment_type, subscription_id, product)
        return payment_id

    def refund(self, payment_id: str, amount: int, paid_at: datetime) -> None:
        if self.rng.random() >= 0.045:
            return
        at = paid_at + timedelta(days=self.rng.uniform(3, 25))
        if at > END:
            return
        row = (f"r-{payment_id[2:]}", payment_id, amount if self.rng.random() < 0.65 else amount // 2, _iso(at))
        (self.replay_refunds if payment_id in self.replay_ids else self.rows["refunds"]).append(row)

    def collect(self, contact: dict, deal_id: str, product: str, amount: int, first_at: datetime) -> None:
        rng = self.rng
        count, installment = sc.INSTALLMENTS.get(product, (1, amount))
        due = first_at
        paid_first = False
        for index in range(count):
            payment_type = "new" if index == 0 else "installment"
            if index and rng.random() < 0.07:
                self.payment(contact, deal_id, installment, due, payment_type, product, status="failed")
                if rng.random() >= 0.65:
                    break
                due += timedelta(days=4)
            payment_id = self.payment(contact, deal_id, installment, due, payment_type, product)
            if payment_id is None:
                break
            if index == 0:
                paid_first = True
                contact["paid_at"] = due
                contact["events"].append(("paid", due))
                self.first_paid_contacts.append((contact["contact_id"], due, amount))
                self.refund(payment_id, installment, due)
            due += timedelta(days=30)
        if not paid_first:
            return
        paid_at = contact["paid_at"]
        contact["events"].append(("activated", paid_at + timedelta(minutes=rng.uniform(1, 45))))
        if rng.random() < 0.38:
            self.community(contact, paid_at + timedelta(days=rng.uniform(0, 14)))

    def community(self, contact: dict, at: datetime) -> None:
        if at > END:
            return
        rng = self.rng
        self.deal_count += 1
        deal_id = f"d-{self.deal_count:06d}"
        self.add("deals", deal_id, contact["contact_id"], 150000, "closed_won", _iso(at), "community", _iso(at))
        subscription_id = f"sub-{self.deal_count:06d}"
        payment_id = self.payment(contact, deal_id, 150000, at, "new", "community", subscription_id)
        if payment_id:
            self.refund(payment_id, 150000, at)
        due = at + timedelta(days=365)
        status = "active"
        attempt = 0
        while due <= END:
            roll = rng.random()
            attempt += 1
            if roll < 0.08:
                status = "canceled"
                break
            if roll < 0.24:
                code = rng.choice(("card_declined", "expired_card", "insufficient_funds"))
                self.add("renewal_attempts", f"ra-{subscription_id}-{attempt}", subscription_id, _iso(due), "failed", code)
                retry = due + timedelta(days=3)
                if rng.random() >= 0.45 or retry > END:
                    break  # Still active, renewal outstanding: the risk monitor's job.
                attempt += 1
                due = retry
            self.add("renewal_attempts", f"ra-{subscription_id}-{attempt}", subscription_id, _iso(due), "succeeded", None)
            self.payment(contact, None, 150000, due, "renewal", "community", subscription_id)
            if due < END - timedelta(days=sc.WORKFLOW_REPLAY_DAYS):
                contact["events"].append(("renewed", due))
            due += timedelta(days=365)
        self.add("subscriptions", subscription_id, contact["contact_id"], "founder_community", _iso(at),
                 _iso(due), status)

    # --- platform claims -------------------------------------------------
    def platform_claims(self) -> None:
        """Each ad platform credits itself for a purchase inside its own window, at pixel value."""
        rng = self.rng
        live_by_platform = defaultdict(list)
        for campaign in sc.CAMPAIGNS:
            if campaign.platform in PLATFORM_WINDOWS:
                live_by_platform[campaign.platform].append(campaign)
        claim = 0
        for contact_id, paid_at, value in self.first_paid_contacts:
            touches = self.person_touches[contact_id]
            for platform, (setting, window) in PLATFORM_WINDOWS.items():
                clicks = [(at, cid) for at, cid in touches if sc.CAMPAIGN_BY_ID[cid].platform == platform
                          and paid_at - timedelta(days=window) <= at <= paid_at]
                campaign_id, click = None, 0
                if clicks:
                    campaign_id, click = max(clicks)[1], 1
                elif rng.random() < VIEW_THROUGH.get(platform, 0):
                    live = [c for c in live_by_platform[platform]
                            if c.start <= paid_at.date() and (c.end is None or paid_at.date() < c.end)]
                    if live:
                        campaign_id = rng.choice(live).campaign_id
                if campaign_id is None:
                    continue
                claim += 1
                self.add("platform_conversions", f"pc-{claim:06d}", platform, campaign_id, contact_id,
                         _iso(paid_at + timedelta(minutes=rng.randrange(1, 90))), value, click, setting)

    # --- CRM ------------------------------------------------------------
    def crm(self) -> None:
        rng = self.rng
        legacy_count = 0
        duplicate_count = 0
        migration_at = datetime.combine(sc.MIGRATION_DATE, time(9), UTC)
        for contact_id, contact in self.contacts.items():
            events = contact["events"]
            replayed = contact_id in self.replay_contacts
            reached = {stage for stage, at in events if at <= END}
            if replayed:
                reached -= {"paid", "activated"}
            stage = "customer" if "paid" in reached else "opportunity" if "opportunity" in reached else \
                "mql" if "mql" in reached else "lead"
            owner, source, legacy_id = contact["owner"], contact["source"], None
            if contact["legacy"]:
                legacy_count += 1
                legacy_id = f"legacy-{legacy_count:06d}"
                before = {s for s, at in events if at < migration_at}
                legacy_stage = "customer" if "paid" in before else "opportunity" if "opportunity" in before \
                    else "mql" if "mql" in before else "lead"
                self.add("legacy_contacts", legacy_id, contact["email"], owner, source, legacy_stage)
                if rng.random() < 0.05:
                    owner = None
                if source and rng.random() < 0.08:
                    source = "offline_import"
                if STAGE_RANK[legacy_stage] > 0 and STAGE_RANK[stage] <= STAGE_RANK[legacy_stage] and rng.random() < 0.04:
                    stage = "lead"  # Lifecycle regressed during import.
                if "paid" in before:
                    if rng.random() < 0.05:
                        events[:] = [(s, at) for s, at in events if s != "opportunity"]
                    elif rng.random() < 0.02:
                        booked = next((at for s, at in events if s == "call_booked"), None)
                        if booked:
                            events[:] = [(s, booked + timedelta(hours=2) if s == "mql" else at) for s, at in events]
                if rng.random() < 0.018:
                    duplicate_count += 1
                    dup_id = f"c-dup-{duplicate_count:04d}"
                    self.add("contacts", dup_id, contact["email"].upper(), None, None, "offline_import", stage,
                             _iso(migration_at))
                    self.add("lifecycle_events", f"le-{dup_id}-lead", dup_id, "lead",
                             _iso(migration_at + timedelta(minutes=duplicate_count)))
                    if contact.get("deal") and rng.random() < 0.5:
                        self.add("deals", f"d-dup-{duplicate_count:04d}", dup_id, contact["deal"][1], "closed_won",
                                 _iso(migration_at), None, _iso(migration_at))
            elif rng.random() < 0.012:
                owner = None  # Post-migration routing workflow gap.
            self.add("contacts", contact_id, contact["email"], legacy_id, owner, source, stage,
                     _iso(contact["lead_at"]))
            for stage_name, at in events:
                if at <= END and not (replayed and stage_name in ("paid", "activated")):
                    self.add("lifecycle_events", f"le-{contact_id[2:]}-{stage_name}", contact_id, stage_name, _iso(at))
            if "activated" in reached:
                self.add("access_entitlements", contact_id, "active",
                         _iso(next(at for s, at in events if s == "activated")), f"seed-{contact_id}")
        for index in range(max(3, legacy_count // 200)):
            self.add("legacy_contacts", f"legacy-lost-{index:04d}", f"lost{index}@synthetic.scalelab.test",
                     rng.choice(sc.OWNERS), rng.choice(("meta", "google", "youtube")), "lead")

    def unmatched_payments(self) -> None:
        """Migration lost a small share of payment-to-deal links."""
        cutoff = sc.MIGRATION_DATE.isoformat()
        rows = self.rows["payments"]
        for index, row in enumerate(rows):
            if row[1] and row[5] < cutoff and row[6] != "renewal" and self.rng.random() < 0.025:
                rows[index] = (row[0], None, *row[2:])

    # --- experiment -----------------------------------------------------
    def experiment(self) -> None:
        rng = self.rng
        # Visitors are randomized 50/50 (a binomial split, as in a real test); B converts better.
        total_leads = sum(len(leads) for leads in self.experiment_leads.values())
        total_visitors = round(total_leads / 0.0825)
        split = rng.binomialvariate(total_visitors, 0.5)
        counts = {"cta_a": split, "cta_b": total_visitors - split}
        start = datetime.combine(sc.EXPERIMENT_WINDOW[0], time(0), UTC)
        span = (sc.EXPERIMENT_WINDOW[1] - sc.EXPERIMENT_WINDOW[0]).days + 1
        visitor = 0
        for variant, leads in self.experiment_leads.items():
            visitors = max(counts[variant], len(leads))
            for index in range(visitors):
                visitor += 1
                if index < len(leads):
                    contact_id, lead_at = leads[index]
                    exposed = lead_at - timedelta(minutes=rng.randrange(2, 40))
                else:
                    contact_id = None
                    exposed = start + timedelta(seconds=rng.randrange(span * 86400))
                self.add("experiment_exposures", f"exp-{visitor:06d}", "cta_growth_plan", variant,
                         f"visitor-{visitor:06d}", f"session-{visitor:06d}", contact_id, _iso(exposed))

    # --- persistence ----------------------------------------------------
    def write(self, connection: sqlite3.Connection) -> None:
        self.rows["campaigns"] = [(
            campaign.campaign_id, campaign.source, campaign.medium,
            campaign.campaign_id.lower().replace("-", "_"), self.spend_totals[campaign.campaign_id],
            campaign.registry_valid, campaign.platform, campaign.landing_page,
            campaign.start.isoformat(), campaign.end.isoformat() if campaign.end else None,
        ) for campaign in sc.CAMPAIGNS]
        order = ("products", "incidents", "campaigns", "ad_spend_daily", "content_items", "contacts",
                 "legacy_contacts", "touches", "content_engagements", "experiments", "experiment_variants",
                 "experiment_exposures", "lifecycle_events", "deals", "payments", "refunds", "subscriptions",
                 "renewal_attempts", "platform_conversions", "access_entitlements")
        connection.execute("BEGIN")
        for table in order:
            rows = self.rows.get(table, [])
            if rows:
                connection.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)
        connection.execute("COMMIT")


def _faults(step: str, event: PaymentEvent, attempt: int, now: datetime) -> str | None:
    """Simulated provider behaviour for the recent-payment replay."""
    marker = int(event.payment_id[2:]) % 100
    if step == "grant_access" and sc.ACCESS_OUTAGE[0] <= now < sc.ACCESS_OUTAGE[1]:
        return "community_access: HTTP 503 Service Unavailable"
    if step == "update_crm" and attempt == 1 and marker < 4:
        return "crm: HTTP 429 rate limited"
    if step == "update_crm" and attempt == 1 and 4 <= marker < 6:
        return "CRM contact not found; identity sync lag"
    if step == "send_onboarding" and attempt == 1 and marker == 7:
        return "messaging: provider timeout"
    return None


def _replay(connection: sqlite3.Connection, generator: Generator) -> None:
    """Deliver the last 30 days of payments as webhooks, with at-least-once redelivery."""
    rng = generator.rng
    deliveries = []
    for event in generator.replay:
        at = event.paid_at + timedelta(seconds=rng.uniform(2, 20))
        deliveries.append((at, event))
        if rng.random() < 0.08:
            deliveries.append((at + timedelta(seconds=rng.uniform(30, 900)), event))
    deliveries.sort(key=lambda item: (item[0], item[1].event_id))

    def drain(until: datetime) -> None:
        while True:
            due = connection.execute(
                "SELECT MIN(next_attempt_at) FROM processed_events WHERE status='failed'"
            ).fetchone()[0]
            if due is None or datetime.fromisoformat(due) > until:
                return
            run_due(connection, datetime.fromisoformat(due), faults=_faults)

    for at, event in deliveries:
        if at > END:
            break
        drain(at)
        process_payment(connection, event, now=at, faults=_faults)
    drain(END)
    recorded = {row[0] for row in connection.execute("SELECT payment_id FROM payments")}
    connection.executemany("INSERT INTO refunds VALUES (?, ?, ?, ?)",
                           [row for row in generator.replay_refunds if row[1] in recorded])


def seed(database: str, *, seed_value: int = 29, scale: float = 1.0) -> dict[str, int]:
    """Rebuild the database from scratch; the file is a generated artifact."""
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    connection = connect(database)
    initialize(connection)
    generator = Generator(seed_value, scale)
    generator.catalogue()
    generator.acquisition()
    generator.platform_claims()
    generator.crm()
    generator.unmatched_payments()
    generator.experiment()
    generator.write(connection)
    _replay(connection, generator)
    counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in reversed(TABLES)}
    connection.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()
    counts = seed(args.database, scale=args.scale)
    print(f"Seeded {counts['contacts']:,} synthetic contacts in {args.database}")


if __name__ == "__main__":
    main()
