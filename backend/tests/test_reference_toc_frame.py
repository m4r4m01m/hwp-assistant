"""Fixed reference TOC frame contracts; no fabricated renderer measurements."""
import copy
import json
import uuid

import pytest
from fastapi import HTTPException

from app.block_contract import canonical
from app.block_hwpx import HH, HP, plain_text
from app.document_standard import source_parts
from app.hwpx import digest
from app.reference_report import STANDARD_STYLE, TOC_LAYOUT_LEGACY_POLICY, TOC_LAYOUT_POLICY, toc_settings
from app.reference_standard import defaults
from app.toc_contract import TocPreview, TocVerify
from app.toc_hwpx import TocSemantic, PREFIX, analyze, compose_toc, packed, verify_toc
from test_document_toc import edit, refreshed, verified_body
from test_reference_report import generated, parts
from test_reference_toc_spacing import prepared


def table_parts(raw):
    header, section = parts(raw)
    info = analyze(raw, digest(raw))["toc"]
    return header, section, info, section[info["paragraph"]].find(f".//{{{HP}}}tbl")


def marker_layout(raw, layout):
    def change(header):
        marker = next(p for p in header.iter(f"{{{HH}}}style") if p.get("name") == STANDARD_STYLE)
        value = json.loads(marker.get("engName"))
        if layout is None: value.pop("toc_layout", None)
        else: value["toc_layout"] = layout
        marker.set("engName", canonical(value))
    return edit(raw, "Contents/header.xml", change)


def test_native_frame_matches_source_cutout_and_frozen_settings():
    raw, plan = generated({**defaults("appendix-2"), "toc_border_style": "DASH", "toc_border_color": "#2468AC"})
    assert plan[0]["toc_layout"] == toc_settings(parts(raw)[0])["toc_layout"] == TOC_LAYOUT_POLICY
    _, output, _ = prepared(raw, plan)
    header, _, info, table = table_parts(output)
    assert info["layout"] == TOC_LAYOUT_POLICY and info["status"] == "current"
    assert table.get("pageBreak") == "CELL" and table.get("textWrap") == "TOP_AND_BOTTOM"
    assert table.find(f"{{{HP}}}pos").get("treatAsChar") == "0"
    assert table.find(f"{{{HP}}}pos").get("flowWithText") == "1"
    rows = [row.findall(f"{{{HP}}}tc") for row in table.findall(f"{{{HP}}}tr")]
    assert [len(row) for row in rows] == [3, 2] + [1] * len(info["headings"])
    assert rows[0][1].find(f"{{{HP}}}cellSpan").attrib == dict(colSpan="1", rowSpan="2")
    source_table = source_parts()[2][129].find(f".//{{{HP}}}tbl")
    expected_title = plain_text(source_table.findall(f"{{{HP}}}tr")[0].findall(f"{{{HP}}}tc")[1].find(f"{{{HP}}}subList/{{{HP}}}p"))
    assert expected_title.strip() and plain_text(rows[0][1].find(f"{{{HP}}}subList/{{{HP}}}p")) == expected_title
    fills = {fill.get("id"): fill for fill in header.iter(f"{{{HH}}}borderFill")}
    def edges(cell):
        borders = [edge for edge in fills[cell.get("borderFillIDRef")] if edge.tag.endswith("Border") and edge.get("type") != "NONE"]
        assert all(edge.get("type") == "DASH" and edge.get("color") == "#2468AC" for edge in borders)
        return {edge.tag.split("}")[-1] for edge in borders}
    assert edges(rows[0][0]) == edges(rows[0][2]) == {"bottomBorder"}
    assert edges(rows[0][1]) == set()
    assert edges(rows[1][0]) == {"leftBorder", "topBorder"}
    assert edges(rows[1][1]) == {"rightBorder", "topBorder"}
    assert edges(rows[2][0]) == {"leftBorder", "rightBorder"}
    assert edges(rows[-1][0]) == {"leftBorder", "rightBorder", "bottomBorder"}
    assert all(row[0].find(f"{{{HP}}}cellSpan").attrib == dict(colSpan="3", rowSpan="1") for row in rows[2:])


