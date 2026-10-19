"""New artifact no-fill and outline boundaries; canonical/frozen data stay exact."""
import base64
import copy
import io
import re
import uuid
import zipfile
from xml.etree import ElementTree as E

import pytest

from app import document_standard as standard, visual_standards as visual
from app.hwpx import digest, validate
from app.report_contract import report_blocks
from app.report_hwpx import scaffold
from app.report_outline import split_bullet
from test_collaboration import secured
from test_reports import DRAFT, seed, report_env
from test_report_preview import prepare


def assert_no_unsupported_fill(data):
    _, roots = validate(data)
    assert not [(name, element.tag, key) for name, root in roots.items() for element in root.iter()
                for key in ("faceColor", "shadeColor") if element.get(key, "").casefold() == "none"]


def test_reference_view_is_a_separate_color_only_copy_and_original_stays_exact(secured):
    client, _, _, _ = secured
    before = {path.name: path.read_bytes() for path in standard.ASSETS.iterdir() if path.is_file()}
    metadata = client.get("/api/document-standard").json()
    original = client.get(metadata["reference_url"])
    viewed = client.get(metadata["reference_view_url"])
    assert original.content == before["reference.hwpx"]
    assert digest(original.content) == metadata["reference_sha256"]
    assert viewed.status_code == 200 and viewed.content != original.content
    assert viewed.headers["x-hwp-original-sha256"] == digest(original.content)
    assert viewed.headers["etag"] == '"' + digest(viewed.content) + '"'
    assert "document-writing-standard-view.hwpx" in viewed.headers["content-disposition"]
    assert client.get(metadata["reference_view_url"]).content == viewed.content
    assert_no_unsupported_fill(viewed.content)
    with zipfile.ZipFile(io.BytesIO(original.content)) as source, zipfile.ZipFile(io.BytesIO(viewed.content)) as target:
        assert source.namelist() == target.namelist()
        for name in source.namelist():
            old, new = source.read(name), target.read(name)
            if old == new:
                continue
            assert name.endswith((".xml", ".hpf"))
            expected, actual = E.fromstring(old), E.fromstring(new)
            standard.normalize_no_fill(expected)
            # XML prefixes/declarations are lexical; document text, geometry,
            # all RGB colors, other attributes and every binary resource remain.
            assert [(node.tag, node.attrib, node.text, node.tail) for node in expected.iter()] == [
                (node.tag, node.attrib, node.text, node.tail) for node in actual.iter()]
    assert {path.name: path.read_bytes() for path in standard.ASSETS.iterdir() if path.is_file()} == before


def test_no_fill_normalization_keeps_real_colors_and_non_color_none_values():
    root = E.fromstring('<root><brush faceColor="none" shadeColor="NONE" type="none"/>'
                        '<black faceColor="#000000" shadeColor="#FFFFFF"/>'
                        '<color faceColor="#123456" shadeColor="#FFFFFFFF"/></root>')
    assert standard.normalize_no_fill(root) == 2
    assert root[0].attrib == {"faceColor": "#FFFFFFFF", "shadeColor": "#FFFFFFFF", "type": "none"}
    assert root[1].attrib == {"faceColor": "#000000", "shadeColor": "#FFFFFF"}
    assert root[2].attrib == {"faceColor": "#123456", "shadeColor": "#FFFFFFFF"}
    assert standard.normalize_no_fill(root) == 0


@pytest.mark.parametrize("settings", [None, visual.LEGACY_DEFAULTS, visual.DEFAULTS])
def test_new_scaffolds_and_visual_direct_outputs_use_native_no_fill(settings):
    data, _ = scaffold(seed(), report_blocks(copy.deepcopy(DRAFT)), visual_settings=settings)
    assert_no_unsupported_fill(data)
    if settings is not None:
        assert_no_unsupported_fill(visual.render_preview(seed(), settings))


