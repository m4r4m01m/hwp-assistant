"""Revision-3 source selections and immutable registration; no AI/engine proof."""
import base64
import io
import uuid
import zipfile
from xml.etree import ElementTree as E

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import visual_standards as visual
from app.db import VisualStandard, transaction
from app.document_standard import load_standard, HP, HH
from app.hwpx import digest
from app.reference_standard import VisualSettingsV3, defaults, render_preset
from app.report_contract import report_hash
from test_collaboration import secured
from test_reports import seed


def _selection(item):
    return {key: item[key] for key in visual.VisualSelection.model_fields}


def test_source_catalog_has_complete_fields_exact_builtin_bindings_and_legacy_contracts(secured):
    client, app, _, folder = secured
    response = client.get("/api/visual-standards")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["revision"] == 3 and value["max_pages"] == 32
    assert {field["key"] for field in value["fields"]} == set(value["defaults"]) == set(VisualSettingsV3.model_fields)
    assert value["legacy"]["1"]["defaults"] == visual.LEGACY_DEFAULTS
    assert value["legacy"]["2"]["defaults"] == visual.DEFAULTS
    assert {field["key"] for field in value["legacy"]["1"]["fields"]} == set(visual.LEGACY_DEFAULTS)
    assert len(value["presets"]) == 3 and value["default_id"] == value["presets"][0]["id"]
    assert value["source"]["reference_sha256"] == digest(load_standard()[1])
    assert value["source"]["provenance"]["caption"]["defaults"]["size_pt"] == 14
    assert all(rule["text"] and all(anchor["path"].startswith("/sec/") and isinstance(anchor["top_level_index"], int)
        for anchor in rule["anchors"]) for rule in value["rules"])
    with transaction(app.state.engine) as db:
        for item in value["presets"]:
            selection = _selection(item)
            assert selection["revision"] == 3
            assert selection["settings_sha256"] == report_hash(selection["settings"])
            assert selection["preview_sha256"] == digest(render_preset(selection["settings"]))
            assert visual.validate_visual_selection(db, "alice", visual.VisualSelection.model_validate(selection), folder=folder) == selection
            forged = {**selection, "name": "변조한 표준"}
            with pytest.raises(HTTPException) as exc:
                visual.validate_visual_selection(db, "alice", visual.VisualSelection.model_validate(forged), folder=folder)
            assert exc.value.status_code == 409
        assert db.scalar(select(VisualStandard)) is None
    assert list(folder.iterdir()) == []


def test_original_readonly_projection_is_authenticated_and_hash_bound_without_download(secured):
    client, _, _, _ = secured
    catalog = client.get("/api/visual-standards").json()
    result = client.get(catalog["reference_preview_url"])
    assert result.status_code == 200
    body = result.json()
    data = base64.b64decode(body["preview_base64"], validate=True)
    assert body["reference_sha256"] == digest(load_standard()[1])
    assert body["preview_sha256"] == digest(data) != body["reference_sha256"]
    assert "content-disposition" not in result.headers and body["max_pages"] == 32
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        section = E.fromstring(source.read("Contents/section0.xml"))
    assert len(list(section.iter(f"{{{HP}}}p"))) == 504
    client.headers.pop("Authorization")
    assert client.get(catalog["reference_preview_url"]).status_code == 401


def test_new_reference_registration_persists_actual_preset_bytes_and_retries_from_frozen_receipt(secured, monkeypatch):
    client, app, _, folder = secured
    body = {"settings": {**defaults("appendix-2"), "cover_title_size_pt": 26},
            "seed_base64": base64.b64encode(seed()).decode()}
    result = client.post("/api/visual-standards/preview", json=body)
    assert result.status_code == 200, result.text
    preview = result.json()
    assert preview["max_pages"] == 32
    registration = {**body, **{key: preview[key] for key in ("settings_sha256", "preview_sha256", "base_standard")},
                    "id": str(uuid.uuid4()), "name": "원문 별첨2 편집 표준"}
    result = client.post("/api/visual-standards", json=registration)
    assert result.status_code == 200, result.text
    snapshot = result.json()
    assert snapshot["revision"] == 3 and snapshot["settings"]["preset_id"] == "appendix-2"
    downloaded = client.get(f"/api/visual-standards/{snapshot['id']}/file")
    assert downloaded.content == base64.b64decode(preview["preview_base64"])
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        package = E.fromstring(archive.read("Contents/content.hpf"))
        assert not any((node.text or "").strip() for node in package.iter() if node.tag.split("}")[-1] == "meta")
    with transaction(app.state.engine) as db:
        row = db.get(VisualStandard, snapshot["id"])
        assert (folder / row.storage_key).read_bytes() == downloaded.content
    monkeypatch.setattr(visual, "render_preview", lambda *_: (_ for _ in ()).throw(AssertionError("rebuilt frozen preview")))
    monkeypatch.setattr(visual, "base_standard", lambda: (_ for _ in ()).throw(AssertionError("recaptured original")))
    assert client.post("/api/visual-standards", json=registration).json() == snapshot
    assert len(list(folder.iterdir())) == 1


@pytest.mark.parametrize("changes", [
    {"table_border_width_mm": True}, {"table_border_width_mm": .16}, {"body_size_pt": "14"},
    {"body_line_percent": 160.5}, {"body_font": "글꼴\n이름"}, {"numbered_1_marker": "1.\x00"}, {"margin_left_mm": 80, "margin_right_mm": 80, "page_width_mm": 100},
])
def test_reference_settings_reject_ambiguous_or_impossible_values(changes):
    with pytest.raises(ValueError):
        VisualSettingsV3.model_validate({**defaults(), **changes})


@pytest.mark.parametrize("style,kinds", [("DASH", []), ("NONE", []), ("PLAIN", ["PAGE"]), ("PAGE_TOTAL", ["PAGE", "TOTAL_PAGE"])])
def test_reference_sample_page_number_fields_are_native_and_positioned(style, kinds):
    output = render_preset(VisualSettingsV3.model_validate({**defaults(), "page_number_style": style, "page_number_position": "BOTTOM_RIGHT"}).model_dump(mode="json"))
    with zipfile.ZipFile(io.BytesIO(output)) as archive:
        header = E.fromstring(archive.read("Contents/header.xml"))
        section = E.fromstring(archive.read("Contents/section0.xml"))
    assert [n.get("numType") for n in section.iter(f"{{{HP}}}autoNum")] == kinds
    page = section.find(f".//{{{HP}}}pageNum")
    assert page.get("pos") == ("BOTTOM_RIGHT" if style == "DASH" else "NONE")
    footer = section.find(f".//{{{HP}}}footer")
    if kinds:
        para = footer.find(f".//{{{HP}}}p")
        format = next(node for node in header.iter(f"{{{HH}}}paraPr") if node.get("id") == para.get("paraPrIDRef"))
        assert format.find(f"{{{HH}}}align").get("horizontal") == "RIGHT"
    else:
        assert footer is None