@pytest.mark.parametrize("damage", ["blank-text", "extra-blank", "missing-blank", "wrong-marker", "span", "root-copy", "root-blank", "unknown-layout", "inline-flow"])
def test_frame_structure_stays_strict_even_with_recomputed_managed_hash(damage):
    raw, plan = generated(defaults("appendix-2"))
    _, output, _ = prepared(raw, plan)
    header, section, info, table = table_parts(output)
    cells = list(table.iter(f"{{{HP}}}tc"))
    first = cells[0].find(f"{{{HP}}}subList/{{{HP}}}p")
    root = cells[1].find(f"{{{HP}}}subList/{{{HP}}}p")
    if damage == "blank-text": first.find(f"{{{HP}}}run/{{{HP}}}t").text = "hidden entry"
    elif damage == "extra-blank": cells[0].find(f"{{{HP}}}subList").append(copy.deepcopy(first))
    elif damage == "missing-blank": cells[0].find(f"{{{HP}}}subList").remove(first)
    elif damage == "wrong-marker": first.set("styleIDRef", cells[2].find(f"{{{HP}}}subList/{{{HP}}}p").get("styleIDRef"))
    elif damage == "span": cells[1].find(f"{{{HP}}}cellSpan").set("rowSpan", "1")
    elif damage == "root-copy": cells[0].find(f"{{{HP}}}subList").append(copy.deepcopy(root))
    elif damage == "root-blank": root.find(f"{{{HP}}}run/{{{HP}}}t").text = ""
    elif damage == "inline-flow": table.find(f"{{{HP}}}pos").set("treatAsChar", "1")
    marker = next(p for p in header.iter(f"{{{HH}}}style") if p.get("id") == root.get("styleIDRef"))
    meta = json.loads(marker.get("engName"))
    if damage == "unknown-layout": meta["layout"] = "arbitrary-frame"
    sem = TocSemantic(packed(output, header, section))
    meta["managed_sha256"] = digest(canonical(sem.node(sem.section[info["paragraph"]])).encode())
    marker.set("engName", canonical(meta))
    changed = packed(output, header, section)
    assert analyze(changed, digest(changed))["toc"]["status"] == "damaged"


def test_frame_spacer_cannot_be_copied_outside_its_managed_host():
    raw, plan = generated(defaults("appendix-2"))
    _, output, _ = prepared(raw, plan)
    header, section, _, table = table_parts(output)
    section.append(copy.deepcopy(table.find(f"{{{HP}}}tr/{{{HP}}}tc/{{{HP}}}subList/{{{HP}}}p")))
    changed = packed(output, header, section)
    with pytest.raises(HTTPException): analyze(changed, digest(changed))


def test_ordinary_refresh_keeps_the_existing_layout_when_general_marker_changes():
    raw, plan = generated(defaults("appendix-2"))
    frozen = copy.deepcopy(plan); frozen[0].pop("toc_layout")
    legacy = marker_layout(raw, None)
    value, output, _ = prepared(legacy, frozen)
    changed = marker_layout(output, TOC_LAYOUT_POLICY)
    updated, _ = compose_toc(changed, TocPreview.model_validate(refreshed(changed, value)))
    _, _, info, table = table_parts(updated)
    assert "layout" not in info and table.get("colCnt") == "1"
    # Conversely, removal of the general frame marker does not downgrade a
    # frame whose layout is already frozen in the managed root metadata.
    value, output, _ = prepared(raw, plan)
    changed = marker_layout(output, None)
    updated, _ = compose_toc(changed, TocPreview.model_validate(refreshed(changed, value)))
    _, _, info, table = table_parts(updated)
    assert info["layout"] == TOC_LAYOUT_POLICY and table.get("colCnt") == "3"


def test_frame_refresh_reuses_four_spacer_styles_and_retains_history():
    raw, plan = generated(defaults("appendix-2"))
    value, output, _ = prepared(raw, plan)
    original = {style.get("id"): copy.deepcopy(style.attrib) for style in parts(output)[0].iter(f"{{{HH}}}style")}
    for _ in range(5):
        output, _ = compose_toc(output, TocPreview.model_validate(refreshed(output, value)))
    styles = list(parts(output)[0].iter(f"{{{HH}}}style"))
    assert len(styles) == len(original) + 5
    assert len([style for style in styles if style.get("name", "").startswith(PREFIX + "frame:")]) == 4
    assert original.items() <= {style.get("id"): style.attrib for style in styles}.items()
    assert analyze(output, digest(output))["toc"]["status"] == "current"


