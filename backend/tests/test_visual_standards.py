"""Immutable visual-standard API/storage contracts; no real AI or engine proof."""
import base64
import io
import json
import uuid
import zipfile
from xml.etree import ElementTree as ET

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import visual_standards as visual
from app import reports
from app.db import Document, ReportTask, VisualStandard, transaction
from app.hwpx import digest
from app.report_contract import report_hash
from app.report_hwpx import HP
from test_collaboration import as_user, secured
from test_reports import ready, report_env, seed


def preview(client, **settings):
    values = {**visual.DEFAULTS, **settings}
    body = {"settings": values, "seed_base64": base64.b64encode(seed()).decode()}
    response = client.post("/api/visual-standards/preview", json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


def registration(client, **settings):
    body, rendered = preview(client, **settings)
    return {**body, **{key: rendered[key] for key in ("settings_sha256", "preview_sha256", "base_standard")},
            "id": str(uuid.uuid4()), "name": "합성 표준"}, rendered


def test_catalog_schema_and_synthetic_preview_do_not_publish_or_call_ai(secured, monkeypatch):
    client, app, _, _ = secured
    monkeypatch.setattr("app.reports.generate_report", lambda *_: (_ for _ in ()).throw(AssertionError("AI called")))
    before = {path: digest(path.read_bytes()) for path in visual.metadata.__globals__["ASSETS"].glob("*") if path.is_file()}
    catalog = client.get("/api/visual-standards").json()
    assert catalog["items"] == [] and catalog["defaults"] == visual.DEFAULTS
    assert {item["key"] for item in catalog["fields"]} == set(visual.DEFAULTS)
    body, first = preview(client)
    second = client.post("/api/visual-standards/preview", json=body).json()
    assert first == second
    assert first["settings_sha256"] == report_hash(body["settings"])
    data = base64.b64decode(first["preview_base64"])
    assert digest(data) == first["preview_sha256"]
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        section = source.read("Contents/section0.xml").decode()
    combined = "".join(t.text or "" for t in ET.fromstring(section).iter(f"{{{HP}}}t"))
    assert "문서작성 표준 예시" in combined
    assert "실제 업무자료가 아닙니다" in combined
    assert "tbl" in section
    with transaction(app.state.engine) as db:
        assert db.scalar(select(Document)) is None
        assert db.scalar(select(ReportTask)) is None
        assert db.scalar(select(VisualStandard)) is None
    assert {path: digest(path.read_bytes()) for path in before} == before


@pytest.mark.parametrize("settings", [
    {"body_size_pt": True}, {"body_size_pt": "15"}, {"body_size_pt": 40},
    {"body_line_percent": 150.5}, {"table_size_pt": 21}, {"margin_left_mm": 1},
    {"table_header_fill": "red"}, {"table_border_color": "#00000000"},
    {"square_indent_pt": 11, "circle_indent_pt": 10},
    {"unknown_setting": 1},
])
def test_invalid_settings_are_rejected_without_storage(secured, settings):
    client, app, _, folder = secured
    response = client.post("/api/visual-standards/preview", json={
        "settings": {**visual.DEFAULTS, **settings}, "seed_base64": base64.b64encode(seed()).decode()})
    assert response.status_code == 422, response.text
    assert list(folder.iterdir()) == []
    with transaction(app.state.engine) as db:
        assert db.scalar(select(VisualStandard)) is None


@pytest.mark.parametrize("value", [float("inf"), float("nan"), float("-inf")])
def test_nonfinite_settings_fail_validation(value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        visual.VisualSettings.model_validate({**visual.DEFAULTS, "body_size_pt": value})


def test_selection_revision_does_not_coerce_boolean():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        visual.VisualSelection.model_validate({"id": str(uuid.uuid4()), "revision": True, "name": "합성 표준",
            "settings": visual.DEFAULTS, "settings_sha256": report_hash(visual.DEFAULTS),
            "base_standard": visual.base_standard(), "preview_sha256": "a" * 64})


def test_empty_seed_required_and_layout_failure_is_actionable(secured, monkeypatch):
    client, _, _, _ = secured
    body = {"settings": visual.DEFAULTS, "seed_base64": base64.b64encode(b"not a zip").decode()}
    assert client.post("/api/visual-standards/preview", json=body).status_code == 422
    body["seed_base64"] = "!"
    assert client.post("/api/visual-standards/preview", json=body).status_code == 422
    body["seed_base64"] = base64.b64encode(seed()).decode()
    monkeypatch.setattr(visual, "layout_rich_report", lambda *_args, **_kw: (_ for _ in ()).throw(ValueError("page overflow")))
    result = client.post("/api/visual-standards/preview", json=body)
    assert result.status_code == 422 and "수치를 조정" in result.json()["detail"]


def test_registration_persists_immutable_file_and_owner_scoped_retry(secured, monkeypatch):
    client, app, _, folder = secured
    body, rendered = registration(client, body_size_pt=16, table_header_fill="#aabbcc")
    stored = client.post("/api/visual-standards", json=body)
    assert stored.status_code == 200, stored.text
    snapshot = stored.json()
    assert snapshot["settings"]["table_header_fill"] == "#AABBCC"
    assert snapshot["revision"] == 2 and "seed_base64" not in snapshot
    assert client.get("/api/visual-standards").json()["items"] == [snapshot]
    assert client.get("/api/visual-standards/" + body["id"]).json() == snapshot
    result = client.get("/api/visual-standards/" + body["id"] + "/file")
    assert result.content == base64.b64decode(rendered["preview_base64"])
    assert result.headers["etag"] == '"' + body["preview_sha256"] + '"'
    with transaction(app.state.engine) as db:
        row = db.get(VisualStandard, body["id"])
        original = (folder / row.storage_key).read_bytes()
        assert original == result.content
        assert row.size == len(original)
        assert visual.validate_visual_selection(db, "alice", visual.VisualSelection.model_validate(snapshot), folder=folder) == snapshot
    # A completed registration is acknowledged from its frozen receipt even if
    # the current base assets later become unavailable. No preview is rebuilt.
    monkeypatch.setattr(visual, "base_standard", lambda: (_ for _ in ()).throw(HTTPException(503, "unavailable")))
    assert client.post("/api/visual-standards", json=body).json() == snapshot
    assert len(list(folder.iterdir())) == 1
    changed = {**body, "name": "다른 이름"}
    assert client.post("/api/visual-standards", json=changed).status_code == 409
    as_user(client, "bob")
    assert client.get("/api/visual-standards").json()["items"] == []
    assert client.get("/api/visual-standards/" + body["id"]).status_code == 404
    assert client.get("/api/visual-standards/" + body["id"] + "/file").status_code == 404
    assert client.post("/api/visual-standards", json=body).status_code == 404
    with transaction(app.state.engine) as db:
        with pytest.raises(HTTPException) as exc:
            visual.validate_visual_selection(db, "bob", visual.VisualSelection.model_validate(snapshot))
        assert exc.value.status_code == 404


@pytest.mark.parametrize("damage", ["settings_sha256", "preview_sha256", "base_standard"])
def test_registration_cannot_accept_stale_or_forged_preview(secured, damage):
    client, app, _, folder = secured
    body, _ = registration(client)
    if damage == "base_standard":
        body[damage]["profile_sha256"] = "0" * 64
    else:
        body[damage] = "0" * 64
    assert client.post("/api/visual-standards", json=body).status_code == 409
    assert list(folder.iterdir()) == []
    with transaction(app.state.engine) as db:
        assert db.scalar(select(VisualStandard)) is None


def test_missing_or_corrupt_registered_blob_fails_closed(secured):
    client, app, _, folder = secured
    body, _ = registration(client)
    assert client.post("/api/visual-standards", json=body).status_code == 200
    with transaction(app.state.engine) as db:
        path = folder / db.get(VisualStandard, body["id"]).storage_key
    original = path.read_bytes()
    path.write_bytes(b"damaged")
    assert client.get("/api/visual-standards/" + body["id"] + "/file").status_code == 422
    assert client.post("/api/visual-standards", json=body).status_code == 422
    with transaction(app.state.engine) as db:
        with pytest.raises(HTTPException) as exc:
            visual.validate_visual_selection(db, "alice", visual.VisualSelection.model_validate(
                client.get("/api/visual-standards").json()["items"][0]), folder=folder)
        assert exc.value.status_code == 422
    path.unlink()
    assert client.get("/api/visual-standards/" + body["id"]).status_code == 503
    assert client.post("/api/visual-standards", json=body).status_code == 503
    path.write_bytes(original)
    assert client.post("/api/visual-standards", json=body).status_code == 200


def test_fsync_failure_does_not_commit_registration(secured, monkeypatch):
    client, app, _, _ = secured
    body, _ = registration(client)
    monkeypatch.setattr(visual.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("disk failure")))
    result = client.post("/api/visual-standards", json=body)
    assert result.status_code == 503
    with transaction(app.state.engine) as db:
        assert db.get(VisualStandard, body["id"]) is None


def test_selection_rejects_modified_snapshot_and_changed_base(secured, monkeypatch):
    client, app, _, _ = secured
    body, _ = registration(client)
    snapshot = client.post("/api/visual-standards", json=body).json()
    changed = {**snapshot, "name": "변조한 이름"}
    with transaction(app.state.engine) as db:
        with pytest.raises(HTTPException) as exc:
            visual.validate_visual_selection(db, "alice", visual.VisualSelection.model_validate(changed))
        assert exc.value.status_code == 409
        monkeypatch.setattr(visual, "base_standard", lambda: {**snapshot["base_standard"], "profile_sha256": "0" * 64})
        with pytest.raises(HTTPException) as exc:
            visual.validate_visual_selection(db, "alice", visual.VisualSelection.model_validate(snapshot))
        assert exc.value.status_code == 409


def test_local_owner_migration_requires_explicit_owner_and_keeps_snapshot(secured, monkeypatch):
    from app.schema import migrate
    client, app, _, _ = secured
    body, _ = registration(client)
    snapshot = client.post("/api/visual-standards", json=body).json()
    with transaction(app.state.engine) as db:
        db.get(VisualStandard, body["id"]).owner_id = "local"
    monkeypatch.delenv("HWP_LEGACY_OWNER", raising=False)
    with pytest.raises(ValueError, match="HWP_LEGACY_OWNER"):
        migrate(app.state.engine, {"alice": "configured"})
    with transaction(app.state.engine) as db:
        row = db.get(VisualStandard, body["id"])
        assert row.owner_id == "local" and json.loads(row.snapshot) == snapshot
    monkeypatch.setenv("HWP_LEGACY_OWNER", "alice")
    migrate(app.state.engine, {"alice": "configured"})
    with transaction(app.state.engine) as db:
        row = db.get(VisualStandard, body["id"])
        assert row.owner_id == "alice" and json.loads(row.snapshot) == snapshot


def test_new_report_uses_frozen_selection_and_retries_without_registry_resolution(report_env, monkeypatch):
    (client, app, _, _), calls = report_env
    registration_body, _ = registration(client, body_size_pt=16.5, body_line_percent=180)
    snapshot = client.post("/api/visual-standards", json=registration_body).json()
    task = {"id": str(uuid.uuid4()), "prompt": "합성 업무 자료로 보고서를 작성해주세요.", "research": False,
            "template": client.get("/api/report-templates").json()["items"][0], "visual_standard": snapshot}
    result = client.post("/api/report-tasks", json=task)
    assert result.status_code == 200, result.text
    job = ready(client, task)
    assert job["input"] == task and calls == [task]
    assert job["execution"]["input_sha256"] == report_hash(task)
    prepare = {"id": str(uuid.uuid4()), "draft_sha256": job["draft_sha256"],
               "seed_base64": base64.b64encode(seed()).decode()}
    preview_result = client.post(f"/api/report-tasks/{task['id']}/preview", json=prepare)
    assert preview_result.status_code == 200, preview_result.text
    frozen_preview = preview_result.json()
    assert frozen_preview["visual_standard"] == snapshot
    assert frozen_preview["document_standard"]["id"] == snapshot["base_standard"]["id"]
    monkeypatch.setattr(reports, "validate_visual_selection", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("resolved frozen registry")))
    monkeypatch.setattr(reports, "capture_settings", lambda: (_ for _ in ()).throw(AssertionError("recaptured executor")))
    assert client.post("/api/report-tasks", json=task).json()["input"] == task
    modified = {**task, "visual_standard": {**snapshot, "name": "다른 표준"}}
    assert client.post("/api/report-tasks", json=modified).status_code == 409
    assert client.post(f"/api/report-tasks/{task['id']}/preview", json=prepare).json() == frozen_preview
    assert calls == [task]


@pytest.mark.parametrize("damage,expected", [("missing", 503), ("corrupt", 422), ("owner", 404)])
def test_invalid_registered_selection_blocks_ai_start(report_env, damage, expected):
    (client, app, _, folder), calls = report_env
    registration_body, _ = registration(client)
    snapshot = client.post("/api/visual-standards", json=registration_body).json()
    with transaction(app.state.engine) as db:
        row = db.get(VisualStandard, snapshot["id"])
        path = folder / row.storage_key
        if damage == "missing":
            path.unlink()
        elif damage == "corrupt":
            path.write_bytes(b"not a valid template")
        else:
            row.owner_id = "bob"
    task = {"id": str(uuid.uuid4()), "prompt": "합성 업무 보고서를 작성해주세요.", "research": False,
            "template": client.get("/api/report-templates").json()["items"][0], "visual_standard": snapshot}
    result = client.post("/api/report-tasks", json=task)
    assert result.status_code == expected, result.text
    assert calls == []
    with transaction(app.state.engine) as db:
        assert db.get(ReportTask, task["id"]) is None
