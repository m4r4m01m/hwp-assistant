"""MOCK OpenAI-compatible transport + real task/storage APIs. No actual model or engine."""
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from sqlalchemy import event, select
from app.db import Application, Document, RewriteCandidate, RewriteTask, transaction
from app.hwpx import digest
from app.llm import OpenAICompatible
from app.rewrite_contract import parse_candidate_pair
from test_collaboration import secured, document, thread, snapshot, share, as_user
from test_storage import package, save

PAIR = {"candidates": [{"replacement": "김민수는 10월 9일까지 30만원을 지출하지 않습니다.", "reason": "간결한 문체"},
                        {"replacement": "10월 9일까지 김민수의 30만원 지출은 없습니다.", "reason": "기한 강조"}]}


def completion(value=PAIR, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps(value, ensure_ascii=False)}}]}


@pytest.fixture
def local_jobs(secured, monkeypatch):
    c, app, _, _ = secured
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "llm")
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LLM_MODEL", "synthetic-contract-fixture")
    monkeypatch.setenv("LLM_API_KEY", "secret-must-not-appear")
    monkeypatch.setenv("HWP_CODEX_USER", "somebody-else")
    queued, calls = [], []

    class CaptureThread:
        def __init__(self, *, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            queued.append((self.target, self.args))

    async def transport(self, method, route, body=None):
        calls.append(body)
        return completion()

    monkeypatch.setattr("app.rewrite.Thread", CaptureThread)
    monkeypatch.setattr(OpenAICompatible, "request", transport)
    doc = document(c)
    tid = thread(c, doc)
    lease = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    selected = snapshot()
    selected["evidence"]["text"] = "김민수는 10월 9일까지 30만원을 쓰지 않는다."
    selected["evidence"]["textSha256"] = digest(selected["evidence"]["text"].encode())
    selected["selection"]["target"] = {"kind": "body_paragraph", "section": 0, "paragraph": 0,
                                        "charOffset": 0, "length": len(selected["evidence"]["text"])}
    body = {"id": str(uuid.uuid4()), "base_version": doc["current_version"], "expected_revision": 0,
            "selection": selected, "instruction": "사실·날짜·금액·부정 표현을 유지하고 문체를 개선"}
    return c, app, doc, tid, lease, body, queued, calls


def submit(job, body=None):
    c, _, _, tid, lease, original, _, _ = job
    return c.post(f"/api/threads/{tid}/rewrite-tasks", json=body or original, headers={"X-Edit-Session": lease})


def execute(job, index=0):
    target, args = job[6][index]
    target(*args)
    return job[0].get(f"/api/threads/{job[3]}/rewrite-tasks").json()[0]


def test_two_candidates_use_frozen_unsaved_input_without_codex_or_writes(local_jobs, monkeypatch):
    c, app, doc, tid, _, body, queued, calls = local_jobs
    def no_codex(*_):
        pytest.fail("local request must not execute Codex")
    monkeypatch.setattr("app.codex_runner.run_codex", no_codex)
    assert c.get("/api/rewrite-provider").json()["allowed"]
    assert submit(local_jobs).status_code == 200
    # Changing server config after acceptance cannot replace this job's provider/model/input.
    monkeypatch.setenv("LLM_MODEL", "changed-after-request")
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "codex")
    task = execute(local_jobs)
    assert task["status"] == "ready" and len(task["candidates"]) == 2
    assert task["snapshot"] == body["selection"]
    assert task["execution"]["provider"] == "llm"
    assert task["execution"]["model"] == "synthetic-contract-fixture"
    assert task["execution"]["generation_calls"] == 1
    assert task["execution"]["context_used"] is False
    assert not any(key.startswith("_") for key in task["execution"])
    assert "secret-must-not-appear" not in json.dumps(task)
    payload = json.loads(calls[0]["messages"][1]["content"])
    assert payload == {"selected_paragraph": body["selection"]["evidence"]["text"], "instruction": body["instruction"]}
    assert task["execution"]["input_sha256"] == digest(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())
    assert calls[0]["model"] == "synthetic-contract-fixture" and "response_format" not in calls[0]
    assert len(c.get(f"/api/documents/{doc['id']}/versions").json()) == 1
    assert c.get(f"/api/documents/{doc['id']}/applications").json() == []
    assert c.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content == package()
    assert submit(local_jobs).json()["status"] == "ready" and len(queued) == 1


