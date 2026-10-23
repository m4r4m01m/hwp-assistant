"""Reference formatting in production report paths; synthetic text, no AI claim."""
import copy
import io
import uuid
import zipfile
from xml.etree import ElementTree as E

import pytest

from app.document_standard import HP, HH, format_signature, load_standard
from app.hwpx import digest
from app.reference_standard import defaults, VisualSettingsV3
from app.reference_report import toc_settings, TOC_ANCHOR_STYLE
from app.report_contract import report_blocks
from app.report_hwpx import scaffold, paragraphs
from app.report_rich import layout_rich_report
from app.report_runner import writing_prompt, PROMPT
from app.report_toc import initial_config
from app.toc_contract import TocPreview
from app.toc_hwpx import compose_toc, analyze
from test_reports import seed

DRAFT = dict(title="자료 정비 검수", summary="요약은 보존됨.", sections=[
    dict(heading="수집 현황", paragraphs=["□ 2026년 10월 자료 12건을 확인함. [S1]", "ㅇ 보완 조건(승인 후)을 유지함. [S2]"],
         tables=[dict(caption="자료 상태", columns=["항목", "수량"], rows=[["완료", "12건"]])]),
    dict(heading="정비 방안", paragraphs=["□ 담당자가 누락 항목을 확인함."]),
    dict(heading="추진 일정", paragraphs=["□ 매주 금요일 15:00에 공유함."])],
    sources=[dict(id="S1", title="합성 자료 1", url="https://example.com/1", published="2026. 10. 1."),
             dict(id="S2", title="합성 자료 2", url="https://example.com/2", published="2026. 10. 2.")],
    limitations=["합성 검수 자료이며 실제 모델 출력이 아님."])


def generated(settings):
    settings = VisualSettingsV3.model_validate(settings).model_dump(mode="json")
    blocks = report_blocks(DRAFT, concise=True, rich=True, visual_settings=settings)
    raw, plan = scaffold(seed(), blocks, visual_settings=settings)
    root, _ = paragraphs(raw)
    for item in plan:
        root[item["paragraph"]].find(f"{{{HP}}}run/{{{HP}}}t").text = item["text"]
        item["command_id"] = str(uuid.uuid4())
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(out, "w") as target:
        for entry in source.infolist():
            target.writestr(entry, E.tostring(root) if entry.filename == "Contents/section0.xml" else source.read(entry))
    applied = out.getvalue()
    assert format_signature(applied, plan) == format_signature(raw, plan)
    return layout_rich_report(applied, plan, visual_settings=settings), plan


