"""Write the Power BI project from the specs in this package.

A PBIR report is one JSON file per visual, and this one has well over a hundred.
Typing those by hand is how a report ends up carrying three ``visualContainer``
schema versions, a property name Desktop silently drops on the next save, and no
way to check either: Power BI treats a bad property as an empty visual rather than
an error, so a broken report looks finished.

So the report is generated from :mod:`growthops.bi.report_spec`, the model from
:mod:`growthops.bi.model_spec`, and both are committed. ``--check`` regenerates into
a temporary directory and diffs, so CI fails if the committed project and the spec
(or the data) have drifted apart.

The data is embedded in each import partition as an M ``#table`` literal, one row
per line, built from the governed CSVs in ``dashboards/powerbi-data``. The project
therefore opens with no data path or credentials, rebuilds byte for byte on any
machine, and a data change shows up in review as changed rows rather than as a
different compressed blob. Column types come from the same inference that writes
``Table.TransformColumnTypes``, so a column can never be typed one way in the model
and another in the query that feeds it.

Usage::

    python -m growthops.bi.build_pbip
    python -m growthops.bi.build_pbip --check
"""

from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from growthops.bi.model_spec import (
    DATE_COLUMNS,
    MEASURES,
    RELATIONSHIPS,
    ROW_RATIOS,
    SORT_BY,
    TABLES,
)
from growthops.bi.report_chrome import (
    ACCENT,
    CHROME_KINDS,
    EDGE,
    PANEL,
    PANEL_X,
    PANEL_Y,
    RAISED,
    bookmarks,
    theme_styles,
    tile_measure,
    ui_measures,
)
from growthops.bi.report_spec import PAGES, VISUAL_TYPES

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "dashboards" / "powerbi-data"
PBIP_DIR = ROOT / "dashboards" / "powerbi-project"
PROJECT = "GrowthOpsOS"
THEME = "GrowthOpsDark.json"

# Hand-written files that live alongside the generated project. The cleanup below
# removes anything the generator does not own, and the drift check would otherwise
# report these as stale.
KEEP = {"OPEN_ME_FIRST.md"}

# Schema versions Microsoft has actually published. The schema sets
# additionalProperties:false, which is the one cheap way to catch a mistyped
# property in a generated report; a version nobody published gives that up while
# looking like you still have it.
SCHEMA = {
    "pbip": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
    "platform": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
    "pbism": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
    "pbir": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/1.0.0/schema.json",
    "report": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json",
    "version": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
    "pages": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
    "page": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/1.4.0/schema.json",
    "visual": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.0.0/schema.json",
    "bookmark": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/bookmark/2.1.0/schema.json",
    "bookmarks": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/bookmarksMetadata/1.0.0/schema.json",
}

# Stable ids: same name in, same GUID out, so a rebuild produces no diff.
NAMESPACE = uuid.UUID("0b7f5e1c-2d3a-4c6b-9e8f-7a6b5c4d3e2f")


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(parts)))


# --------------------------------------------------------------------------
# Column typing
# --------------------------------------------------------------------------

M_TYPES = {"int64": "Int64.Type", "double": "type number", "dateTime": "type date", "string": "type text",
           "boolean": "type logical"}

# Identifiers and ordering keys. Summing a campaign id or a sort key is never what
# anyone meant; ordering keys must still stay numeric, or a waterfall ordered by
# them sorts 1, 10, 2 and puts its total in the middle.
ORDER_COLUMNS = {"ordinal", "month_index", "week_index", "day_of_week", "campaign_order", "check_order",
                 "risk_order", "year", "days_before_as_of"}


def format_for(column: str, dtype: str) -> str:
    if dtype == "dateTime":
        return "yyyy-mm-dd"
    if dtype == "boolean" or dtype == "string":
        return ""
    if column in ORDER_COLUMNS:
        return "0"
    if column in ROW_RATIOS or column.endswith("_rate") or column in ("rate", "target"):
        return "0.0%"
    if column.endswith("_cents"):
        return "#,0"
    return "#,0" if dtype == "int64" else "#,0.00"


def infer_columns(path: Path) -> list[dict]:
    """Describe each CSV column for TMDL and for the M query, from one inference."""
    frame = pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False, dtype=str)
    columns = []
    for name in frame.columns:
        present = [value for value in frame[name] if value != ""]
        if name in DATE_COLUMNS:
            dtype = "dateTime"
        elif name.endswith(("_id", "_label", "_name")) or name in ("title", "subject", "description", "note"):
            dtype = "string"
        elif present and all(value in ("True", "False") for value in present):
            dtype = "boolean"
        elif present and all(_is_int(value) for value in present):
            dtype = "int64"
        elif present and all(_is_number(value) for value in present):
            dtype = "double"
        else:
            dtype = "string"
        numeric = dtype in ("int64", "double")
        summarize = "sum" if numeric and name not in ORDER_COLUMNS and name not in ROW_RATIOS else "none"
        columns.append({"name": name, "dataType": dtype, "mType": M_TYPES[dtype],
                        "format": format_for(name, dtype), "summarizeBy": summarize})
    return columns