@pytest.mark.parametrize("change", ["instruction", "expected_revision", "purpose", "facts", "selection"])
def test_same_id_different_request_conflicts_without_duplicate_execution(local_jobs, change):
    assert submit(local_jobs).status_code == 200
    assert submit(local_jobs).status_code == 200
    changed = json.loads(json.dumps(local_jobs[5]))
    if change == "selection":
        changed[change]["revision"] += 1
    elif change == "expected_revision":
        changed[change] += 1
    else:
        changed[change] = "다른 요청"
    assert submit(local_jobs, changed).status_code == 409
    assert len(local_jobs[6]) == 1


def test_concurrent_same_id_dispatches_once(local_jobs):
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: submit(local_jobs), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert len(local_jobs[6]) == 1


@pytest.mark.parametrize("role,status", [("reader", 403), ("none", 404)])
def test_reader_and_external_user_cannot_generate(local_jobs, role, status):
    c, _, doc, _, _, _, queued, _ = local_jobs
    if role != "none":
        share(c, doc, role)
    as_user(c, "bob")
    assert submit(local_jobs).status_code == status
    assert not queued


def test_editor_can_use_local_model_with_own_lease_not_codex_account(local_jobs):
    c, _, doc, tid, _, body, queued, _ = local_jobs
    share(c, doc, "editor")
    assert c.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": local_jobs[4]}).status_code == 200
    as_user(c, "bob")
    own = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    assert submit(local_jobs).status_code == 409
    result = c.post(f"/api/threads/{tid}/rewrite-tasks", json=body, headers={"X-Edit-Session": own})
    assert result.status_code == 200
    assert execute(local_jobs)["status"] == "ready" and len(queued) == 1


@pytest.mark.parametrize("when", ["before", "during"])
@pytest.mark.parametrize("mutation", ["lease", "base", "expired"])
def test_generation_rechecks_state_without_holding_write_transaction(local_jobs, monkeypatch, when, mutation):
    c, app, doc, _, _, body, _, calls = local_jobs
    original_transport = OpenAICompatible.request
    def mutate():
        # A distinct connection can write while the model is executing.
        with transaction(app.state.engine) as db:
            row = db.get(Document, doc["id"])
            if mutation == "lease":
                row.lease_token = "different-lease"
            elif mutation == "base":
                row.current_version = str(uuid.uuid4())
            else:
                db.get(RewriteTask, body["id"]).created_at = time.time() - 601
    async def transport(self, *args):
        with ThreadPoolExecutor(1) as pool:
            pool.submit(mutate).result(timeout=3)
        return await original_transport(self, *args)
    assert submit(local_jobs).status_code == 200
    if when == "before":
        mutate()
    else:
        monkeypatch.setattr(OpenAICompatible, "request", transport)
    task = execute(local_jobs)
    assert task["status"] == "failed" and not task["candidates"]
    assert len(calls) == (0 if when == "before" else 1)
    assert task["application"] is None


def test_permission_revocation_during_model_call_prevents_publication(local_jobs, monkeypatch):
    c, app, doc, tid, _, body, _, _ = local_jobs
    share(c, doc, "editor")
    assert c.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": local_jobs[4]}).status_code == 200
    as_user(c, "bob")
    lease = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    assert c.post(f"/api/threads/{tid}/rewrite-tasks", json=body, headers={"X-Edit-Session": lease}).status_code == 200
    async def transport(*_):
        as_user(c, "alice")
        assert c.delete(f"/api/documents/{doc['id']}/access/bob").status_code == 200
        return completion()
    monkeypatch.setattr(OpenAICompatible, "request", transport)
    task = execute(local_jobs)
    assert task["status"] == "failed" and task["candidates"] == []
    as_user(c, "bob")
    assert submit(local_jobs).status_code == 404
    with transaction(app.state.engine) as db:
        assert not list(db.scalars(select(Application)))