def parts(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        return E.fromstring(source.read("Contents/header.xml")), E.fromstring(source.read("Contents/section0.xml"))


def test_reference_numbering_note_ranges_and_facts_are_frozen_before_layout():
    draft = copy.deepcopy(DRAFT)
    settings = {**defaults(), "numbering_style": "NUMBERED", "page_number_style": "PAGE_TOTAL"}
    plan = report_blocks(draft, rich=True, visual_settings=settings)
    assert draft == DRAFT
    assert not any(p["text"] == "핵심 요약" for p in plan)
    first = next(p for p in plan if "12건을 확인" in p["text"])
    assert first["text"] == "가. 2026년 10월 자료 12건을 확인함. *"
    assert first["style_kind"] == "subheading"
    assert [first["text"][a:b] for a, b in first["citation_ranges"]] == ["*"]
    assert any(p["text"] == "(1) 보완 조건(승인 후)을 유지함. *" for p in plan)
    raw, _ = generated(settings)
    h, s = parts(raw)
    chars = {n.get("id"): n for n in h.iter(f"{{{HH}}}charPr")}
    marked = [r for r in s.iter(f"{{{HP}}}run") if r.find(f"{{{HP}}}t") is not None and r.find(f"{{{HP}}}t").text in ("*", "**")]
    assert marked and all(chars[r.get("charPrIDRef")].find(f"{{{HH}}}supscript") is not None for r in marked)
    assert [n.get("numType") for n in s.iter(f"{{{HP}}}autoNum")] == ["PAGE", "TOTAL_PAGE"]


def test_reference_font_geometry_borders_and_marker_controls_reach_report():
    settings = {**defaults(), "numbering_style": "BULLETS", "bullet_square_marker": "◆", "citation_style": "NOTE",
                "square_size_pt": 16, "square_ratio_percent": 90, "square_after_pt": 8,
                "table_border_style": "DOT", "table_header_fill": "#DDDDDD", "table_padding_left_mm": 2,
                "margin_left_mm": 25, "table_repeat_header": False}
    raw, plan = generated(settings)
    h, s = parts(raw)
    assert any(p["text"].startswith("◆ 2026년") and "주1)" in p["text"] for p in plan)
    assert s.find(f".//{{{HP}}}margin").get("left") == str(round(25 * 7200 / 25.4))
    table = next(t for t in s.iter(f"{{{HP}}}tbl") if t.get("colCnt") == "2")
    assert table.get("repeatHeader") == "0"
    cells = list(table.iter(f"{{{HP}}}tc"))
    fills = {n.get("id"): n for n in h.iter(f"{{{HH}}}borderFill")}
    assert fills[cells[0].get("borderFillIDRef")].find(f"{{{HH}}}leftBorder").get("type") == "NONE"
    assert fills[cells[0].get("borderFillIDRef")].find(f"{{{HH}}}rightBorder").get("type") == "DOT"
    assert cells[0].find(f"{{{HP}}}cellMargin").get("left") == str(round(2 * 7200 / 25.4))


@pytest.mark.parametrize("preset_id", ["appendix-2", "appendix-3"])
def test_cover_toc_anchor_preserves_title_identity_and_custom_toc_after_refresh(preset_id):
    settings = {**defaults(preset_id), "toc_major_size_pt": 18, "toc_major_left_indent_pt": 24, "toc_padding_left_mm": 1}
    raw, plan = generated(settings)
    preview = dict(id=str(uuid.uuid4()), plan=plan)
    request_id = str(uuid.uuid4())
    config = initial_config(preview, raw, request_id)
    assert config["target"]["paragraph"] == (2 if preset_id == "appendix-2" else 1)
    value = TocPreview.model_validate(dict(request_id=request_id, base_version=preview["id"],
        source_base64=__import__("base64").b64encode(raw).decode(), source_sha256=digest(raw), **config,
        pass_index=1, pages={}))
    output, proof = compose_toc(raw, value)
    h, s = parts(output)
    assert toc_settings(h)["toc_major_size_pt"] == 18
    info = analyze(output, digest(output))
    assert info["toc"]["status"] == "current"
    assert len(info["toc"]["headings"]) == 4
    assert all(item["text_sha256"] == digest(item["native_text"].encode()) for item in info["toc"]["headings"])
    table = s[info["toc"]["paragraph"]].find(f".//{{{HP}}}tbl")
    assert table.find(f"{{{HP}}}inMargin").get("left") == str(round(7200 / 25.4))
    assert "2015" not in "".join(t.text or "" for t in s.iter(f"{{{HP}}}t"))
    # Ordinary editor refresh obtains the persisted format without a task lookup.
    again = TocPreview.model_validate(dict(request_id=str(uuid.uuid4()), base_version=preview["id"],
        source_base64=__import__("base64").b64encode(output).decode(), source_sha256=digest(output),
        toc_id=info["toc"]["id"], target=info["toc"]["anchor"],
        headings=[dict(heading_id=p["id"], paragraph=p["paragraph"], level=p["level"], expected_text_sha256=p["text_sha256"]) for p in info["toc"]["headings"]], pass_index=1, pages={}))
    refreshed, _ = compose_toc(output, again)
    assert analyze(refreshed, digest(refreshed))["toc"]["status"] == "current"
    assert toc_settings(parts(refreshed)[0])["toc_major_size_pt"] == 18


def test_reference_prompt_uses_frozen_rules_without_changing_legacy_prompt():
    settings = {**defaults(), "date_rule": "날짜는 2026. 10. 20. 형식으로 작성한다."}
    prompt = writing_prompt(dict(visual_standard=dict(revision=3, settings=settings)))
    assert settings["date_rule"] in prompt and "selected 14pt" in prompt
    assert "Do not add parenthesized lead labels" in prompt
    assert writing_prompt({}) == PROMPT


def test_page_total_literal_is_not_body_text_but_is_fenced_by_format_signature():
    settings = {**defaults(), "page_number_style": "PAGE_TOTAL"}
    raw, plan = scaffold(seed(), report_blocks(DRAFT, rich=True, visual_settings=settings), visual_settings=settings)
    assert paragraphs(raw)[1][0] == ""
    assert format_signature(raw, plan)["footer_numbers"][0]["paragraphs"][0]["text"] == "/"
    assert digest(load_standard()[1]) == "6c68b50656867f1fc724c50549e60e1109e2f7a00219f2928ef58de0a45711f1"


@pytest.mark.parametrize("preset_id", ["appendix-1", "appendix-2", "appendix-3"])
def test_original_preset_body_prose_keeps_bullets_and_body_font(preset_id):
    settings = defaults(preset_id)
    before = copy.deepcopy(DRAFT)
    raw, plan = generated(settings)
    assert DRAFT == before
    first = next(p for p in plan if "12건을 확인" in p["text"])
    assert first["text"] == "□ 2026년 10월 자료 12건을 확인함. *"
    assert first["style_kind"] == "square"
    assert any(p["text"] == "ㅇ 보완 조건(승인 후)을 유지함. *" for p in plan)
    header, section = parts(raw)
    chars = {node.get("id"): node for node in header.iter(f"{{{HH}}}charPr")}
    hangul = next(node for node in header.iter(f"{{{HH}}}fontface") if node.get("lang") == "HANGUL")
    fonts = {node.get("id"): node.get("face") for node in hangul}
    prose = next(p for p in section.iter(f"{{{HP}}}p")
                 if "12건을 확인" in "".join(t.text or "" for t in p.iter(f"{{{HP}}}t")))
    run = next(r for r in prose.findall(f"{{{HP}}}run")
               if "12건을 확인" in "".join(t.text or "" for t in r.findall(f"{{{HP}}}t")))
    char = chars[run.get("charPrIDRef")]
    assert char.get("height") == "1400"
    assert fonts[char.find(f"{{{HH}}}fontRef").get("hangul")] == "휴먼명조"