def test_frozen_v1_frame_replay_and_refresh_keep_inline_flow():
    raw, plan = generated(defaults("appendix-2"))
    frozen = copy.deepcopy(plan); frozen[0]["toc_layout"] = TOC_LAYOUT_LEGACY_POLICY
    raw = marker_layout(raw, TOC_LAYOUT_LEGACY_POLICY)
    value, output, _ = prepared(raw, frozen)
    _, _, info, table = table_parts(output)
    assert info["layout"] == TOC_LAYOUT_LEGACY_POLICY and table.find(f"{{{HP}}}pos").get("treatAsChar") == "1"
    changed = marker_layout(output, TOC_LAYOUT_POLICY)
    updated, _ = compose_toc(changed, TocPreview.model_validate(refreshed(changed, value)))
    _, _, info, table = table_parts(updated)
    assert info["layout"] == TOC_LAYOUT_LEGACY_POLICY and table.find(f"{{{HP}}}pos").get("treatAsChar") == "1"


def test_frame_supports_thirty_entries_and_three_hundred_character_headings(monkeypatch):
    import test_reference_report as fixture
    draft = dict(title="목차 경계 검증", summary="합성 경계시험", sources=[], limitations=[], sections=[
        dict(heading=f"검토 범위 {i}", paragraphs=[f"{label}. " + "긴" * 297 for label in "가나다라"])
        for i in range(6)])
    monkeypatch.setattr(fixture, "DRAFT", draft)
    raw, plan = generated(defaults("appendix-2"))
    # The arbitrary-document TOC contract accepts 300 native scalars. Generated
    # soft line breaks count as scalars too, so construct the exact native
    # boundary without soft breaks (the renderer remains free to wrap it).
    def unwrap(section):
        for paragraph in section:
            text = plain_text(paragraph)
            if len(text) == 300:
                run = paragraph.find(f"{{{HP}}}run")
                content = run.find(f"{{{HP}}}t")
                for child in list(content): content.remove(child)
                content.text = text
                for child in list(paragraph):
                    if child is not run: paragraph.remove(child)
    raw = edit(raw, "Contents/section0.xml", unwrap)
    value, output, proof = prepared(raw, plan)
    _, _, info, table = table_parts(output)
    assert len(info["headings"]) == 30 and max(h["length"] for h in info["headings"]) == 300
    assert len(list(table.iter(f"{{{HP}}}p"))) == 35
    assert all(row.find(f"{{{HP}}}tc/{{{HP}}}cellSpan").get("colSpan") == "3" for row in table.findall(f"{{{HP}}}tr")[2:])
    pages = {h["id"]: 2 + i // 2 for i, h in enumerate(info["headings"])}
    result = verify_toc(raw, output, output, TocVerify.model_validate(verified_body(value, output, proof, pages)), proof)
    assert not result["can_adopt"] and not result["exhausted"]
    second = value | dict(pass_index=2, pages=pages)
    updated, updated_proof = compose_toc(raw, TocPreview.model_validate(second))
    result = verify_toc(raw, updated, updated, TocVerify.model_validate(verified_body(second, updated, updated_proof, pages)), updated_proof)
    assert result["can_adopt"]
    assert [h["text"] for h in analyze(updated, digest(updated))["toc"]["headings"]] == [h["text"] for h in info["headings"]]
    third = value | dict(pass_index=3, pages=pages)
    updated, updated_proof = compose_toc(raw, TocPreview.model_validate(third))
    result = verify_toc(raw, updated, updated, TocVerify.model_validate(verified_body(third, updated, updated_proof, {key: page + 1 for key, page in pages.items()})), updated_proof)
    assert result["exhausted"] and not result["can_adopt"]
    with pytest.raises(Exception): TocPreview.model_validate(value | dict(pass_index=4, pages=pages))
