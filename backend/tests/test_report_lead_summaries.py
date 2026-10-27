"""Explicit new lead labels hide without consuming factual parentheses or old hashes."""
import copy

import pytest
from pydantic import ValidationError

from app.reference_standard import defaults
from app.report_contract import ReportDraft, report_blocks, report_hash
from app.report_rich import emphasis_ranges


def draft():
    return {
        "title": "합성 서비스 점검 보고",
        "summary": "□ (영향범위) 점검 대상은 서비스 12개이며 승인 후 조치함. [S1]",
        "sections": [
            {"heading": "현황", "paragraphs": [
                "□ (패치현황) 서비스 12개 중 8개의 패치를 마쳤고 4개는 승인 후 적용함. [S1]",
                "ㅇ (승인 후) 남은 4개를 2026. 11. 3.까지 점검함. [S1]",
                "□ (현황)이라는 표현은 원문 인용이므로 유지함.",
            ]},
            {"heading": "대응", "paragraphs": ["□ 담당자가 남은 조치의 승인 여부를 확인함."]},
            {"heading": "완료 기준", "paragraphs": ["□ 12개 서비스의 조치 확인 후 종료함."]},
        ],
        "sources": [{"id": "S1", "title": "합성 점검표", "url": "https://example.com/check", "published": "2026. 11. 1."}],
        "limitations": [],
        "lead_summaries": [
            {"target": "summary", "label": "영향범위"},
            {"target": "sections.0.paragraphs.0", "label": "패치현황"},
        ],
    }


@pytest.mark.parametrize("numbering", ["BULLETS", "NUMBERED"])
def test_declared_labels_follow_scope_and_keep_facts_citations_and_bold(numbering):
    value = draft()
    original = copy.deepcopy(value)
    settings = {**defaults(), "show_core_summary": True, "numbering_style": numbering,
                "body_parenthetical_labels": True, "body_parenthetical_scope": "square"}
    blocks = report_blocks(value, rich=True, visual_settings=settings)
    body = next(item for item in blocks if "서비스 12개 중" in item["text"])
    assert "(패치현황)" in body["text"]
    assert body["lead_summary_label"] == "패치현황"
    marker = body["text"].index("(패치현황)")
    assert all(flag & 1 for flag in emphasis_ranges(body["text"], body["emphasis"])[marker:marker + len("(패치현황)")])
    summary = next(item["text"] for item in blocks if "점검 대상은 서비스" in item["text"])
    assert "(영향범위)" not in summary
    assert "승인 후 조치함" in summary
    off = report_blocks(value, rich=True, visual_settings={**settings, "body_parenthetical_labels": False})
    texts = "\n".join(item["text"] for item in off)
    assert "(패치현황)" not in texts and "(영향범위)" not in texts
    assert "(승인 후) 남은 4개를 2026. 11. 3.까지 점검함." in texts
    assert "(현황)이라는 표현은 원문 인용이므로 유지함." in texts
    assert "서비스 12개 중 8개의 패치를 마쳤고 4개는 승인 후 적용함." in texts
    assert any(item.get("citation_ranges") for item in off)
    assert value == original and report_hash(value) == report_hash(original)


def test_declared_label_stays_before_disabled_heading_context():
    value = draft()
    value["sections"][0]["paragraphs"].insert(0, "가. 외부 서비스")
    value["lead_summaries"][1]["target"] = "sections.0.paragraphs.1"
    settings = {**defaults(), "enabled_heading_roles": "heading", "heading_structure_policy": "CONTENT_V1",
                "body_parenthetical_labels": True, "body_parenthetical_scope": "square"}
    texts = [item["text"] for item in report_blocks(value, rich=True, visual_settings=settings)]
    assert any(text.startswith("□ (패치현황) 외부 서비스: 서비스 12개 중") for text in texts)
    off = [item["text"] for item in report_blocks(value, rich=True, visual_settings={**settings, "body_parenthetical_labels": False})]
    assert any(text.startswith("□ 외부 서비스: 서비스 12개 중") for text in off)


@pytest.mark.parametrize("same_text", [False, True])
@pytest.mark.parametrize("heading_context", [False, True])
def test_repeated_lead_text_is_emphasized_only_at_its_declared_source(same_text, heading_context):
    value = draft()
    first = "□ (패치현황) 서비스 점검을 준비함."
    second = first if same_text else "□ (패치현황) 서비스 점검을 완료함."
    value["sections"][0]["paragraphs"] = [first, *(["가. 외부 서비스"] if heading_context else []), second]
    value["lead_summaries"] = [{"target": f"sections.0.paragraphs.{2 if heading_context else 1}", "label": "패치현황"}]
    original = copy.deepcopy(value)
    settings = {**defaults(), "enabled_heading_roles": "heading", "heading_structure_policy": "CONTENT_V1",
                "body_parenthetical_labels": True, "body_parenthetical_scope": "square"}
    blocks = report_blocks(value, rich=True, visual_settings=settings)
    repeated = [item for item in blocks if "(패치현황)" in item["text"]]
    assert len(repeated) == 2
    assert "lead_summary_label" not in repeated[0]
    assert repeated[0].get("emphasis", []) == []
    assert repeated[1]["lead_summary_label"] == "패치현황"
    assert {"text": "(패치현황)", "style": "bold"} in repeated[1]["emphasis"]
    if heading_context:
        assert repeated[1]["text"].startswith("□ (패치현황) 외부 서비스: ")
    assert value == original and report_hash(value) == report_hash(original)


@pytest.mark.parametrize("change", ["location", "label", "duplicate", "heading", "empty_body"])
def test_invalid_or_ambiguous_declarations_are_rejected(change):
    value = draft()
    if change == "location": value["lead_summaries"][1]["target"] = "sections.5.paragraphs.8"
    elif change == "label": value["lead_summaries"][1]["label"] = "없는 표제"
    elif change == "duplicate": value["lead_summaries"].append(copy.deepcopy(value["lead_summaries"][1]))
    elif change == "heading": value["sections"][0]["paragraphs"][0] = "가. (패치현황) 검토 범위"
    else: value["sections"][0]["paragraphs"][0] = "□ (패치현황) "
    with pytest.raises(ValidationError): ReportDraft.model_validate(value)


def test_absent_declarations_preserve_legacy_draft_and_only_new_schema_requests_them():
    value = draft()
    value.pop("lead_summaries")
    stored = ReportDraft.model_validate(value).model_dump()
    assert "lead_summaries" not in stored
    assert report_hash(ReportDraft.model_validate(stored).model_dump()) == report_hash(stored)
    legacy = report_blocks(stored, rich=True, visual_settings={**defaults(), "body_parenthetical_labels": False})
    assert any("(패치현황)" in item["text"] for item in legacy)
    schema = ReportDraft.generation_schema()
    assert "lead_summaries" in schema["required"]
    assert schema["$defs"]["ReportLeadSummary"]["required"] == ["target", "label"]
