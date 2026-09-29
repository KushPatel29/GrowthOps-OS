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
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th)?"
# One point in time: an ISO date, "3 September [2026]", "September 3[rd] [2026]", "August [2025]" or a year.
POINT = re.compile(
    r"\b(?:(?P<iso_y>20\d\d)-(?P<iso_m>\d{1,2})-(?P<iso_d>\d{1,2})"
    rf"|(?P<dm_d>\d{{1,2}}){_ORD}\s+(?:of\s+)?(?P<dm_m>{_MONTH_RE})\.?(?:,?\s+(?P<dm_y>20\d\d))?"
    rf"|(?P<md_m>{_MONTH_RE})\.?\s+(?P<md_d>\d{{1,2}}){_ORD}(?:,?\s+(?P<md_y>20\d\d))?"
    rf"|(?P<m_m>{_MONTH_RE})\.?(?:\s+(?P<m_y>20\d\d))?"
    r"|(?P<y>20\d\d))\b")
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
QUARTER = re.compile(r"\b(?:(?P<pre>20\d\d)\s+)?(?:q(?P<q>[1-4])|(?P<qw>first|second|third|fourth|1st|2nd|3rd|4th)"
                     r"\s+quarter)\b(?:\s+(?:of\s+)?(?P<year>20\d\d))?")
HALF = re.compile(r"\b(?:(?P<pre>20\d\d)\s+)?(?:h(?P<h>[12])|(?P<hw>first|second|1st|2nd)\s+half)\b"
                  r"(?:\s+(?:of\s+)?(?P<year>20\d\d))?")
# "the start of June" is June's first day and "the end of July" its last, which is how a range reads them anyway.
_EDGE_START = r"(\s+the\s+(start|beginning)\s+of)?"
_EDGE_END = r"(\s+the\s+(end|close)\s+of)?"
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")

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
    ("calls_booked", r"(booked|discovery) calls?|calls? (were |got |have been )?booked|bookings"),
    ("deals_won", (r"deals? (won|closed)|deals? .{0,15}\b(win|won|close|closed)\b|\b(win|won|close|closed) .{0,12}"
                   r"deals?|closed[- ]won|wins\b|new customers|how many customers")),
    ("refund_rate", (r"refund (rate|ratio)|(share|percent(age)?|%) of (cash|revenue|sales|payments|gross)[a-z ]{0,12}"
                     r"refunded|refunds? as a (share|percent(age)?)")),
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
    "leads": "leads", "calls_booked": "booked calls", "deals_won": "deals won", "refunds": "refunds", "refund_rate": "refund rate",
    "gross_collected": "gross cash", "crm_booked": "CRM bookings", "net_cash": "net cash", "spend": "ad spend",
}

