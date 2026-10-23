"""Local notes retain canonical identities and freeze all text before layout."""
import copy

import pytest

from app.reference_standard import defaults
from app.report_contract import report_blocks
from app.report_notes import POLICY
from test_reference_body_spacing import generated, paragraph_named
from test_reference_report import parts


DRAFT = dict(title="문단별 주석 검수", summary="요약 사실 [S8]", sections=[
    dict(heading="확인 결과", paragraphs=["□ 자료 24건을 확인함. [S8][S4] 동일 근거를 유지함. [S8]",
         "ㅇ 보완 6건은 담당 확인 후 게시함. [S4]"],
         tables=[dict(caption="확인 대상 [S4]", columns=["항목", "수량"], rows=[["완료 [S8]", "18건"], ["보완 [S4]", "6건"]])]),
    dict(heading="추진 조건", paragraphs=["□ 원본과 이전 버전을 보존함. [S8]"]),
    dict(heading="추진 일정", paragraphs=["□ 2026. 11. 2.~11. 13. 시범 운영 예정임."])],
    sources=[dict(id="S8", title="합성 점검 자료", url="https://example.com/check", published="2026. 10. 23."),
             dict(id="S4", title="합성 승인 조건", url="https://example.com/approval", published="")], limitations=[])


@pytest.mark.parametrize("style,one,two", [("STARS", "*", "**"), ("NOTE", "주1)", "주2)")])
def test_notes_follow_each_logical_paragraph_or_visual_and_reset(style, one, two):
    original = copy.deepcopy(DRAFT)
    plan = report_blocks(DRAFT, rich=True, visual_settings={**defaults(), "citation_style": style})
    assert DRAFT == original
    assert plan[0]["local_notes"] == POLICY
    first = next(i for i, item in enumerate(plan) if item["text"].startswith("□ 자료"))
    assert plan[first]["text"] == f"□ 자료 24건을 확인함. {one}, {two} 동일 근거를 유지함. {one}"
    assert [plan[first + n]["note_source_id"] for n in (1, 2)] == ["S8", "S4"]
    assert [plan[first]["text"][a:b] for a, b in plan[first]["citation_ranges"]] == [one, two, one]
    second = next(item for item in plan if item["text"].startswith("ㅇ 보완"))
    assert second["text"].endswith(one) and second["citation_source_ids"] == ["S4"]
    cells = [i for i, item in enumerate(plan) if item.get("visual_id") == "table-1"]
    assert [plan[cells[-1] + n]["note_source_id"] for n in (1, 2)] == ["S4", "S8"]
    assert plan[cells[-1] + 1]["style_kind"] == "table_source"
    assert plan[cells[-1] + 1]["text"].startswith(one + " ")
    assert {plan[i]["note_scope"] for i in cells} == {plan[cells[-1] + 1]["note_scope"]}
    assert not any(item["text"] == "출처 및 확인 사항" for item in plan)
    assert all(item.get("kind") != "heading" for item in plan if item.get("note_source_id"))


def test_auto_counts_distinct_notes_per_paragraph_and_visual_then_resets():
    original = copy.deepcopy(DRAFT)
    plan = report_blocks(DRAFT, rich=True, visual_settings=defaults())
    assert DRAFT == original
    first = next(i for i, item in enumerate(plan) if item["text"].startswith("□ 자료"))
    assert plan[first]["text"] == "□ 자료 24건을 확인함. 주1), 주2) 동일 근거를 유지함. 주1)"
    assert [plan[first + n]["note_source_id"] for n in (1, 2)] == ["S8", "S4"]
    assert [plan[first + n]["note_marker"] for n in (1, 2)] == ["주1)", "주2)"]
    assert [plan[first]["text"][a:b] for a, b in plan[first]["citation_ranges"]] == ["주1)", "주2)", "주1)"]
    second = next(i for i, item in enumerate(plan) if item["text"].startswith("ㅇ 보완"))
    assert plan[second]["text"].endswith("*")
    assert plan[second + 1]["note_marker"] == "*" and plan[second + 1]["note_source_id"] == "S4"
    caption = next(item for item in plan if item.get("note_visual_caption"))
    assert caption["text"] == "확인 대상 주1)"
    cells = [i for i, item in enumerate(plan) if item.get("visual_id") == "table-1"]
    assert any(plan[i]["text"] == "완료 주2)" for i in cells)
    assert [plan[cells[-1] + n]["note_marker"] for n in (1, 2)] == ["주1)", "주2)"]
    following = next(i for i, item in enumerate(plan) if item["text"].startswith("□ 원본"))
    assert plan[following]["text"].endswith("*") and plan[following + 1]["note_marker"] == "*"


