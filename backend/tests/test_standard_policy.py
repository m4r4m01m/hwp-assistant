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


def test_line_policy_catalog_explains_each_selectable_value_without_becoming_settings():
    from app.reference_standard import field_catalog
    from app.reference_source import roles_source
    from app.standard_policy import BODY_ROLES, HEADING_ROLES
    catalog = {field["key"]: field for field in field_catalog()}
    for key in ("line_break_unit", "line_space_policy"):
        field = catalog[key]
        assert set(field["help_by_value"]) == {option["value"] for option in field["options"]}
        assert all(field["help_by_value"].values()) and field["help"]
        assert len(set(field["help_by_value"].values())) == len(field["options"])
        for option in field["options"]:
            settings = VisualSettingsV3.model_validate({**defaults(), key: option["value"]}).model_dump(mode="json")
            assert settings[key] == option["value"]
            assert "help_by_value" not in settings
    source = roles_source()
    source_condense = {int(source[role]["para"].get("condense", "0"))
                      for role in (*BODY_ROLES, *HEADING_ROLES, "table", "note", "table_source")}
    assert all(f"{value}%" in catalog["line_space_policy"]["help_by_value"]["ORIGINAL"] for value in source_condense)
    assert "한글" in catalog["line_break_unit"]["help_by_value"]["WORD"]
    assert "영문" in catalog["line_break_unit"]["help_by_value"]["WORD"]
    assert "기준서" not in catalog["line_break_unit"]["help_by_value"]["WORD"]


def test_reference_alias_requires_equivalence_for_every_affected_role(monkeypatch):
    from app import reference_source
    from app.standard_policy import LINE_POLICY_EXCLUDED_ROLES, line_fields
    source = reference_source.roles_source()
    for role, item in source.items():
        if role in LINE_POLICY_EXCLUDED_ROLES:
            continue
        item["para"].set("condense", "0")
        item["para"].find(f"{{{HH}}}breakSetting").attrib.update(
            breakNonLatinWord="BREAK_WORD", breakLatinWord="KEEP_WORD")
    monkeypatch.setattr(reference_source, "roles_source", lambda: copy.deepcopy(source))
    fields = {field["key"]: field for field in line_fields()}
    assert fields["line_space_policy"]["option_aliases"] == {"ORIGINAL": "NONE"}
    assert fields["line_break_unit"]["option_aliases"] == {"ORIGINAL": "WORD"}
    # A less common role still participates: aliasing only common body/table
    # roles could otherwise hide a real difference in an attachment heading.
    source["attachment"]["para"].set("condense", "10")
    source["attachment"]["para"].find(f"{{{HH}}}breakSetting").set("breakLatinWord", "BREAK_WORD")
    fields = {field["key"]: field for field in line_fields()}
    assert fields["line_space_policy"]["option_aliases"] == {}
    assert fields["line_break_unit"]["option_aliases"] == {}


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


@pytest.mark.parametrize('text', ['(금) 오후 점검 예정', '금. 오후 점검 예정', '(각) 담당자 확인', '각. 담당자 확인'])
def test_reduced_heading_structure_preserves_nonordinal_korean_text(text):
    from app.standard_policy import organize_paragraphs
    settings = {**defaults(), 'heading_structure_policy': 'CONTENT_V1', 'enabled_heading_roles': 'heading'}
    assert organize_paragraphs([text], settings) == [text]
    draft = copy.deepcopy(SAMPLE)
    draft['sections'][0]['paragraphs'] = [text]
    plan = report_blocks(draft, rich=True, visual_settings=settings)
    assert any(item['text'] == text for item in plan)
    assert draft['sections'][0]['paragraphs'] == [text]


@pytest.mark.parametrize('marker', ['가.', '하.', '(가)', '(하)'])
def test_disabled_korean_ordinal_headings_still_join_their_content(marker):
    from app.standard_policy import organize_paragraphs
    settings = {'heading_structure_policy': 'CONTENT_V1', 'enabled_heading_roles': 'heading'}
    assert organize_paragraphs([marker + ' 확인 기준', '□ 금요일에 점검함.'], settings) == [
        '□ 확인 기준: 금요일에 점검함.']


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