# Attribution models by name. "Linear" and "lead creation" are ordinary words too, so they count only beside a word
# that makes them a model ("linear attribution", "credit under lead creation"); the others are unambiguous. "Last
# touch" and "last click" read as the house last-touch model, last non-direct, and the answer names it so.
ATTRIBUTION_MODELS: tuple[tuple[str, str], ...] = (
    ("first_touch", r"\bfirst[- ](touch|click)\b"),
    ("lead_creation", (r"\blead[- ]creation\b(?=.*\b(attribut\w*|credit\w*|model)\b)|"
                       r"\b(attribut\w*|credit\w*|model)\b.*\blead[- ]creation\b")),
    ("last_non_direct", r"\blast[- ](non[- ]direct|touch|click)\b"),
    ("u_shaped", r"\bu[- ]?shaped\b"),
    ("linear", (r"\blinear\b(?=.*\b(attribut\w*|credit\w*|model|give|gives|get|gets)\b)|"
                r"\b(attribut\w*|credit\w*|model)\b.*\blinear\b")),
    ("time_decay", r"\btime[- ]decay\b"),
)

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
    platforms: tuple[str, ...] = ()       # two or more named together: a comparison between them
    untracked_platform: str | None = None
    campaign: str | None = None
    measure: str | None = None
    attribution_models: tuple[str, ...] = ()  # models named, in the order they are listed in ATTRIBUTION_MODELS
    notes: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        parts = []
        if self.window:
            parts.append(self.window["phrase"])
        names = [name.title() if name != "linkedin" else "LinkedIn"
                 for name in self.platforms or ((self.platform,) if self.platform else ())]
        if names:
            parts.append(" vs ".join(names))
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
    # "since the start" is all time, but "since the start of August" is a date and is read below.
    if re.search(r"\b(all[- ]time|ever|since (the )?(start|launch|beginning)(?!\s+of\b)|to date overall|in total|overall)\b",
                 t):
        return {"kind": "all", "phrase": "all time"}
    # The data's as-of date is the last complete day, which is what the daily update calls "yesterday";
    # "today" is not in yet, so it means the same latest complete day and says so.
    if re.search(r"\byesterday\b", t):
        return {"kind": "days", "n": 1, "offset": 0, "phrase": "yesterday (the latest complete day)"}
    if re.search(r"\btoday\b", t):
        return {"kind": "days", "n": 1, "offset": 0, "phrase": "the latest complete day (today is not in yet)"}
    if re.search(r"\b(the )?week before last\b", t):
        return {"kind": "days", "n": 7, "offset": 7, "phrase": "the week before last"}
    if re.search(r"\b(last|past|previous|this past)\s+fortnight\b", t):
        return {"kind": "days", "n": 14, "offset": 0, "phrase": "the last 14 days"}
    m = re.search(r"\b(last|past|previous|trailing|over the last|in the last)\s+(\d+|[a-z]+)\s+(day|week|month)s?\b", t)
    n = _count(m.group(2)) if m else None
    if m and n:
        unit = m.group(3)
        if unit == "month":
            return {"kind": "months", "n": n, "phrase": f"the last {n} month{'s' * (n > 1)}"}
        days = n * (7 if unit == "week" else 1)
        return {"kind": "days", "n": days, "offset": 0, "phrase": f"the last {days} days"}
    if re.search(r"\b(this|current) week\b|\bweek to date\b|\bwtd\b", t):
        return {"kind": "week_to_date", "phrase": "this week to date"}
    # "Last week" is the last seven days, the same rolling week the morning brief reports; the calendar reading
    # (the previous Monday to Sunday) is there for anyone who asks for it by name.
    if re.search(r"\b(last|past|previous|prior) (calendar|full) week\b|\bweek before this one\b", t):
        return {"kind": "previous_week", "phrase": "last calendar week (Monday to Sunday)"}
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
    return _explicit(t)


def _point(m: re.Match) -> dict | None:
    """A matched point in time as a spec; an impossible date such as 31 February is kind "invalid"."""
    g = m.groupdict()
    if g["y"]:
        return {"kind": "year", "year": int(g["y"]), "phrase": g["y"]}
    if g["m_m"]:
        year = int(g["m_y"]) if g["m_y"] else None
        return {"kind": "month", "month": MONTHS[g["m_m"]], "year": year,
                "phrase": g["m_m"].title() + (f" {year}" if year else "")}
    if g["iso_y"]:
        month, day, year = int(g["iso_m"]), int(g["iso_d"]), int(g["iso_y"])
    else:
        name = g["dm_m"] or g["md_m"]
        month, day = MONTHS[name], int(g["dm_d"] or g["md_d"])
        year = int(g["dm_y"] or g["md_y"]) if (g["dm_y"] or g["md_y"]) else None
    try:
        date(year or 2024, month, day)  # 2024 is a leap year, so 29 February is allowed without a year
    except ValueError:
        # Kept, not dropped: a question about "31 February" is answered with a refusal, never with all time.
        named = f"{day} {MONTH_NAMES[month - 1]}" + (f" {year}" if year else "") if 1 <= month <= 12 else m.group(0)
        return {"kind": "invalid", "phrase": named}
    return {"kind": "date", "month": month, "day": day, "year": year,
            "phrase": f"{day} {MONTH_NAMES[month - 1][:3]}" + (f" {year}" if year else "")}