def test_auto_repeated_one_source_stays_star_and_emphasis_uses_the_whole_scope():
    draft = copy.deepcopy(DRAFT)
    draft["sections"][0]["paragraphs"] = ["□ 같은 근거 [S4]를 반복 인용함. [S4]", "ㅇ 자료 [S8]와 조건 [S4]을 확인함."]
    draft["sections"][1]["diagrams"] = [dict(caption="확인 절차 [S8]", steps=["자료 검토 [S4]", "승인 [S8]"])]
    draft["emphasis"] = [dict(text="자료 [S8]", style="bold")]
    plan = report_blocks(draft, rich=True, visual_settings=defaults())
    single = next(item for item in plan if item["text"].startswith("□ 같은"))
    assert single["text"].count("*") == 2
    assert single["citation_source_ids"] == ["S4"]
    multiple = next(item for item in plan if item["text"].startswith("ㅇ 자료"))
    assert multiple["emphasis"] == [dict(text="자료 주1)", style="bold")]
    caption = next(item for item in plan if item.get("note_visual_caption") and item.get("note_visual_kind") == "diagram")
    assert caption["text"].endswith("주1)")
    diagram = [i for i, item in enumerate(plan) if item.get("visual_id") == "diagram-1"]
    assert any(plan[i]["text"] == "자료 검토 주2)" for i in diagram)
    assert [plan[diagram[-1] + n]["note_marker"] for n in (1, 2)] == ["주1)", "주2)"]


def test_split_prose_keeps_one_scope_and_definitions_after_its_last_chunk():
    draft = copy.deepcopy(DRAFT)
    draft["sections"][0]["paragraphs"] = ["□ " + "확인 조건과 원본을 보존함. " * 25 + "[S4]"]
    plan = report_blocks(draft, rich=True, visual_settings=defaults())
    prose = [i for i, item in enumerate(plan) if item.get("note_scope") == "note-1" and not item.get("note_source_id")]
    assert len(prose) > 1
    assert plan[prose[-1] + 1]["note_source_id"] == "S4"
    assert sum(item.get("note_scope") == "note-1" and bool(item.get("note_source_id")) for item in plan) == 1


def test_unused_reference_is_preserved_without_a_fictitious_local_marker():
    draft = copy.deepcopy(DRAFT)
    draft["sources"].append(dict(id="S2", title="미인용 합성 자료", url="https://example.com/extra", published=""))
    plan = report_blocks(draft, rich=True, visual_settings=defaults())
    unused = [item for item in plan if item.get("unreferenced_source_id")]
    assert len(unused) == 1 and unused[0]["unreferenced_source_id"] == "S2"
    assert unused[0]["text"].startswith("미인용 합성 자료")
    assert not unused[0].get("citation_ranges")


def test_legacy_requests_retain_global_markers_and_trailing_source_section():
    plan = report_blocks(DRAFT, rich=True, visual_settings={"citation_style": "NOTE"})
    assert "local_notes" not in plan[0]
    assert any("주8)주4)" in item["text"] for item in plan)
    assert any(item["text"] == "출처 및 확인 사항" for item in plan)
    assert not any(item.get("note_scope") for item in plan)


def test_plain_scaffold_keeps_table_notes_after_all_cells_too():
    plan = report_blocks(DRAFT, visual_settings=defaults())
    cells = [i for i, item in enumerate(plan) if item.get("note_visual_group") == "table-1"]
    assert plan[cells[-1] + 1]["note_source_id"] == "S4"
    assert plan[cells[-1] + 2]["note_source_id"] == "S8"
    assert plan[cells[-1] + 1]["style_kind"] == "table_source"