@pytest.mark.parametrize("settings", [visual.LEGACY_DEFAULTS, visual.DEFAULTS])
def test_existing_visual_registration_keeps_old_blob_hash_and_retry_after_fix(secured, monkeypatch, settings):
    client, _, _, folder = secured
    def old_no_fill(root):
        for element in root.iter():
            if element.get("shadeColor") == "none":
                element.set("shadeColor", "#FFFFFFFF")
    with monkeypatch.context() as old_code:
        old_code.setattr(standard, "normalize_no_fill", old_no_fill)
        preview_request = {"settings": settings, "seed_base64": base64.b64encode(seed()).decode()}
        preview = client.post("/api/visual-standards/preview", json=preview_request)
        assert preview.status_code == 200, preview.text
        receipt = preview.json()
        request = {**preview_request, **{key: receipt[key] for key in (
            "settings_sha256", "preview_sha256", "base_standard")}, "id": str(uuid.uuid4()), "name": "이전 저장 표준"}
        registered = client.post("/api/visual-standards", json=request)
        assert registered.status_code == 200, registered.text
    frozen = registered.json()
    files = {path.name: path.read_bytes() for path in folder.iterdir()}
    old_file = client.get(f"/api/visual-standards/{frozen['id']}/file").content
    assert digest(old_file) == frozen["preview_sha256"]
    with zipfile.ZipFile(io.BytesIO(old_file)) as source:
        assert b'faceColor="none"' in source.read("Contents/header.xml")
    monkeypatch.setattr(visual, "render_preview", lambda *_: (_ for _ in ()).throw(AssertionError("Rebuilt frozen standard")))
    assert client.post("/api/visual-standards", json=request).json() == frozen
    assert client.get(f"/api/visual-standards/{frozen['id']}/file").content == old_file
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == files


def test_new_registered_file_contains_the_compatible_preview_bytes(secured):
    client, _, _, _ = secured
    preview_request = {"settings": visual.DEFAULTS, "seed_base64": base64.b64encode(seed()).decode()}
    preview = client.post("/api/visual-standards/preview", json=preview_request)
    assert preview.status_code == 200, preview.text
    receipt = preview.json()
    request = {**preview_request, **{key: receipt[key] for key in (
        "settings_sha256", "preview_sha256", "base_standard")}, "id": str(uuid.uuid4()), "name": "호환 표준"}
    result = client.post("/api/visual-standards", json=request)
    assert result.status_code == 200, result.text
    registered = result.json()
    downloaded = client.get(f"/api/visual-standards/{registered['id']}/file").content
    assert downloaded == base64.b64decode(receipt["preview_base64"])
    assert digest(downloaded) == registered["preview_sha256"]
    assert_no_unsupported_fill(downloaded)


def test_pre_fix_frozen_report_plan_and_scaffold_are_reused_without_reformatting(report_env, monkeypatch):
    client, _, _, _ = report_env[0]
    def old_no_fill(root):
        for element in root.iter():
            if element.get("shadeColor") == "none":
                element.set("shadeColor", "#FFFFFFFF")
    with monkeypatch.context() as old_code:
        old_code.setattr(standard, "normalize_no_fill", old_no_fill)
        url, request, frozen = prepare(client)
    raw = base64.b64decode(frozen["scaffold_base64"])
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        assert b'faceColor="none"' in source.read("Contents/header.xml")
    monkeypatch.setattr("app.report_preview.report_blocks", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Rebuilt frozen plan")))
    monkeypatch.setattr(standard, "report_styles", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Reformatted frozen artifact")))
    assert client.get(url).json() == frozen
    assert client.post(url, json=request).json() == frozen
    assert base64.b64decode(client.get(url).json()["scaffold_base64"]) == raw


@pytest.mark.parametrize("ending", ["필요함", "아님", "있음", "없음", "가능함", "예정임"])
@pytest.mark.parametrize("citation", ["[S1]", "주1)", "*1"])
def test_new_outline_keeps_predicates_with_context_and_source_markers(ending, citation):
    for count in range(1, 9):
        text = "□ (확인) " + "자료 범위와 담당자의 승인 조건을 확인하고 " * count + f"검토가 {ending} {citation}"
        parts = split_bullet(text)
        assert "".join(part[2:] for part in parts) == text[2:]
        assert all(not re.match(rf"^□\s*{ending}(?:\s|[.!?。]|$)", part) for part in parts)
        assert any(f"검토가 {ending} {citation}" in part for part in parts)


def test_outline_endings_and_selected_emphasis_both_retain_full_text():
    phrase = "담당자의 승인 조건을 확인"
    for count in range(4, 12):
        text = "□ " + "자료를 확인하고 " * count + phrase + " 후 검토가 필요함 [S1]"
        parts = split_bullet(text, [phrase])
        assert "".join(part[2:] for part in parts) == text[2:]
        assert any(phrase in part for part in parts)
        assert any("검토가 필요함 [S1]" in part for part in parts)