def _is_int(value: str) -> bool:
    return value.lstrip("-").isdigit()


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def read_rows(path: Path) -> list[list[str]]:
    frame = pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False, dtype=str)
    return frame.values.tolist()


# --------------------------------------------------------------------------
# TMDL
# --------------------------------------------------------------------------

def _m_text(value: str) -> str:
    return "null" if value == "" else '"' + value.replace('"', '""') + '"'


def partition_source(columns: list[dict], rows: list[list[str]]) -> list[str]:
    """The M query, as TMDL lines: the rows as a text #table, then typed.

    Text first and typed second, rather than typed literals, because the typing
    step is then the single place a type is decided, and it is generated from the
    same inference as the column's ``dataType``.
    """
    schema = ", ".join(f'#"{c["name"]}" = nullable text' for c in columns)
    types = ", ".join(f'{{"{c["name"]}", {c["mType"]}}}' for c in columns)
    body = [f"\t\t\t\t        {{{', '.join(_m_text(v) for v in row)}}}," for row in rows]
    if body:
        body[-1] = body[-1].rstrip(",")
    return [
        "\t\tsource =",
        "\t\t\t\tlet",
        f"\t\t\t\t    Source = #table(type table [{schema}], {{",
        *body,
        "\t\t\t\t    }),",
        f"\t\t\t\t    Typed = Table.TransformColumnTypes(Source, {{{types}}})",
        "\t\t\t\tin",
        "\t\t\t\t    Typed",
    ]


def table_tmdl(name: str, meta: dict, columns: list[dict], rows: list[list[str]]) -> str:
    lines = [f"table {name}", f"\tlineageTag: {tag('table', name)}"]
    if meta.get("date_table"):
        # Marking the table as the date table is what lets a relative-date slicer
        # and the time-intelligence functions treat its key as the calendar.
        lines.append("\tdataCategory: Time")
    lines.append("")
    for column in columns:
        lines.append(f"\tcolumn {column['name']}")
        lines.append(f"\t\tdataType: {column['dataType']}")
        if meta.get("date_table") == column["name"]:
            lines.append("\t\tisKey")
        if column["format"]:
            lines.append(f"\t\tformatString: {column['format']}")
        lines.append(f"\t\tlineageTag: {tag('column', name, column['name'])}")
        lines.append(f"\t\tsummarizeBy: {column['summarizeBy']}")
        lines.append(f"\t\tsourceColumn: {column['name']}")
        sort_column = SORT_BY.get(name, {}).get(column["name"])
        if sort_column:
            # Without this a text column sorts alphabetically on every axis: "Apr
            # 2026" before "Jul 2025", and a bridge that opens on "Duplicate deals".
            lines.append(f"\t\tsortByColumn: {sort_column}")
        lines.append("")
    lines += [f"\tpartition {name} = m", "\t\tmode: import", *partition_source(columns, rows), ""]
    return "\n".join(lines)


def measures_tmdl() -> str:
    lines = ["table _Measures", f"\tlineageTag: {tag('table', '_Measures')}", ""]
    for name, dax, fmt, folder, description in MEASURES:
        body = dax.split("\n")
        lines.append(f"\t/// {description}")
        if len(body) == 1:
            lines.append(f"\tmeasure '{name}' = {body[0]}")
        else:
            lines.append(f"\tmeasure '{name}' =")
            lines.extend(f"\t\t\t{line}" if line else "" for line in body)
        if fmt:
            lines.append(f"\t\tformatString: {fmt}")
        lines.append(f"\t\tlineageTag: {tag('measure', name)}")
        lines.append(f"\t\tdisplayFolder: {folder}")
        lines.append("")

    # The report's own measures: SVG tiles and headers, filter context and the
    # Filters button's label. Kept out of MEASURES so the metric reference stays a
    # list of business definitions.
    formats = {name: fmt for name, _dax, fmt, _folder, _description in MEASURES}
    for name, dax, image in ui_measures(PAGES, formats):
        lines.append(f"\tmeasure '{name}' =")
        lines.extend(f"\t\t\t{line}" if line else "" for line in dax.split("\n"))
        lines.append(f"\t\tlineageTag: {tag('measure', name)}")
        if image:
            # Without it the image visual shows nothing: the string is a data URI
            # only once the column says it is one.
            lines.append("\t\tdataCategory: ImageUrl")
        lines.append("\t\tdisplayFolder: Report UI")
        lines.append("")

    # A measures table needs one hidden column or Desktop will not show it in the
    # field list at all. dataType is not optional on a column fed by M: without it
    # Desktop refuses the project ("cannot be of type Empty").
    lines += [
        "\tcolumn _placeholder",
        "\t\tdataType: int64",
        "\t\tisHidden",
        "\t\tformatString: 0",
        f"\t\tlineageTag: {tag('column', '_Measures', '_placeholder')}",
        "\t\tsummarizeBy: none",
        "\t\tsourceColumn: _placeholder",
        "",
        "\tpartition _Measures = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        '\t\t\t\t    Source = #table(type table [_placeholder = Int64.Type], {{0}})',
        "\t\t\t\tin",
        "\t\t\t\t    Source",
        "",
    ]
    return "\n".join(lines)


