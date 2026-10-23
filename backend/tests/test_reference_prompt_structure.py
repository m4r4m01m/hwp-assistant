"""New V3 prose uses existing reference hierarchy; stored drafts stay intact."""
import copy

import pytest

from app.reference_standard import defaults
from app.report_contract import ReportDraft, report_blocks, report_hash
from app.report_runner import PROMPT, writing_prompt
from app.report_templates import catalog


@pytest.mark.parametrize("preset", ["appendix-1", "appendix-2", "appendix-3"])
@pytest.mark.parametrize("template_id", ["standard-business", "executive-decision", "response-plan"])
def test_reference_subheading_guidance_reaches_each_purpose_without_changing_frozen_input(preset, template_id):
    template = next(item for item in catalog()["items"] if item["id"] == template_id)
    value = {"template": template, "visual_standard": {"revision": 3, "settings": defaults(preset)}}
    original_hash = report_hash(value)
    prompt = writing_prompt(value)

    assert "'가. ...', '나. ...'" in prompt
    assert "'(1) ...', '(2) ...'" in prompt
    assert "Keep substantive □/ㅇ/- prose beneath these headings" in prompt
    assert "Do not duplicate the section heading" in prompt
    assert "9 paragraphs and 1500 characters per section" in prompt
    assert "preserving important facts, conditions and citations" in prompt
    assert "without adding automatic table/figure numbers" in prompt
    assert "Write new summaries, body items and limitations in concise Korean business-report prose" in prompt
    assert "Do not mechanically append '함' to every sentence" in prompt
    assert "a conditional action remains conditional" in prompt
    assert "source IDs and exact quotations" in prompt
    assert "not by rewriting a frozen draft or formatted document" in prompt
    assert ("automatic TOC limit of 30 headings in total" in prompt) == (preset != "appendix-1")
    assert report_hash(value) == original_hash


def test_numbered_body_does_not_receive_a_second_conflicting_numbered_hierarchy():
    settings = {**defaults(), "numbering_style": "NUMBERED"}
    prompt = writing_prompt({"visual_standard": {"revision": 3, "settings": settings}})
    assert "The chosen hierarchy is NUMBERED" in prompt
    assert "'가. ...', '나. ...'" not in prompt
    assert writing_prompt({}) == PROMPT
    assert writing_prompt({"visual_standard": {"revision": 2, "settings": {}}}) == PROMPT


def test_reference_style_and_review_feedback_do_not_mutate_the_frozen_source_draft():
    value = {
        "visual_standard": {"revision": 3, "settings": defaults()},
        "revision": {
            "draft": {"title": "미확인 자료 검토", "sections": [{"paragraphs": [
                "담당자 확인 후에만 자료 6건을 2026. 11. 13.까지 배포할 예정입니다. [S1]",
                "자료에는 ‘승인 전 배포 금지’라고 명시되어 있습니다. [S1]",
            ]}]},
            "feedback": "보고서 문체로 다듬어 주세요.",
        },
    }
    frozen = copy.deepcopy(value)
    prompt = writing_prompt(value)

    assert "a plan must not become a completed action" in prompt
    assert "Keep every existing source entry exactly" in prompt
    assert "The previous draft and any formatted or saved document remain unchanged" in prompt
    assert value == frozen


def test_explicit_subheadings_use_existing_roles_and_preserve_facts_and_citations():
    draft = {
        "title": "자료 정비 계획", "summary": "합성 자료의 정비 일정을 검토함.",
        "sections": [
            {"heading": "수집 현황", "paragraphs": [
                "가. 수집 대상", "(1) 접수 자료", "□ 2026년 10월 접수 자료 12건을 확인함. [S1]",
                "(2) 보완 자료", "□ 승인 후 보완 자료 3건을 추가 확인함. [S1]",
                "나. 확인 일정", "□ 매주 금요일 15:00에 담당자가 확인함.",
                "ㅇ 승인 전에는 자료를 배포하지 않음.", "- 조건 변경 시 담당자에게 알림."]},
            {"heading": "정비 방안", "paragraphs": ["□ 담당자가 누락 항목을 확인함."]},
            {"heading": "완료 기준", "paragraphs": ["□ 접수 자료와 보완 자료의 확인을 마치면 완료함."]},
        ],
        "sources": [{"id": "S1", "title": "합성 접수 목록", "url": "https://example.com/intake", "published": "2026. 10. 1."}],
        "limitations": ["시험용 합성 자료임."],
    }
    original = copy.deepcopy(draft)
    ReportDraft.model_validate(draft)
    blocks = report_blocks(draft, concise=True, rich=True, visual_settings=defaults())
    by_text = {item["text"]: item for item in blocks}

    assert by_text["가. 수집 대상"]["style_kind"] == "subheading"
    assert by_text["나. 확인 일정"]["style_kind"] == "subheading"
    assert by_text["(1) 접수 자료"]["style_kind"] == "minor_heading"
    assert by_text["(2) 보완 자료"]["style_kind"] == "minor_heading"
    assert by_text["□ 2026년 10월 접수 자료 12건을 확인함. *"]["style_kind"] == "square"
    assert by_text["□ 승인 후 보완 자료 3건을 추가 확인함. *"]["style_kind"] == "square"
    assert by_text["ㅇ 승인 전에는 자료를 배포하지 않음."]["style_kind"] == "circle"
    assert by_text["- 조건 변경 시 담당자에게 알림."]["style_kind"] == "dash"
    assert draft == original
