import copy
import re

import pytest
from jsonschema import Draft202012Validator

from app.reference_standard import defaults
from app.report_contract import ReportDraft, report_blocks, report_hash
from app.report_notes import EXPLANATORY_POLICY, SECTION_GROUPING_POLICY
from app.report_research import apply_research_policy
from report_explanatory_fixture import EXPLANATORY_DRAFT
from test_reference_body_spacing import generated
from test_reference_report import parts


def test_explanations_follow_each_paragraph_and_table_with_local_numbers():
    draft = copy.deepcopy(EXPLANATORY_DRAFT)
    original = copy.deepcopy(draft)
    plan = report_blocks(draft, rich=True, visual_settings={**defaults(), "citation_note_abbreviated": True})
    assert plan[0]["note_presentation"] == EXPLANATORY_POLICY
    first = next(i for i, item in enumerate(plan) if item["text"].startswith("□ 추가 확인"))
    assert plan[first]["text"] == "□ 추가 확인*은 기존 사고의 재점검 결과임."
    assert plan[first + 1]["text"] == "* 추가 확인은 신규 사고 발생을 뜻하지 않음."
    assert plan[first + 2]["text"].startswith("ㅇ 영향 범위주1)와 조치 완료주2)")
    assert plan[first + 3]["text"].startswith("주1) 영향 범위")
    assert plan[first + 4]["text"].startswith("2) 조치 완료")
    table_end = max(i for i, item in enumerate(plan) if item.get("visual_kind") == "table")
    assert plan[table_end + 1]["text"] == plan[first + 1]["text"]
    assert plan[table_end + 1]["note_scope"] != plan[first + 1]["note_scope"]
    text = "\n".join(item["text"] for item in plan)
    assert not re.search(r"\[[SN]\d+\]|확인 사항|검토 메모|합성 뉴스|example.invalid", text)
    assert draft == original
    raw, _ = generated(draft=draft, settings={"citation_note_abbreviated": True})
    _, section = parts(raw)
    xml_text = "".join(section.itertext())
    assert "신규 사고 발생을 뜻하지 않음" in xml_text and "합성 뉴스" not in xml_text


def test_no_notes_means_no_automatic_source_notes_but_explicit_bibliography_is_honored():
    draft = copy.deepcopy(EXPLANATORY_DRAFT)
    draft["notes"] = []
    for section in draft["sections"]:
        section["paragraphs"] = [re.sub(r"\[N\d+\]", "", p) for p in section["paragraphs"]]
        for table in section.get("tables", []):
            table["rows"] = [[re.sub(r"\[N\d+\]", "", cell) for cell in row] for row in table["rows"]]
    plan = report_blocks(draft, rich=True, visual_settings=defaults())
    assert not any(item.get("note_source_id") for item in plan)
    explicit = report_blocks(draft, rich=True, visual_settings={**defaults(), "reference_list": "ALL"})
    assert sum(bool(item.get("unreferenced_source_id")) for item in explicit) == 2


def test_legacy_hash_and_old_note_rendering_remain_unchanged():
    from test_report_note_groups import DRAFT
    old = ReportDraft.model_validate(DRAFT).model_dump()
    assert "notes" not in old
    assert report_hash(old) == report_hash(ReportDraft.model_validate(old).model_dump())
    assert report_blocks(old, rich=True, visual_settings=defaults())[0]["note_grouping"] == SECTION_GROUPING_POLICY


def test_emphasis_across_explanatory_anchor_uses_local_marker():
    draft = copy.deepcopy(EXPLANATORY_DRAFT)
    draft["emphasis"] = [{"text": "추가 확인[N1]", "style": "bold"}]
    plan = report_blocks(draft, rich=True, visual_settings=defaults())
    first = next(item for item in plan if item["text"].startswith("□ 추가 확인"))
    assert {"text": "추가 확인*", "style": "bold"} in first["emphasis"]


@pytest.mark.parametrize("mutation", ["missing", "unused", "duplicate", "headline", "url", "nested", "unknown_source"])
def test_explanatory_note_references_are_bounded_and_cannot_be_bibliographic(mutation):
    draft = copy.deepcopy(EXPLANATORY_DRAFT)
    if mutation == "missing": draft["notes"].pop()
    if mutation == "unused": draft["notes"].append({"id": "N4", "text": "사용하지 않는 설명"})
    if mutation == "duplicate": draft["notes"].append(draft["notes"][0])
    if mutation == "headline": draft["notes"][0]["text"] = draft["sources"][0]["title"]
    if mutation == "url": draft["notes"][0]["text"] = "https://example.invalid/source"
    if mutation == "nested": draft["notes"][0]["text"] = "설명[N2]"
    if mutation == "unknown_source": draft["notes"][0]["text"] = "설명[S99]"
    with pytest.raises(ValueError): ReportDraft.model_validate(draft)


def test_generation_schema_and_research_notice_remain_visible_to_reviewer_not_printed():
    schema = Draft202012Validator(ReportDraft.generation_schema())
    draft = ReportDraft.model_validate(EXPLANATORY_DRAFT).model_dump()
    assert schema.is_valid(draft)
    assert not schema.is_valid({key: value for key, value in draft.items() if key != "notes"})
    annotated, _ = apply_research_policy({"research": True, "research_policy": "allow_unverified"}, draft,
        {"completed": True, "web_search_calls": 0, "web_search_started": 0, "web_search_incomplete": 0})
    assert annotated["research_notice"]["warnings"] and annotated["limitations"] == draft["limitations"]
    assert annotated["sources"] == draft["sources"]
    text = "\n".join(item["text"] for item in report_blocks(annotated, rich=True, visual_settings=defaults()))
    assert "확인 사항" not in text and "미검증 후보" not in text
