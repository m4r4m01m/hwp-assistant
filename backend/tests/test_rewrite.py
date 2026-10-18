"""Fixed candidates/receipts: protection tests, NOT actual Codex or rhwp execution."""
import json
import uuid
import pytest
from sqlalchemy import event
from app.db import RewriteTask, transaction
from app.hwpx import digest
from test_collaboration import secured, document, thread, snapshot, share, as_user, patch
from test_storage import package, save


@pytest.fixture
def job(secured, monkeypatch):
    c, app, _, _ = secured
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    runs = []
    monkeypatch.setattr("app.codex_runner.run_codex", lambda *args: runs.append(args))
    doc = document(c)
    tid = thread(c, doc, "private")
    lease = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    selected = snapshot()
    selected["evidence"]["text"] = "아직 저장하지 않은 문장"
    selected["evidence"]["textSha256"] = digest(selected["evidence"]["text"].encode())
    selected["selection"]["target"] = {"kind": "body_paragraph", "section": 0, "paragraph": 0,
                                         "charOffset": 0, "length": len(selected["evidence"]["text"])}
    body = {"id": str(uuid.uuid4()), "base_version": doc["current_version"], "expected_revision": 0,
            "selection": selected, "instruction": "문체 개선"}
    path = f"/api/threads/{tid}/rewrite-tasks"
    result = c.post(path, json=body, headers={"X-Edit-Session": lease})
    assert result.status_code == 200, result.text
    assert c.post(path, json=body, headers={"X-Edit-Session": lease}).json() == result.json()
    assert len(runs) == 1
    return c, app, doc, tid, lease, body, runs[0][2]


def ready(job):
    c, app, doc, tid, lease, body, token = job
    task = body["id"]
    value = {"original_sha256": body["selection"]["evidence"]["textSha256"],
             "candidates": [{"replacement": "후보 하나", "reason": "FIXTURE"},
                            {"replacement": "후보 둘", "reason": "FIXTURE"}]}
    headers = {"Authorization": "Bearer " + token}
    path = f"/api/rewrite-tasks/{task}/candidates"
    assert c.post(path, json=value).status_code == 403
    assert c.post(path, json={**value, "original_sha256": "f" * 64}, headers=headers).status_code == 409
    r = c.post(path, json=value, headers=headers)
    assert r.status_code == 200, r.text
    assert c.post(path, json=value, headers=headers).json() == r.json()
    with transaction(app.state.engine) as db:
        db.get(RewriteTask, task).status = "ready"  # fixed runner result, never claims model execution
    return r.json()["candidate_ids"][0]


def approve(job, candidate):
    c, _, _, _, lease, body, _ = job
    value = {"id": str(uuid.uuid4()), "candidate_id": candidate}
    path = f"/api/rewrite-tasks/{body['id']}/approve"
    r = c.post(path, json=value, headers={"X-Edit-Session": lease})
    assert r.status_code == 200, r.text
    assert c.post(path, json=value, headers={"X-Edit-Session": lease}).json() == r.json()
    return value["id"]


def output_and_receipt(job, application):
    selected = job[5]["selection"]
    data = package(extra={"Contents/section0.xml": "<section><p><run><t>후보 하나</t></run></p></section>"})
    receipt = {"commandId": application, "operation": "apply", "documentEpoch": selected["state"]["documentEpoch"],
               "beforeChangeSeq": selected["state"]["changeSeq"], "beforeDocumentSha256": selected["state"]["documentSha256"],
               "beforeTextSha256": selected["evidence"]["textSha256"], "afterTextSha256": digest("후보 하나".encode()),
               "formatSha256": selected["evidence"]["formatSha256"], "adjacentContextSha256": selected["evidence"]["adjacentContextSha256"],
               "target": selected["selection"]["target"]}
    return data, {"engine_receipt": receipt, "export_sha256": digest(data)}


def test_frozen_unsaved_input_and_scoped_executor(job):
    c, _, doc, tid, _, body, token = job
    headers = {"Authorization": "Bearer " + token}
    r = c.get(f"/api/rewrite-tasks/{body['id']}/input", headers=headers)
    assert r.status_code == 200 and r.json()["snapshot"] == body["selection"]
    for path in ["/api/documents", f"/api/threads/{tid}", f"/api/rewrite-tasks/{uuid.uuid4()}/input"]:
        assert c.get(path, headers=headers).status_code == 403
    candidate = ready(job)
    assert candidate
    assert len(c.get(f"/api/documents/{doc['id']}/versions").json()) == 1
    assert c.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content == package()
    assert c.get(f"/api/rewrite-tasks/{body['id']}/input", headers=headers).status_code == 401


def test_malformed_target_and_other_users_codex_execution_are_rejected(job):
    c, _, doc, tid, lease, body, _ = job
    invalid = json.loads(json.dumps(body))
    invalid["id"] = str(uuid.uuid4())
    invalid["selection"]["selection"]["target"]["kind"] = "table_cell"
    assert c.post(f"/api/threads/{tid}/rewrite-tasks", json=invalid, headers={"X-Edit-Session": lease}).status_code == 422
    share(c, doc, "editor")
    as_user(c, "bob")
    assert c.post(f"/api/threads/{tid}/rewrite-tasks", json={**body, "id": str(uuid.uuid4())}, headers={"X-Edit-Session": lease}).status_code == 403


