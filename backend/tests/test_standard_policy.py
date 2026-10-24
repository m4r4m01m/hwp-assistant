import copy
import pytest
from pydantic import ValidationError
from app.reference_standard import VisualSettingsV3, defaults
from app.standard_policy import OPTIONAL_KEYS
from app.report_contract import report_blocks, report_hash
from app.report_runner import writing_prompt
from app.reference_report import report_styles
from app.document_standard import HH
from app.standard_samples import SAMPLE
from test_standard_samples import prepare, finish, registration
from test_collaboration import secured


def test_optional_controls_do_not_change_historical_settings_hash():
    old = defaults()
    parsed = VisualSettingsV3.model_validate(old).model_dump(mode="json")
    assert parsed == old
    assert not set(OPTIONAL_KEYS) & parsed.keys()
    # Baseline from the pre-change model; numeric fields already normalize to floats.
    assert report_hash(parsed) == "e9895a9204a8ee1e144d65cc038df3f115d4d879d08e5354cafa2e928cb355dc"


def test_reduced_heading_structure_integrates_context_without_repeated_headings():
    from app.standard_policy import organize_paragraphs
    original = copy.deepcopy(SAMPLE)
    settings = {**defaults(), "enabled_heading_roles": "heading,subheading,minor_heading,numbered_4", "heading_structure_policy": "CONTENT_V1"}
    plan = report_blocks(SAMPLE, concise=True, rich=True, visual_settings=settings)
    assert SAMPLE == original
    assert len([p for p in plan if p.get("style_kind") == "numbered_4"]) == 1
    assert not any(p.get("style_kind") in ("numbered_5", "numbered_6") for p in plan)
    text = "\n".join(p["text"] for p in plan)
    assert "확인 순서: 합성 점검 대상 20건 중 5건" in text
    assert "세부 기준: 작성 담당자가 문서명과 담당 부서를 확인" in text
    assert "나머지 15건" in text and "2026년 10월 30일" in text
    assert organize_paragraphs(["① 확인 순서", "㉮ 세부 기준", "□ (현황) 원문 [S1]"], settings) == ["□ (현황) 확인 순서 · 세부 기준: 원문 [S1]"]
    assert organize_paragraphs(["① 확인 순서", "가. 다음 구분"], settings) == ["□ 확인 순서", "가. 다음 구분"]
    assert organize_paragraphs(["① 확인 순서"], settings) == ["□ 확인 순서"]
    assert organize_paragraphs(SAMPLE["sections"][0]["paragraphs"], defaults()) == SAMPLE["sections"][0]["paragraphs"]
    prompt = writing_prompt({"visual_standard": {"revision": 3, "settings": settings}})
    assert "permission, not an instruction" in prompt
    assert "Integrate their distinctions" in prompt


@pytest.mark.parametrize("key,value", [("enabled_body_roles", ""), ("enabled_body_roles", "circle,square"),
    ("enabled_body_roles", "square,square"), ("enabled_heading_roles", "body"),
    ("line_break_unit", "anything"), ("line_space_policy", "SHRINK_CHARACTERS"), ("body_tracking_percent", -21)])
def test_invalid_controls_rejected(key, value):
    with pytest.raises(ValidationError):
        VisualSettingsV3.model_validate({**defaults(), key: value})


def test_disabled_roles_preserve_words_and_semantic_heading_links():
    original = copy.deepcopy(SAMPLE)
    settings = {**defaults(), "enabled_body_roles": "square", "enabled_heading_roles": "heading",
                "body_parenthetical_labels": True, "body_parenthetical_scope": "square"}
    plan = report_blocks(SAMPLE, concise=True, rich=True, visual_settings=settings)
    text = "\n".join(item["text"] for item in plan)
    assert SAMPLE == original
    assert all(item.get("style_kind") not in ("circle", "dash", "dot", "subheading", "minor_heading", "numbered_4", "numbered_5", "numbered_6") for item in plan)
    for phrase in ("작성 담당자가 문서명과 담당 부서를 확인", "필수 항목이 모두 확인된 자료만", "확인 일자와 담당자를", "세부 기준", "20건", "15건", "필수 항목 충족 시"):
        assert phrase in text
    assert any(item.get("toc_level") == 2 and "적용 범위" in item["text"] for item in plan)
    assert len([item for item in plan if item["kind"] == "heading"]) == 3


def test_line_controls_write_native_styles_without_touching_titles_or_text():
    settings = {**defaults(), "line_break_unit": "WORD", "line_space_policy": "TIGHTER", "body_tracking_percent": -3}
    _, header, _, formats = report_styles(settings)
    chars = {node.get("id"): node for node in header.iter(f"{{{HH}}}charPr")}
    paras = {node.get("id"): node for node in header.iter(f"{{{HH}}}paraPr")}
    cid, pid = formats["square"]
    assert set(chars[cid].find(f"{{{HH}}}spacing").attrib.values()) == {"-3"}
    assert paras[pid].get("condense") == "20"
    assert paras[pid].find(f"{{{HH}}}breakSetting").get("breakLatinWord") == "KEEP_WORD"
    assert paras[pid].find(f"{{{HH}}}breakSetting").get("breakNonLatinWord") == "BREAK_WORD"
    _, old_header, _, old_formats = report_styles(defaults())
    old_chars = {node.get("id"): node for node in old_header.iter(f"{{{HH}}}charPr")}
    assert chars[formats["title"][0]].find(f"{{{HH}}}spacing").attrib == old_chars[old_formats["title"][0]].find(f"{{{HH}}}spacing").attrib
    assert report_blocks(SAMPLE, concise=True, rich=True, visual_settings=settings) == report_blocks(SAMPLE, concise=True, rich=True, visual_settings=defaults())


def test_new_prompt_limits_roles_and_never_requires_generic_parenthetical_labels():
    settings = {**defaults(), "enabled_body_roles": "square,dash", "enabled_heading_roles": "heading",
                "body_parenthetical_labels": True, "body_parenthetical_scope": "square"}
    prompt = writing_prompt({"visual_standard": {"revision": 3, "settings": settings}})
    assert "permission, never a requirement" in prompt
    assert "Do not routinely prepend (요약)" in prompt
    assert "Allowed body roles: square,dash" in prompt
    assert "Allowed heading styles: heading." in prompt


def test_policy_settings_survive_registration_retry(secured):
    client, _, _, _ = secured
    settings = {**defaults(), "enabled_body_roles": "square,dash", "enabled_heading_roles": "heading,subheading",
                "body_parenthetical_labels": True, "body_parenthetical_scope": "square", "line_break_unit": "WORD",
                "line_space_policy": "TIGHT", "body_tracking_percent": -2}
    _, stage = prepare(client, settings)
    complete = finish(client, stage)
    body = registration(complete)
    response = client.post("/api/visual-standards/sample-register", json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["settings"] == settings
    assert client.post("/api/visual-standards/sample-register", json=body).json() == saved
