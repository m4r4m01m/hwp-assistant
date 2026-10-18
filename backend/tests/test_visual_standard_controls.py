"""Revisioned presentation controls. Synthetic AI/receipts, no real engine proof."""
import base64
import copy
import io
import json
import uuid
import zipfile
from xml.etree import ElementTree as E

import pytest
from pydantic import ValidationError

from app import visual_standards as visual
from app.db import ReportTask, transaction
from app.document_standard import HP, HH, format_signature
from app.report_contract import report_blocks, report_hash
from app.report_hwpx import scaffold
from app.report_rich import layout_rich_report
from report_rich_fixture import RICH_DRAFT
from test_collaboration import secured
from test_reports import report_env, seed, ready
from test_reports import synthetic_output
from test_visual_standard_rendering import applied, parsed, logical_text
from test_visual_report_integration import create_task, prepared, registered
from test_report_preview import render


def settings(**values):
    return {**visual.DEFAULTS, **values}


def rendered(**values):
    config = settings(**values)
    blocks = report_blocks(copy.deepcopy(RICH_DRAFT), concise=True, rich=True, visual_settings=config)
    data, plan = scaffold(seed(), blocks, visual_settings=config)
    output = layout_rich_report(applied(data, plan), plan, visual_settings=config)
    header, section = parsed(output)
    assert [t for t in logical_text(section) if t] == [p["text"] for p in plan]
    return config, header, section


def test_revision_one_snapshot_and_old_registration_retry_keep_exact_hash_and_json(secured):
    client, _, _, _ = secured
    old = visual.LEGACY_DEFAULTS
    body = {"settings": old, "seed_base64": base64.b64encode(seed()).decode()}
    preview = client.post("/api/visual-standards/preview", json=body)
    assert preview.status_code == 200, preview.text
    receipt = preview.json()
    assert receipt["settings_sha256"] == report_hash(old)
    body.update({key: receipt[key] for key in ("settings_sha256", "preview_sha256", "base_standard")},
                id=str(uuid.uuid4()), name="이전 계약 표준")
    result = client.post("/api/visual-standards", json=body)
    assert result.status_code == 200, result.text
    snapshot = result.json()
    assert snapshot["revision"] == 1 and snapshot["settings"] == old
    assert len(snapshot["settings"]) == 23
    assert visual.VisualSelection.model_validate(snapshot).model_dump(mode="json") == snapshot
    assert client.post("/api/visual-standards", json=body).json() == snapshot
    assert client.get("/api/visual-standards/" + snapshot["id"]).json() == snapshot
    # A revision cannot be relabelled while retaining the old setting hash.
    with pytest.raises(ValidationError):
        visual.VisualSelection.model_validate({**snapshot, "revision": 2})


@pytest.mark.parametrize("change", [
    {"show_core_summary": 0}, {"body_parenthetical_labels": "false"},
    {"table_horizontal_align": "JUSTIFY"}, {"table_vertical_align": "MIDDLE"},
    {"table_border_width_mm": True}, {"table_border_width_mm": .8}, {"table_cell_padding_mm": -1},
    {"title_vertical_offset_pt": -13}, {"page_number_style": "PAREN"},
    {"citation_style": "custom-unbound"},
])
def test_new_controls_validate_strictly(change):
    with pytest.raises(ValidationError):
        visual.VisualSettingsV2.model_validate(settings(**change))


@pytest.mark.parametrize("horizontal,vertical", [("LEFT", "TOP"), ("CENTER", "CENTER"), ("RIGHT", "BOTTOM")])
def test_table_alignment_and_uniform_cell_padding_use_native_xml(horizontal, vertical):
    _, header, section = rendered(table_horizontal_align=horizontal, table_vertical_align=vertical, table_cell_padding_mm=2.5)
    paras = {p.get("id"): p for p in header.iter(f"{{{HH}}}paraPr")}
    # Only the real data table is configurable; flow cards keep their own layout.
    table = next(section.iter(f"{{{HP}}}tbl"))
    for cell in table.iter(f"{{{HP}}}tc"):
        sub = cell.find(f"{{{HP}}}subList")
        assert sub.get("vertAlign") == vertical
        p = sub.find(f"{{{HP}}}p")
        assert paras[p.get("paraPrIDRef")].find(f"{{{HH}}}align").get("horizontal") == horizontal
        assert set(cell.find(f"{{{HP}}}cellMargin").attrib.values()) == {str(round(2.5 * 7200 / 25.4))}


