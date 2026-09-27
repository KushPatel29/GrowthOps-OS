"""What a question is about, beyond which governed answer it wants.

Routing picks the governed answer; this module reads the details that make the
answer the one that was asked for: the time window ("last week", "in August",
"year to date"), the ad platform, a campaign, and the measure ("cost per lead",
"CTR", "net cash"). Everything here is pattern matching over a closed
vocabulary, so it can be read, tested and trusted: nothing is guessed.

Windows are parsed into a symbolic spec here and resolved against the data's
own as-of date when the answer runs, so routing never touches the database and
"last week" means the last week of data, not of the viewer's calendar.

Two things are refused rather than answered wrongly: an ad platform the
business does not buy (TikTok, Pinterest...), and a window the data does not
cover.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from growthops.scenario import CAMPAIGNS

MONTHS = {name: number for number, names in enumerate(
    (("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
     ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
     ("november", "nov"), ("december", "dec")), start=1) for name in names}
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "twelve": 12, "fourteen": 14, "thirty": 30, "ninety": 90, "a": 1, "an": 1}

PLATFORMS = {
    "meta": r"\b(meta|facebook|fb|instagram ads?)\b",
    "google": r"\b(google|adwords|search ads|paid search)\b",
    "linkedin": r"\blinked ?in\b",
}
UNTRACKED_PLATFORMS = r"\b(tik ?tok|pinterest|snap(chat)?|reddit|twitter|x ads|bing|microsoft ads|amazon ads|quora)\b"

# (measure id, pattern). Checked in order: "cost per mql" before "mql", "net cash" before "cash".
MEASURES: tuple[tuple[str, str], ...] = (
    ("cost_per_booked_call", (r"cost (per|of an?|for an?) (booked )?(discovery )?call|cost per booking|cpdm|"
                              r"(booked|discovery) calls? costs?|does an? (booked |discovery )?call cost")),
    ("cost_per_mql", r"cost (per|of an?|for an?) mql|mql cost|cost per qualified lead|does an mql cost"),
    ("cpl", r"\bcpl\b|cost (per|of an?|for an?) lead|lead cost|does an? lead cost|price (per|of an?) lead"),
    ("cpm", r"\bcpm\b|cost per (thousand|1,?000|mille) impressions"),
    ("cpc", r"\bcpc\b|cost per click"),
    ("ctr", r"\bctr\b|click[- ]?through( rate)?"),
    ("roas", r"\broas\b|return on (ad )?spend"),
    ("mql_rate", r"mql rate|lead[- ]to[- ]mql|qualif(y|ication) rate|share of leads (that )?qualif"),
    ("mqls", r"\bmqls?\b|marketing[- ]qualified"),
    ("leads", r"\bleads?\b|sign[- ]?ups?\b|new contacts"),
    ("calls_booked", r"(booked|discovery) calls?|calls? booked|bookings"),
    ("deals_won", (r"deals? (won|closed)|deals? .{0,15}\b(win|won|close|closed)\b|\b(win|won|close|closed) .{0,12}"
                   r"deals?|closed[- ]won|wins\b|new customers|how many customers")),
    ("refunds", r"\brefunds?\b|refunded"),
    ("gross_collected", r"gross (cash|collected|revenue|payments)"),
    ("crm_booked", r"\bbooked (revenue|value)|bookings value|\bcrm\b"),
    ("net_cash", (r"net cash|net collected|cash (collected|in)|revenue|sales|money (we )?(made|collected|brought in)|"
                  r"\b(bring|brought|take|took|bringing|taking) in\b")),
    ("spend", r"\b(ad |media |marketing )?spend\b|spent|ad budget|cost of ads|how much .*(spend|spent)"),
)

# How a measure is named back to the reader ("Understood as: Google · cost per lead"), never its internal id.
MEASURE_LABELS = {
    "cost_per_booked_call": "cost per booked call", "cost_per_mql": "cost per MQL", "cpl": "cost per lead",
    "cpm": "CPM", "cpc": "CPC", "ctr": "CTR", "roas": "ROAS", "mql_rate": "MQL rate", "mqls": "MQLs",
    "leads": "leads", "calls_booked": "booked calls", "deals_won": "deals won", "refunds": "refunds",
    "gross_collected": "gross cash", "crm_booked": "CRM bookings", "net_cash": "net cash", "spend": "ad spend",
}

CAMPAIGN_IDS = tuple(campaign.campaign_id for campaign in CAMPAIGNS)


def _campaign_pattern(campaign_id: str) -> str:
    # "meta_broad_v17", "meta broad v17" and "broad v17" all name the same campaign.
    words = [re.escape(part) for part in re.split(r"[_-]", campaign_id.lower()) if part]
    full = r"[\s_-]*".join(words)
    tail = r"[\s_-]*".join(words[1:]) if len(words) > 2 else None
    return rf"\b({full}{'|' + tail if tail else ''})\b"


CAMPAIGN_PATTERNS = {campaign_id: re.compile(_campaign_pattern(campaign_id), re.IGNORECASE)
                     for campaign_id in CAMPAIGN_IDS if campaign_id not in ("direct",)}


@dataclass(frozen=True)
class Slots:
    window: dict | None = None            # symbolic: resolved against the data's as-of date by resolve_window
    platform: str | None = None
    untracked_platform: str | None = None
    campaign: str | None = None
    measure: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        parts = []
        if self.window:
            parts.append(self.window["phrase"])
        if self.platform:
            parts.append(self.platform.title() if self.platform != "linkedin" else "LinkedIn")
        if self.campaign:
            parts.append(self.campaign)
        if self.measure:
            parts.append(MEASURE_LABELS.get(self.measure, self.measure.replace("_", " ")))
        return " · ".join(parts)


def _count(word: str) -> int | None:
    return int(word) if word.isdigit() else NUMBERS.get(word)


def parse_window(text: str) -> dict | None:
    """The period a question names, as a spec. None when it names none."""
    t = text.lower()
    if re.search(r"\b(all[- ]time|ever|since (the )?(start|launch|beginning)|to date overall|in total|overall)\b", t):
        return {"kind": "all", "phrase": "all time"}
    # The data's as-of date is the last complete day, which is what the daily update calls "yesterday";
    # "today" is not in yet, so it means the same latest complete day and says so.
    if re.search(r"\byesterday\b", t):
        return {"kind": "days", "n": 1, "offset": 0, "phrase": "yesterday (the latest complete day)"}
    if re.search(r"\btoday\b", t):
        return {"kind": "days", "n": 1, "offset": 0, "phrase": "the latest complete day (today is not in yet)"}
    m = re.search(r"\b(last|past|previous|trailing|over the last|in the last)\s+(\d+|[a-z]+)\s+(day|week|month)s?\b", t)
    if m and _count(m.group(2)):
        n, unit = _count(m.group(2)), m.group(3)
        if unit == "month":
            return {"kind": "months", "n": n, "phrase": f"the last {n} month{'s' * (n > 1)}"}
        days = n * (7 if unit == "week" else 1)
        return {"kind": "days", "n": days, "offset": 0, "phrase": f"the last {days} days"}
    if re.search(r"\b(this|current) week\b|\bweek to date\b|\bwtd\b", t):
        return {"kind": "week_to_date", "phrase": "this week to date"}
    if re.search(r"\b(last|past|previous) week\b|\bthis past week\b", t):
        return {"kind": "days", "n": 7, "offset": 0, "phrase": "the last 7 days"}
    if re.search(r"\b(this|current) month\b|\bmonth to date\b|\bmtd\b", t):
        return {"kind": "month_to_date", "phrase": "this month to date"}
    if re.search(r"\b(last|previous|past) month\b", t):
        return {"kind": "previous_month", "phrase": "last month"}
    if re.search(r"\b(this|current) quarter\b|\bquarter to date\b|\bqtd\b", t):
        return {"kind": "quarter_to_date", "phrase": "this quarter to date"}
    if re.search(r"\b(last|previous) quarter\b", t):
        return {"kind": "previous_quarter", "phrase": "last quarter"}
    if re.search(r"\b(this|current) year\b|\byear to date\b|\bytd\b", t):
        return {"kind": "year_to_date", "phrase": "this year to date"}
    if re.search(r"\b(last|previous) year\b", t):
        return {"kind": "previous_year", "phrase": "last year"}
    month = re.search(r"\b(in|during|for|of|over)?\s*(" + "|".join(sorted(MONTHS, key=len, reverse=True))
                      + r")\.?\s*(20\d\d)?\b", t)
    # A full month name stands on its own ("August net cash"). "May" and abbreviations ("mar", "dec") are
    # ordinary words too, so they need "in"/"during" or a year before they are read as a month.
    full_name = month and len(month.group(2)) > 3 and month.group(2) != "may"
    if month and (month.group(1) or month.group(3) or full_name):
        number = MONTHS[month.group(2)]
        year = int(month.group(3)) if month.group(3) else None
        label = month.group(2).title() + (f" {year}" if year else "")
        return {"kind": "month", "month": number, "year": year, "phrase": label}
    year = re.search(r"\b(in|during|for)\s+(20\d\d)\b", t)
    if year:
        return {"kind": "year", "year": int(year.group(2)), "phrase": year.group(2)}
    return None


def day_label(day: date) -> str:
    """"25 Sep 2026" on every platform (strftime's %-d does not exist on Windows)."""
    return f"{day.day} {day:%b %Y}"


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    days_in = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30,
               31, 30, 31][month - 1]
    return date(year, month, min(day.day, days_in))


