"""
The report's chrome, applied to the page specs instead of typed into them.

Every page gets the same things, and hand-edited pages would get them each a
slightly different way:

* a header drawn by DAX -- the page's name, its place in the report and the
  filters in effect -- so a page, or a screenshot of one, says what it shows;
* Previous and Next buttons, because a report navigated only by its tab strip is
  a report most people see the first page of;
* KPI cards drawn as SVG by a measure: the figure, the reference line under it
  and, for a rate, a progress rail, in one tile rather than a card with a
  subtitle squeezed under a callout;
* the page's slicers moved into a panel that two bookmarks open and close,
  which gives their slot on the canvas back to the charts.

`add_chrome` returns new page specs. The literal pages in report_spec stay as
they were written, and every layout test in the repo runs on what the report
actually draws rather than on the positions before the header moved them.
"""

from __future__ import annotations


CANVAS_W, CANVAS_H = 1280, 720
HEADER_X, HEADER_Y, HEADER_H = 24, 16, 56
BODY_TOP, BODY_BOTTOM = 88, 704
BUTTON_W, BUTTON_H, BUTTON_Y = 88, 40, 24
PANEL_X, PANEL_Y, PANEL_W = 888, 80, 368
PANEL_SLICER_H, PANEL_STEP = 80, 88
SLICER_MIN_H = 76
TILE_VIEW_H = 112

# The report's palette (build_pbip.SURFACE and friends), repeated rather than
# imported: build_pbip imports report_spec, which imports this module. The same
# validated dark palette as the other reports in this portfolio.
SURFACE = "#141416"
RAISED = "#1b1b20"
PANEL = "#101013"
HAIRLINE = "#26262a"
EDGE = "#3a3a41"
INK = "#f2f2f4"
INK_2 = "#a6a6ad"
INK_3 = "#7d7d85"
ACCENT = "#3987e5"
# Caption tones for a tile whose change has a direction: better, worse, level.
UP_GOOD = "#3fb96b"
DOWN_BAD = "#ef6f61"

# Slicers on these tables stay on the canvas: moving them is what the page is for.
WHATIF_TABLES: set[str] = set()

# Only kinds that bind a model field are held to the binding and alt-text tests;
# these draw the report's frame and bind a Report UI measure or nothing at all.
CHROME_KINDS = {"page_header", "nav", "filters_button", "filter_panel",
                "panel_background", "panel_title", "panel_close", "panel_clear"}


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------

def header_measure(display: str) -> str:
    return f"Header SVG - {display}"


def context_measure(display: str) -> str:
    return f"Filter Context - {display}"


def filter_button_measure(display: str) -> str:
    return f"Filter Button Label - {display}"


def tile_view_width(pos: tuple[int, int, int, int]) -> int:
    """The SVG's own width for a tile, so its aspect matches the tile's and
    `Fit` fills it instead of centring a small card in blank space."""
    _x, _y, width, height = pos
    return int(round(TILE_VIEW_H * width / max(1, height)))


def tile_measure(spec: dict) -> str:
    name = spec["field"].strip("[]")
    if spec.get("subtitle"):
        name += " | " + spec["subtitle"].strip("[]")
    if spec.get("trend"):
        name += f" | {spec['good']} " + spec["trend"].strip("[]")
    if spec.get("label"):
        name += f" | {spec['label']}"
    if spec.get("rail") is False:
        name += " | no rail"
    return f"KPI SVG {name} @{tile_view_width(spec['pos'])}"


def _table(reference: str) -> str:
    return reference.split("[", 1)[0]


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0, min(a1, b1) - max(a0, b0))


def _collides(specs: list[dict], target: dict, box: tuple) -> bool:
    x, y, w, h = box
    for spec in specs:
        if spec is target:
            continue
        sx, sy, sw, sh = spec["pos"]
        if _overlap(sx, sx + sw, x, x + w) > 0 and _overlap(sy, sy + sh, y, y + h) > 0:
            return True
    return False


