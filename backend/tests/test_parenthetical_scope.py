"""Optional parenthetical-label scope; old settings/receipts stay unchanged."""
import copy
import subprocess
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import report_runner, report_stream, standard_samples, visual_standards as visual
from app.reference_standard import VisualSettingsV3, defaults, legacy_defaults
from app.report_contract import PARENTHETICAL_SCOPES, parenthetical_role, presentation_text, report_blocks, report_hash
from app.report_templates import catalog
from test_collaboration import secured
from test_standard_samples import prepare, finish, registration

ALL = ",".join(key for key, _ in PARENTHETICAL_SCOPES)


@pytest.mark.parametrize("model,settings", [(VisualSettingsV3, defaults()), (VisualSettingsV3, legacy_defaults()), (visual.VisualSettingsV2, visual.DEFAULTS)])
def test_optional_scope_does_not_change_old_settings_hash_or_default_fields(model, settings):
    frozen = model.model_validate(settings).model_dump(mode="json")
    assert "body_parenthetical_scope" not in frozen
    old_hash = report_hash(frozen)
    assert report_hash(model.model_validate_json(__import__("json").dumps(frozen)).model_dump(mode="json")) == old_hash
    for scope in ("", ALL, "square,dot,body", "circle,dash,summary"):
        explicit = model.model_validate({**settings, "body_parenthetical_scope": scope}).model_dump(mode="json")
        assert explicit["body_parenthetical_scope"] == scope
        assert {k: v for k, v in explicit.items() if k != "body_parenthetical_scope"} == frozen


@pytest.mark.parametrize("invalid", [None, True, 1, [], "unknown", "square,square", "body,square", "square, body", "square,", "summary\n"])
@pytest.mark.parametrize("model,settings", [(VisualSettingsV3, defaults()), (visual.VisualSettingsV2, visual.DEFAULTS)])
def test_scope_is_a_strict_canonical_scalar(model, settings, invalid):
    with pytest.raises(ValidationError):
        model.model_validate({**settings, "body_parenthetical_scope": invalid})


@pytest.mark.parametrize("selected", [key for key, _ in PARENTHETICAL_SCOPES])
@pytest.mark.parametrize("numbering", ["BULLETS", "NUMBERED"])
def test_individual_scope_uses_canonical_roles_and_preserves_conditions_and_sources(selected, numbering):
    draft = copy.deepcopy(standard_samples.SAMPLE)
    before = copy.deepcopy(draft)
    settings = {**defaults(), "show_core_summary": True, "body_parenthetical_labels": True,
                "body_parenthetical_scope": selected, "numbering_style": numbering}
    blocks = report_blocks(draft, concise=True, rich=True, visual_settings=settings)
    first = {}
    for item in blocks:
        if item.get("parenthetical_role"):
            first.setdefault(item["parenthetical_role"], item["text"])
    markers = {"square": "(현황)", "circle": "(조치)", "dash": "(결론)", "dot": "(핵심)", "body": "(목적)", "summary": "(요약)"}
    assert set(first) == set(markers)
    assert all((markers[role] in text) == (role == selected) for role, text in first.items())
    joined = "\n".join(item["text"] for item in blocks)
    for token in ("20건", "5건", "15건", "2026년 10월 30일", "(필수 항목 충족 시)", "합성 문서 점검 기준", "합성 검토 기록 기준"):
        assert token in joined
    assert draft == before
    assert {item["note_source_id"] for item in blocks if item.get("note_source_id")} == {"S1", "S2"}
    regions = standard_samples.sample_regions(blocks, settings, False)
    aliases = {region["role"] for region in regions}
    assert {"parenthetical_" + key for key, _ in PARENTHETICAL_SCOPES} <= aliases


def test_master_switch_empty_scope_and_summary_are_independent():
    base = {**defaults(), "show_core_summary": True, "body_parenthetical_labels": True}
    all_enabled = report_blocks(standard_samples.SAMPLE, visual_settings=base)
    assert all("body_parenthetical_scope" not in item for item in all_enabled)
    for settings in ({**base, "body_parenthetical_scope": ""}, {**base, "body_parenthetical_scope": ALL, "body_parenthetical_labels": False}):
        joined = "\n".join(item["text"] for item in report_blocks(standard_samples.SAMPLE, visual_settings=settings))
        assert "핵심 요약" in joined
        assert all(label not in joined for label in ("(현황)", "(조치)", "(결론)", "(핵심)", "(목적)", "(요약)", "(방안)", "(일정)"))
    hidden = report_blocks(standard_samples.SAMPLE, visual_settings={**base, "body_parenthetical_scope": "summary", "show_core_summary": False})
    assert not any(item.get("parenthetical_role") == "summary" for item in hidden)