@pytest.mark.parametrize("value", [
    {}, {"candidates": PAIR["candidates"][:1]}, {"candidates": PAIR["candidates"] * 2},
    {"candidates": PAIR["candidates"] + [PAIR["candidates"][0]]},
    {"candidates": [PAIR["candidates"][0]] * 2},
    {"candidates": [{"replacement": " ", "reason": "x"}, PAIR["candidates"][1]]},
    {"candidates": [{"replacement": "x" * 12001, "reason": "x"}, PAIR["candidates"][1]]},
    {"candidates": [{"replacement": "x" * 4001, "reason": "x"}, PAIR["candidates"][1]]},
    {"candidates": [{"replacement": "x\ny", "reason": "x"}, PAIR["candidates"][1]]},
    {"candidates": [{"replacement": "x\ty", "reason": "x"}, PAIR["candidates"][1]]},
    {"candidates": [{"replacement": "x", "reason": " "}, PAIR["candidates"][1]]},
    {"candidates": [{"replacement": "x", "reason": "x" * 2001}, PAIR["candidates"][1]]},
    {"candidates": [{"replacement": "x", "reason": "x", "approved": True}, PAIR["candidates"][1]]},
    {**PAIR, "task_id": "model-cannot-choose"},
    {"candidates": [{"replacement": 123, "reason": "x"}, PAIR["candidates"][1]]},
    {"candidates": [{"replacement": "\u200b", "reason": "x"}, PAIR["candidates"][1]]},
])
def test_invalid_model_candidates_fail_without_publication(local_jobs, monkeypatch, value):
    async def transport(*_):
        return completion(value)
    monkeypatch.setattr(OpenAICompatible, "request", transport)
    assert submit(local_jobs).status_code == 200
    task = execute(local_jobs)
    assert task["status"] == "failed" and not task["candidates"]
    assert task["execution"]["generation_calls"] == 1 and task["execution"]["completed"] is False
    assert task["application"] is None


@pytest.mark.parametrize("content", ["not json", "```json\n{}\n```", '{"candidates":[',
    '{"candidates":[],"candidates":[]}',
    '{"candidates":[{"replacement":"A","reason":"x"},{"replacement":" A ","reason":"y"}]}',
    '{"candidates":[{"replacement":"a","reason":"x","reason":"y"}]}'])
def test_malformed_or_ambiguous_json_is_not_repaired(content):
    with pytest.raises(ValueError):
        parse_candidate_pair(content)


@pytest.mark.parametrize("finish", ["length", "content_filter", "tool_calls", None])
def test_incomplete_completion_rejected_even_with_valid_json(local_jobs, monkeypatch, finish):
    async def transport(*_):
        return completion(finish=finish)
    monkeypatch.setattr(OpenAICompatible, "request", transport)
    assert submit(local_jobs).status_code == 200
    assert execute(local_jobs)["status"] == "failed"


@pytest.mark.parametrize("exception,status", [(httpx.ConnectError("secret-must-not-appear"), 502),
                                               (httpx.ReadTimeout("secret-must-not-appear"), 504)])
def test_transport_failure_has_one_call_no_fallback_and_no_secrets(local_jobs, monkeypatch, exception, status):
    # Exercise the real transport wrapper rather than replacing its sanitized errors.
    # Exercise HTTP exception sanitization, not just a prebuilt error fixture.
    monkeypatch.setattr(OpenAICompatible, "request", REAL_REQUEST)
    calls = []
    async def fail(*args, **kwargs):
        calls.append(kwargs)
        raise exception
    monkeypatch.setattr(httpx.AsyncClient, "request", fail)
    assert submit(local_jobs).status_code == 200
    task = execute(local_jobs)
    assert task["status"] == "failed" and task["execution"]["error_status"] == status
    assert len(calls) == 1 and task["candidates"] == []
    assert "secret-must-not-appear" not in json.dumps(task)
    assert submit(local_jobs).json()["status"] == "failed" and len(local_jobs[6]) == 1


REAL_REQUEST = OpenAICompatible.request


