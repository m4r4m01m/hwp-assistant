"""Section-local notes and shared table sources are opt-in plan presentation."""
import copy
import re

import pytest

from app.reference_standard import defaults
from app.report_contract import ReportDraft, report_blocks
from app.report_notes import GROUPING_POLICY, SECTION_GROUPING_POLICY, TITLE_PRESENTATION_POLICY, localize_notes, without_source_tokens
from test_reference_body_spacing import generated
from test_reference_report import parts


DRAFT = dict(title="합성 대응 현황", summary="확인된 현황만 비교함.", sections=[
    dict(heading="조치 현황", paragraphs=["□ 완료 20건[S1]임.", "ㅇ 담당 부서가 증빙을 확인함[S2].",
         "- 나머지 3건은 승인 전임[S1].", "ㅇ 같은 확인 조건을 유지함[S2].", "□ 다음 점검 2건[S1]임.",
         "가. 추가 확인", "ㅇ 후속 검수 1건[S2]임.", "- 원본을 보존함[S2]."], tables=[]),
    dict(heading="추진 조건", paragraphs=["□ 공개 범위만 확인함."]),
    dict(heading="일정", paragraphs=["□ 다음 일정은 확인 필요."])],
    sources=[dict(id="S1", title="합성 실적 자료", url="https://example.com/status", published="2026. 10. 26."),
             dict(id="S2", title="합성 검수 기준", url="https://example.com/check", published="")], limitations=[])


def make_plan(draft=None, **settings):
    return report_blocks(draft or DRAFT, rich=True, visual_settings={**defaults(), **settings})


def test_body_items_share_sources_until_an_explicit_heading_without_repeating_explanations():
    original = copy.deepcopy(DRAFT)
    plan = make_plan()
    assert plan[0]["note_grouping"] == SECTION_GROUPING_POLICY
    start = next(i for i, p in enumerate(plan) if p["text"].startswith("□ 완료"))
    assert len({p["note_scope"] for p in plan[start:start + 5]}) == 1
    assert [p["note_source_id"] for p in plan[start + 5:start + 7]] == ["S1", "S2"]
    assert not any(p.get("note_source_id") for p in plan[start:start + 5])
    assert plan[start + 2]["text"] == "- 나머지 3건은 승인 전임주1)."
    following = plan[start + 4]
    assert following["text"] == "□ 다음 점검 2건주1)임."
    assert following["note_scope"] == plan[start]["note_scope"]
    heading = next(p for p in plan if p["text"] == "가. 추가 확인")
    assert not heading.get("note_scope")
    orphan = next(i for i, p in enumerate(plan) if p["text"].startswith("ㅇ 후속"))
    assert plan[orphan]["note_scope"] == plan[orphan + 1]["note_scope"]
    assert plan[orphan + 2]["note_source_id"] == "S2"
    assert DRAFT == original


@pytest.mark.parametrize("numbering", ["BULLETS", "NUMBERED"])
def test_disabled_square_and_display_numbering_do_not_split_a_section_scope(numbering):
    plan = make_plan(enabled_body_roles="circle,dash", numbering_style=numbering)
    root = next(i for i, p in enumerate(plan) if "담당 부서가 증빙" in p["text"])
    assert plan[root]["note_body_role"] == "circle"
    assert plan[root + 1]["note_body_role"] == "dash"
    assert plan[root]["note_scope"] == plan[root + 1]["note_scope"]
    notes = [p for p in plan if p.get("note_source_id") and p["note_scope"] == plan[root]["note_scope"]]
    assert [p["note_source_id"] for p in notes] == ["S1", "S2"]
    following = next(p for p in plan if "같은 확인 조건" in p["text"])
    assert following["note_scope"] == plan[root]["note_scope"]
    assert [plan[root]["text"][a:b] for a, b in plan[root]["citation_ranges"]] == ["주2)"]


def test_chunked_root_keeps_children_and_one_note_after_the_last_child():
    draft = copy.deepcopy(DRAFT)
    draft["sections"][0]["paragraphs"] = ["□ " + "확인 조건과 원본을 보존함. " * 25 + "[S1]", "ㅇ 후속 확인 3건[S1]임."]
    plan = make_plan(draft)
    group = [p for p in plan if p.get("note_scope") == "note-1"]
    assert len(group) > 3
    assert group[-1]["text"] == "* 합성 실적 자료"
    assert group[-2]["text"] == "ㅇ 후속 확인 3건*임."
    assert sum(bool(p.get("note_source_id")) for p in group) == 1
    assert sum(len(p.get("citation_ranges", [])) for p in group) == 2


def test_repeated_sources_are_once_per_section_and_keep_every_inline_anchor():
    draft = copy.deepcopy(DRAFT)
    draft["sections"][0]["paragraphs"] = ["□ 첫 판단임.", "ㅇ 공식 조건 20건[S1]임.",
        "□ 별도 판단임.", "ㅇ 같은 공식 자료의 다른 조건 3건[S1]임."]
    draft["sections"][1]["paragraphs"] = ["□ 다음 절의 적용 조건[S1]임."]
    original = copy.deepcopy(draft)
    plan = make_plan(draft, reference_list="UNUSED")
    notes = [p for p in plan if p.get("note_source_id") == "S1"]
    assert len(notes) == 2
    cited = [p for p in plan if p.get("citation_ranges") and "S1" in p["citation_source_ids"]]
    assert len(cited) == 3
    assert cited[0]["note_scope"] == cited[1]["note_scope"] != cited[2]["note_scope"]
    assert [[p["text"][a:b] for a, b in p["citation_ranges"]] for p in cited] == [["*"], ["*"], ["*"]]
    assert plan.index(notes[0]) == plan.index(cited[1]) + 1
    assert plan.index(notes[1]) == plan.index(cited[2]) + 1
    assert draft == original


