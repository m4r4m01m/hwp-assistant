"""New printed note wording and limitation roles leave stored evidence intact."""
import copy

import pytest

from app.reference_standard import defaults
from app.report_contract import ReportDraft, limitation_source_ranges, report_blocks
from app.report_notes import TITLE_PRESENTATION_POLICY, localize_notes
from test_reference_body_spacing import generated, paragraph_named
from test_reference_report import parts
from test_report_local_notes import DRAFT


def test_new_notes_print_titles_only_and_preserve_complete_canonical_sources():
    draft = copy.deepcopy(DRAFT)
    draft["sources"][0]["published"] = "추가 안내 2026. 10."
    original = copy.deepcopy(draft)
    plan = report_blocks(draft, rich=True, visual_settings={**defaults(), "reference_list": "ALL"})
    assert plan[0]["note_presentation"] == TITLE_PRESENTATION_POLICY
    sources = {source["id"]: source for source in original["sources"]}
    notes = [item for item in plan if item.get("note_source_id")]
    assert notes
    for item in notes:
        assert item["text"] == item["note_marker"] + " " + sources[item["note_source_id"]]["title"]
    references = [item for item in plan if item.get("unreferenced_source_id")]
    assert [item["text"] for item in references] == [source["title"] for source in original["sources"]]
    assert draft == original
    assert ReportDraft.model_validate(draft).sources[0].published == "추가 안내 2026. 10."


def test_old_local_note_contract_keeps_its_full_printed_source_without_new_gate():
    source = ReportDraft.model_validate(DRAFT).sources[0]
    blocks = [("기존 제목", "title", False, {}), ("□ 보존 사실 [S8]", "body", False, {"style_kind": "square"})]
    result = localize_notes(blocks, [source], defaults())
    assert "note_presentation" not in result[0][3]
    assert result[-1][0] == f"* {source.title} ({source.published}) · {source.url}"


@pytest.mark.parametrize("roles,marker,role", [("square,circle,dash", "□", "square"), ("circle,dash", "ㅇ", "circle")])
def test_limitations_use_enabled_bullets_and_actual_indents(roles, marker, role):
    draft = copy.deepcopy(DRAFT)
    draft["limitations"] = ["실제 시스템을 검사하지 않아 개별 위험은 확정하지 않음.", "공식 자료 [S4] 확인이 필요함."]
    original = copy.deepcopy(draft)
    settings = {"enabled_body_roles": roles, role + "_left_indent_pt": 37}
    raw, plan = generated(draft=draft, settings=settings)
    header, section = parts(raw)
    item = next(item for item in plan if "실제 시스템을 검사하지" in item["text"])
    assert item["text"] == marker + " " + draft["limitations"][0]
    assert item["style_kind"] == role
    assert paragraph_named(header, section, item["text"])[1]["left"] == 37
    cited = next(i for i, item in enumerate(plan) if "공식 자료" in item["text"])
    assert plan[cited + 1]["note_source_id"] == "S4"
    assert draft == original


def test_known_limitation_source_range_uses_local_superscripts_without_rewriting_draft():
    draft = copy.deepcopy(DRAFT)
    draft["sources"] = [dict(id=f"S{number}", title=f"합성 자료 {number}", url=f"https://example.com/{number}", published="")
                        for number in range(1, 4)] + draft["sources"]
    draft["limitations"] = ["기존 문서 S1~S3는 검토 초안의 출처 정보를 유지함."]
    original = copy.deepcopy(draft)
    plan = report_blocks(draft, rich=True, visual_settings=defaults())
    index = next(i for i, item in enumerate(plan) if "기존 문서" in item["text"])
    item = plan[index]
    assert item["text"] == "□ 기존 문서 주1), 주2), 주3)는 검토 초안의 출처 정보를 유지함."
    assert [item["text"][a:b] for a, b in item["citation_ranges"]] == ["주1)", "주2)", "주3)"]
    assert [plan[index + n]["note_source_id"] for n in (1, 2, 3)] == ["S1", "S2", "S3"]
    assert draft == original


@pytest.mark.parametrize("value", ["문서 S1~S4는 확인 필요", "자료 S3~S1", "제품 S1", "제품 S1~S3",
                                    "S1~S3", "XS1~S3", "[S1~S3]", "https://example.com/S1~S3"])
def test_ambiguous_or_unknown_source_tokens_are_never_invented(value):
    assert limitation_source_ranges(value, {"S1", "S2", "S3"}) == value


@pytest.mark.parametrize("prefix", ["기존 일반 보안 문서 ", "출처: ", "자료 번호 ", "근거 목록 "])
def test_only_explicit_source_context_allows_known_source_ranges(prefix):
    assert limitation_source_ranges(prefix + "S1~S3는 유지함.", {"S1", "S2", "S3"}) == prefix + "[S1][S2][S3]는 유지함."


def test_generation_schema_explains_source_quality_without_rejecting_stored_dates():
    schema = ReportDraft.generation_schema()["$defs"]["ReportSource"]["properties"]
    assert "title only" in schema["title"]["description"]
    assert "complete" in schema["published"]["description"]
    assert "empty string" in schema["published"]["description"]
    assert "description" not in ReportDraft.model_json_schema()["$defs"]["ReportSource"]["properties"]["published"]