def test_bounded_labels_only_and_titles_tables_notes_are_never_rewritten():
    settings = {**defaults(), "body_parenthetical_labels": True, "body_parenthetical_scope": ""}
    untouched = ["(2026년 10월) 자료를 확인함.", "(필수 조건 충족 시) 배포함. [S1]", "(1) 적용 범위", "(가) 세부 범위", "□ 본문 (현황) 조건을 기록함.", "□ (현황)띄어쓰기 없는 원문"]
    for text in untouched:
        assert presentation_text(text, {**settings, "citation_style": "BRACKET_S"}, {"S1"}, body=True) == text
    draft = copy.deepcopy(standard_samples.SAMPLE)
    draft["title"] = "(목적) 문서 관리 개선 계획"
    draft["sections"][0]["heading"] = "(현황) 정비 필요성"
    draft["sections"][1]["tables"][0]["caption"] = "(요약) 담당별 정비 항목"
    draft["sections"][1]["tables"][0]["columns"][0] = "(현황) 정비 항목"
    draft["sources"][0]["title"] = "(요약) 합성 문서 점검 기준"
    frozen = copy.deepcopy(draft)
    joined = "\n".join(item["text"] for item in report_blocks(draft, rich=True, visual_settings=settings))
    for text in (draft["title"], draft["sections"][0]["heading"], draft["sections"][1]["tables"][0]["caption"], "(현황) 정비 항목", draft["sources"][0]["title"]):
        assert text in joined
    assert draft == frozen


def test_adjacent_marker_and_role_specific_emphasis_keep_the_selected_text():
    for marker, role in (("□", "square"), ("ㅇ", "circle"), ("-", "dash"), ("․", "dot")):
        value = marker + "(현황) 자료를 확인함."
        settings = {**defaults(), "body_parenthetical_labels": True, "body_parenthetical_scope": role}
        assert parenthetical_role(value) == role
        assert presentation_text(value, settings, set(), body=True) == value
        assert presentation_text(value, {**settings, "body_parenthetical_scope": "body"}, set(), body=True) == marker + "자료를 확인함."
    draft = copy.deepcopy(standard_samples.SAMPLE)
    draft["sections"][0]["paragraphs"] = ["□ (조치) 자료 검토함.", "ㅇ (조치) 자료 확인함.", "(조치) 자료 정리함."]
    draft["emphasis"] = [{"text": "(조치) 자료", "style": "blue"}]
    frozen = copy.deepcopy(draft)
    blocks = report_blocks(draft, rich=True, visual_settings={**defaults(), "body_parenthetical_labels": True, "body_parenthetical_scope": "circle"})
    for scope, expected in (("square", "자료"), ("circle", "(조치) 자료"), ("body", "자료")):
        item = next(item for item in blocks if item.get("parenthetical_role") == scope)
        assert item["emphasis"] == [{"text": expected, "style": "blue"}]
    assert draft == frozen


@pytest.mark.parametrize("template", catalog()["items"], ids=lambda item: item["id"])
def test_scope_reaches_actual_report_execution_input_without_model_calls(monkeypatch, template):
    captured = {}
    settings = {**defaults(), "body_parenthetical_labels": True, "body_parenthetical_scope": "square,dot,body"}
    value = {"template": template, "visual_standard": {"revision": 3, "settings": settings}}
    frozen = copy.deepcopy(value)
    monkeypatch.setattr(report_runner, "run_process", lambda *a, **k: subprocess.CompletedProcess([], 0, "Logged in using ChatGPT", ""))
    def generate(request, config, directory, prompt, contract, progress, active, started):
        captured.update(request=copy.deepcopy(request), prompt=prompt)
        return standard_samples.SAMPLE, {"mock": True}
    monkeypatch.setattr(report_stream, "generate_stream", generate)
    config = SimpleNamespace(executable="mock-codex", environment={}, login_timeout=1)
    report_runner.generate_report(value, config)
    assert captured["request"] == value == frozen
    assert "Frozen parenthetical lead-summary scope: square,dot,body." in captured["prompt"]
    assert "summary=the separate summary value, even when it starts with □" in captured["prompt"]
    assert "Preserve all genuine parenthetical facts, numbers, conditions, quotations and citations" in captured["prompt"]


def test_legacy_prompt_is_unchanged_without_scope_and_new_v2_scope_is_precise():
    assert report_runner.writing_prompt({}) == report_runner.PROMPT
    assert report_runner.writing_prompt({"visual_standard": {"revision": 2, "settings": visual.DEFAULTS}}) == report_runner.PROMPT
    settings = {**visual.DEFAULTS, "body_parenthetical_scope": "summary"}
    prompt = report_runner.writing_prompt({"visual_standard": {"revision": 2, "settings": settings}})
    assert "Frozen parenthetical lead-summary scope: summary." in prompt
    prompt = report_runner.writing_prompt({"visual_standard": {"revision": 2, "settings": {**settings, "body_parenthetical_labels": False}}})
    assert "Frozen parenthetical lead-summary scope: none." in prompt


def test_scope_catalog_and_immutable_partial_registration(secured):
    client, _, _, _ = secured
    catalog_response = client.get("/api/visual-standards").json()
    assert catalog_response["parenthetical_scopes"] == [{"value": key, "label": label} for key, label in PARENTHETICAL_SCOPES]
    assert "body_parenthetical_scope" not in catalog_response["defaults"]
    settings = {**defaults(), "body_parenthetical_labels": True, "body_parenthetical_scope": "square,dot,body"}
    _, initial = prepare(client, settings)
    final = finish(client, initial)
    body = registration(final)
    saved = client.post("/api/visual-standards/sample-register", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["body_parenthetical_scope"] == settings["body_parenthetical_scope"]
    assert client.post("/api/visual-standards/sample-register", json=body).json() == saved.json()
    assert client.get(f"/api/visual-standards/{body['id']}").json() == saved.json()
