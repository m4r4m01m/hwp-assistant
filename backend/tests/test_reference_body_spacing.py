"""Source paragraph separation for new plans; frozen layouts stay reproducible."""
import copy
import io
import zipfile
from xml.etree import ElementTree as E

import pytest

from app.document_standard import HH, HP
from app.reference_report import (
    BODY_SPACING_POLICY, STANDARD_STYLE, body_item, body_spacing_profile, toc_settings,
)
from app.reference_standard import defaults
from app.report_contract import report_blocks
from app.report_hwpx import paragraphs, scaffold
from app.report_rich import layout_rich_report
from test_reference_report import DRAFT, parts
from test_reports import seed


def generated(*, policy=True, settings=None, draft=None):
    settings = {**defaults(), **(settings or {})}
    blocks = report_blocks(draft or DRAFT, rich=True, visual_settings=settings)
    blocks[0].pop("body_spacing", None)
    if policy:
        blocks[0]["body_spacing"] = BODY_SPACING_POLICY
    raw, plan = scaffold(seed(), blocks, visual_settings=settings)
    section, _ = paragraphs(raw)
    for item in plan:
        section[item["paragraph"]].find(f"{{{HP}}}run/{{{HP}}}t").text = item["text"]
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as target:
        for entry in source.infolist():
            target.writestr(entry, E.tostring(section) if entry.filename == "Contents/section0.xml" else source.read(entry))
    return layout_rich_report(output.getvalue(), plan, visual_settings=settings), plan


def text(p):
    return "".join(t.text or "" for run in p.findall(f"{{{HP}}}run") for t in run.findall(f"{{{HP}}}t"))


def paragraph_named(header, section, starts):
    paragraph = next(p for p in section.iter(f"{{{HP}}}p") if text(p).startswith(starts))
    props = next(p for p in header.iter(f"{{{HH}}}paraPr") if p.get("id") == paragraph.get("paraPrIDRef"))
    margin = props.find(f"{{{HP}}}switch/{{{HP}}}case/{{{HH}}}margin")
    return paragraph, {node.tag.split("}")[-1]: int(node.get("value")) / 100 for node in margin}


def test_source_spacing_is_restored_only_for_new_plans_and_text_stays_identical():
    raw, plan = generated()
    old, old_plan = generated(policy=False)
    header, section = parts(raw)
    old_header, old_section = parts(old)
    assert body_spacing_profile() == {"heading": 32, "after_heading": 25.6, "body": 22.4, "support": 9.6}
    assert paragraph_named(header, section, "1. 수집 현황")[1]["prev"] == 0
    assert paragraph_named(header, section, "□ 2026년")[1]["prev"] == 25.6
    assert paragraph_named(header, section, "ㅇ 보완")[1]["prev"] == 9.6
    assert paragraph_named(header, section, "2. 정비 방안")[1]["prev"] == 32
    assert paragraph_named(header, section, "자료 상태")[1]["prev"] == 22.4
    assert paragraph_named(old_header, old_section, "□ 2026년")[1]["prev"] == 0
    assert paragraph_named(old_header, old_section, "2. 정비 방안")[1]["prev"] == 0
    assert [text(p) for p in section.iter(f"{{{HP}}}p") if text(p)] == [text(p) for p in old_section.iter(f"{{{HP}}}p") if text(p)]
    assert [p["text"] for p in plan] == [p["text"] for p in old_plan]
    assert toc_settings(header)["body_spacing"] == BODY_SPACING_POLICY
    assert "body_spacing" not in toc_settings(old_header)


@pytest.mark.parametrize("settings,expected", [({"square_before_pt": 8}, 8), ({"heading_after_pt": 7}, 0)])
def test_user_paragraph_spacing_replaces_automatic_gap_without_double_addition(settings, expected):
    raw, _ = generated(settings=settings)
    header, section = parts(raw)
    assert paragraph_named(header, section, "□ 2026년")[1]["prev"] == expected
    if "heading_after_pt" in settings:
        assert paragraph_named(header, section, "1. 수집 현황")[1]["next"] == 7


def test_source_supported_short_closing_parenthesis_is_a_heading_without_text_rewrite():
    for value, role in (("1) 적용 범위", "minor_heading"), ("(1) 적용 범위", "minor_heading"),
                        ("가) 세부 조건", "numbered_4"), ("(가) 세부 조건", "numbered_4")):
        assert body_item(value, defaults(), [0, 0, 0, 0]) == (value, role)


