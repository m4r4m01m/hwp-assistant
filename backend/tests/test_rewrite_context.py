"""Writing-context persistence and frozen inputs; synthetic providers, no real models."""
import copy
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.rewrite_context import input_hash, input_json
from test_collaboration import secured, document, thread, share, as_user, TOKENS
from test_local_rewrite import local_jobs, submit, execute


def context_body(job, revision=0, **updates):
    return {"expected_revision": revision, "base_version": job[2]["current_version"],
            "purpose": "팀장에게 집행 현황 보고", "facts": "이 메모는 모델 입력에 포함하지 않는다",
            "writing_criteria": "간결한 존댓말, 부정 표현 유지",
            "confirmed_facts": [{"id": str(uuid.uuid4()), "text": "지출 승인은 하지 않았다.",
                                 "source": "user_confirmed_ai_excerpt", "source_note": "AI 초안을 사용자가 확인"}],
            "confirm_for_rewrite": True, **updates}


def put_context(job, value=None):
    return job[0].put(f"/api/threads/{job[3]}/context", json=value or context_body(job))


def with_context(job, revision=1):
    return {**job[5], "context_revision": revision}


def test_confirmed_context_is_frozen_and_exact_provider_input_is_reconstructable(local_jobs):
    job = local_jobs; c, app, doc, tid, _, _, queued, calls = job
    assert put_context(job).status_code == 200
    request = with_context(job)
    assert submit(job, request).status_code == 200
    before = c.get(f"/api/rewrite-tasks/{request['id']}/input").json()
    context = before["generation_input"]["writing_context"]
    assert context["revision"] == 1 and context["id"] == tid and context["document_id"] == doc["id"]
    assert context["confirmation"]["by"] == "alice"
    assert context["confirmed_facts"][0]["source"] == "user_confirmed_ai_excerpt"
    assert "facts" not in context and "selection" not in context
    assert "이 메모" not in json.dumps(before, ensure_ascii=False)
    assert put_context(job, context_body(job, 1, purpose="나중의 목적")).status_code == 200
    result = execute(job)
    assert result["status"] == "ready" and result["stale"] and result["context_stale"]
    actual = calls[0]["messages"][1]["content"]
    assert actual == input_json(before["generation_input"])
    assert result["execution"]["input_sha256"] == input_hash(json.loads(actual)) == before["input_sha256"]
    assert result["execution"]["context_used"] is True
    assert result["execution"]["context_revision"] == 1
    assert "_context_snapshot" not in result["execution"]
    assert c.get(f"/api/rewrite-tasks/{request['id']}/input").json()["generation_input"] == before["generation_input"]
    # Same ID/input reconfirms the old request despite the newer context; never regenerates.
    assert submit(job, request).status_code == 200 and len(queued) == 1 and len(calls) == 1
    assert submit(job, {**request, "context_revision": 2}).status_code == 409
    approval = c.post(f"/api/rewrite-tasks/{request['id']}/approve", json={"id": str(uuid.uuid4()), "candidate_id": result["candidates"][0]["id"]}, headers={"X-Edit-Session": job[4]})
    assert approval.status_code == 409 and "작성 정보" in approval.text
    assert len(c.get(f"/api/documents/{doc['id']}/versions").json()) == 1
    assert c.get(f"/api/documents/{doc['id']}/applications").json() == []


def test_new_session_reconstructs_identical_context_without_execution(local_jobs, secured):
    job = local_jobs; assert put_context(job).status_code == 200
    assert submit(job, with_context(job)).status_code == 200
    path = f"/api/rewrite-tasks/{job[5]['id']}/input"
    before = job[0].get(path).json()
    with TestClient(create_app(secured[2], secured[3]), headers={"Authorization": "Bearer " + TOKENS["alice"]}) as other:
        assert other.get(path).json() == before
        ctx = other.get(f"/api/threads/{job[3]}").json()["context"]
        assert ctx["confirmation"] == before["generation_input"]["writing_context"]["confirmation"]
    assert not job[7] and len(job[6]) == 1


@pytest.mark.parametrize("case", ["missing", "unconfirmed", "legacy", "revision", "base"])
def test_context_requires_current_explicit_confirmation(local_jobs, case):
    job = local_jobs; body = context_body(job)
    if case == "unconfirmed": body["confirm_for_rewrite"] = False
    if case == "legacy": body = {k: body[k] for k in ("expected_revision", "base_version", "purpose", "facts")}
    if case != "missing": assert put_context(job, body).status_code == 200
    if case == "base":
        from test_storage import save
        assert save(job[0], job[2], job[4]).status_code == 200
        job[5]["base_version"] = job[0].get(f"/api/documents/{job[2]['id']}").json()["current_version"]
    assert submit(job, with_context(job, 2 if case == "revision" else 1)).status_code == 409
    assert not job[6] and not job[7]