def test_candidate_commit_failure_never_exposes_ready_or_partial_candidates(local_jobs):
    assert submit(local_jobs).status_code == 200
    engine = local_jobs[1].state.engine
    def fail_once(connection):
        if connection.execute(select(RewriteCandidate.id)).first():
            raise RuntimeError("candidate commit failure")
    event.listen(engine, "commit", fail_once)
    try:
        task = execute(local_jobs)
    finally:
        event.remove(engine, "commit", fail_once)
    assert task["status"] == "failed" and task["candidates"] == []
    assert task["execution"]["completed"] is False


def test_unknown_provider_and_missing_local_config_do_not_dispatch(local_jobs, monkeypatch):
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "unknown")
    assert submit(local_jobs).status_code == 503
    assert c_settings(local_jobs)["configured"] is False
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "llm")
    monkeypatch.delenv("LLM_MODEL")
    assert submit(local_jobs).status_code == 503 and not local_jobs[6]


def c_settings(job):
    return job[0].get("/api/rewrite-provider").json()


@pytest.mark.parametrize("value", [{}, [], {"choices": []}, {"choices": ["invalid"]},
                                  {"choices": [{"message": []}]},
                                  {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}])
def test_invalid_completion_envelopes_fail_safely(local_jobs, monkeypatch, value):
    async def transport(*_):
        return value
    monkeypatch.setattr(OpenAICompatible, "request", transport)
    assert submit(local_jobs).status_code == 200
    task = execute(local_jobs)
    assert task["status"] == "failed" and task["candidates"] == []
    assert task["execution"]["error_status"] == 502


@pytest.mark.parametrize("url", ["http://api.openai.com/v1", "https://API.OPENAI.COM./v1", "https://x.openai.com/v1",
                                "http://user:secret@localhost/v1", "http:///v1", "http://localhost:invalid/v1",
                                "http://localhost/v1?key=secret", "http://localhost/v1#secret"])
def test_unsafe_or_invalid_config_never_dispatches(local_jobs, monkeypatch, url):
    monkeypatch.setenv("LLM_BASE_URL", url)
    result = submit(local_jobs)
    assert result.status_code == 503 and not local_jobs[6]
    assert "secret" not in result.text


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0", "241", "invalid"])
def test_timeout_config_is_finite_and_bounded(local_jobs, monkeypatch, value):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", value)
    assert submit(local_jobs).status_code == 503 and not local_jobs[6]


def test_local_task_has_no_public_candidate_registration_token(local_jobs):
    c, app, _, _, _, body, _, _ = local_jobs
    assert submit(local_jobs).status_code == 200
    with transaction(app.state.engine) as db:
        assert db.get(RewriteTask, body["id"]).token_hash == ""
    path = f"/api/rewrite-tasks/{body['id']}/candidates"
    payload = {**PAIR, "original_sha256": body["selection"]["evidence"]["textSha256"]}
    assert c.post(path, json=payload).status_code == 403
    assert c.post(path, json=payload, headers={"Authorization": "Bearer hwp-run-fake"}).status_code == 401


def test_no_context_reread_or_unreported_context_in_prompt(local_jobs, monkeypatch):
    c, _, doc, tid, _, body, _, calls = local_jobs
    assert submit(local_jobs).status_code == 200
    assert c.put(f"/api/threads/{tid}/context", json={"expected_revision": 0, "base_version": doc["current_version"],
                 "purpose": "new purpose not used", "facts": "new facts not used", "selection": None}).status_code == 200
    task = execute(local_jobs)
    assert task["status"] == "ready"
    assert "new facts" not in json.dumps(calls)
    assert task["execution"]["context_used"] is False


@pytest.mark.parametrize("during", [False, True])
def test_cancelled_task_does_not_publish_late_results_or_restart(local_jobs, monkeypatch, during):
    c, _, _, tid, _, body, queued, calls = local_jobs
    original = OpenAICompatible.request
    path = f"/api/rewrite-tasks/{body['id']}/cancel"
    async def transport(self, *args):
        assert c.post(path).json()["status"] == "cancelled"
        return await original(self, *args)
    assert submit(local_jobs).status_code == 200
    if during:
        monkeypatch.setattr(OpenAICompatible, "request", transport)
    else:
        assert c.post(path).json()["status"] == "cancelled"
    task = execute(local_jobs)
    assert c.post(path).json()["status"] == "cancelled"
    assert task["status"] == "cancelled" and not task["candidates"] and not task["can_cancel"]
    assert len(calls) == int(during)
    assert submit(local_jobs).json()["status"] == "cancelled" and len(queued) == 1