def relationships_tmdl() -> str:
    # No `///` description here: TMDL reads it as the description of the next
    # object, a relationship has none, and the whole model then fails to load.
    blocks = []
    for from_table, from_column, to_table, to_column in RELATIONSHIPS:
        blocks.append(
            f"relationship {tag('relationship', from_table, from_column, to_table)}\n"
            f"\tfromColumn: {from_table}.{from_column}\n"
            f"\ttoColumn: {to_table}.{to_column}\n"
        )
    return "\n".join(blocks)


def model_tmdl(table_names: list[str]) -> str:
    """Every M-backed table must be in PBI_QueryOrder: one left out is never loaded,
    and Desktop's refresh skips it silently, leaving a page of blank visuals."""
    order = json.dumps(table_names)
    refs = "\n".join(f"ref table {name}" for name in table_names)
    return (
        "model Model\n"
        "\tculture: en-US\n"
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tsourceQueryCulture: en-US\n"
        "\tdataAccessOptions\n"
        "\t\tlegacyRedirects\n"
        "\t\treturnErrorValuesAsNull\n"
        "\n"
        f"annotation PBI_QueryOrder = {order}\n"
        "\n"
        "annotation __PBI_TimeIntelligenceEnabled = 0\n"
        "\n"
        'annotation PBI_ProTooling = ["DevMode"]\n'
        "\n"
        f"{refs}\n"
    )


# --------------------------------------------------------------------------
# PBIR
# --------------------------------------------------------------------------

def literal(value) -> dict:
    """A formatting literal, with the type suffix Power BI requires.

    A number written as ``{"Value": "11"}`` is dropped by Desktop on the next save,
    with no error. It has to be ``"11D"``. Strings are single-quoted *inside* the
    value; booleans are bare.
    """
    if isinstance(value, bool):
        return {"expr": {"Literal": {"Value": "true" if value else "false"}}}
    if isinstance(value, (int, float)):
        return {"expr": {"Literal": {"Value": f"{value}D"}}}
    return {"expr": {"Literal": {"Value": f"'{value}'"}}}