@pytest.mark.parametrize("case", ["forged_confirmation", "unknown_source", "foreign_version", "duplicate_id", "blank_fact", "empty_context", "surrogate"])
def test_context_rejects_invalid_facts_or_forged_confirmation(local_jobs, case):
    job = local_jobs; body = context_body(job)
    if case == "forged_confirmation": body["confirmation"] = {"by": "bob"}
    if case == "unknown_source": body["confirmed_facts"][0]["source"] = "verified_by_ai"
    if case == "foreign_version":
        foreign = document(job[0])
        body["confirmed_facts"][0].update(source="document_version", version_id=foreign["current_version"])
    if case == "duplicate_id": body["confirmed_facts"].append(copy.deepcopy(body["confirmed_facts"][0]))
    if case == "blank_fact": body["confirmed_facts"][0]["text"] = "  "
    if case == "empty_context": body.update(purpose="", writing_criteria="", confirmed_facts=[])
    if case == "surrogate":
        body["purpose"] = "\ud800"
        response = job[0].put(f"/api/threads/{job[3]}/context", content=json.dumps(body), headers={"Content-Type": "application/json"})
    else: response = put_context(job, body)
    assert response.status_code == 422, response.text
    assert job[0].get(f"/api/threads/{job[3]}").json()["context"]["revision"] == 0


def test_fact_source_version_is_preserved_and_context_writes_use_cas(local_jobs):
    job = local_jobs; body = context_body(job)
    body["confirmed_facts"][0].update(source="document_version", version_id=job[2]["current_version"])
    assert put_context(job, body).status_code == 200
    assert put_context(job, body).status_code == 409
    assert submit(job, with_context(job)).status_code == 200
    ctx = job[0].get(f"/api/rewrite-tasks/{job[5]['id']}/input").json()["generation_input"]["writing_context"]
    assert ctx["confirmed_facts"][0]["version_id"] == job[2]["current_version"]


@pytest.mark.parametrize("role,status", [("reader", 403), ("none", 404)])
def test_context_mutation_and_task_retry_recheck_access(local_jobs, role, status):
    job = local_jobs; assert put_context(job).status_code == 200
    assert submit(job, with_context(job)).status_code == 200
    if role == "reader": share(job[0], job[2], "reader")
    as_user(job[0], "bob")
    assert put_context(job, context_body(job, 1)).status_code == status
    assert submit(job, with_context(job)).status_code == status
    assert len(job[6]) == 1


def test_already_approved_output_remains_retryable_after_context_edit(local_jobs):
    job = local_jobs; assert put_context(job).status_code == 200
    assert submit(job, with_context(job)).status_code == 200
    task = execute(job)
    approval = {"id": str(uuid.uuid4()), "candidate_id": task["candidates"][0]["id"]}
    path = f"/api/rewrite-tasks/{task['id']}/approve"
    response = job[0].post(path, json=approval, headers={"X-Edit-Session": job[4]})
    assert response.status_code == 200
    assert put_context(job, context_body(job, 1)).status_code == 200
    assert job[0].post(path, json=approval, headers={"X-Edit-Session": job[4]}).json() == response.json()
    history = job[0].get(f"/api/threads/{job[3]}/rewrite-tasks").json()[0]
    assert history["context_stale"] and not history["stale"]


def test_codex_task_scoped_input_contains_only_frozen_confirmed_values(local_jobs, monkeypatch):
    job = local_jobs
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "codex")
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    captured = []
    monkeypatch.setattr("app.codex_runner.run_codex", lambda *args: captured.append(args))
    assert put_context(job).status_code == 200
    assert submit(job, with_context(job)).status_code == 200
    target, args = job[6][0]; target(*args)
    token, settings = captured[0][2:]
    assert settings.prompt_contract == "rewrite-paragraph-pair-context-v1"
    assert "generation_input.writing_context" in settings.prompt
    headers = {"Authorization": "Bearer " + token}
    result = job[0].get(f"/api/rewrite-tasks/{job[5]['id']}/input", headers=headers)
    assert result.status_code == 200 and result.json()["context_used"]
    assert input_hash(result.json()["generation_input"]) == result.json()["input_sha256"]
    assert job[0].get(f"/api/threads/{job[3]}", headers=headers).status_code == 403
    assert not job[7]


def test_context_save_response_loss_retries_without_new_revision_or_confirmation(local_jobs):
    job = local_jobs; body = context_body(job, request_id=str(uuid.uuid4()))
    first = put_context(job, body)
    assert first.status_code == 200
    stored = job[0].get(f"/api/threads/{job[3]}").json()["context"]
    assert put_context(job, body).json() == first.json()
    assert job[0].get(f"/api/threads/{job[3]}").json()["context"] == stored
    assert not any(key.startswith("_") for key in stored)
    assert put_context(job, {**body, "purpose": "같은 ID 다른 내용"}).status_code == 409
    assert put_context(job, context_body(job, 1, request_id=str(uuid.uuid4()))).status_code == 200
    assert put_context(job, body).status_code == 409  # Later writes are never rolled back.


def test_context_save_retry_checks_writer_and_current_permission(local_jobs):
    job = local_jobs; body = context_body(job, request_id=str(uuid.uuid4()))
    assert put_context(job, body).status_code == 200
    share(job[0], job[2], "editor"); as_user(job[0], "bob")
    assert put_context(job, body).status_code == 409
    as_user(job[0], "alice"); share(job[0], job[2], "reader"); as_user(job[0], "bob")
    assert put_context(job, body).status_code == 403