@pytest.mark.parametrize("policy,percent", [("TIGHTER", 20), ("TIGHT_30", 30), ("TIGHT_50", 50)])
def test_line_controls_write_native_styles_without_touching_titles_or_text(policy, percent):
    from app.standard_policy import LINE_POLICY_EXCLUDED_ROLES
    settings = VisualSettingsV3.model_validate({**defaults(), "line_break_unit": "WORD",
        "line_space_policy": policy, "body_tracking_percent": -3}).model_dump(mode="json")
    _, header, _, formats = report_styles(settings)
    chars = {node.get("id"): node for node in header.iter(f"{{{HH}}}charPr")}
    paras = {node.get("id"): node for node in header.iter(f"{{{HH}}}paraPr")}
    cid, pid = formats["square"]
    assert set(chars[cid].find(f"{{{HH}}}spacing").attrib.values()) == {"-3"}
    assert paras[pid].get("condense") == str(percent)
    assert paras[pid].find(f"{{{HH}}}breakSetting").get("breakLatinWord") == "KEEP_WORD"
    assert paras[pid].find(f"{{{HH}}}breakSetting").get("breakNonLatinWord") == "BREAK_WORD"
    _, old_header, _, old_formats = report_styles(defaults())
    old_chars = {node.get("id"): node for node in old_header.iter(f"{{{HH}}}charPr")}
    old_paras = {node.get("id"): node for node in old_header.iter(f"{{{HH}}}paraPr")}
    for role, (_, paragraph_id) in formats.items():
        if role in LINE_POLICY_EXCLUDED_ROLES or role == "empty":
            assert paras[paragraph_id].get("condense") == old_paras[old_formats[role][1]].get("condense")
        else:
            assert paras[paragraph_id].get("condense") == str(percent)
    assert chars[formats["title"][0]].find(f"{{{HH}}}spacing").attrib == old_chars[old_formats["title"][0]].find(f"{{{HH}}}spacing").attrib
    assert report_blocks(SAMPLE, concise=True, rich=True, visual_settings=settings) == report_blocks(SAMPLE, concise=True, rich=True, visual_settings=defaults())


def test_minus_five_tracking_adds_to_each_role_and_clamps_only_final_spacing():
    settings = {**defaults(), "body_tracking_percent": -5, "line_space_policy": "TIGHT_50",
                "square_spacing_percent": 0, "circle_spacing_percent": 3, "dash_spacing_percent": 45,
                "dot_spacing_percent": -18}
    _, header, _, formats = report_styles(settings)
    chars = {node.get("id"): node for node in header.iter(f"{{{HH}}}charPr")}
    for role, expected in {"square": -5, "circle": -2, "dash": 40, "dot": -20}.items():
        assert set(chars[formats[role][0]].find(f"{{{HH}}}spacing").attrib.values()) == {str(expected)}
    assert settings["body_tracking_percent"] == -5 and settings["dot_spacing_percent"] == -18


def test_new_prompt_limits_roles_and_never_requires_generic_parenthetical_labels():
    settings = {**defaults(), "enabled_body_roles": "square,dash", "enabled_heading_roles": "heading",
                "body_parenthetical_labels": True, "body_parenthetical_scope": "square"}
    prompt = writing_prompt({"visual_standard": {"revision": 3, "settings": settings}})
    assert "permission, never a requirement" in prompt
    assert "Do not routinely prepend (요약)" in prompt
    assert "Allowed body roles: square,dash" in prompt
    assert "Allowed heading styles: heading." in prompt


@pytest.mark.parametrize("policy", ["TIGHT", "TIGHT_50"])
def test_policy_settings_survive_registration_retry(secured, policy):
    client, _, _, _ = secured
    settings = {**defaults(), "enabled_body_roles": "square,dash", "enabled_heading_roles": "heading,subheading",
                "body_parenthetical_labels": True, "body_parenthetical_scope": "square", "line_break_unit": "WORD",
                "line_space_policy": policy, "body_tracking_percent": -2}
    _, stage = prepare(client, settings)
    complete = finish(client, stage)
    body = registration(complete)
    response = client.post("/api/visual-standards/sample-register", json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["settings"] == settings
    assert client.post("/api/visual-standards/sample-register", json=body).json() == saved
