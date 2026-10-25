"""New report readability policies preserve frozen geometry and chosen standards."""
import copy
import io
import zipfile
from xml.etree import ElementTree as E

import pytest

from app.document_standard import HP, HH
from app.reference_standard import defaults
from app.report_contract import report_blocks
from app.report_hwpx import paragraphs, scaffold
from app.report_layout import width
from app.report_rich import TABLE_LAYOUT_POLICY, content_column_widths, layout_rich_report
from test_reference_report import DRAFT, parts
from test_reports import seed


def render(*, settings=None, policy=True, note_policy=True):
    settings = defaults() if settings is None else settings
    draft = copy.deepcopy(DRAFT)
    draft["sections"][0]["tables"] = [dict(caption="합성 제품 비교", columns=["구분", "제품 A", "제품 B"], rows=[
        ["식별자", "CVE-2099-123456 관련 합성 조건 확인", "CVE-2099-654321 관련 합성 조건 확인"],
        ["영향·심각도", "담당 부서가 영향 범위를 확인한 뒤 변경을 승인함.", "시범 운영 결과와 담당자 검토를 거쳐 변경을 승인함."],
    ])]
    blocks = report_blocks(draft, rich=True, visual_settings=settings)
    if policy:
        blocks[0]["table_layout"] = TABLE_LAYOUT_POLICY
    else:
        blocks[0].pop("table_layout", None)
    if note_policy:
        blocks[0]["note_presentation"] = "reference-note-titles-v1"
    else:
        blocks[0].pop("note_presentation", None)
    raw, plan = scaffold(seed(), blocks, visual_settings=settings)
    section, _ = paragraphs(raw)
    for item in plan:
        section[item["paragraph"]].find(f"{{{HP}}}run/{{{HP}}}t").text = item["text"]
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as target:
        for entry in source.infolist():
            target.writestr(entry, E.tostring(section) if entry.filename == "Contents/section0.xml" else source.read(entry))
    return layout_rich_report(output.getvalue(), plan, visual_settings=settings), plan


def data_table(section):
    return next(table for table in section.iter(f"{{{HP}}}tbl") if table.get("colCnt") == "3")


@pytest.mark.parametrize("settings", [{}, {"table_width_percent": 80, "table_anchor_role": "circle", "table_padding_left_mm": 2.5}])
def test_content_columns_keep_table_span_and_text_but_give_explanations_more_space(settings):
    settings = {**defaults(), **settings, "line_break_unit": "WORD"}
    before = copy.deepcopy(settings)
    raw, plan = render(settings=settings)
    legacy, _ = render(settings=settings, policy=False)
    header, section = parts(raw)
    _, old_section = parts(legacy)
    table, old_table = data_table(section), data_table(old_section)
    assert table.find(f"{{{HP}}}sz").get("width") == old_table.find(f"{{{HP}}}sz").get("width")
    span = int(table.find(f"{{{HP}}}sz").get("width"))
    widths = [int(cell.find(f"{{{HP}}}cellSz").get("width")) for cell in table.find(f"{{{HP}}}tr")]
    assert widths[0] < widths[1] and widths[0] < widths[2]
    for row in table.findall(f"{{{HP}}}tr"):
        assert [int(cell.find(f"{{{HP}}}cellSz").get("width")) for cell in row] == widths
        assert sum(widths) == span
    for row in old_table.findall(f"{{{HP}}}tr"):
        old_widths = [int(cell.find(f"{{{HP}}}cellSz").get("width")) for cell in row]
        assert old_widths == [span // 3, span // 3, span - 2 * (span // 3)]
    padding = table.find(f"{{{HP}}}inMargin")
    usable = widths[1] - int(padding.get("left")) - int(padding.get("right"))
    assert usable >= width("CVE-2099-123456") * settings["table_size_pt"] * 100
    label_usable = widths[0] - int(padding.get("left")) - int(padding.get("right"))
    assert label_usable >= width("영향·심각도") * settings["table_size_pt"] * 100
    actual = ["".join(t.text or "" for run in p.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t")) for p in section.iter(f"{{{HP}}}p")]
    assert [text for text in actual if text] == [item["text"] for item in plan]
    props = {para.get("id"): para for para in header.iter(f"{{{HH}}}paraPr")}
    cell_para = table.find(f".//{{{HP}}}tc/{{{HP}}}subList/{{{HP}}}p")
    assert props[cell_para.get("paraPrIDRef")].find(f"{{{HH}}}breakSetting").get("breakLatinWord") == "KEEP_WORD"
    assert settings == before


def test_oversize_identifier_does_not_expand_table_or_starve_short_cells():
    rows = [[{"text": value} for value in row] for row in [["구분", "주소", "조치"], ["A", "https://example.com/" + "long" * 10, "확인"]]]
    result = content_column_widths(rows, 21000, text_unit=1200, padding=1000)
    assert sum(result) == 21000 and min(result) >= 2200


def test_joined_identifiers_keep_their_superscript_space_without_expanding_narrow_tables():
    identifiers, marker = "ADV-2099-123456·ADV-2099-654321", "주1)"
    rows = [[{"text": value} for value in row] for row in [
        ["구분", "제품 A", "제품 B"],
        ["영향·심각도", identifiers + marker, "확인된 합성 조건과 적용 범위를 비교하고 결과를 기록함."],
    ]]
    rows[1][1]["citation_ranges"] = [[len(identifiers), len(identifiers + marker)]]
    before = copy.deepcopy(rows)
    result = content_column_widths(rows, 48190, text_unit=1200, padding=1000)
    assert result[1] - 1000 >= (width(identifiers) + .75 * width(marker)) * 1200
    assert result[0] - 1000 >= width("영향·심각도") * 1200
    assert sum(result) == 48190
    narrow = content_column_widths(rows, 21000, text_unit=1200, padding=1000)
    assert sum(narrow) == 21000 and min(narrow) >= 2200
    assert rows == before


@pytest.mark.parametrize("explicit", [None, "JUSTIFY", "CENTER", "LEFT"])
@pytest.mark.parametrize("indent", [0, 14])
def test_compact_note_alignment_defaults_left_only_when_not_explicit(explicit, indent):
    settings = defaults()
    settings["note_left_indent_pt"] = indent
    if explicit is None:
        settings.pop("note_horizontal_align")
    else:
        settings["note_horizontal_align"] = explicit
    raw, plan = render(settings=settings)
    header, section = parts(raw)
    note = next(item for item in plan if item.get("note_source_id") and item.get("style_kind") == "note")
    paragraph = next(p for p in section.iter(f"{{{HP}}}p") if "".join(t.text or "" for t in p.iter(f"{{{HP}}}t")) == note["text"])
    props = next(p for p in header.iter(f"{{{HH}}}paraPr") if p.get("id") == paragraph.get("paraPrIDRef"))
    assert props.find(f"{{{HH}}}align").get("horizontal") == (explicit or "LEFT")
    if explicit is None:
        old, old_plan = render(settings=settings, note_policy=False)
        old_header, old_section = parts(old)
        old_note = next(item for item in old_plan if item.get("note_source_id") and item.get("style_kind") == "note")
        old_p = next(p for p in old_section.iter(f"{{{HP}}}p") if "".join(t.text or "" for t in p.iter(f"{{{HP}}}t")) == old_note["text"])
        old_props = next(p for p in old_header.iter(f"{{{HH}}}paraPr") if p.get("id") == old_p.get("paraPrIDRef"))
        assert old_props.find(f"{{{HH}}}align").get("horizontal") == "JUSTIFY"