def field_expr(reference: str) -> tuple[dict, str, str]:
    """Parse ``table[column]`` or ``[Measure]`` into a PBIR field expression, its
    ``queryRef`` and its ``nativeQueryRef``: all three have to agree or the visual
    binds to nothing and renders empty."""
    reference = reference.strip()
    if reference.startswith("["):
        name = reference.strip("[]")
        return (
            {"Measure": {"Expression": {"SourceRef": {"Entity": "_Measures"}}, "Property": name}},
            f"_Measures.{name}", name,
        )
    entity, _, rest = reference.partition("[")
    column = rest.rstrip("]")
    return (
        {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": column}},
        f"{entity}.{column}", column,
    )


# Tokens that are not words: sentence-casing "utm" gives "Utm", which reads as a typo.
LABEL_TOKENS = {"id": "ID", "utm": "UTM", "cta": "CTA", "mql": "MQL", "crm": "CRM", "url": "URL"}

# Where sentence case is right but the source name carries a word that only meant
# something to the pipeline.
LABEL_OVERRIDES = {
    "month_label": "Month", "week_start": "Week", "step_label": "Step", "stage_label": "Stage",
    "channel_group": "Channel", "campaign_id": "Campaign", "check_name": "Check", "label": "Variant",
    "utm_campaign": "UTM campaign", "missing_utm": "Missing UTM", "off_taxonomy": "Off taxonomy",
    "unregistered_campaign": "Unregistered", "subscription_id": "Subscription", "link_id": "Link",
    "failed_attempts": "Failed attempts", "risk_level": "Risk", "started_on": "Started",
    "ended_on": "Ended", "sending_domain": "Sending domain", "email_type": "Email type",
}


def display_name(column: str) -> str:
    """The label a column wears in a visual. Applied per projection rather than by
    renaming the column, because the column name is also what DAX, the
    relationships and every ``sortByColumn`` refer to."""
    if column in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[column]
    if " " in column or any(c.isupper() for c in column):
        return column
    words = [LABEL_TOKENS.get(w, w) for w in column.split("_")]
    first, rest = words[0], words[1:]
    first = first if first in LABEL_TOKENS.values() else first.capitalize()
    return " ".join([first, *rest]).strip()


def projection(reference: str, *, active: bool = False) -> dict:
    expression, query_ref, native = field_expr(reference)
    out: dict[str, object] = {"field": expression, "queryRef": query_ref, "nativeQueryRef": native}
    if not reference.strip().startswith("["):
        label = display_name(reference.split("[", 1)[1].rstrip("]"))
        if label != native:
            out["displayName"] = label
    if active:
        out["active"] = True
    return out


def colour(hex_code: str) -> dict:
    return {"solid": {"color": literal(hex_code)}}


def raw(value: str) -> dict:
    """A literal written exactly, for the integers (`8L`) some shape properties take."""
    return {"expr": {"Literal": {"Value": value}}}


def _off(*names: str) -> dict[str, list]:
    return {name: [{"properties": {"show": literal(False)}}] for name in names}


def _padding(size: int) -> list[dict]:
    return [{"properties": {side: literal(size) for side in ("top", "bottom", "left", "right")}}]


def _alt(text: str) -> list[dict]:
    return [{"properties": {"altText": literal(text)}}]


def _measure(name: str) -> dict:
    return field_expr(f"[{name}]")[0]


def _image(measure: str, alt: str, *, framed: bool) -> dict:
    containers = {"general": _alt(alt), "padding": _padding(0), **_off("title")}
    if not framed:
        containers.update(_off("background", "border", "dropShadow"))
    return {
        "visualType": "image",
        "objects": {"image": [{"properties": {
            "sourceType": literal("imageUrl"),
            "sourceUrl": {"expr": _measure(measure)},
            "fit": literal("Fit"),
        }}]},
        "visualContainerObjects": containers,
    }


BUTTON_HOW = "Ctrl+click in Power BI Desktop · click in the Power BI service"


def _button(text: dict, alt: str, link: dict) -> dict:
    return {
        "visualType": "actionButton",
        "objects": {
            "icon": [{"properties": {"show": literal(False)}, "selector": {"id": "default"}}],
            "text": [{"properties": {"show": literal(True), "text": text,
                                     "fontColor": colour(INK), "fontSize": literal(10)},
                      "selector": {"id": "default"}}],
            "fill": [{"properties": {"show": literal(True), "fillColor": colour(RAISED),
                                     "transparency": literal(0)},
                      "selector": {"id": "default"}},
                     {"properties": {"show": literal(True), "fillColor": colour(EDGE),
                                     "transparency": literal(0)},
                      "selector": {"id": "hover"}}],
            "outline": [{"properties": {"show": literal(True), "lineColor": colour(EDGE),
                                        "weight": literal(1)},
                         "selector": {"id": "default"}},
                        {"properties": {"show": literal(True), "lineColor": colour(ACCENT),
                                        "weight": literal(1)},
                         "selector": {"id": "hover"}}],
            "shape": [{"properties": {"tileShape": literal("rectangleRounded"),
                                      "roundEdge": raw("8L")}}],
        },
        "visualContainerObjects": {
            # visualLink is how a button acts; in Desktop's edit mode it takes
            # Ctrl+click, in reading view and the Service a plain click.
            "visualLink": [{"properties": {
                "show": literal(True), **link,
                "tooltip": literal(alt.removeprefix("Button. ").rstrip(".") + ". " + BUTTON_HOW),
            }}],
            "general": _alt(alt),
            "padding": _padding(0),
            **_off("background", "border", "dropShadow", "title"),
        },
    }


def chrome_visual_json(spec: dict, index: int) -> dict:
    """The frame report_chrome adds, and every card drawn as an SVG tile."""
    kind = spec["type"]
    x, y, width, height = spec["pos"]
    z = 30000 + index if (spec.get("group") or kind == "filter_panel") else 1000 + index
    if spec.get("group"):
        # A group's children are positioned relative to the group. Absolute page
        # coordinates put every one of them off the canvas.
        x, y = x - PANEL_X, y - PANEL_Y
    doc: dict = {
        "$schema": SCHEMA["visual"],
        "name": spec["id"],
        "position": {"x": x, "y": y, "z": z, "height": height, "width": width, "tabOrder": z},
    }
    if kind == "filter_panel":
        doc["visualGroup"] = {"displayName": "Filter panel", "groupMode": "ScaleMode",
                              "objects": {"background": [{"properties": {"show": literal(False)}}]}}
        doc["isHidden"] = True
        return doc

    if kind == "card":
        alt = spec["alt"]
        if alt.startswith("Card."):
            alt = "KPI tile." + alt[len("Card."):]
        visual = _image(tile_measure(spec), alt, framed=True)
    elif kind == "page_header":
        visual = _image(spec["measure"], spec["alt"], framed=False)
    elif kind == "nav":
        visual = _button(literal(spec["label"]), spec["alt"],
                         {"type": literal("PageNavigation"),
                          "navigationSection": literal(spec["target"])})
    elif kind == "filters_button":
        visual = _button({"expr": _measure(spec["measure"])}, spec["alt"],
                         {"type": literal("Bookmark"), "bookmark": literal(spec["bookmark"])})
    elif kind == "panel_close":
        visual = _button(literal(spec["label"]), spec["alt"],
                         {"type": literal("Bookmark"), "bookmark": literal(spec["bookmark"])})
    elif kind == "panel_clear":
        visual = _button(literal(spec["label"]), spec["alt"], {"type": literal("ClearAllSlicers")})
    elif kind == "panel_background":
        visual = {
            "visualType": "shape",
            "objects": {
                "shape": [{"properties": {"tileShape": literal("rectangleRounded"),
                                          "rectangleRoundedCurve": raw("12L")},
                           "selector": {"id": "default"}}],
                "fill": [{"properties": {"show": literal(True), "fillColor": colour(PANEL),
                                         "transparency": literal(0)},
                          "selector": {"id": "default"}}],
                "outline": [{"properties": {"show": literal(True), "lineColor": colour(EDGE),
                                            "weight": literal(1)},
                             "selector": {"id": "default"}}],
            },
            "visualContainerObjects": {"general": _alt(spec["alt"]), **_off("title")},
        }
    elif kind == "panel_title":
        visual = {
            "visualType": "textbox",
            "objects": {"general": [{"properties": {"paragraphs": [{"textRuns": [{
                "value": spec["text"],
                "textStyle": {"fontFamily": "Segoe UI Semibold", "fontSize": "13pt", "color": INK},
            }]}]}}]},
            "visualContainerObjects": {"general": _alt(spec["alt"]), "padding": _padding(0),
                                       **_off("background", "border", "dropShadow", "title")},
        }
    else:
        raise ValueError(f"no writer for a {kind!r} visual")
    if spec.get("group"):
        doc["parentGroupName"] = spec["group"]
    doc["visual"] = visual
    return doc


CARTESIAN = ("bar", "column", "stacked_column", "line", "area", "waterfall")


def visual_json(spec: dict, index: int) -> dict:
    kind = spec["type"]
    if kind == "card" or kind in CHROME_KINDS:
        return chrome_visual_json(spec, index)
    visual_type = VISUAL_TYPES[kind]
    x, y, width, height = spec["pos"]
    z = 1000 + index
    if spec.get("group"):
        # A slicer in the filter panel: above the page, relative to its group.
        z = 30000 + index
        x, y = x - PANEL_X, y - PANEL_Y

    query_state: dict[str, dict] = {}
    if kind == "narrative":
        query_state["Values"] = {"projections": [projection(spec["field"])]}
    elif kind == "slicer":
        query_state["Values"] = {"projections": [projection(spec["field"], active=True)]}
    elif kind == "table":
        query_state["Values"] = {"projections": [projection(c) for c in spec["columns"]]}
    elif kind == "matrix":
        query_state["Rows"] = {"projections": [projection(spec["rows"], active=True)]}
        query_state["Columns"] = {"projections": [projection(spec["columns_by"], active=True)]}
        query_state["Values"] = {"projections": [projection(v) for v in spec["values"]]}
    elif kind == "scatter":
        # A scatter's identity field goes in the role named "Category" -- the field
        # well Desktop labels "Details". Putting it in "Details" binds nothing and
        # the chart draws one point.
        query_state["Category"] = {"projections": [projection(spec["category"], active=True)]}
        query_state["X"] = {"projections": [projection(spec["x"])]}
        query_state["Y"] = {"projections": [projection(f) for f in spec["y"]]}
    elif kind == "treemap":
        # Group and Values, not Category and Y: given cartesian role names a treemap
        # binds nothing and draws an empty box.
        query_state["Group"] = {"projections": [projection(spec["x"], active=True)]}
        query_state["Values"] = {"projections": [projection(f) for f in spec["y"]]}
    else:
        query_state["Category"] = {"projections": [projection(spec["x"], active=True)]}
        query_state["Y"] = {"projections": [projection(f) for f in spec["y"]]}
        if spec.get("series"):
            query_state["Series"] = {"projections": [projection(spec["series"], active=True)]}

    container_objects: dict[str, list] = {}
    if spec.get("title"):
        container_objects["title"] = [{"properties": {"text": literal(spec["title"]), "show": literal(True)}}]
    container_objects["general"] = [{"properties": {"altText": literal(spec["alt"])}}]

    objects: dict[str, list] = {}
    if kind in CARTESIAN:
        objects["categoryAxis"] = [{"properties": {"showAxisTitle": literal(False)}}]
        value_axis = {"showAxisTitle": literal(False)}
        if "y_start" in spec:
            # Rates that all sit between 90% and 100% look identical from zero.
            value_axis["start"] = literal(spec["y_start"])
        objects["valueAxis"] = [{"properties": value_axis}]
    if kind in ("bar", "column") and len(spec["y"]) == 1 and not spec.get("series"):
        # Labels on one series only: on two, every month carries two overlapping numbers.
        objects["labels"] = [{"properties": {"show": literal(True)}}]
    if kind == "waterfall":
        # Thousands (1000): the steps are tens of thousands, and on auto units a
        # $4,800 step reads "$0.00M".
        objects["labels"] = [{"properties": {"show": literal(True), "labelDisplayUnits": literal(1000)}}]
    if kind == "funnel":
        # Display units "none" (1): on auto a stage of 438 people reads "0K".
        objects["labels"] = [{"properties": {"show": literal(True), "labelDisplayUnits": literal(1)}}]
    if kind == "line" and len(spec["y"]) == 1 and not spec.get("series"):
        objects["legend"] = [{"properties": {"show": literal(False)}}]
    if spec.get("color"):
        # A conditional colour needs the wildcard selector or it validates and
        # colours nothing: the rule has to apply to every data point, not the series.
        objects["dataPoint"] = [{
            "properties": {"fill": {"solid": {"color": {"expr": field_expr(spec["color"])[0]}}}},
            "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]},
        }]
    if kind == "narrative":
        # On the legacy card, word wrap is an object of its own (wordWrap.show), not
        # a property of labels; Microsoft's validator rejects labels.wordWrap and a
        # card without it truncates the sentence to one line with an ellipsis.
        objects["labels"] = [{"properties": {"fontSize": literal(12), "color": colour(INK)}}]
        objects["wordWrap"] = [{"properties": {"show": literal(True)}}]
        objects["categoryLabels"] = [{"properties": {"show": literal(False)}}]
    if kind == "matrix" and spec.get("totals") is False:
        objects["subTotals"] = [{"properties": {"rowSubtotals": literal(False),
                                                "columnSubtotals": literal(False)}}]
    if kind == "table" and spec.get("totals") is False:
        objects["total"] = [{"properties": {"totals": literal(False)}}]
    if kind == "slicer":
        # slicer.data.mode, not slicer.mode: a name that is merely plausible
        # validates and does nothing.
        objects["data"] = [{"properties": {"mode": literal("Dropdown")}}]
        objects["items"] = [{"properties": {"textSize": literal(10)}}]
    if kind == "slicer" and spec.get("group"):
        objects["items"] = [{"properties": {"textSize": literal(10), "background": colour(PANEL),
                                            "fontColor": colour(INK)}}]
        container_objects["background"] = [{"properties": {
            "show": literal(True), "color": colour(SURFACE), "transparency": literal(0)}}]
        container_objects["padding"] = _padding(8)

    query: dict = {"queryState": query_state}
    if spec.get("sort"):
        # `sortByColumn` in the model decides how a column's members sort; the
        # visual keeps sorting by its measure until this says otherwise. Both are
        # needed, and only one of them is visible in TMDL.
        field, direction = spec["sort"]
        expression, _ref, _native = field_expr(field)
        query["sortDefinition"] = {"sort": [{"field": expression, "direction": direction}],
                                   "isDefaultSort": False}

    body: dict = {
        "visualType": visual_type,
        "query": query,
        "drillFilterOtherVisuals": True,
        "visualContainerObjects": container_objects,
    }
    if objects:
        body["objects"] = objects

    doc = {
        "$schema": SCHEMA["visual"],
        "name": spec["id"],
        "position": {"x": x, "y": y, "z": z, "height": height, "width": width, "tabOrder": z},
        "visual": body,
    }
    if spec.get("group"):
        doc["parentGroupName"] = spec["group"]
    return doc


# --------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------

# The dark palette shared by every report in this portfolio. Same eight hues, same
# fixed order, same surface.
SURFACE = "#141416"          # visual background; the palette is validated on it
PLANE = "#0c0c0f"            # the canvas behind the visuals
HAIRLINE = "#26262a"
INK = "#f2f2f4"
INK_2 = "#a6a6ad"
INK_3 = "#7d7d85"
SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500",
          "#d55181", "#008300", "#9085e9", "#e66767"]


