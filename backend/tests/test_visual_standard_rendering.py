"""Style rendering checks use synthetic content, not model or engine evidence."""
import base64
import copy
import io
import zipfile
from xml.etree import ElementTree as E

import pytest

from app.document_standard import HC, HH, HP, format_signature, layout_metrics
from app.report_contract import report_blocks
from app.report_hwpx import scaffold
from app.report_layout import layout_report
from app.report_rich import layout_rich_report
from report_rich_fixture import RICH_DRAFT
from test_reports import seed, synthetic_output


SETTINGS = {
    "title_size_pt": 24, "title_line_percent": 180, "title_after_pt": 22,
    "heading_size_pt": 18, "heading_line_percent": 170, "heading_after_pt": 16,
    "body_size_pt": 16, "body_line_percent": 190, "body_after_pt": 11,
    "source_size_pt": 11, "source_line_percent": 140, "source_after_pt": 8,
    "square_indent_pt": 2, "circle_indent_pt": 12, "dash_indent_pt": 22,
    "margin_top_mm": 25, "margin_bottom_mm": 15,
    "margin_left_mm": 25, "margin_right_mm": 25,
    "table_size_pt": 14, "table_line_percent": 150,
    "table_header_fill": "#F1E8D2", "table_border_color": "#6A502D",
}


def applied(data, plan):
    commands = [{**p, "command_id": str(i)} for i, p in enumerate(plan)]
    result = synthetic_output({"id": "synthetic", "scaffold_base64": base64.b64encode(data).decode(), "plan": commands})
    return base64.b64decode(result["output_base64"])