@pytest.mark.parametrize("scope", ["ALL", "OUTER", "HORIZONTAL", "NONE"])
@pytest.mark.parametrize("style", ["SOLID", "DASH", "DOT", "NONE"])
def test_table_border_scope_style_width_and_color_are_per_cell(scope, style):
    _, header, section = rendered(table_border_scope=scope, table_border_style=style,
                                  table_border_width_mm=.4, table_border_color="#123456")
    fills = {fill.get("id"): fill for fill in header.iter(f"{{{HH}}}borderFill")}
    table = next(section.iter(f"{{{HP}}}tbl"))
    rows = table.findall(f"{{{HP}}}tr")
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            fill = fills[cell.get("borderFillIDRef")]
            expected = {"leftBorder": scope == "ALL" or scope == "OUTER" and x == 0,
                        "rightBorder": scope == "ALL" or scope == "OUTER" and x == len(row)-1,
                        "topBorder": scope in ("ALL", "HORIZONTAL") or scope == "OUTER" and y == 0,
                        "bottomBorder": scope in ("ALL", "HORIZONTAL") or scope == "OUTER" and y == len(rows)-1}
            for child in fill:
                name = child.tag.split("}")[-1]
                if name.endswith("Border"):
                    assert child.get("type") == (style if expected.get(name, False) else "NONE")
                    assert child.get("width") == "0.4 mm" and child.get("color") == "#123456"


@pytest.mark.parametrize("offset", [-12, 0, 12, 24])
def test_title_offset_adds_inside_frame_space_and_retains_blue_rules(offset):
    blocks = report_blocks(RICH_DRAFT)
    data, plan = scaffold(seed(), blocks, visual_settings=settings(title_vertical_offset_pt=offset))
    header, section = parsed(data)
    title = section[1]
    para = next(p for p in header.iter(f"{{{HH}}}paraPr") if p.get("id") == title.get("paraPrIDRef"))
    border = para.find(f"{{{HH}}}border")
    assert int(border.get("offsetTop")) == 365
    assert int(border.get("offsetBottom")) == 365 + max(0, -offset*100)
    signature = format_signature(data, plan)["paragraphs"][0]
    assert dict(signature["margins"])["prev"] == str(max(0, offset*100))
    assert ("topBorder", "SOLID", "1.0 mm", "#437FC1") in signature["borders"]
    assert ("bottomBorder", "SOLID", "1.0 mm", "#437FC1") in signature["borders"]


@pytest.mark.parametrize("style,number_format,side", [("DASH", "DIGIT", "-"), ("PLAIN", "DIGIT", ""),
                                                     ("ROMAN", "ROMAN_CAPITAL", ""), ("NONE", "DIGIT", "")])
@pytest.mark.parametrize("position", ["BOTTOM_LEFT", "BOTTOM_CENTER", "BOTTOM_RIGHT"])
def test_page_number_controls_remain_actual_page_fields(style, number_format, side, position):
    data, _ = scaffold(seed(), report_blocks(RICH_DRAFT), visual_settings=settings(page_number_style=style, page_number_position=position))
    _, section = parsed(data)
    numbers = list(section.iter(f"{{{HP}}}pageNum"))
    assert len(numbers) == 1
    assert numbers[0].attrib == {"pos": position if style in ("DASH", "ROMAN") else "NONE", "formatType": number_format, "sideChar": "-"}
    fields = list(section.iter(f"{{{HP}}}autoNum"))
    if style == "PLAIN":
        assert len(fields) == 1 and fields[0].get("numType") == "PAGE"
        assert fields[0].find(f"{{{HP}}}autoNumFormat").get("type") == number_format
        header, _ = parsed(data)
        fp = section.find(f".//{{{HP}}}footer/{{{HP}}}subList/{{{HP}}}p")
        para = next(p for p in header.iter(f"{{{HH}}}paraPr") if p.get("id") == fp.get("paraPrIDRef"))
        assert para.find(f"{{{HH}}}align").get("horizontal") == position.removeprefix("BOTTOM_")
    else:
        assert fields == []