def theme_json() -> dict:
    """The validated palette, as a Power BI theme.

    The slot order is the colour-vision-safety mechanism, not decoration: adjacent
    slots were checked for separation under protanopia, deuteranopia and tritanopia
    against this surface, and reordering them breaks that without changing a hex.
    """
    theme: dict[str, Any] = {
        # The theme's name has to be its file name, .json included: report.json
        # refers to it that way, and the validator reports a mismatch.
        "name": THEME,
        "dataColors": list(SERIES),
        "background": PLANE,
        "foreground": INK,
        "tableAccent": SERIES[0],
        "good": "#0ca30c",
        "neutral": "#fab219",
        "bad": "#d03b3b",
        "minimum": "#0d366b",
        "center": "#3987e5",
        "maximum": "#cde2fb",
        "textClasses": {
            "title": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": INK},
            "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": INK_2},
            "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 30, "color": INK},
        },
        "visualStyles": {
            "*": {
                "*": {
                    "background": [{"show": True, "color": {"solid": {"color": SURFACE}},
                                    "transparency": 0}],
                    "border": [{"show": True, "color": {"solid": {"color": HAIRLINE}}, "radius": 8}],
                    "visualHeader": [{"show": False}],
                    "title": [{"show": True, "fontColor": {"solid": {"color": INK}},
                               "fontSize": 11, "alignment": "left"}],
                    "categoryAxis": [{"gridlineShow": False,
                                      "labelColor": {"solid": {"color": INK_3}}, "fontSize": 9}],
                    "valueAxis": [{"gridlineColor": {"solid": {"color": HAIRLINE}},
                                   "labelColor": {"solid": {"color": INK_3}}, "fontSize": 9}],
                    "legend": [{"show": True, "position": "Top",
                                "labelColor": {"solid": {"color": INK_2}}, "fontSize": 9}],
                    "labels": [{"color": {"solid": {"color": INK_2}}, "fontSize": 9}],
                }
            },
            "card": {"*": {
                "labels": [{"color": {"solid": {"color": INK}}, "fontSize": 26}],
                "categoryLabels": [{"color": {"solid": {"color": INK_3}}, "fontSize": 10}],
            }},
            "tableEx": {"*": {
                "grid": [{"gridVertical": False, "gridHorizontalColor": {"solid": {"color": HAIRLINE}},
                          "outlineColor": {"solid": {"color": HAIRLINE}}}],
                "columnHeaders": [{"fontColor": {"solid": {"color": INK_2}},
                                   "backColor": {"solid": {"color": SURFACE}}, "fontSize": 9}],
                "values": [{"fontColor": {"solid": {"color": INK}},
                            "backColor": {"solid": {"color": SURFACE}}, "fontSize": 9}],
                "total": [{"fontColor": {"solid": {"color": INK}},
                           "backColor": {"solid": {"color": RAISED}}}],
            }},
            "pivotTable": {"*": {
                "grid": [{"gridVertical": False, "gridHorizontalColor": {"solid": {"color": HAIRLINE}},
                          "outlineColor": {"solid": {"color": HAIRLINE}}}],
                "columnHeaders": [{"fontColor": {"solid": {"color": INK_2}},
                                   "backColor": {"solid": {"color": SURFACE}}, "fontSize": 9}],
                "rowHeaders": [{"fontColor": {"solid": {"color": INK_2}},
                                "backColor": {"solid": {"color": SURFACE}}, "fontSize": 9}],
                "values": [{"fontColor": {"solid": {"color": INK}},
                            "backColor": {"solid": {"color": SURFACE}}, "fontSize": 9}],
            }},
            "slicer": {"*": {
                "background": [{"show": True, "color": {"solid": {"color": SURFACE}}, "transparency": 0}],
                # `items` styles the list rows; a dropdown slicer draws its closed
                # control from these too, which is why dark slicers came out white.
                "items": [{"fontColor": {"solid": {"color": INK}},
                           "background": {"solid": {"color": RAISED}}}],
                # Off: it prints the field name under a title that already names it.
                "header": [{"show": False}],
            }},
        },
    }
    theme["visualStyles"].update(theme_styles())
    return theme


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" so the committed project is identical on Windows and Linux.
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def build(out_dir: Path, data_dir: Path = DATA_DIR) -> dict[str, int]:
    model_dir = out_dir / f"{PROJECT}.SemanticModel"
    report_dir = out_dir / f"{PROJECT}.Report"

    # --- semantic model ---------------------------------------------------
    table_names = []
    for name, meta in TABLES.items():
        csv_path = data_dir / f"{name}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"{csv_path} is missing. Run `python -m growthops.export_bi` first.")
        columns = infer_columns(csv_path)
        write_text(model_dir / "definition" / "tables" / f"{name}.tmdl",
                   table_tmdl(name, meta, columns, read_rows(csv_path)))
        table_names.append(name)

    write_text(model_dir / "definition" / "tables" / "_Measures.tmdl", measures_tmdl())
    table_names.append("_Measures")
    write_text(model_dir / "definition" / "relationships.tmdl", relationships_tmdl())
    write_text(model_dir / "definition" / "model.tmdl", model_tmdl(table_names))
    write_text(model_dir / "definition" / "database.tmdl", "database\n\tcompatibilityLevel: 1606\n")
    write_json(model_dir / ".platform", {
        "$schema": SCHEMA["platform"],
        "metadata": {"type": "SemanticModel", "displayName": PROJECT},
        "config": {"version": "2.0", "logicalId": tag("model", PROJECT)},
    })
    write_json(model_dir / "definition.pbism", {"$schema": SCHEMA["pbism"], "version": "4.2", "settings": {}})

    # --- report -----------------------------------------------------------
    visual_count = 0
    for page_index, page in enumerate(PAGES):
        page_dir = report_dir / "definition" / "pages" / page["name"]
        write_json(page_dir / "page.json", {
            "$schema": SCHEMA["page"],
            "name": page["name"],
            "displayName": page["display"],
            "displayOption": "FitToPage",
            "height": 720,
            "width": 1280,
        })
        for visual_index, spec in enumerate(page["visuals"]):
            write_json(page_dir / "visuals" / spec["id"] / "visual.json",
                       visual_json(spec, page_index * 100 + visual_index))
            visual_count += 1

    write_json(report_dir / "definition" / "pages" / "pages.json", {
        "$schema": SCHEMA["pages"],
        "pageOrder": [p["name"] for p in PAGES],
        "activePageName": PAGES[0]["name"],
    })
    saved = bookmarks(PAGES)
    for bookmark in saved:
        write_json(report_dir / "definition" / "bookmarks" / f"{bookmark['name']}.bookmark.json",
                   {"$schema": SCHEMA["bookmark"], **bookmark})
    if saved:
        write_json(report_dir / "definition" / "bookmarks" / "bookmarks.json",
                   {"$schema": SCHEMA["bookmarks"], "items": [{"name": b["name"]} for b in saved]})
    write_json(report_dir / "definition" / "version.json", {"$schema": SCHEMA["version"], "version": "2.0.0"})
    # `reportVersionAtImport` is required on every entry in themeCollection.
    versions = {"visual": "1.8.97", "report": "2.0.97", "page": "1.3.97"}
    write_json(report_dir / "definition" / "report.json", {
        "$schema": SCHEMA["report"],
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": versions, "type": "SharedResources"},
            "customTheme": {"name": THEME, "reportVersionAtImport": versions, "type": "RegisteredResources"},
        },
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": THEME, "path": THEME, "type": "CustomTheme"}]},
        ],
        "settings": {
            "useStylableVisualContainerHeader": True,
            "defaultDrillFilterOtherVisuals": True,
            "useEnhancedTooltips": True,
        },
    })
    write_json(report_dir / "StaticResources" / "RegisteredResources" / THEME, theme_json())
    # definition.pbir binds the report to its semantic model. Without it Desktop
    # refuses the whole project on open, and every check of the files that ARE
    # there still passes.
    write_json(report_dir / "definition.pbir", {
        "$schema": SCHEMA["pbir"],
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{PROJECT}.SemanticModel"}},
    })
    write_json(report_dir / ".platform", {
        "$schema": SCHEMA["platform"],
        "metadata": {"type": "Report", "displayName": PROJECT},
        "config": {"version": "2.0", "logicalId": tag("report", PROJECT)},
    })
    write_json(out_dir / f"{PROJECT}.pbip", {
        "$schema": SCHEMA["pbip"],
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{PROJECT}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })
    return {"tables": len(table_names), "measures": len(MEASURES), "relationships": len(RELATIONSHIPS),
            "pages": len(PAGES), "visuals": visual_count}