def parsed(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return E.fromstring(z.read("Contents/header.xml")), E.fromstring(z.read("Contents/section0.xml"))


def logical_text(section):
    return ["".join(t.text or "" for run in p.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t"))
            for p in section.iter(f"{{{HP}}}p")]


def test_custom_roles_keep_hanging_indents_and_blue_title_rules():
    blocks = [{"kind": kind, "text": text, "page_break": False} for kind, text in (
        ("title", "합성 표준 예시"), ("heading", "1. 검토 쟁점"),
        ("body", "일반 본문"), ("body", "□ (범위) 확인 대상"),
        ("body", "ㅇ 담당 부서"), ("body", "- 완료 조건"), ("source", "합성 자료"))]
    blocks.append({"kind": "body", "text": "계속되는 설명", "style_kind": "circle_continuation", "page_break": False})
    data, plan = scaffold(seed(), blocks, visual_settings=SETTINGS)
    signature = format_signature(data, plan)
    title, heading, body, square, circle, dash, source, continuation = signature["paragraphs"]
    assert [(p["height"], p["line"], dict(p["margins"])["next"]) for p in (title, heading, body, source)] == [
        ("2400", "180", "2200"), ("1800", "170", "1600"),
        ("1600", "190", "1100"), ("1100", "140", "800")]
    for para, left, intent in ((square, 200, -2250), (circle, 1200, -2250),
                               (dash, 2200, -1250), (continuation, 3450, 0)):
        margins = dict(para["margins"])
        assert (margins["left"], margins["intent"]) == (str(left), str(intent))
        assert para["height"] == "1600" and para["line"] == "190"
    assert ("topBorder", "SOLID", "1.0 mm", "#437FC1") in title["borders"]
    assert ("bottomBorder", "SOLID", "1.0 mm", "#437FC1") in title["borders"]
    assert all(b[1] == "NONE" for b in title["borders"] if b[0] in ("leftBorder", "rightBorder"))


def test_optional_none_keeps_legacy_scaffold_and_both_layout_outputs():
    blocks = report_blocks(copy.deepcopy(RICH_DRAFT), concise=True, rich=True)
    first, plan = scaffold(seed(), blocks)
    explicit, same_plan = scaffold(seed(), blocks, visual_settings=None)
    assert first == explicit and plan == same_plan
    text = applied(first, plan)
    for layout in (layout_report, layout_rich_report):
        assert layout(text, plan) == layout(text, plan, visual_settings=None)


def test_custom_table_width_cells_colors_and_complete_text_are_used():
    blocks = report_blocks(copy.deepcopy(RICH_DRAFT), concise=True, rich=True)
    data, plan = scaffold(seed(), blocks, visual_settings=SETTINGS)
    result = layout_rich_report(applied(data, plan), plan, visual_settings=SETTINGS)
    header, section = parsed(result)
    assert [t for t in logical_text(section) if t] == [p["text"] for p in plan]
    page = section.find(f".//{{{HP}}}pagePr")
    margins = page.find(f"{{{HP}}}margin")
    available = int(page.get("width")) - int(margins.get("left")) - int(margins.get("right"))
    assert int(margins.get("top")) == int(25 * 7200 / 25.4)
    chars = {c.get("id"): c for c in header.iter(f"{{{HH}}}charPr")}
    paras = {p.get("id"): p for p in header.iter(f"{{{HH}}}paraPr")}
    fills = {f.get("id"): f for f in header.iter(f"{{{HH}}}borderFill")}
    for table in section.iter(f"{{{HP}}}tbl"):
        assert int(table.find(f"{{{HP}}}sz").get("width")) == available
        for row in table.findall(f"{{{HP}}}tr"):
            assert sum(int(c.find(f"{{{HP}}}cellSz").get("width")) for c in row) == available
        for cell in table.iter(f"{{{HP}}}tc"):
            p = cell.find(f"{{{HP}}}subList/{{{HP}}}p")
            assert all(chars[r.get("charPrIDRef")].get("height") == "1400" for r in p.findall(f"{{{HP}}}run"))
            assert all(e.get("value") == "150" for e in paras[p.get("paraPrIDRef")].iter(f"{{{HH}}}lineSpacing"))
            fill = fills[cell.get("borderFillIDRef")]
            assert all(e.get("color") == "#6A502D" for e in fill if e.tag.endswith("Border"))
            brush = fill.find(f"{{{HC}}}fillBrush/{{{HC}}}winBrush")
            if brush is not None:
                assert brush.get("faceColor") == "#F1E8D2"


def test_custom_font_spacing_and_page_margins_change_wrapping_and_breaks():
    blocks = [{"kind": "body", "text": "□ " + "검증 자료와 담당자 확인을 수행함. " * 10, "page_break": False}
              for _ in range(8)]
    compact = {**SETTINGS, "body_size_pt": 12, "body_line_percent": 130,
               "margin_top_mm": 20, "margin_bottom_mm": 10,
               "margin_left_mm": 20, "margin_right_mm": 20}
    spacious = {**SETTINGS, "body_size_pt": 18, "body_line_percent": 200}
    outputs = []
    for settings in (compact, spacious):
        raw, plan = scaffold(seed(), blocks, visual_settings=settings)
        output = layout_report(applied(raw, plan), plan, visual_settings=settings)
        _, section = parsed(output)
        assert logical_text(section) == [""] + [p["text"] for p in plan]
        outputs.append((sum(p.get("pageBreak") == "1" for p in section),
                        len(list(section.iter(f"{{{HP}}}lineBreak")))))
        width, budget = layout_metrics(section, settings)
        assert width > 300 and budget <= 625
    assert outputs[1][0] > outputs[0][0]
    assert outputs[1][1] > outputs[0][1]


def test_oversized_custom_table_is_rejected_without_dropping_content():
    blocks = report_blocks(copy.deepcopy(RICH_DRAFT), concise=True, rich=True)
    settings = {**SETTINGS, "table_size_pt": 32, "table_line_percent": 240,
                "margin_left_mm": 40, "margin_right_mm": 40}
    raw, plan = scaffold(seed(), blocks, visual_settings=settings)
    with pytest.raises(ValueError, match="Table exceeds a report page"):
        layout_rich_report(applied(raw, plan), plan, visual_settings=settings)
