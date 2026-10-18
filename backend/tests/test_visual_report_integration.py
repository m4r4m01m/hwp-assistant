"""Frozen report/style integration with synthetic AI, receipts and page evidence."""
import base64
import copy
import io
import json
import uuid
import zipfile
from xml.etree import ElementTree as E

import pytest

from app import reports
from app.db import ReportTask, transaction
from app.document_standard import HH, format_signature
from app.hwpx import digest
from app.report_contract import report_hash
from test_collaboration import secured
from test_report_preview import render
from test_report_toc import complete, save_value
from test_reports import report_env, ready, seed, synthetic_output
from test_visual_standards import registration


def registered(client, **settings):
    body, _ = registration(client, **settings)
    result = client.post("/api/visual-standards", json=body)
    assert result.status_code == 200, result.text
    return result.json()


def create_task(client, visual, index=None):
    body = {"id": str(uuid.uuid4()), "prompt": "합성 자료로 업무 보고서를 작성해주세요.",
            "research": False, "visual_standard": visual}
    if index is not None:
        body["template"] = client.get("/api/report-templates").json()["items"][index]
    result = client.post("/api/report-tasks", json=body)
    assert result.status_code == 200, result.text
    task = ready(client, body)
    assert task["status"] == "ready", task
    return body, task


def prepared(client, body, task):
    url = "/api/report-tasks/" + body["id"] + "/preview"
    value = {"id": str(uuid.uuid4()), "draft_sha256": task["draft_sha256"],
             "seed_base64": base64.b64encode(seed()).decode()}
    result = client.post(url, json=value)
    assert result.status_code == 200, result.text
    return url, value, result.json()


@pytest.mark.parametrize("index", [0, 1, 2])
def test_each_writing_profile_passes_frozen_visual_values_to_generation_and_scaffold(report_env, monkeypatch, index):
    (client, _, _, _), calls = report_env
    visual = registered(client, body_size_pt=17, body_line_percent=180, heading_size_pt=19)
    assert calls == []
    body, task = create_task(client, visual, index)
    assert calls == [body]
    assert task["input"] == body
    assert task["execution"]["input_sha256"] == report_hash(body)
    url, request, preview = prepared(client, body, task)
    assert preview["visual_standard"] == visual
    assert preview["document_standard"]["id"] == visual["base_standard"]["id"]
    signatures = format_signature(base64.b64decode(preview["scaffold_base64"]), preview["plan"])["paragraphs"]
    for item, signature in zip(preview["plan"], signatures):
        if item["kind"] == "body":
            assert signature["height"] == "1700" and signature["line"] == "180"
        elif item["kind"] == "heading":
            assert signature["height"] == "1900"
    monkeypatch.setattr(reports, "validate_visual_selection", lambda *_: (_ for _ in ()).throw(AssertionError("retry resolved a mutable selection")))
    assert client.post("/api/report-tasks", json=body).status_code == 200
    assert client.post(url, json=request).json() == preview
    changed = copy.deepcopy(body)
    changed["visual_standard"]["name"] = "다른 표준 이름"
    assert client.post("/api/report-tasks", json=changed).status_code == 409
    assert calls == [body]


def test_format_fence_rejects_custom_style_tampering_before_layout(report_env):
    (client, _, _, _), _ = report_env
    visual = registered(client, body_size_pt=17)
    body, task = create_task(client, visual)
    url, _, preview = prepared(client, body, task)
    payload = synthetic_output(preview)
    payload.pop("approval_id")
    payload["preview_id"] = preview["id"]
    raw = base64.b64decode(payload["output_base64"])
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        header = E.fromstring(source.read("Contents/header.xml"))
        char = next(c for c in header.iter(f"{{{HH}}}charPr") if c.get("height") == "1700")
        char.set("height", "900")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as target:
            for entry in source.infolist():
                target.writestr(entry, E.tostring(header) if entry.filename == "Contents/header.xml" else source.read(entry))
    damaged = {**payload, "output_base64": base64.b64encode(output.getvalue()).decode()}
    response = client.post(url + "/render", json=damaged)
    assert response.status_code == 422, response.text
    assert "layout_base64" not in client.get(url).json()
    assert client.post(url + "/render", json=payload).status_code == 200
    assert client.get("/api/documents").json() == []


def test_new_registration_does_not_change_prepared_rendered_or_saved_report(report_env):
    (client, app, _, _), calls = report_env
    original = registered(client, body_size_pt=17, table_header_fill="#E5F0D8")
    body, task = create_task(client, original, 0)
    url, request, preview = prepared(client, body, task)
    newer = registered(client, body_size_pt=20, table_header_fill="#F5DDD8")
    assert newer["id"] != original["id"]
    assert client.post(url, json=request).json() == preview
    assert client.get(url).json() == preview
    switched = {**body, "visual_standard": newer}
    assert client.post("/api/report-tasks", json=switched).status_code == 409
    payload, rendered = render(client, url, preview)
    assert rendered["visual_standard"] == original
    assert client.post(url + "/render", json=payload).json() == rendered
    _, _, _, toc_result = complete(client, url, rendered)
    final = toc_result["preview"]
    assert final["visual_standard"] == original and final["toc_verified"]
    save = save_value(final)
    response = client.post(url + "/save", json=save)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert client.post(url + "/save", json=save).json() == saved
    file = client.get(f"/api/documents/{saved['id']}/versions/{saved['current_version']}/file").content
    assert digest(file) == digest(base64.b64decode(final["layout_base64"]))
    with transaction(app.state.engine) as db:
        row = db.get(ReportTask, body["id"])
        assert json.loads(row.input)["visual_standard"] == original
        assert json.loads(row.approval)["visual_standard"] == original
    assert len(calls) == 1


def test_legacy_approval_keeps_custom_snapshot_and_frozen_scaffold(report_env, monkeypatch):
    (client, app, _, _), _ = report_env
    visual = registered(client, title_size_pt=25, body_size_pt=16)
    body, task = create_task(client, visual)
    url = "/api/report-tasks/" + body["id"]
    request = {"id": str(uuid.uuid4()), "draft_sha256": task["draft_sha256"],
               "seed_base64": base64.b64encode(seed()).decode()}
    result = client.post(url + "/approve", json=request)
    assert result.status_code == 200, result.text
    approval = result.json()
    assert approval["visual_standard"] == visual
    raw = base64.b64decode(approval["scaffold_base64"])
    assert format_signature(raw, approval["plan"])["paragraphs"][0]["height"] == "2500"
    monkeypatch.setattr(reports, "scaffold", lambda *_args, **_kw: (_ for _ in ()).throw(AssertionError("retry rebuilt a frozen scaffold")))
    assert client.post(url + "/approve", json=request).json() == approval
    token = client.post(f"/api/documents/{approval['document_id']}/session").json()["token"]
    payload = {**synthetic_output(approval), "token": token}
    response = client.post(url + "/save", json=payload)
    assert response.status_code == 200, response.text
    with transaction(app.state.engine) as db:
        assert json.loads(db.get(ReportTask, body["id"]).approval)["visual_standard"] == visual