def resolve_window(spec: dict | None, as_of: date, first: date) -> dict | None:
    """(start, end, label, clipped) for a spec against the data's own range; None if it covers no data."""
    if spec is None:
        return None
    kind = spec["kind"]
    if kind == "all":
        start, end = first, as_of
    elif kind == "days":
        end = as_of - timedelta(days=spec.get("offset", 0))
        start = end - timedelta(days=spec["n"] - 1)
    elif kind == "months":
        end, start = as_of, _add_months(as_of, -spec["n"]) + timedelta(days=1)
    elif kind == "week_to_date":
        end, start = as_of, as_of - timedelta(days=as_of.weekday())
    elif kind == "month_to_date":
        end, start = as_of, as_of.replace(day=1)
    elif kind == "previous_month":
        end = as_of.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    elif kind == "quarter_to_date":
        end, start = as_of, date(as_of.year, 3 * ((as_of.month - 1) // 3) + 1, 1)
    elif kind == "previous_quarter":
        this_start = date(as_of.year, 3 * ((as_of.month - 1) // 3) + 1, 1)
        end = this_start - timedelta(days=1)
        start = date(end.year, 3 * ((end.month - 1) // 3) + 1, 1)
    elif kind == "year_to_date":
        end, start = as_of, date(as_of.year, 1, 1)
    elif kind == "previous_year":
        start, end = date(as_of.year - 1, 1, 1), date(as_of.year - 1, 12, 31)
    elif kind == "month":
        year = spec["year"] or (as_of.year if spec["month"] <= as_of.month else as_of.year - 1)
        start = date(year, spec["month"], 1)
        end = _add_months(start, 1) - timedelta(days=1)
    elif kind == "year":
        start, end = date(spec["year"], 1, 1), date(spec["year"], 12, 31)
    else:  # pragma: no cover - every kind above is produced by parse_window
        raise ValueError(kind)
    if end < first or start > as_of:
        return None
    clipped = start < first or end > as_of
    start, end = max(start, first), min(end, as_of)
    label = day_label(start) if start == end else f"{day_label(start)} to {day_label(end)}"
    return {"start": start, "end": end, "label": label, "clipped": clipped,
            "phrase": spec["phrase"], "days": (end - start).days + 1}


def parse(text: str) -> Slots:
    t = " ".join(text.lower().split())
    platform = next((name for name, pattern in PLATFORMS.items() if re.search(pattern, t)), None)
    untracked = re.search(UNTRACKED_PLATFORMS, t)
    campaign = next((campaign_id for campaign_id, pattern in CAMPAIGN_PATTERNS.items() if pattern.search(t)), None)
    measure = next((name for name, pattern in MEASURES if re.search(pattern, t)), None)
    return Slots(window=parse_window(t), platform=platform,
                 untracked_platform=untracked.group(0) if untracked and not platform else None,
                 campaign=campaign, measure=measure)