def _standalone_month(t: str, m: re.Match) -> bool:
    # A full month name stands on its own ("August net cash"). "May" and abbreviations ("mar", "dec") are
    # ordinary words too, so they need "in"/"during" or a year before they are read as a month.
    name = m.group("m_m")
    return bool(m.group("m_y") or (len(name) > 3 and name != "may")
                or re.search(r"\b(in|during|for|of|over|on|since|from|between|to|until|till|through|thru|and|starting)"
                             r"\s*$", t[:m.start()]))


def _year(t: str, m: re.Match) -> bool:
    # "in 2019" is a year even outside the data, so it can be refused; a bare number is one only if it is
    # plausibly this business's year, so "spend over 2000" is not read as the year 2000.
    return bool(re.search(r"\b(in|during|for|of|since|from|between|to|and|through|until)\s*$", t[:m.start()])
                or 2020 <= int(m.group("y")) <= 2039)


def _explicit(t: str) -> dict | None:
    """Named periods: a quarter or half, a range, "since" a point, one day, one month or one year."""
    for pattern, kind, words in ((QUARTER, "quarter", "q"), (HALF, "half", "h")):
        m = pattern.search(t)
        if m:
            number = int(m.group(words)) if m.group(words) else _ORDINALS[m.group(words + "w")]
            raw = m.group("year") or m.group("pre")
            year = int(raw) if raw else None
            label = f"{'Q' if kind == 'quarter' else 'H'}{number}" + (f" {year}" if year else "")
            return {"kind": kind, "n": number, "year": year, "phrase": label}
    matches = [m for m in POINT.finditer(t)
               if (not m.group("m_m") or _standalone_month(t, m)) and (not m.group("y") or _year(t, m))]
    points = [(m, spec) for m in matches if (spec := _point(m))]
    invalid = next((spec for _m, spec in points if spec["kind"] == "invalid"), None)
    if invalid:
        return invalid
    if len(points) >= 2:
        (m0, first), (m1, second) = points[0], points[1]
        joined = t[m0.end():m1.start()]
        # "compare July and August", "August vs July": two periods side by side, not one range.
        if re.search(r"\b(compare[sd]?|comparing|vs|versus|against)\b", t) \
                and re.search(r"^\s*(and|vs\.?|versus|against|with|compared (to|with))\s*$", joined):
            return {"kind": "compare", "from": first, "to": second,
                    "phrase": f"{first['phrase']} vs {second['phrase']}"}
        # "between July and August", "July to August", "from the start of June to the end of July".
        if (re.search(rf"\b(between|from){_EDGE_START}\s*$", t[:m0.start()])
                and re.search(rf"^\s*(and|to|until|till|through|thru|-|–){_EDGE_END}\s*$", joined)) \
                or re.search(rf"^\s*(to|until|till|through|thru|-|–){_EDGE_END}\s*$", joined):
            return {"kind": "span", "from": first, "to": second, "phrase": f"{first['phrase']} to {second['phrase']}"}
    if not points:
        return None
    m0, first = points[0]
    # "since August" and "August onwards"; a bare "from August" is read as August itself.
    if re.search(rf"\b(since|starting( from)?){_EDGE_START}\s*$", t[:m0.start()]) \
            or re.search(r"^\s*(onwards?|and after|or later)\b", t[m0.end():]):
        return {"kind": "since", "from": first, "phrase": f"since {first['phrase']}"}
    return first


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


def _latest(candidate, as_of: date) -> tuple[date, date]:
    # A period named without a year is the most recent one that has started by the as-of date.
    start, end = candidate(as_of.year)
    return (start, end) if start <= as_of else candidate(as_of.year - 1)


