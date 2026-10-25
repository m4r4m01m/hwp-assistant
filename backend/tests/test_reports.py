"""API/storage contracts with synthetic report/receipts, not real engine or AI proof."""
import base64
import copy
import io
import json
import time
import uuid
import zipfile
from threading import Event
from xml.etree import ElementTree as ET
import pytest
from app import reports
from app.hwpx import digest
from app.report_contract import ReportDraft
from app.report_hwpx import HP, paragraphs
from test_collaboration import secured, as_user
from test_storage import package

DRAFT = {"title": "금융 보안 보고서", "summary": "합성 시험입니다.", "sections": [
    {"heading": s, "paragraphs": ["검증용 제안 문단입니다."]} for s in ("위험", "대응", "확인")],
    "sources": [], "limitations": ["실모델 결과가 아닙니다."]}


@pytest.fixture
def report_env(secured, monkeypatch):
    monkeypatch.setenv("HWP_CODEX_EXECUTABLE", "synthetic-codex-not-executed")
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    calls = []
    def generate(value, settings, *args):
        calls.append(value)
        return copy.deepcopy(DRAFT), {"completed": True, "executor": "mock"}
    monkeypatch.setattr(reports, "generate_report", generate)
    return secured, calls


def create(c):
    body = {"id": str(uuid.uuid4()), "prompt": "금융 보안 보고서를 작성해주세요.", "research": False}
    response = c.post("/api/report-tasks", json=body)
    assert response.status_code == 200, response.text
    return body


def ready(c, body):
    for _ in range(100):
        job = c.get("/api/report-tasks/" + body["id"]).json()
        if job["status"] != "running":
            return job
        time.sleep(.01)
    raise AssertionError("worker did not finish")


def seed():
    return package("", {"Contents/section0.xml": f'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="{HP}"><hp:p id="0"><hp:run charPrIDRef="0"><hp:t/></hp:run></hp:p></hs:sec>'})


def approve(c, body):
    job = ready(c, body)
    value = {"id": str(uuid.uuid4()), "draft_sha256": job["draft_sha256"], "seed_base64": base64.b64encode(seed()).decode()}
    response = c.post(f"/api/report-tasks/{body['id']}/approve", json=value)
    assert response.status_code == 200, response.text
    return value, response.json()


def synthetic_output(plan):
    raw = base64.b64decode(plan["scaffold_base64"])
    root, _ = paragraphs(raw)
    receipts = []
    for i, item in enumerate(plan["plan"]):
        root[item["paragraph"]].find(f"{{{HP}}}run/{{{HP}}}t").text = item["text"]
        receipts.append({"schemaVersion": 1, "operation": "apply", "commandId": item["command_id"], "documentEpoch": 1,
            "beforeChangeSeq": i, "afterChangeSeq": i + 1,
            "beforeDocumentSha256": digest(str(i).encode()), "afterDocumentSha256": digest(str(i + 1).encode()),
            "beforeTextSha256": digest(item["before"].encode()), "afterTextSha256": digest(item["text"].encode()),
            "formatSha256": "a" * 64, "adjacentContextSha256": "b" * 64, "pageCountBefore": 5, "pageCountAfter": 5,
            "target": {"kind": "body_paragraph", "section": 0, "paragraph": item["paragraph"], "charOffset": 0, "length": len(item["before"])}})
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as z, zipfile.ZipFile(output, "w") as dest:
        for info in z.infolist():
            dest.writestr(info, ET.tostring(root) if info.filename == "Contents/section0.xml" else z.read(info))
    return {"approval_id": plan["id"], "output_base64": base64.b64encode(output.getvalue()).decode(), "receipts": receipts,
            "document_sha256": receipts[-1]["afterDocumentSha256"]}


def test_report_frozen_retry_and_owner_scope(report_env):
    (c, app, _, _), calls = report_env
    body = create(c); job = ready(c, body)
    assert job["status"] == "ready" and job["execution"]["context_used"] is False
    assert c.post("/api/report-tasks", json=body).json()["id"] == body["id"]
    assert len(calls) == 1
    assert c.post("/api/report-tasks", json={**body, "prompt": "다른 보고서 생성 지시입니다."}).status_code == 409
    assert c.get("/api/documents").json() == []  # Draft is not a saved document.
    as_user(c, "bob")
    assert c.get("/api/report-tasks").json() == []
    assert c.get(f"/api/report-tasks/{body['id']}").status_code == 404
    assert c.post("/api/report-tasks", json=body).status_code == 403