REFERENCE = ROOT / "docs" / "power-bi-measures.md"


def measure_reference() -> str:
    """The business measures as a readable page, generated so it cannot drift from the model."""
    lines = [
        "# Power BI measures",
        "",
        "Generated from `growthops/bi/model_spec.py` by `python -m growthops.bi.build_pbip`; do not edit by hand.",
        (f"{len(MEASURES)} business measures in {len({m[3] for m in MEASURES})} display folders. The report's own "
        "SVG tile, header and button measures live in the *Report UI* folder and are not listed."),
        "",
    ]
    folder = None
    for name, dax, fmt, group, description in MEASURES:
        if group != folder:
            folder = group
            lines += ["", f"## {folder}", "", "| Measure | Definition | Format | DAX |", "|---|---|---|---|"]
        code = dax.replace("\n", " ").replace("|", "\\|")
        code = " ".join(code.split())
        lines.append(f"| {name} | {description} | `{fmt or 'text'}` | `{code}` |")
    return "\n".join(lines) + "\n"


def differences(left: Path, right: Path) -> list[str]:
    """Every path under `left` whose file differs from `right`, or is missing."""
    out = []
    for path in sorted(left.rglob("*")):
        # localSettings and the .abf cache are Desktop's machine-local state and
        # are gitignored; they are not part of what the generator owns.
        if path.is_dir() or ".pbi" in path.parts:
            continue
        relative = path.relative_to(left)
        other = right / relative
        if not other.exists():
            out.append(f"missing: {relative}")
        elif not filecmp.cmp(path, other, shallow=False):
            out.append(f"differs: {relative}")
    for path in sorted(right.rglob("*")):
        if path.is_dir() or ".pbi" in path.parts:
            continue
        relative = path.relative_to(right)
        if relative.name not in KEEP and not (left / relative).exists():
            out.append(f"stale: {relative}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="regenerate into a temp dir and diff against the committed project")
    ap.add_argument("--out-dir", type=Path, default=PBIP_DIR)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = ap.parse_args(argv)

    if args.check:
        with tempfile.TemporaryDirectory() as temporary:
            stats = build(Path(temporary), args.data_dir)
            drift = differences(Path(temporary), args.out_dir)
        if not REFERENCE.exists() or REFERENCE.read_text(encoding="utf-8") != measure_reference():
            drift.append(f"differs: {REFERENCE.relative_to(ROOT)}")
        if drift:
            print("the committed project does not match the spec:", file=sys.stderr)
            for line in drift[:40]:
                print(f"  {line}", file=sys.stderr)
            print("\nrun: python -m growthops.bi.build_pbip", file=sys.stderr)
            return 1
        print(f"pbip matches the spec ({stats['visuals']} visuals, {stats['tables']} tables)")
        return 0

    if args.out_dir.exists():
        # A page removed from the spec leaves its directory behind otherwise, and
        # Power BI opens it as a page nothing generates.
        for child in args.out_dir.iterdir():
            if child.name.startswith(".") or child.name in KEEP:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()

    stats = build(args.out_dir, args.data_dir)
    write_text(REFERENCE, measure_reference())
    print(f"wrote {args.out_dir} and {REFERENCE.name}")
    for key, value in stats.items():
        print(f"  {key:16s} {value:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