def _bounds(spec: dict, as_of: date, first: date) -> tuple[date, date]:
    """The unclipped (start, end) a spec names."""
    kind = spec["kind"]
    if kind in ("date", "month", "quarter", "half", "year"):
        def candidate(year: int) -> tuple[date, date]:
            if kind == "date":
                day = date(year, spec["month"], min(spec["day"], 28 if spec["month"] == 2 and year % 4 else 31))
                return day, day
            if kind == "month":
                start = date(year, spec["month"], 1)
                return start, _add_months(start, 1) - timedelta(days=1)
            if kind == "year":
                return date(year, 1, 1), date(year, 12, 31)
            months = 3 if kind == "quarter" else 6
            start = date(year, months * (spec["n"] - 1) + 1, 1)
            return start, _add_months(start, months) - timedelta(days=1)
        year = spec.get("year")
        return candidate(year) if year else _latest(candidate, as_of)
    if kind == "since":
        return _bounds(spec["from"], as_of, first)[0], as_of
    if kind == "span":
        start_spec, end_spec = spec["from"], spec["to"]
        start, _ = _bounds(start_spec, as_of, first)
        _, end = _bounds(end_spec, as_of, first)
        # "from 1 Aug 2025 to 15 Aug" and "between November and February": carry the named year across,
        # and move an unnamed year back so the range runs forwards.
        if end_spec.get("year") is None and start_spec.get("year") is not None:
            _, end = _bounds({**end_spec, "year": start.year}, as_of, first)
            if end < start:
                _, end = _bounds({**end_spec, "year": start.year + 1}, as_of, first)
        elif start > end and start_spec.get("year") is None:
            start, _ = _bounds({**start_spec, "year": end.year - (1 if start.year >= end.year else 0)}, as_of, first)
        return start, end
    raise ValueError(kind)  # pragma: no cover - every kind is produced by parse_window


def resolve_window(spec: dict | None, as_of: date, first: date) -> dict | None:
    """(start, end, label, clipped) for a spec against the data's own range; None if it covers no data."""
    if spec is None:
        return None
    kind = spec["kind"]
    if kind == "invalid":
        return None
    if kind == "compare":
        # The later period is the answer and the earlier one its comparison, in whichever order they were named.
        one, other = (resolve_window(spec[side], as_of, first) for side in ("from", "to"))
        if one is None or other is None:
            return None
        later, earlier = sorted((one, other), key=lambda w: w["start"], reverse=True)
        return {**later, "phrase": spec["phrase"], "compare_with": earlier,
                "clipped": later["clipped"] or earlier["clipped"]}
    if kind in ("date", "month", "quarter", "half", "year", "since", "span"):
        start, end = _bounds(spec, as_of, first)
        if start > end:
            return None
    elif kind == "all":
        start, end = first, as_of
    elif kind == "days":
        end = as_of - timedelta(days=spec.get("offset", 0))
        start = end - timedelta(days=spec["n"] - 1)
    elif kind == "months":
        end, start = as_of, _add_months(as_of, -spec["n"]) + timedelta(days=1)
    elif kind == "week_to_date":
        end, start = as_of, as_of - timedelta(days=as_of.weekday())
    elif kind == "previous_week":
        end = as_of - timedelta(days=as_of.weekday() + 1)
        start = end - timedelta(days=6)
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
    else:  # pragma: no cover - every kind above is produced by parse_window
        raise ValueError(kind)
    if end < first or start > as_of:
        return None
    clipped_start, clipped_end = start < first, end > as_of
    start, end = max(start, first), min(end, as_of)
    label = day_label(start) if start == end else f"{day_label(start)} to {day_label(end)}"
    return {"start": start, "end": end, "label": label, "clipped": clipped_start or clipped_end,
            "clipped_start": clipped_start, "clipped_end": clipped_end,
            "phrase": spec["phrase"], "days": (end - start).days + 1}


def parse(text: str) -> Slots:
    t = " ".join(text.lower().split())
    named = [name for name, pattern in PLATFORMS.items() if re.search(pattern, t)]
    platform = named[0] if len(named) == 1 else None
    untracked = re.search(UNTRACKED_PLATFORMS, t)
    campaign = next((campaign_id for campaign_id, pattern in CAMPAIGN_PATTERNS.items() if pattern.search(t)), None)
    measure = next((name for name, pattern in MEASURES if re.search(pattern, t)), None)
    models = tuple(name for name, pattern in ATTRIBUTION_MODELS if re.search(pattern, t))
    return Slots(window=parse_window(t), platform=platform, platforms=tuple(named) if len(named) > 1 else (),
                 untracked_platform=untracked.group(0) if untracked and not named else None,
                 campaign=campaign, measure=measure, attribution_models=models)