def _merge(freed: list[list[int]]) -> list[list[int]]:
    """Slots side by side or stacked become one slot, so a neighbour can take
    the whole of it rather than half."""
    merged: list[list[int]] = []
    for r in sorted(freed, key=lambda r: (r[1], r[0])):
        for m in merged:
            same_row = _overlap(r[1], r[1] + r[3], m[1], m[1] + m[3]) >= 0.6 * min(r[3], m[3])
            beside = r[0] - (m[0] + m[2]) <= 32 and m[0] - (r[0] + r[2]) <= 32
            same_col = _overlap(r[0], r[0] + r[2], m[0], m[0] + m[2]) >= 0.6 * min(r[2], m[2])
            stacked = r[1] - (m[1] + m[3]) <= 32 and m[1] - (r[1] + r[3]) <= 32
            if (same_row and beside) or (same_col and stacked):
                x0, y0 = min(m[0], r[0]), min(m[1], r[1])
                x1, y1 = max(m[0] + m[2], r[0] + r[2]), max(m[1] + m[3], r[1] + r[3])
                m[:] = [x0, y0, x1 - x0, y1 - y0]
                break
        else:
            merged.append(list(r))
    return merged


def _reflow(specs: list[dict], freed: list[list[int]]) -> list[list[int]]:
    """Scale the body vertically into the space under the header."""
    tops = [s["pos"][1] for s in specs] + [r[1] for r in freed]
    bottoms = [s["pos"][1] + s["pos"][3] for s in specs] + [r[1] + r[3] for r in freed]
    top, bottom = min(tops), max(bottoms)
    k = (BODY_BOTTOM - BODY_TOP) / max(1, bottom - top)

    def y_map(y: float) -> int:
        return int(round(BODY_TOP + (y - top) * k))

    for spec in specs:
        x, y, w, h = spec["pos"]
        y0, y1 = y_map(y), y_map(y + h)
        spec["pos"] = (x, y0, w, y1 - y0)
    return [[r[0], y_map(r[1]), r[2], y_map(r[1] + r[3]) - y_map(r[1])] for r in freed]


def _absorb(specs: list[dict], free: list[int]) -> bool:
    """Give a slot a slicer left to the visual beside or below it."""
    fx, fy, fw, fh = free
    gap = 32
    for spec in specs:                                   # left neighbour widens
        x, y, w, h = spec["pos"]
        if 0 <= fx - (x + w) <= gap and _overlap(y, y + h, fy, fy + fh) >= 0.6 * fh:
            box = (x, y, fx + fw - x, h)
            if not _collides(specs, spec, box):
                spec["pos"] = box
                return True
    for spec in specs:                                   # neighbour below grows up
        x, y, w, h = spec["pos"]
        if 0 <= y - (fy + fh) <= gap and _overlap(x, x + w, fx, fx + fw) >= 0.6 * fw:
            box = (min(x, fx), fy, max(x + w, fx + fw) - min(x, fx), y + h - fy)
            if not _collides(specs, spec, box):
                spec["pos"] = box
                return True
    for spec in specs:                                   # right neighbour widens left
        x, y, w, h = spec["pos"]
        if 0 <= x - (fx + fw) <= gap and _overlap(y, y + h, fy, fy + fh) >= 0.6 * fh:
            box = (fx, y, x + w - fx, h)
            if not _collides(specs, spec, box):
                spec["pos"] = box
                return True
    for spec in specs:                                   # neighbour above grows down
        x, y, w, h = spec["pos"]
        if 0 <= fy - (y + h) <= gap and _overlap(x, x + w, fx, fx + fw) >= 0.6 * fw:
            box = (x, y, w, fy + fh - y)
            if not _collides(specs, spec, box):
                spec["pos"] = box
                return True
    return False