@pytest.mark.parametrize("style,marker", [("BRACKET_S", "[S1]"), ("NOTE", "주1)"), ("ASTERISK", "*1")])
def test_presentation_toggles_keep_canonical_draft_conditions_facts_and_source_mapping(style, marker):
    draft = copy.deepcopy(RICH_DRAFT)
    draft["sections"][0]["paragraphs"][0] = "□ (조건) 2026년 10월 (승인한 경우에만) 3건을 처리함. [S1]"
    draft["sources"] = [{"id": "S1", "title": "검증 자료", "url": "https://example.com/facts", "published": "2026. 10. 18."}]
    draft["limitations"].append("기준 자료 [S1]의 조건을 추가 확인함")
    frozen = copy.deepcopy(draft)
    blocks = report_blocks(draft, concise=True, rich=True, visual_settings=settings(
        body_parenthetical_labels=False, show_core_summary=False, citation_style=style))
    text = " ".join(p["text"] for p in blocks)
    assert "핵심 요약" not in text and draft["summary"] not in text
    assert f"□ 2026년 10월 (승인한 경우에만) 3건을 처리함. {marker}" in text
    assert "(조건)" not in text and "조건:" not in text
    assert "(조건)" in draft["sections"][0]["paragraphs"][0]
    assert f"{marker} 검증 자료" in text and "https://example.com/facts" in text
    assert draft == frozen
    assert [p["text"] for p in blocks if p["kind"] == "heading"] == [f"{i}. " + s["heading"] for i, s in enumerate(draft["sections"], 1)] + ["출처 및 확인 사항"]
    assert all(s["heading"] in text for s in draft["sections"])
    assert all(value.replace("[S1]", marker) in text for value in draft["limitations"])
    assert f"확인 사항: 기준 자료 {marker}의 조건을 추가 확인함" in text


def test_new_report_fences_visible_controls_and_frozen_draft_separately(report_env, monkeypatch):
    client, app, _, _ = report_env[0]
    visual_snapshot = registered(client, show_core_summary=False, body_parenthetical_labels=False, citation_style="NOTE")
    assert visual_snapshot["revision"] == 2
    body, task = create_task(client, visual_snapshot)
    original_draft, original_hash = copy.deepcopy(task["draft"]), task["draft_sha256"]
    url, request, preview = prepared(client, body, task)
    assert all(item["text"] != "핵심 요약" and item["text"] != original_draft["summary"] for item in preview["plan"])
    payload, output = render(client, url, preview)
    assert client.post(url, json=request).json() == output
    assert client.post(url + "/render", json=payload).json() == output
    current = client.get(url.removesuffix("/preview")).json()
    assert current["draft"] == original_draft and current["draft_sha256"] == original_hash
    with transaction(app.state.engine) as db:
        row = db.get(ReportTask, body["id"])
        assert json.loads(row.input)["visual_standard"] == visual_snapshot


def test_version_two_preview_has_fixed_source_example_but_old_sample_is_unchanged():
    legacy = visual.render_preview(seed(), visual.LEGACY_DEFAULTS)
    modern = visual.render_preview(seed(), settings(citation_style="NOTE"))
    _, old_section = parsed(legacy)
    _, new_section = parsed(modern)
    old_text = " ".join(logical_text(old_section))
    new_text = " ".join(logical_text(new_section))
    assert "합성 예시 자료" not in old_text
    assert "주1) 합성 예시 자료" in new_text and "[S1]" not in new_text
    assert "https://example.com/synthetic-standard" in new_text


