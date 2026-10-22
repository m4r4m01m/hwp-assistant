"""Long source title frames retain ink clearance; frozen plans stay unchanged."""
import copy
import math
from xml.etree import ElementTree as E

import pytest

from app.document_standard import HH, HP, paragraph_line_factor
from app.reference_report import TITLE_OVERFLOW_POLICY, title_layout_cost
from app.reference_standard import defaults
from app.report_contract import report_blocks
from app.report_hwpx import body_text
import test_report_cover as cover_fixture
from test_reference_report import DRAFT, parts


LONG_TITLE = "가상 금융회사 합성 훈련의 긴 한글 제목과 업무 연속성 및 정보 보호 개선 과제를 함께 검토하는 경영 보고서"


def generated(monkeypatch, preset, title, *, policy=True, **settings):
    draft = copy.deepcopy(DRAFT)
    draft["title"] = title
    monkeypatch.setattr(cover_fixture, "DRAFT", draft)

    def blocks(*args, **kwargs):
        result = report_blocks(*args, **kwargs)
        if not policy:
            result[0].pop("title_overflow")
        return result

    monkeypatch.setattr(cover_fixture, "report_blocks", blocks)
    raw, plan = cover_fixture.generated(preset, {}, **settings)
    header, section = parts(raw)
    table = section[1].find(f".//{{{HP}}}tbl")
    return header, table, plan


def geometry(header, table):
    cell = table.find(f"{{{HP}}}tr/{{{HP}}}tc")
    paragraph = cell.find(f"{{{HP}}}subList/{{{HP}}}p")
    chars = {c.get("id"): c for c in header.iter(f"{{{HH}}}charPr")}
    para = next(p for p in header.iter(f"{{{HH}}}paraPr") if p.get("id") == paragraph.get("paraPrIDRef"))
    size = int(chars[paragraph.find(f"{{{HP}}}run").get("charPrIDRef")].get("height"))
    lines = 1 + len(list(paragraph.iter(f"{{{HP}}}lineBreak")))
    margins = para.find(f"{{{HP}}}switch/{{{HP}}}case/{{{HH}}}margin")
    vertical = sum(int(n.get("value")) for n in margins if n.tag in (f"{{{HH}}}prev", f"{{{HH}}}next"))
    content = math.ceil(size + (lines - 1) * size * paragraph_line_factor(para) + vertical)
    padding = {side: int(cell.find(f"{{{HP}}}cellMargin").get(side)) for side in ("top", "bottom")}
    fill = next(f for f in header.iter(f"{{{HH}}}borderFill") if f.get("id") == cell.get("borderFillIDRef"))
    return paragraph, cell, size, content, padding, fill


@pytest.mark.parametrize("preset", ["appendix-1", "appendix-2", "appendix-3"])
def test_expanded_title_reserves_clearance_inside_rule_without_changing_text_or_font(monkeypatch, preset):
    header, table, plan = generated(monkeypatch, preset, LONG_TITLE)
    paragraph, cell, size, content, padding, fill = geometry(header, table)
    assert plan[0]["title_overflow"] == TITLE_OVERFLOW_POLICY
    assert body_text(paragraph) == LONG_TITLE
    assert size == (2200 if preset == "appendix-1" else 2400)
    assert len(list(paragraph.iter(f"{{{HP}}}lineBreak"))) >= 2
    for side in ("top", "bottom"):
        stroke = float(fill.find(f"{{{HH}}}{side}Border").get("width").split()[0]) * 7200 / 25.4
        assert padding[side] - stroke / 2 >= 300
    assert int(cell.find(f"{{{HP}}}cellSz").get("height")) == content + sum(padding.values())
    assert table.find(f"{{{HP}}}sz").get("height") == cell.find(f"{{{HP}}}cellSz").get("height")
    old_header, old_table, old_plan = generated(monkeypatch, preset, LONG_TITLE, policy=False)
    old_paragraph, _, old_size, _, old_padding, _ = geometry(old_header, old_table)
    assert "title_overflow" not in old_plan[0]
    assert body_text(old_paragraph) == body_text(paragraph) and old_size == size
    assert old_padding == {"top": 141, "bottom": 141}
    prefix = "title" if preset == "appendix-1" else "cover"
    assert int(old_table.find(f"{{{HP}}}sz").get("height")) == round(defaults(preset)[prefix + "_frame_height_mm"] * 7200 / 25.4)


@pytest.mark.parametrize("preset", ["appendix-1", "appendix-2", "appendix-3"])
def test_ordinary_single_line_source_title_geometry_is_unchanged(monkeypatch, preset):
    _, current, _ = generated(monkeypatch, preset, "자료 정비 검수")
    _, old, _ = generated(monkeypatch, preset, "자료 정비 검수", policy=False)
    assert E.tostring(current) == E.tostring(old)


def test_large_user_frame_is_preserved_and_expanded_frame_respects_selected_rule(monkeypatch):
    _, table, _ = generated(monkeypatch, "appendix-2", LONG_TITLE, cover_frame_height_mm=60)
    assert int(table.find(f"{{{HP}}}sz").get("height")) == round(60 * 7200 / 25.4)
    assert table.find(f"{{{HP}}}tr/{{{HP}}}tc/{{{HP}}}cellMargin").get("top") == "141"
    header, table, _ = generated(monkeypatch, "appendix-2", LONG_TITLE, cover_rule_width_mm=2, cover_rule_color="#123456")
    _, _, _, _, padding, fill = geometry(header, table)
    assert fill.find(f"{{{HH}}}topBorder").get("color") == "#123456"
    assert padding["top"] - 2 * 7200 / 25.4 / 2 >= 300


def test_appendix1_page_budget_includes_expanded_frame_padding():
    settings = {**defaults("appendix-1"), "title_line_percent": 100}
    old = title_layout_cost(settings, 66)
    current = title_layout_cost(settings, 66, overflow_padding=True)
    assert current > old
    assert title_layout_cost(settings, 22, overflow_padding=True) == title_layout_cost(settings, 22)