def add_chrome(pages: list[dict]) -> list[dict]:
    total = len(pages)
    out = []
    for index, page in enumerate(pages, start=1):
        prefix = f"p{index:02d}"
        display = page["display"]
        visuals = [dict(v) for v in page["visuals"]]
        # Ids come from each visual's place in the spec as written, so adding
        # the frame in front of them does not renumber every visual on the page.
        for position, spec in enumerate(visuals, start=1):
            spec.setdefault("id", f"v{index:02d}{position:02d}")
        panel = [v for v in visuals
                 if v["type"] == "slicer" and _table(v["field"]) not in WHATIF_TABLES]
        in_panel = {id(v) for v in panel}
        body = [v for v in visuals if id(v) not in in_panel]

        freed = _merge([list(v["pos"]) for v in panel])
        for free in _reflow(body, freed):
            _absorb(body, free)
        for spec in body:
            # A dropdown slicer needs its full height or the control hangs off
            # the bottom of the visual; the reflow scaled it with everything else.
            if spec["type"] == "slicer" and spec["pos"][3] < SLICER_MIN_H:
                x, y, w, _h = spec["pos"]
                box = (x, y, w, SLICER_MIN_H)
                if y + SLICER_MIN_H <= CANVAS_H and not _collides(body, spec, box):
                    spec["pos"] = box

        chrome: list[dict] = []
        right = CANVAS_W - 24
        group = f"{prefix}FilterPanel"
        if panel:
            chrome.append({
                "type": "filters_button", "id": f"{prefix}FiltersButton",
                "measure": filter_button_measure(display), "bookmark": f"{prefix}FiltersOpen",
                "pos": (right - BUTTON_W, BUTTON_Y, BUTTON_W, BUTTON_H),
                "alt": "Button. Opens the filter panel; its label counts the filters "
                       "in effect on this page.",
            })
            right -= BUTTON_W + 16
        if index < total:
            following = pages[index]
            chrome.append({
                "type": "nav", "id": f"{prefix}Next", "label": "Next ›",
                "target": following["name"],
                "pos": (right - BUTTON_W, BUTTON_Y, BUTTON_W, BUTTON_H),
                "alt": f"Button. Goes to the next page, {following['display']}.",
            })
            right -= BUTTON_W + 8
        if index > 1:
            previous = pages[index - 2]
            chrome.append({
                "type": "nav", "id": f"{prefix}Prev", "label": "‹ Previous",
                "target": previous["name"],
                "pos": (right - BUTTON_W, BUTTON_Y, BUTTON_W, BUTTON_H),
                "alt": f"Button. Goes to the previous page, {previous['display']}.",
            })
            right -= BUTTON_W
        header_width = min(720, right - HEADER_X - 24)
        chrome.insert(0, {
            "type": "page_header", "id": f"{prefix}Header",
            "measure": header_measure(display),
            "pos": (HEADER_X, HEADER_Y, header_width, HEADER_H),
            "page_index": index, "page_total": total,
            "context": [v["field"] for v in panel],
            "alt": f"Page header: {display}, page {index} of {total}"
                   + (", with the filters in effect." if panel else "."),
        })

        if panel:
            height = 56 + PANEL_STEP * len(panel) + 40
            members: list[dict] = [
                {"type": "panel_background", "id": f"{prefix}PanelBg", "group": group,
                 "pos": (PANEL_X, PANEL_Y, PANEL_W, height),
                 "alt": "Filter panel background."},
                {"type": "panel_title", "id": f"{prefix}PanelTitle", "group": group,
                 "text": "Filter this page", "pos": (PANEL_X + 16, PANEL_Y + 12, 216, 32),
                 "alt": "Heading. Filter this page."},
                {"type": "panel_close", "id": f"{prefix}PanelClose", "group": group,
                 "label": "Close", "bookmark": f"{prefix}FiltersClosed",
                 "pos": (PANEL_X + 256, PANEL_Y + 12, 96, 32),
                 "alt": "Button. Closes the filter panel."},
            ]
            for i, spec in enumerate(panel):
                spec["group"] = group
                spec["pos"] = (PANEL_X + 16, PANEL_Y + 56 + PANEL_STEP * i,
                               PANEL_W - 32, PANEL_SLICER_H)
                members.append(spec)
            members.append(
                {"type": "panel_clear", "id": f"{prefix}PanelClear", "group": group,
                 "label": "Clear all filters",
                 "pos": (PANEL_X + 16, PANEL_Y + height - 40, PANEL_W - 32, 32),
                 "alt": "Button. Clears every slicer on this page."})
            chrome.append({
                "type": "filter_panel", "id": group,
                "pos": (PANEL_X, PANEL_Y, PANEL_W, height),
                "members": [m["id"] for m in members],
                "bookmarks": (f"{prefix}FiltersOpen", f"{prefix}FiltersClosed"),
                "alt": "Filter panel.",
            })
            chrome.extend(members)

        out.append({**page, "visuals": [*chrome, *body]})
    return out


