"""Source-derived TOC spacing; synthetic page evidence is not renderer proof."""
import base64
import copy
import io
import json
import uuid
import zipfile

import pytest
from fastapi import HTTPException

from app.block_hwpx import HH, HP
from app.hwpx import digest
from app.reference_report import BODY_SPACING_POLICY, STANDARD_STYLE, toc_settings
from app.reference_standard import defaults
from app.report_toc import initial_config
from app.toc_contract import TocPreview, TocVerify
from app.toc_hwpx import analyze, compose_toc, verify_toc
from test_document_toc import edit, refreshed, verified_body
from test_reference_report import generated, parts


def prepared(raw, plan):
    preview = dict(id=str(uuid.uuid4()), plan=plan)
    request_id = str(uuid.uuid4())
    config = initial_config(preview, raw, request_id)
    value = dict(request_id=request_id, base_version=preview["id"],
                 source_base64=base64.b64encode(raw).decode(), source_sha256=digest(raw),
                 **config, pass_index=1, pages={})
    output, proof = compose_toc(raw, TocPreview.model_validate(value))
    return value, output, proof


def toc_rows(raw):
    header, section = parts(raw)
    info = analyze(raw, digest(raw))["toc"]
    table = section[info["paragraph"]].find(f".//{{{HP}}}tbl")
    paras = {p.get("id"): p for p in header.iter(f"{{{HH}}}paraPr")}
    styles = {p.get("id"): p.get("name", "") for p in header.iter(f"{{{HH}}}style")}
    rows = []
    for cell in table.iter(f"{{{HP}}}tc"):
        p = cell.find(f"{{{HP}}}subList/{{{HP}}}p")
        if styles[p.get("styleIDRef")].startswith("HWP Assistant TOC frame:"):
            continue
        para = paras[p.get("paraPrIDRef")]
        margins = [{n.tag.split("}")[-1]: int(n.get("value")) for n in margin}
                   for margin in para.iter(f"{{{HH}}}margin")]
        rows.append(dict(margins=margins, height=int(cell.find(f"{{{HP}}}cellSz").get("height"))))
    return info, rows


def test_new_reference_toc_matches_source_gaps_without_anonymous_paragraphs():
    raw, plan = generated(defaults("appendix-2"))
    assert plan[0]["body_spacing"] == BODY_SPACING_POLICY
    assert toc_settings(parts(raw)[0])["body_spacing"] == BODY_SPACING_POLICY
    value, output, proof = prepared(raw, plan)
    info, rows = toc_rows(output)
    assert info["status"] == "current"
    # Pinned top129: 2232 + 2034 title-row height; blank 1400 * 160%.
    assert rows[0]["height"] == 4266
    assert all(row["margins"][0]["prev"] == 2240 for row in rows[1:])
    assert all(row["margins"][1]["prev"] == 4480 for row in rows[1:])
    header, section = parts(output)
    host = section[info["paragraph"]]
    assert len(list(host.iter(f"{{{HP}}}p"))) == len(info["headings"]) + 6
    assert all(h["text_sha256"] == digest(h["native_text"].encode()) for h in info["headings"])
    # Existing evidence validation and convergence logic remains the authority.
    result = verify_toc(raw, output, output, TocVerify.model_validate(verified_body(value, output, proof)), proof)
    assert result["can_adopt"]
    changed = edit(output, "Contents/section0.xml", lambda section:
        setattr(section[-1].find(f"{{{HP}}}run/{{{HP}}}t"), "text", "Manual source note retained."))
    assert analyze(changed, digest(changed))["toc"]["status"] == "stale"
    updated, _ = compose_toc(changed, TocPreview.model_validate(refreshed(changed, value)))
    updated_info, updated_rows = toc_rows(updated)
    assert updated_info["status"] == "current" and updated_info["heading_ids"] == info["heading_ids"]
    assert updated_rows == rows
    with zipfile.ZipFile(io.BytesIO(updated)) as archive:
        assert b"Manual source note retained." in archive.read("Contents/section0.xml")