@pytest.mark.parametrize("style,damage", [
    ("DASH", "remove_page_num"), ("DASH", "position"), ("DASH", "decoration"),
    ("NONE", "remove_page_num"), ("NONE", "position"),
    ("PLAIN", "remove_footer"), ("PLAIN", "field_format"), ("PLAIN", "remove_field_format"), ("PLAIN", "alignment"),
    ("ROMAN", "remove_page_num"), ("PLAIN", "field_kind"), ("ROMAN", "number_format"),
    ("DASH", "title_frame"),
])
def test_native_page_fields_and_title_frame_are_fenced_before_layout(report_env, style, damage):
    client, _, _, _ = report_env[0]
    snapshot = registered(client, page_number_style=style, page_number_position="BOTTOM_RIGHT")
    body, task = create_task(client, snapshot)
    url, _, preview = prepared(client, body, task)
    payload = synthetic_output(preview)
    payload.pop("approval_id")
    payload["preview_id"] = preview["id"]
    raw = base64.b64decode(payload["output_base64"])
    header, section = parsed(raw)
    if damage == "remove_page_num":
        for ctrl in section.iter(f"{{{HP}}}ctrl"):
            for number in ctrl.findall(f"{{{HP}}}pageNum"):
                ctrl.remove(number)
    elif damage == "position":
        section.find(f".//{{{HP}}}pageNum").set("pos", "BOTTOM_LEFT")
    elif damage == "decoration":
        section.find(f".//{{{HP}}}pageNum").set("sideChar", "*")
    elif damage == "number_format":
        section.find(f".//{{{HP}}}pageNum").set("formatType", "DIGIT")
    elif damage == "remove_footer":
        for ctrl in section.iter(f"{{{HP}}}ctrl"):
            for footer in ctrl.findall(f"{{{HP}}}footer"):
                ctrl.remove(footer)
    elif damage in ("field_format", "field_kind", "remove_field_format"):
        number = section.find(f".//{{{HP}}}footer//{{{HP}}}autoNum")
        if damage == "field_format":
            number.find(f"{{{HP}}}autoNumFormat").set("type", "LATIN_CAPITAL")
        elif damage == "field_kind":
            number.set("numType", "FIGURE")
        else:
            number.remove(number.find(f"{{{HP}}}autoNumFormat"))
    elif damage == "alignment":
        footer_p = section.find(f".//{{{HP}}}footer/{{{HP}}}subList/{{{HP}}}p")
        para = next(p for p in header.iter(f"{{{HH}}}paraPr") if p.get("id") == footer_p.get("paraPrIDRef"))
        para.find(f"{{{HH}}}align").set("horizontal", "LEFT")
    else:
        para = next(p for p in header.iter(f"{{{HH}}}paraPr") if p.get("id") == section[1].get("paraPrIDRef"))
        para.find(f"{{{HH}}}border").set("offsetTop", "999")
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as target:
        for entry in source.infolist():
            node = {"Contents/header.xml": header, "Contents/section0.xml": section}.get(entry.filename)
            target.writestr(entry, E.tostring(node) if node is not None else source.read(entry))
    tampered = {**payload, "output_base64": base64.b64encode(output.getvalue()).decode()}
    result = client.post(url + "/render", json=tampered)
    assert result.status_code == 422, result.text
    assert "layout_base64" not in client.get(url).json()
    assert client.post(url + "/render", json=payload).status_code == 200


def test_native_page_field_signature_resolves_renumbered_style_ids():
    data, plan = scaffold(seed(), report_blocks(RICH_DRAFT), visual_settings=settings(page_number_style="PLAIN"))
    expected = format_signature(data, plan)
    header, section = parsed(data)
    for tag, attribute in (("charPr", "charPrIDRef"), ("paraPr", "paraPrIDRef")):
        mapping = {}
        for element in header.iter(f"{{{HH}}}{tag}"):
            old = element.get("id")
            mapping[old] = str(int(old) + 5000)
            element.set("id", mapping[old])
        for element in section.iter():
            if element.get(attribute) in mapping:
                element.set(attribute, mapping[element.get(attribute)])
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
        for entry in source.infolist():
            node = {"Contents/header.xml": header, "Contents/section0.xml": section}.get(entry.filename)
            target.writestr(entry, E.tostring(node) if node is not None else source.read(entry))
    assert format_signature(output.getvalue(), plan) == expected