def test_cancel_barrier_never_revives_ready(report_env, monkeypatch):
    (c, _, _, _), _ = report_env
    entered, release, finished = Event(), Event(), Event()
    def delayed(*args):
        entered.set(); release.wait(5); finished.set(); return DRAFT, {}
    monkeypatch.setattr(reports, "generate_report", delayed)
    try:
        body = create(c); assert entered.wait(2)
        assert c.post(f"/api/report-tasks/{body['id']}/cancel").json()["status"] == "cancelled"
    finally:
        release.set(); assert finished.wait(2)
    assert ready(c, body)["status"] == "cancelled"


def test_ready_cancel_preserves_published_draft(report_env):
    (c, _, _, _), _ = report_env
    body = create(c); job = ready(c, body)
    assert c.post(f"/api/report-tasks/{body['id']}/cancel").json() == job


def test_approval_and_save_retry_preserve_base_blob(report_env):
    (c, _, _, folder), _ = report_env
    body = create(c); value, plan = approve(c, body)
    url = f"/api/report-tasks/{body['id']}"
    assert c.post(url + "/approve", json=value).json() == plan
    assert c.get(url + "/approved").json() == plan
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    token = c.post(f"/api/documents/{plan['document_id']}/session").json()["token"]
    payload = {**synthetic_output(plan), "token": token}
    result = c.post(url + "/save", json=payload)
    assert result.status_code == 200, result.text
    assert c.post(url + "/save", json=payload).json() == result.json()
    assert len(c.get(f"/api/documents/{plan['document_id']}/versions").json()) == 2
    assert len(list(folder.iterdir())) == 2
    assert all((folder / name).read_bytes() == raw for name, raw in before.items())
    as_user(c, "bob")
    assert c.post(url + "/save", json=payload).status_code == 404


@pytest.mark.parametrize("damage", ["target", "text", "chain", "pages", "output", "missing", "unapproved"])
def test_save_rejects_mismatched_output_without_version(report_env, damage):
    (c, _, _, folder), _ = report_env
    body = create(c); _, plan = approve(c, body)
    token = c.post(f"/api/documents/{plan['document_id']}/session").json()["token"]
    payload = {**synthetic_output(plan), "token": token}
    if damage == "target": payload["receipts"][0]["target"]["paragraph"] = 100
    if damage == "text": payload["receipts"][0]["afterTextSha256"] = "0" * 64
    if damage == "chain": payload["receipts"][1]["beforeDocumentSha256"] = "0" * 64
    if damage == "pages": payload["receipts"][0]["pageCountAfter"] = 6
    if damage == "output": payload["output_base64"] = plan["scaffold_base64"]
    if damage == "missing": payload["receipts"].pop()
    if damage == "unapproved": payload["approval_id"] = str(uuid.uuid4())
    assert c.post(f"/api/report-tasks/{body['id']}/save", json=payload).status_code in (409, 422)
    assert len(c.get(f"/api/documents/{plan['document_id']}/versions").json()) == 1
    assert len(list(folder.iterdir())) == 1


@pytest.mark.parametrize("damage", ["unknown_source", "line_break", "source_auth", "oversize"])
def test_report_contract_rejects_unsafe_or_unbounded_text(damage):
    value = copy.deepcopy(DRAFT)
    if damage == "unknown_source": value["summary"] = "확인되지 않은 주장 [S1]"
    if damage == "line_break": value["sections"][0]["paragraphs"] = ["새로운\n문단"]
    if damage == "source_auth": value["sources"] = [{"id": "S1", "title": "출처", "url": "https://user:password@example.com", "published": ""}]
    if damage == "oversize": value["summary"] = "가" * 701
    with pytest.raises(ValueError): ReportDraft.model_validate(value)