def test_old_reference_marker_never_acquires_new_toc_spacing():
    raw, plan = generated(defaults("appendix-2"))
    plan = copy.deepcopy(plan)
    plan[0].pop("body_spacing")
    plan[0].pop("toc_layout")
    def remove_policy(header):
        marker = next(s for s in header.iter(f"{{{HH}}}style") if s.get("name") == STANDARD_STYLE)
        settings = json.loads(marker.get("engName")); settings.pop("body_spacing"); settings.pop("toc_layout")
        marker.set("engName", json.dumps(settings))
    raw = edit(raw, "Contents/header.xml", remove_policy)
    _, output, _ = prepared(raw, plan)
    assert "body_spacing" not in toc_settings(parts(output)[0])
    _, rows = toc_rows(output)
    assert rows[0]["height"] == 3480  # Existing 32pt title and rounded source cell padding.
    assert all(row["margins"][0]["prev"] == row["margins"][0]["next"] == 0 for row in rows)


def test_explicit_toc_margins_override_source_spacing():
    settings = {**defaults("appendix-2"), "toc_title_before_pt": 12, "toc_title_after_pt": 6,
                "toc_major_before_pt": 5, "toc_major_after_pt": 7}
    raw, plan = generated(settings)
    _, output, _ = prepared(raw, plan)
    _, rows = toc_rows(output)
    assert (rows[0]["margins"][0]["prev"], rows[0]["margins"][0]["next"]) == (1200, 600)
    assert all((r["margins"][0]["prev"], r["margins"][0]["next"]) == (500, 700) for r in rows[1:])


def test_new_toc_includes_native_subheadings_and_preserves_frozen_selection(monkeypatch):
    import test_reference_report as fixture
    draft = copy.deepcopy(fixture.DRAFT)
    draft["sections"][0]["paragraphs"] = ["가. 수집 범위", "□ 12건의 자료를 확인함.",
        "나. 제외 기준", "□ 승인되지 않은 자료를 제외함.", "(1) 별도 검증 항목"]
    monkeypatch.setattr(fixture, "DRAFT", draft)
    raw, plan = generated(defaults("appendix-2"))
    value, output, _ = prepared(raw, plan)
    info, rows = toc_rows(output)
    # Replacing the original cited body paragraphs makes S1/S2 unreferenced.
    # Preserve those supplied sources in the new reference-material section;
    # it is a real heading, distinct from the verification limitations.
    assert [(h["text"], h["level"]) for h in info["headings"]] == [
        ("1. 수집 현황", 1), ("가. 수집 범위", 2), ("나. 제외 기준", 2),
        ("2. 정비 방안", 1), ("3. 추진 일정", 1), ("확인 사항", 1), ("참고자료", 1),
    ]
    assert [h["text"] for h in info["headings"] if h["level"] == 2] == ["가. 수집 범위", "나. 제외 기준"]
    assert [rows[i]["margins"][0]["prev"] for i in (1, 2, 3, 4)] == [2240, 2240, 0, 2240]
    assert all(h["id"] == str(uuid.uuid5(uuid.UUID(value["base_version"]), "heading:" + item["command_id"]))
               for h, item in zip(info["headings"], [p for p in plan if p["kind"] == "heading" or p.get("toc_level") == 2]))
    frozen = copy.deepcopy(plan); frozen[0].pop("body_spacing")
    legacy = initial_config(dict(id=value["base_version"], plan=frozen), raw, str(uuid.uuid4()))
    assert [h["level"] for h in legacy["headings"]] == [1, 1, 1, 1, 1]
    assert all(h["heading_id"] in info["heading_ids"] for h in legacy["headings"])


def test_numbered_body_items_are_not_mistaken_for_toc_subheadings():
    raw, plan = generated({**defaults("appendix-2"), "numbering_style": "NUMBERED"})
    assert any(p.get("style_kind") == "subheading" for p in plan)
    assert not any(p.get("toc_level") == 2 for p in plan)
    _, output, _ = prepared(raw, plan)
    info, _ = toc_rows(output)
    assert [h["level"] for h in info["headings"]] == [1, 1, 1, 1]


def test_subheading_selection_keeps_existing_thirty_entry_bound(monkeypatch):
    import test_reference_report as fixture
    draft = copy.deepcopy(fixture.DRAFT)
    for section in draft["sections"]:
        section["paragraphs"] = [f"가. 검토 대상 {i}" for i in range(9)]
    monkeypatch.setattr(fixture, "DRAFT", draft)
    raw, plan = generated(defaults("appendix-2"))
    with pytest.raises(HTTPException) as exc:
        initial_config(dict(id=str(uuid.uuid4()), plan=plan), raw, str(uuid.uuid4()))
    assert exc.value.status_code == 422 and "1~30" in exc.value.detail
