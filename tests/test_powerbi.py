"""Hold the generated Power BI project to its spec, its data and the rules Power BI enforces silently.

Power BI fails quietly on a report definition: a visual bound to a field that does not
exist renders empty, a mistyped formatting property is ignored, a numeric literal
without its type suffix is dropped on the next save, and a table missing from
``PBI_QueryOrder`` is never loaded. None of that raises, so each is a test here.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from growthops.bi import build_pbip
from growthops.bi.model_spec import MEASURES, RELATIONSHIPS, SORT_BY, TABLES, UNRELATED, measure_names
from growthops.bi.report_chrome import CHROME_KINDS, ui_measures
from growthops.bi.report_spec import PAGES

ROOT = Path(__file__).resolve().parent.parent
PBIP = ROOT / "dashboards" / "powerbi-project"
MODEL = PBIP / "GrowthOpsOS.SemanticModel" / "definition"
REPORT = PBIP / "GrowthOpsOS.Report" / "definition"
DATA = ROOT / "dashboards" / "powerbi-data"


def _csv(name: str) -> tuple[list[str], list[list[str]]]:
    with (DATA / f"{name}.csv").open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    return rows[0], rows[1:]


def _visuals() -> list[tuple[str, dict]]:
    return [(path.parts[-4], json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(REPORT.glob("pages/*/visuals/*/visual.json"))]


def _fields(node) -> list[tuple[str, str, str]]:
    """Every (kind, entity, property) a visual's JSON refers to."""
    found = []
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            if kind in node and isinstance(node[kind], dict) and "Property" in node[kind]:
                entity = node[kind]["Expression"]["SourceRef"]["Entity"]
                found.append((kind, entity, node[kind]["Property"]))
        for value in node.values():
            found.extend(_fields(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_fields(value))
    return found


# --------------------------------------------------------------------------- generation


def test_the_committed_project_matches_the_spec_and_the_data(tmp_path):
    build_pbip.build(tmp_path)
    assert build_pbip.differences(tmp_path, PBIP) == []


def test_the_measure_reference_is_current():
    assert (ROOT / "docs" / "power-bi-measures.md").read_text(encoding="utf-8") == build_pbip.measure_reference()


def test_the_project_carries_its_required_manifests():
    # definition.pbir binds the report to the model; without it Desktop refuses the project.
    for path in ("GrowthOpsOS.pbip", "GrowthOpsOS.Report/definition.pbir", "GrowthOpsOS.Report/.platform",
                 "GrowthOpsOS.SemanticModel/definition.pbism", "GrowthOpsOS.SemanticModel/.platform"):
        assert (PBIP / path).exists(), path


# --------------------------------------------------------------------------- model


def test_every_table_is_loaded_and_embeds_its_csv_row_for_row():
    model = (MODEL / "model.tmdl").read_text(encoding="utf-8")
    order = json.loads(re.search(r"annotation PBI_QueryOrder = (\[.*\])", model).group(1))
    for table in TABLES:
        # A table missing from PBI_QueryOrder is skipped by refresh, silently.
        assert table in order and f"ref table {table}\n" in model, table
        tmdl = (MODEL / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
        header, rows = _csv(table)
        embedded = re.findall(r"^\t{4} {8}\{(.*)\},?$", tmdl, re.MULTILINE)
        assert len(embedded) == len(rows), table
        first = [None if v == "null" else v.strip('"') for v in re.findall(r'null|"(?:[^"]|"")*"', embedded[0])]
        assert first == [v if v != "" else None for v in rows[0]], table
        for column in header:
            assert f"\tcolumn {column}\n" in tmdl, f"{table}.{column}"
            # Bound but untyped arrives as text and sorts alphabetically.
            assert f'{{"{column}", ' in tmdl, f"{table}.{column} is not typed"


def test_every_relationship_joins_real_keys_to_a_unique_side():
    for from_table, from_column, to_table, to_column in RELATIONSHIPS:
        from_header, from_rows = _csv(from_table)
        to_header, to_rows = _csv(to_table)
        keys = [row[to_header.index(to_column)] for row in to_rows]
        assert len(keys) == len(set(keys)), f"{to_table}.{to_column} is not unique"
        values = {row[from_header.index(from_column)] for row in from_rows}
        missing = values - set(keys)
        # A key outside the dimension shows up on every axis as a "(Blank)" member.
        assert not missing, f"{from_table}.{from_column} has keys missing from {to_table}: {sorted(missing)[:5]}"


def test_every_table_is_related_or_its_isolation_is_explained():
    related = {t for r in RELATIONSHIPS for t in (r[0], r[2])}
    for table in TABLES:
        assert table in related or table in UNRELATED, table


def test_the_date_table_is_marked_and_contiguous():
    tmdl = (MODEL / "tables" / "dim_date.tmdl").read_text(encoding="utf-8")
    assert "\tdataCategory: Time\n" in tmdl and "\tcolumn date\n\t\tdataType: dateTime\n\t\tisKey\n" in tmdl
    header, rows = _csv("dim_date")
    days = [row[0] for row in rows]
    from datetime import date
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    assert len(days) == (last - first).days + 1
    assert rows[-1][header.index("days_before_as_of")] == "0"
    assert sum(r[header.index("is_last_28_days")] == "True" for r in rows) == 28


def test_sort_columns_exist_and_are_numeric():
    for table, pairs in SORT_BY.items():
        tmdl = (MODEL / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
        for column, key in pairs.items():
            assert f"\tcolumn {column}\n" in tmdl
            block = tmdl.split(f"\tcolumn {key}\n", 1)[1].split("\n\n", 1)[0]
            # A text sort key orders 1, 10, 2: a waterfall's total lands mid-chart.
            assert "dataType: int64" in block, f"{table}.{key}"


@pytest.mark.parametrize("name,dax", [(m[0], m[1]) for m in MEASURES])
def test_every_measure_names_only_columns_and_measures_that_exist(name, dax):
    for table, column in re.findall(r"\b(\w+)\[([^\]]+)\]", dax):
        if table in TABLES:
            header, _ = _csv(table)
            assert column in header, f"{name}: {table}[{column}]"
    known = measure_names()
    for reference in re.findall(r"(?<![\w\]])\[([^\]]+)\]", dax):
        assert reference in known or reference.startswith("@"), f"{name}: [{reference}]"


def test_every_var_is_prefixed_to_dodge_the_undocumented_reserved_words():
    # Goal, Status, Trend, Variance, Move and Scope all fail as VAR names at runtime.
    for name, dax, *_ in MEASURES:
        for var in re.findall(r"\bVAR\s+(\w+)", dax):
            assert re.fullmatch(r"v[A-Z]\w*", var), f"{name}: VAR {var}"
    for name, dax, _image in ui_measures(PAGES, {m[0]: m[2] for m in MEASURES}):
        for var in re.findall(r"\bVAR\s+(\w+)", dax):
            assert re.fullmatch(r"v[A-Z]\w*", var), f"{name}: VAR {var}"


def test_measure_names_are_unique_and_every_measure_is_described():
    names = [m[0] for m in MEASURES]
    assert len(names) == len(set(names))
    assert all(m[4] and m[3] for m in MEASURES)


def test_money_and_rate_measures_carry_a_format():
    for name, dax, fmt, folder, _ in MEASURES:
        if folder in ("10 Captions", "12 Narrative", "13 Colours"):
            continue
        assert fmt, name


def test_svg_measures_encode_percent_before_hash():
    # A literal % left in the URI breaks decoding and every fill renders black.
    for name, dax, image in ui_measures(PAGES, {m[0]: m[2] for m in MEASURES}):
        if image:
            assert 'SUBSTITUTE(SUBSTITUTE(vSvg, "%", "%25"), "#", "%23")' in dax, name


def test_no_description_sits_on_a_relationship():
    # TMDL attaches /// to the next object; a relationship has none and the model will not load.
    assert "///" not in (MODEL / "relationships.tmdl").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- report


def test_seven_pages_in_order_opening_on_the_executive_summary():
    pages = json.loads((REPORT / "pages" / "pages.json").read_text(encoding="utf-8"))
    assert pages["pageOrder"] == [p["name"] for p in PAGES] and len(PAGES) == 7
    assert pages["activePageName"] == "p1_executive"


def test_every_field_a_visual_binds_exists_in_the_model():
    columns = {table: set(_csv(table)[0]) for table in TABLES}
    measures = measure_names() | {n for n, *_ in ui_measures(PAGES, {m[0]: m[2] for m in MEASURES})}
    for page, visual in _visuals():
        for kind, entity, prop in _fields(visual):
            if kind == "Measure":
                assert entity == "_Measures" and prop in measures, f"{page}/{visual['name']}: [{prop}]"
            else:
                assert prop in columns[entity], f"{page}/{visual['name']}: {entity}[{prop}]"


def test_query_refs_agree_with_their_fields():
    for page, visual in _visuals():
        state = visual.get("visual", {}).get("query", {}).get("queryState", {})
        for role in state.values():
            for projection in role["projections"]:
                (kind, spec), = projection["field"].items()
                entity, prop = spec["Expression"]["SourceRef"]["Entity"], spec["Property"]
                assert projection["queryRef"] == f"{entity}.{prop}" and projection["nativeQueryRef"] == prop


def test_numeric_formatting_literals_carry_their_type_suffix():
    # "11" is dropped by Desktop on the next save; it has to be "11D".
    for page, visual in _visuals():
        for literal in re.findall(r'"Literal": \{\s*"Value": "([^"]*)"', json.dumps(visual, indent=1)):
            assert not re.fullmatch(r"-?\d+(\.\d+)?", literal), f"{page}/{visual['name']}: {literal}"


def test_every_visual_has_alt_text():
    for page, visual in _visuals():
        if "visual" not in visual:
            continue  # the filter panel group
        general = visual["visual"]["visualContainerObjects"]["general"][0]["properties"]["altText"]
        assert len(general["expr"]["Literal"]["Value"]) > 12, f"{page}/{visual['name']}"


def test_one_visual_container_schema_version():
    versions = {v["$schema"] for _, v in _visuals()}
    assert versions == {build_pbip.SCHEMA["visual"]}


def test_nothing_overlaps_or_leaves_the_canvas():
    for page in PAGES:
        boxes = [(v["id"], v["pos"]) for v in page["visuals"]
                 if v["type"] not in CHROME_KINDS and not v.get("group")]
        for name, (x, y, w, h) in boxes:
            assert x >= 0 and y >= 0 and x + w <= 1280 and y + h <= 720, f"{page['name']}/{name}"
        for i, (a, (ax, ay, aw, ah)) in enumerate(boxes):
            for b, (bx, by, bw, bh) in boxes[i + 1:]:
                overlap = min(ax + aw, bx + bw) - max(ax, bx) > 0 and min(ay + ah, by + bh) - max(ay, by) > 0
                assert not overlap, f"{page['name']}: {a} overlaps {b}"


def test_conditional_colours_use_the_wildcard_selector():
    # Without it a colour rule validates and colours nothing.
    for page, visual in _visuals():
        for rule in visual.get("visual", {}).get("objects", {}).get("dataPoint", []):
            if "expr" in json.dumps(rule["properties"]):
                assert rule["selector"] == {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}


def test_every_page_but_the_first_has_a_way_back_and_every_page_but_the_last_a_way_on():
    for index, page in enumerate(PAGES):
        labels = {v.get("label") for v in page["visuals"] if v["type"] == "nav"}
        assert ("‹ Previous" in labels) == (index > 0) and ("Next ›" in labels) == (index < len(PAGES) - 1)


def test_the_theme_name_is_its_file_name():
    theme = json.loads((PBIP / "GrowthOpsOS.Report" / "StaticResources" / "RegisteredResources"
                        / build_pbip.THEME).read_text(encoding="utf-8"))
    report = json.loads((REPORT / "report.json").read_text(encoding="utf-8"))
    assert theme["name"] == build_pbip.THEME == report["themeCollection"]["customTheme"]["name"]