def test_notes_are_indented_under_parent_text_and_explicit_note_indent_wins():
    settings = defaults()
    raw, plan = generated(settings=settings, draft=DRAFT)
    header, section = parts(raw)
    first = next(item for item in plan if item.get("note_source_id"))
    expected = settings["square_left_indent_pt"] + max(0, -settings["square_first_line_indent_pt"])
    assert paragraph_named(header, section, first["text"])[1]["left"] == expected
    assert paragraph_named(header, section, first["text"])[1]["intent"] < 0
    raw, _ = generated(settings={"note_left_indent_pt": 17, "note_first_line_indent_pt": -5}, draft=DRAFT)
    header, section = parts(raw)
    margins = paragraph_named(header, section, first["text"])[1]
    assert margins["left"] == 17 and margins["intent"] == -5


def test_note_text_and_markers_survive_native_layout_without_becoming_superscript():
    raw, plan = generated(draft=DRAFT)
    header, section = parts(raw)
    from app.document_standard import HP, HH
    chars = {node.get("id"): node for node in header.iter(f"{{{HH}}}charPr")}
    for note in (item for item in plan if item.get("note_source_id")):
        paragraph, _ = paragraph_named(header, section, note["text"])
        assert all(chars[run.get("charPrIDRef")].find(f"{{{HH}}}supscript") is None for run in paragraph.findall(f"{{{HP}}}run"))


def test_narrow_table_notes_stay_within_both_table_edges():
    from app.document_standard import HP, HH
    raw, _ = generated(settings={"table_width_percent": 80}, draft=DRAFT)
    header, section = parts(raw)
    index = next(i for i, p in enumerate(section) if p.find(f".//{{{HP}}}tbl[@colCnt='2']") is not None)
    note = section[index + 1]
    props = next(p for p in header.iter(f"{{{HH}}}paraPr") if p.get("id") == note.get("paraPrIDRef"))
    margins = {n.tag.split("}")[-1]: int(n.get("value")) / 100
               for n in props.find(f"{{{HP}}}switch/{{{HP}}}case/{{{HH}}}margin")}
    assert margins["left"] == pytest.approx(48.19, abs=.01)
    assert margins["right"] == margins["left"]


def test_heading_moves_with_first_paragraph_and_its_note_at_page_boundary(monkeypatch):
    from app import report_rich, reference_report
    from app.document_standard import HP
    draft = dict(title="경계 검수", summary="합성 검수", sections=[
        dict(heading="앞절", paragraphs=["□ 채움 항목"]),
        dict(heading="연결 제목", paragraphs=["□ 주석 있는 항목 [S1]"]),
        dict(heading="뒷절", paragraphs=["□ 마지막 항목"])],
        sources=[dict(id="S1", title="합성 출처", url="https://example.com/note", published="")], limitations=[])
    original_text = report_rich.RichLayout.text
    def measured_text(self, p, item, **kwargs):
        original_text(self, p, item, **kwargs)
        if item.get("note_source_id"): return 80
        return {"□ 채움 항목": 200, "2. 연결 제목": 30, "□ 주석 있는 항목 *": 60}.get(item["text"], 20)
    monkeypatch.setattr(report_rich.RichLayout, "text", measured_text)
    monkeypatch.setattr(report_rich, "layout_metrics", lambda *args: (481.9, 400))
    monkeypatch.setattr(reference_report, "title_layout_cost", lambda settings, cost, **kwargs: cost)
    raw, _ = generated(draft=draft)
    header, section = parts(raw)
    heading, _ = paragraph_named(header, section, "2. 연결 제목")
    body, _ = paragraph_named(header, section, "□ 주석 있는 항목")
    note, _ = paragraph_named(header, section, "* 합성 출처")
    assert heading.get("pageBreak") == "1"
    assert body.get("pageBreak") == note.get("pageBreak") == "0"