# --------------------------------------------------------------------------
# Report UI measures
# --------------------------------------------------------------------------

def _dax_text(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _runtime_safe(expression: str) -> str:
    """Escape text a measure returns before it goes inside the SVG."""
    return f'SUBSTITUTE(SUBSTITUTE({expression}, "&", "&amp;"), "<", "&lt;")'


# `%` has to be encoded before `#`: a literal "73.6%" left in the URI breaks its
# decoding, the `%23` colours stay encoded, and every fill renders black while
# shapes and text still draw -- it looks like a colour bug and is an encoding one.
ENCODE = '"data:image/svg+xml;utf8," & SUBSTITUTE(SUBSTITUTE(vSvg, "%", "%25"), "#", "%23")'


def _tile_dax(measure: str, subtitle: str | None, fmt: str | None, width: int,
              trend: str | None = None, good: str | None = None, label: str | None = None,
              rail_on: bool = True) -> str:
    # A rail reads as "how full": right for a rate out of 100%, wrong for a 2%
    # bounce rate, which would draw as an empty track.
    percent = rail_on and bool(fmt) and "%" in fmt and not fmt.startswith("+")
    money = bool(fmt) and "$" in fmt
    if fmt:
        shown = f"FORMAT(vValue, {_dax_text(fmt)})"
        if money:
            millions = _dax_text("\\$#,0.00")
            shown = (f'IF(ABS(vValue) >= 1000000, FORMAT(vValue / 1000000, {millions}) & "M", '
                     f"{shown})")
    else:
        shown = '"" & vValue'
    rail_width = width - 32
    caption_chars = max(20, int(58 * rail_width / 264))
    lines = [
        f"VAR vValue = [{measure}]",
        f'VAR vShown = IF(ISBLANK(vValue), "—", {shown})',
        "VAR vCaptionRaw = "
        + (_runtime_safe(f'COALESCE([{subtitle}], "")') if subtitle else '""'),
        f"VAR vCaption = IF(LEN(vCaptionRaw) > {caption_chars}, "
        f'LEFT(vCaptionRaw, {caption_chars - 2}) & "…", vCaptionRaw)',
    ]
    if trend:
        # The caption says which way the figure moved; its colour says whether
        # that was good. A cost going up and a rate going down are both red.
        better = ">" if good == "up" else "<"
        lines += [
            f"VAR vTrend = [{trend}]",
            f'VAR vTone = IF(ISBLANK(vTrend) || ROUND(vTrend, 4) = 0, "{INK_3}", '
            f'IF(vTrend {better} 0, "{UP_GOOD}", "{DOWN_BAD}"))',
            'VAR vArrow = IF(ISBLANK(vTrend) || ROUND(vTrend, 4) = 0, "", IF(vTrend > 0, "▲ ", "▼ "))',
        ]
    else:
        lines += [f'VAR vTone = "{INK_3}"', 'VAR vArrow = ""']
    rail = '""'
    if percent:
        lines.append(f"VAR vRail = ROUND({rail_width} * MAX(0, MIN(1, COALESCE(vValue, 0))), 0)")
        track = f"<rect x='16' y='76' width='{rail_width}' height='6' rx='3' fill='{HAIRLINE}'/>"
        rail = (f'"{track}"'
                f" & \"<rect x='16' y='76' width='\" & vRail"
                f" & \"' height='6' rx='3' fill='{ACCENT}'/>\"")
    lines += [
        "VAR vSvg =",
        f"    \"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{TILE_VIEW_H}' "
        f"viewBox='0 0 {width} {TILE_VIEW_H}'>\"",
        f"        & \"<rect x='0' y='18' width='3' height='76' rx='1.5' fill='{ACCENT}'/>\"",
        f"        & \"<text x='16' y='28' font-family='Segoe UI' font-size='11' "
        f"fill='{INK_2}'>{_xml(label or measure)}</text>\"",
        "        & \"<text x='16' y='64' font-family='Segoe UI Light' font-size='30' "
        f"fill='{INK}'>\" & vShown & \"</text>\"",
        f"        & {rail}",
        f"        & \"<text x='16' y='{100 if percent else 90}' font-family='Segoe UI' "
        "font-size='10' fill='\" & vTone & \"'>\" & vArrow & vCaption & \"</text>\"",
        '        & "</svg>"',
        "RETURN",
        "    " + ENCODE,
    ]
    return "\n".join(lines)


LABELS = {"month_label": "months", "channel_group": "channels", "platform": "platforms",
          "campaign_id": "campaigns", "email_type": "email types", "sending_domain": "sending domains",
          "channel": "link channels", "risk_level": "risk levels", "topic": "topics"}


def _label(column: str) -> str:
    return LABELS.get(column, column.replace("_", " ") + "s")


def _context_dax(fields: list[str]) -> str:
    parts = []
    for field in fields:
        table, _, rest = field.partition("[")
        column = rest.rstrip("]")
        ref = f"'{table}'[{column}]"
        parts.append(f'IF(ISFILTERED({ref}), CONCATENATEX(VALUES({ref}), {ref}, ", "), '
                     f'"all {_label(column)}")')
    body = ' & " · " & '.join(parts)
    return "\n".join([f"VAR vText = {body}",
                      'RETURN IF(LEN(vText) > 72, LEFT(vText, 70) & "…", vText)'])


def _button_label_dax(fields: list[str]) -> str:
    count = " + ".join(
        f"INT(ISFILTERED('{f.partition('[')[0]}'[{f.partition('[')[2].rstrip(']')}]))"
        for f in fields)
    return "\n".join([f"VAR vOn = {count}",
                      'RETURN IF(vOn = 0, "Filters", "Filters · " & vOn)'])


def _header_dax(display: str, index: int, total: int, context: str | None, width: int) -> str:
    where = f'"Page {index} of {total}"'
    if context:
        where = f'"Page {index} of {total} · " & {_runtime_safe("[" + context + "]")}'
    return "\n".join([
        f"VAR vContext = {where}",
        "VAR vSvg =",
        f"    \"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{HEADER_H}' "
        f"viewBox='0 0 {width} {HEADER_H}'>\"",
        f"        & \"<text x='0' y='24' font-family='Segoe UI Semibold' font-size='18' "
        f"fill='{INK}'>{_xml(display)}</text>\"",
        f"        & \"<text x='0' y='46' font-family='Segoe UI' font-size='11' "
        f"fill='{INK_3}'>\" & vContext & \"</text>\"",
        '        & "</svg>"',
        "RETURN",
        "    " + ENCODE,
    ])


def ui_measures(pages: list[dict], formats: dict[str, str | None]) -> list[tuple[str, str, bool]]:
    """(name, DAX, draws an image) for every Report UI measure the pages bind."""
    out: dict[str, tuple[str, str, bool]] = {}
    for page in pages:
        display = page["display"]
        for spec in page["visuals"]:
            if spec["type"] == "card":
                name = tile_measure(spec)
                measure = spec["field"].strip("[]")
                subtitle = spec["subtitle"].strip("[]") if spec.get("subtitle") else None
                trend = spec["trend"].strip("[]") if spec.get("trend") else None
                out.setdefault(name, (name, _tile_dax(measure, subtitle, formats.get(measure),
                                                      tile_view_width(spec["pos"]), trend,
                                                      spec.get("good"), spec.get("label"),
                                                      spec.get("rail", True)), True))
            elif spec["type"] == "page_header":
                context = None
                if spec["context"]:
                    context = context_measure(display)
                    out.setdefault(context, (context, _context_dax(spec["context"]), False))
                header = _header_dax(display, spec["page_index"], spec["page_total"],
                                     context, spec["pos"][2])
                out.setdefault(spec["measure"], (spec["measure"], header, True))
            elif spec["type"] == "filters_button":
                fields = [v["field"] for v in page["visuals"]
                          if v["type"] == "slicer" and v.get("group")]
                out.setdefault(spec["measure"], (spec["measure"], _button_label_dax(fields), False))
    return list(out.values())


# --------------------------------------------------------------------------
# Bookmarks and theme
# --------------------------------------------------------------------------

def bookmarks(pages: list[dict]) -> list[dict]:
    """Open and closed states for each page's filter panel. They capture only
    the panel's visibility: `suppressData` keeps a bookmark from also restoring
    the slicer selections it was saved with, which would reset every filter the
    reader had set each time the panel opened."""
    out = []
    for page in pages:
        panel = next((v for v in page["visuals"] if v["type"] == "filter_panel"), None)
        if panel is None:
            continue
        opened, closed = panel["bookmarks"]
        states = ((opened, "filters open", False), (closed, "filters closed", True))
        for name, label, hidden in states:
            out.append({
                "displayName": f"{page['display']} - {label}",
                "name": name,
                "options": {"targetVisualNames": [panel["id"], *panel["members"]],
                            "applyOnlyToTargetVisuals": True, "suppressData": True,
                            "suppressActiveSection": True},
                "explorationState": {
                    "version": "1.3", "activeSection": page["name"],
                    "sections": {page["name"]: {
                        "visualContainers": {member: {} for member in panel["members"]},
                        "visualContainerGroups": {panel["id"]: {"isHidden": hidden}},
                    }},
                },
            })
    return out


def theme_styles() -> dict:
    """The filter pane and its cards, which the dark canvas otherwise opens white.
    They belong under `page`; the validator rejects them under `*`."""
    def solid(colour: str) -> dict:
        return {"solid": {"color": colour}}

    return {
        # A button draws its fill, outline and text from the theme. The same
        # properties set only on the visual left Previous, Next and Filters as
        # empty boxes here, on a theme that had no actionButton entry at all.
        "actionButton": {"*": {
            "fill": [{"show": True, "fillColor": solid(RAISED), "transparency": 0}],
            "outline": [{"show": True, "lineColor": solid(EDGE), "weight": 1}],
            "text": [{"show": True, "fontColor": solid(INK), "fontSize": 10,
                      "fontFamily": "Segoe UI Semibold"}],
        }},
        "page": {"*": {
        "outspacePane": [{"backgroundColor": solid(PANEL), "foregroundColor": solid(INK),
                          "border": True, "borderColor": solid(EDGE),
                          "checkboxAndApplyColor": solid(ACCENT),
                          "inputBoxColor": solid(RAISED), "transparency": 0}],
        "filterCard": [
            {"$id": "Available", "backgroundColor": solid(SURFACE), "foregroundColor": solid(INK),
             "border": True, "borderColor": solid(EDGE), "inputBoxColor": solid(RAISED),
             "transparency": 0},
            {"$id": "Applied", "backgroundColor": solid(RAISED), "foregroundColor": solid(INK),
             "border": True, "borderColor": solid(ACCENT), "inputBoxColor": solid(RAISED),
             "transparency": 0},
        ],
    }}}