def test_body_emphasis_does_not_recolor_or_bolden_matching_titles_in_new_plan():
    draft = copy.deepcopy(DRAFT)
    draft["title"] = "자료 정비 검수"
    draft["sections"][0]["heading"] = "자료 정비"
    draft["sections"][0]["paragraphs"] = ["가. 자료 정비", "1) 자료 정비 범위", "□ 자료 정비 담당자가 12건을 확인함."]
    draft["emphasis"] = [{"text": "자료 정비", "style": "bold_blue"}]
    raw, _ = generated(draft=draft)
    header, section = parts(raw)
    chars = {c.get("id"): c for c in header.iter(f"{{{HH}}}charPr")}
    for prefix in ("자료 정비 검수", "1. 자료 정비", "가. 자료 정비", "1) 자료 정비"):
        p = next(p for p in section.iter(f"{{{HP}}}p") if text(p).startswith(prefix))
        assert all(chars[r.get("charPrIDRef")].get("textColor") == "#000000" for r in p.findall(f"{{{HP}}}run"))
        if prefix != "1) 자료 정비":
            assert all(chars[r.get("charPrIDRef")].find(f"{{{HH}}}bold") is None for r in p.findall(f"{{{HP}}}run"))
    body = next(p for p in section.iter(f"{{{HP}}}p") if text(p).startswith("□ 자료 정비"))
    assert any(chars[r.get("charPrIDRef")].get("textColor") == "#0000FF" for r in body.findall(f"{{{HP}}}run"))


def test_automatic_spacing_does_not_leave_a_blank_line_at_new_page_start():
    raw, _ = generated(settings={"page_height_mm": 120})
    header, section = parts(raw)
    breaks = [p for p in section if p.get("pageBreak") == "1"]
    assert breaks
    for paragraph in breaks:
        if text(paragraph):
            assert paragraph_named(header, section, text(paragraph))[1]["prev"] == 0


def test_consecutive_heading_chain_moves_with_first_body_to_the_next_page():
    draft = copy.deepcopy(DRAFT)
    draft["sections"][0]["tables"] = []
    draft["sections"][1]["paragraphs"] = ["가. 세부 방안", "1) 세부 범위", "□ 업무 자료를 확인함."]
    raw, _ = generated(settings={"page_height_mm": 185}, draft=draft)
    header, section = parts(raw)
    chain = [paragraph_named(header, section, prefix)[0] for prefix in ("2. 정비 방안", "가. 세부 방안", "1) 세부 범위", "□ 업무")]
    assert [p.get("pageBreak") for p in chain] == ["1", "0", "0", "0"]


def test_split_continuation_is_not_separated_as_a_new_body_item():
    draft = copy.deepcopy(DRAFT)
    draft["sections"][0]["paragraphs"] = ["□ " + "자료를 확인하고 조건을 보존함. " * 22]
    draft["sections"][0]["tables"] = []
    raw, plan = generated(settings={"page_width_mm": 420, "page_height_mm": 594}, draft=draft)
    header, section = parts(raw)
    continuation = next(p for p in plan if p.get("style_kind") == "square_continuation")
    p, margins = paragraph_named(header, section, continuation["text"])
    assert p.get("pageBreak") == "0"
    assert margins["prev"] == 0


@pytest.mark.parametrize("marker,role", [("□", "square"), ("ㅇ", "circle"), ("-", "dash"), ("․", "dot")])
def test_split_item_after_spacing_replaces_next_automatic_gap(marker, role):
    draft = copy.deepcopy(DRAFT)
    draft["sections"][0]["paragraphs"] = [
        marker + " " + "자료를 확인하고 조건을 보존함. " * 22,
        "□ 다음 항목을 확인함.",
    ]
    draft["sections"][0]["tables"] = []
    raw, plan = generated(settings={role + "_after_pt": 7, "page_width_mm": 420, "page_height_mm": 594}, draft=draft)
    header, section = parts(raw)
    continuation = next(p for p in plan if p.get("style_kind") == role + "_continuation")
    assert paragraph_named(header, section, continuation["text"])[1]["next"] == 7
    following, margins = paragraph_named(header, section, "□ 다음 항목")
    assert following.get("pageBreak") == "0"
    assert margins["prev"] == 0


def test_unknown_persisted_spacing_policy_is_rejected():
    raw, _ = generated()
    header, _ = parts(raw)
    marker = next(p for p in header.iter(f"{{{HH}}}style") if p.get("name") == STANDARD_STYLE)
    marker.set("engName", marker.get("engName").replace(BODY_SPACING_POLICY, "future-unknown-policy"))
    with pytest.raises(ValueError, match="spacing policy"):
        toc_settings(header)


def test_reference_captions_preserve_supplied_text_without_app_added_numbering():
    draft = copy.deepcopy(DRAFT)
    draft["sections"][0]["diagrams"] = [{"caption": "자료 확인 흐름", "steps": ["수집", "확인"]}]
    original = copy.deepcopy(draft)
    current = report_blocks(draft, rich=True, visual_settings=defaults())
    legacy = report_blocks(draft, rich=True)
    assert [p["text"] for p in current if p.get("visual_caption")] == ["자료 상태", "자료 확인 흐름"]
    assert [p["text"] for p in legacy if p.get("visual_caption")] == ["표 1. 자료 상태", "그림 1. 자료 확인 흐름"]
    assert {p["visual_id"] for p in current if p.get("visual_id")} == {"table-1", "diagram-1"}
    assert draft == original