def test_other_document_reader_sees_candidates_history_but_cannot_apply(job):
    c, _, doc, tid, lease, body, _ = job
    candidate = ready(job)
    application = approve(job, candidate)
    share(c, doc, "reader")
    as_user(c, "bob")
    assert len(c.get(f"/api/threads/{tid}/rewrite-tasks").json()[0]["candidates"]) == 2
    assert c.get(f"/api/documents/{doc['id']}/applications").json()[0]["id"] == application
    assert c.get(f"/api/rewrite-tasks/{body['id']}/input").status_code == 200
    assert c.post(f"/api/rewrite-tasks/{body['id']}/approve", json={"id": application, "candidate_id": candidate}, headers={"X-Edit-Session": lease}).status_code == 403
    as_user(c, "eve")
    for path in [f"/api/threads/{tid}/rewrite-tasks", f"/api/documents/{doc['id']}/applications", f"/api/rewrite-tasks/{body['id']}/input"]:
        assert c.get(path).status_code == 404


def test_stale_base_rejects_approval_and_preserves_candidates(job):
    c, _, doc, tid, lease, body, _ = job
    candidate = ready(job)
    assert save(c, doc, lease).status_code == 200
    assert c.post(f"/api/rewrite-tasks/{body['id']}/approve", json={"id": str(uuid.uuid4()), "candidate_id": candidate}, headers={"X-Edit-Session": lease}).status_code == 409
    task = c.get(f"/api/threads/{tid}/rewrite-tasks").json()[0]
    assert task["stale"] and len(task["candidates"]) == 2 and task["snapshot"] == body["selection"]


def test_receipt_save_atomic_commit_idempotency_and_original_preservation(job):
    c, app, doc, tid, lease, body, _ = job
    application = approve(job, ready(job))
    data, receipt = output_and_receipt(job, application)
    rpath = f"/api/applications/{application}/receipt"
    headers = {"X-Edit-Session": lease}
    bad = {**receipt, "engine_receipt": {**receipt["engine_receipt"], "beforeTextSha256": "f" * 64}}
    assert c.post(rpath, json=bad, headers=headers).status_code == 409
    assert c.post(rpath, json=receipt, headers=headers).status_code == 200
    assert c.post(rpath, json=receipt, headers=headers).status_code == 200
    path = f"/api/documents/{doc['id']}/versions?base_version={doc['current_version']}&cause=web-edit"
    headers.update({"Idempotency-Key": application, "X-Application-Id": application, "Content-Type": "application/vnd.hwp.delta+json"})
    assert c.post(path, json=patch(package(), package("wrong")), headers=headers).status_code == 409
    def fail(_):
        raise RuntimeError("injected commit failure")
    event.listen(app.state.engine, "commit", fail)
    assert c.post(path, json=patch(package(), data), headers=headers).status_code == 500
    event.remove(app.state.engine, "commit", fail)
    assert c.get(f"/api/documents/{doc['id']}").json()["current_version"] == doc["current_version"]
    assert c.get(f"/api/documents/{doc['id']}/applications").json()[0]["saved_version"] is None
    saved = c.post(path, json=patch(package(), data), headers=headers)
    assert saved.status_code == 200, saved.text
    assert c.post(path, json=patch(package(), data), headers=headers).json() == saved.json()
    assert len(c.get(f"/api/documents/{doc['id']}/versions").json()) == 2
    history = c.get(f"/api/documents/{doc['id']}/applications").json()[0]
    assert history["saved_version"] == saved.json()["id"] and history["status"] == "saved"
    assert c.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content == package()


def test_stale_save_after_approval_preserves_latest_and_application(job):
    c, _, doc, _, lease, _, _ = job
    application = approve(job, ready(job))
    data, receipt = output_and_receipt(job, application)
    assert c.post(f"/api/applications/{application}/receipt", json=receipt, headers={"X-Edit-Session": lease}).status_code == 200
    latest = save(c, doc, lease).json()["id"]
    path = f"/api/documents/{doc['id']}/versions?base_version={doc['current_version']}&cause=web-edit"
    r = c.post(path, content=data, headers={"X-Edit-Session": lease, "Idempotency-Key": application, "X-Application-Id": application})
    assert r.status_code == 409
    assert c.get(f"/api/documents/{doc['id']}").json()["current_version"] == latest
    assert c.get(f"/api/documents/{doc['id']}/applications").json()[0]["saved_version"] is None


def test_cancel_codex_submission_preserves_candidates_and_invalidates_run_token(job):
    c, app, _, tid, _, body, token = job
    task_id = body["id"]
    payload = {"original_sha256": body["selection"]["evidence"]["textSha256"],
               "candidates": [{"replacement": "보관 후보 하나", "reason": "FIXTURE"},
                              {"replacement": "보관 후보 둘", "reason": "FIXTURE"}]}
    headers = {"Authorization": "Bearer " + token}
    assert c.post(f"/api/rewrite-tasks/{task_id}/candidates", json=payload, headers=headers).status_code == 200
    before = c.get(f"/api/threads/{tid}/rewrite-tasks").json()[0]
    assert c.post(f"/api/rewrite-tasks/{task_id}/cancel").status_code == 200
    after = c.get(f"/api/threads/{tid}/rewrite-tasks").json()[0]
    assert after["status"] == "cancelled" and after["candidates"] == before["candidates"]
    assert c.get(f"/api/rewrite-tasks/{task_id}/input", headers=headers).status_code == 401