def test_reader_external_and_other_editor_cannot_cancel_someone_elses_task(local_jobs):
    c, _, doc, _, _, body, _, _ = local_jobs
    assert submit(local_jobs).status_code == 200
    path = f"/api/rewrite-tasks/{body['id']}/cancel"
    for role in ("reader", "editor"):
        as_user(c, "alice")
        share(c, doc, role)
        as_user(c, "bob")
        assert c.post(path).status_code == 403
        assert c.get(f"/api/threads/{local_jobs[3]}/rewrite-tasks").json()[0]["can_cancel"] is False
    as_user(c, "eve")
    assert c.post(path).status_code == 404


def test_expired_job_is_visible_as_expired_and_same_id_never_dispatches_again(local_jobs):
    c, app, _, tid, _, body, queued, _ = local_jobs
    assert submit(local_jobs).status_code == 200
    with transaction(app.state.engine) as db:
        db.get(RewriteTask, body["id"]).created_at = time.time() - 601
    task = c.get(f"/api/threads/{tid}/rewrite-tasks").json()[0]
    assert task["status"] == "expired" and task["snapshot"] == body["selection"]
    assert submit(local_jobs).json()["status"] == "expired" and len(queued) == 1


def test_completed_task_cannot_be_cancelled_or_candidates_removed(local_jobs):
    c, _, _, tid, _, body, _, _ = local_jobs
    assert submit(local_jobs).status_code == 200
    before = execute(local_jobs)
    assert c.post(f"/api/rewrite-tasks/{body['id']}/cancel").status_code == 409
    assert c.get(f"/api/threads/{tid}/rewrite-tasks").json()[0] == before


def test_unicode_length_matches_rhwp_codepoints_and_visible_duplicates_are_rejected():
    accepted = {"candidates": [{"replacement": "😀" * 4000, "reason": "한계값"}, PAIR["candidates"][1]]}
    assert len(parse_candidate_pair(json.dumps(accepted)).candidates[0].replacement) == 4000
    for second in ("문장\u200b", " 문장 "):
        with pytest.raises(ValueError):
            parse_candidate_pair(json.dumps({"candidates": [{"replacement": "문장", "reason": "x"},
                                                          {"replacement": second, "reason": "y"}]}))


@pytest.mark.parametrize("bad", ["length", "extra_target", "epoch", "revision", "instruction", "editable"])
def test_unusable_engine_inputs_rejected_before_generation(local_jobs, bad):
    body = json.loads(json.dumps(local_jobs[5]))
    if bad == "length":
        body["selection"]["selection"]["target"]["length"] += 1
    elif bad == "extra_target":
        body["selection"]["selection"]["target"]["extra"] = 1
    elif bad == "epoch":
        body["selection"]["state"]["documentEpoch"] = 0
    elif bad == "revision":
        body["selection"]["revision"] = True
    elif bad == "editable":
        body["selection"]["selection"]["editable"] = "true"
    else:
        body["instruction"] = " "
    assert submit(local_jobs, body).status_code == 422 and not local_jobs[6]


@pytest.mark.parametrize("field", ["instruction", "facts", "original", "nonfinite"])
def test_unencodable_request_is_rejected_before_task_hashing(local_jobs, field):
    c, _, _, tid, lease, body, queued, _ = local_jobs
    invalid = json.loads(json.dumps(body))
    if field == "original":
        invalid["selection"]["evidence"]["text"] = "bad lone surrogate \ud800"
    elif field == "nonfinite":
        invalid["selection"]["revision"] = float("nan")
    else:
        invalid[field] = "bad lone surrogate \ud800"
    response = c.post(f"/api/threads/{tid}/rewrite-tasks", content=json.dumps(invalid).encode("ascii"),
                      headers={"X-Edit-Session": lease, "Content-Type": "application/json"})
    assert response.status_code == 422 and not queued