def test_frozen_argument_group_policy_still_repeats_at_each_root():
    sources = ReportDraft.model_validate(DRAFT).sources
    blocks = [("기존 제목", "title", False, {}),
              ("□ 첫 항목[S1]", "body", False, {"note_body_role": "square"}),
              ("ㅇ 첫 설명[S1]", "body", False, {"note_body_role": "circle"}),
              ("□ 다음 항목[S1]", "body", False, {"note_body_role": "square"})]
    legacy = localize_notes(blocks, sources[:1], defaults(), presentation_policy=TITLE_PRESENTATION_POLICY,
                            grouping_policy=GROUPING_POLICY)
    assert legacy[0][3]["note_grouping"] == GROUPING_POLICY
    assert legacy[1][3]["note_scope"] == legacy[2][3]["note_scope"]
    assert legacy[3][3]["note_source_id"] == legacy[5][3]["note_source_id"] == "S1"
    assert legacy[1][3]["note_scope"] != legacy[4][3]["note_scope"]


def table_draft(mixed=False):
    draft = copy.deepcopy(DRAFT)
    draft["sections"][0]["paragraphs"] = ["□ 별도 본문 20건[S1]임."]
    draft["sections"][0]["tables"] = [dict(caption="합성 조치 현황[S1]", columns=["대상", "상태"],
        rows=[["기관 가", "완료 20건[S1]"], ["기관 나", "확인 3건[S2]" if mixed else "확인 3건[S1]"]])]
    draft["emphasis"] = [dict(text="20건[S1]", style="bold")]
    return draft


def test_one_source_table_prints_one_material_line_and_retains_per_cell_provenance():
    draft = table_draft()
    original = copy.deepcopy(draft)
    plan = make_plan(draft)
    visual = [p for p in plan if p.get("note_visual_group") == "table-1"]
    assert all(p["table_common_source_id"] == "S1" for p in visual)
    assert all(not p.get("citation_ranges") for p in visual)
    for item in visual:
        canonical = item["canonical_citation_text"]
        assert item["text"] == without_source_tokens(canonical, {"S1"})
        assert [canonical[ref["start"]:ref["end"]] for ref in item["canonical_citations"]] == [
            "[" + ref["source_id"] + "]" for ref in item["canonical_citations"]]
    cell = next(p for p in visual if p["text"] == "완료 20건")
    assert cell["emphasis"] == [dict(text="20건", style="bold")]
    table_note = next(p for p in plan if p.get("note_source_id") and p.get("note_visual_kind") == "table")
    assert table_note["text"] == "자료: 합성 실적 자료"
    assert table_note["note_marker"] == "자료:"
    assert table_note["note_scope"] == visual[0]["note_scope"]
    prose_note = next(p for p in plan if p.get("note_source_id") and not p.get("note_visual_kind"))
    assert prose_note["note_scope"] != table_note["note_scope"]
    assert draft == original


def test_mixed_table_keeps_mapped_inline_marks_and_individual_source_notes():
    plan = make_plan(table_draft(mixed=True))
    visual = [p for p in plan if p.get("note_visual_group") == "table-1"]
    assert not any(p.get("table_common_source_id") for p in visual)
    assert any(p["text"] == "완료 20건주1)" for p in visual)
    assert any(p["text"] == "확인 3건주2)" for p in visual)
    notes = [p for p in plan if p.get("note_source_id") and p.get("note_visual_kind") == "table"]
    assert [p["text"] for p in notes] == ["주1) 합성 실적 자료", "주2) 합성 검수 기준"]


def test_source_only_table_cell_keeps_marker_instead_of_becoming_empty():
    draft = table_draft()
    draft["sections"][0]["tables"][0]["rows"][0][1] = "[S1]"
    draft["emphasis"] = []
    plan = make_plan(draft)
    visual = [p for p in plan if p.get("note_visual_group") == "table-1"]
    assert not any(p.get("table_common_source_id") for p in visual)
    assert all(p["text"].strip() for p in visual)
    assert any(p["text"] == "*" for p in visual)


def test_legacy_grouping_does_not_merge_children_or_hide_common_table_marks():
    sources = ReportDraft.model_validate(DRAFT).sources
    blocks = [("기존 제목", "title", False, {}), ("□ 완료 20건[S1]임.", "body", False, {"note_body_role": "square"}),
              ("ㅇ 담당자 확인[S1]임.", "body", False, {"note_body_role": "circle"}),
              ("표제[S1]", "source", False, {"note_visual_caption": True, "note_visual_kind": "table", "note_visual_group": "table-1"}),
              ("표 내용[S1]", "source", False, {"note_visual_kind": "table", "note_visual_group": "table-1"})]
    legacy = localize_notes(blocks, sources, defaults(), presentation_policy=TITLE_PRESENTATION_POLICY)
    assert "note_grouping" not in legacy[0][3]
    assert legacy[2][3]["note_source_id"] == legacy[4][3]["note_source_id"] == "S1"
    assert legacy[5][0] == "표제*" and legacy[6][0] == "표 내용*"
    assert legacy[7][0] == "* 합성 실적 자료"


def test_shared_table_source_and_facts_survive_native_hwpx_layout():
    from app.document_standard import HP
    draft = table_draft()
    raw, plan = generated(draft=draft)
    _, section = parts(raw)
    text = "".join(node.text or "" for node in section.iter(f"{{{HP}}}t"))
    assert "완료 20건" in text and "확인 3건" in text
    assert "자료: 합성 실적 자료" in text
    table = next(p for p in plan if p.get("table_common_source_id") and p["text"] == "완료 20건")
    assert table["canonical_citations"] == [dict(source_id="S1", start=6, end=10)]
