"""Offline HTTP-report contracts; these do not prove real model or HWP engine quality."""
import asyncio
import copy
import json
import time
import uuid
from threading import Event

import httpx
import pytest

from app import llm, reports
from app.db import ReportTask, transaction
from test_collaboration import as_user, secured
from test_reports import DRAFT, create, ready


@pytest.fixture
def http_reports(secured, monkeypatch):
    monkeypatch.setenv("HWP_REPORT_PROVIDER", "llm")
    monkeypatch.setenv("HWP_CODEX_USER", "a-different-owner")
    monkeypatch.setenv("HWP_LLM_ALLOWED_USERS", "alice")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_ALLOW_EXTERNAL", "true")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_MODEL", "example/test-model")
    monkeypatch.setenv("LLM_API_KEY", "private-report-test-key")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "16000")
    monkeypatch.setenv("LLM_RESPONSE_FORMAT", "text")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "5")
    calls, state = [], {"draft": copy.deepcopy(DRAFT)}

    async def respond(request):
        calls.append(json.loads(request.content))
        if delay := state.get("delay"):
            await delay()
        if "http_status" in state:
            return httpx.Response(state["http_status"], json={"error": {"message": "private-report-test-key upstream detail"}})
        return httpx.Response(200, json={"choices": [{"finish_reason": state.get("finish_reason", "stop"),
                   "message": {"content": state.get("content", json.dumps(state["draft"], ensure_ascii=False))}}],
                   "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

    monkeypatch.setattr(llm, "pooled_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), trust_env=False, follow_redirects=False))
    return secured, calls, state


def revise(client, job):
    payload = {"id": str(uuid.uuid4()), "draft_sha256": job["draft_sha256"], "feedback": "확인 절차를 더 구체적으로 써 주세요."}
    return payload, client.post(f"/api/report-tasks/{job['id']}/revise", json=payload)


def test_http_report_ready_keeps_review_boundary_and_secret_out_of_storage(http_reports):
    (client, app, _, folder), calls, _ = http_reports
    body = create(client)
    job = ready(client, body)
    assert job["status"] == "ready", job
    assert job["approval"] is None and job["preview_id"] is None
    assert client.get("/api/documents").json() == []
    assert not list(folder.iterdir())
    evidence = job["execution"]
    assert evidence["executor"] == "llm-http" and evidence["backend"] == "llm"
    assert evidence["provider"] == "openrouter" and evidence["model"] == "example/test-model"
    assert evidence["streamed"] is False and evidence["web_search_calls"] == 0
    assert evidence["progress"]["blocks"] == [] and evidence["progress"]["stage"] == "complete"
    assert evidence["usage"]["total_tokens"] == 150
    assert len(evidence["configuration_sha256"]) == 64
    assert len(calls) == 1 and calls[0]["model"] == "example/test-model"
    assert calls[0]["max_tokens"] == 16000
    assert "Report JSON schema" in calls[0]["messages"][0]["content"]
    with transaction(app.state.engine) as db:
        row = db.get(ReportTask, body["id"])
        persisted = " ".join((row.input, row.execution, row.draft, row.error or ""))
    assert "private-report-test-key" not in persisted
    assert "https://openrouter.ai" not in persisted


def test_http_report_research_is_rejected_before_new_task_or_charge(http_reports):
    (client, app, _, _), calls, _ = http_reports
    body = {"id": str(uuid.uuid4()), "prompt": "금융 보안 보고서를 작성해 주세요.", "research": True}
    response = client.post("/api/report-tasks", json=body)
    assert response.status_code == 422 and "웹 검색" in response.json()["detail"]
    assert not calls and not app.state.ai_budget.recent
    with transaction(app.state.engine) as db:
        assert db.get(ReportTask, body["id"]) is None


def test_http_report_access_matches_shared_llm_policy(http_reports):
    (client, _, _, _), calls, _ = http_reports
    as_user(client, "bob")
    body = {"id": str(uuid.uuid4()), "prompt": "보고서 작성 요청입니다.", "research": False}
    assert client.post("/api/report-tasks", json=body).status_code == 403
    assert not calls
    as_user(client, "alice")
    created = client.post("/api/report-tasks", json=body)
    assert created.status_code == 200
    assert ready(client, body)["status"] == "ready"
    as_user(client, "bob")
    assert client.get(f"/api/report-tasks/{body['id']}").status_code == 404


@pytest.mark.parametrize("kind", ["invalid-json", "invalid-draft", "truncated", "invented-source", "no-limitations", "upstream-error"])
def test_http_report_invalid_outputs_never_become_preview_or_document(http_reports, kind):
    (client, _, _, _), calls, state = http_reports
    if kind == "invalid-json":
        state["content"] = "not-json private-report-test-key"
    elif kind == "invalid-draft":
        state["draft"]["title"] = ""
    elif kind == "truncated":
        state["finish_reason"] = "length"
    elif kind == "invented-source":
        state["draft"]["sources"] = [{"id": "S1", "title": "확인하지 않은 자료", "url": "https://example.com/invented", "published": ""}]
    elif kind == "no-limitations":
        state["draft"]["limitations"] = []
    else:
        state["http_status"] = 401
    job = ready(client, create(client))
    assert job["status"] == "failed" and job["draft"] is None
    assert job["preview_id"] is None and job["approval"] is None
    assert "AI API" in job["error"] and "private-report-test-key" not in json.dumps(job)
    assert client.get("/api/documents").json() == []
    assert len(calls) == 1  # No fallback or automatic second billable generation.


def test_http_report_timeout_is_bounded_and_releases_user_budget(http_reports, monkeypatch):
    (client, app, _, _), calls, state = http_reports
    cancelled = Event()
    async def delayed():
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
    state["delay"] = delayed
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", ".1")
    job = ready(client, create(client))
    assert job["status"] == "failed" and cancelled.wait(1)
    assert "alice" not in app.state.ai_budget.active
    assert len(calls) == 1


def test_http_cancel_aborts_request_and_holds_budget_until_cleanup(http_reports):
    (client, app, _, _), calls, state = http_reports
    entered, cancelled = Event(), Event()
    async def delayed():
        entered.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
    state["delay"] = delayed
    body = create(client)
    assert entered.wait(1) and "alice" in app.state.ai_budget.active
    assert client.post(f"/api/report-tasks/{body['id']}/cancel").json()["status"] == "cancelled"
    assert cancelled.wait(1)
    for _ in range(100):
        if "alice" not in app.state.ai_budget.active:
            break
        time.sleep(.005)
    assert "alice" not in app.state.ai_budget.active
    assert ready(client, body)["status"] == "cancelled"
    assert len(calls) == 1 and client.get("/api/documents").json() == []


def test_http_report_uses_shared_budget_but_retry_never_reexecutes(http_reports, monkeypatch):
    (client, app, _, _), calls, _ = http_reports
    app.state.ai_budget.limit = 1
    body = create(client)
    original = ready(client, body)
    assert original["status"] == "ready"
    monkeypatch.delenv("LLM_API_KEY")
    assert client.post("/api/report-tasks", json=body).json() == original
    assert len(calls) == 1 and len(app.state.ai_budget.recent["alice"]) == 1
    monkeypatch.setenv("LLM_API_KEY", "private-report-test-key")
    response = client.post("/api/report-tasks", json={**body, "id": str(uuid.uuid4())})
    assert response.status_code == 429 and len(calls) == 1


def test_http_revision_freezes_route_and_keeps_original_draft(http_reports, monkeypatch):
    (client, _, _, _), calls, state = http_reports
    source = ready(client, create(client))
    state["draft"]["sections"][0]["paragraphs"] = ["운영팀은 확인 결과를 기록함."]
    payload, response = revise(client, source)
    assert response.status_code == 200, response.text
    result = ready(client, payload)
    assert result["status"] == "ready" and len(calls) == 2
    assert result["execution"]["configuration_sha256"] == source["execution"]["configuration_sha256"]
    assert client.get(f"/api/report-tasks/{source['id']}").json()["draft"] == source["draft"]
    monkeypatch.setenv("LLM_MODEL", "example/different-model")
    assert client.post(f"/api/report-tasks/{source['id']}/revise", json=payload).json() == result
    _, changed = revise(client, source)
    assert changed.status_code == 409 and "설정이 변경" in changed.json()["detail"]
    monkeypatch.setenv("HWP_REPORT_PROVIDER", "codex")
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    _, switched = revise(client, source)
    assert switched.status_code == 409 and len(calls) == 2


def test_http_report_worker_uses_captured_settings_and_own_loop_client(http_reports, monkeypatch):
    (client, app, _, _), calls, _ = http_reports
    workers = []
    class DeferredThread:
        def __init__(self, *, target, args, **_):
            workers.append((target, args))
        def start(self):
            pass
    monkeypatch.setattr(reports, "Thread", DeferredThread)
    body = create(client)
    assert "alice" in app.state.ai_budget.active and not calls
    monkeypatch.setenv("LLM_MODEL", "example/different-model")
    monkeypatch.setenv("LLM_API_KEY", "different-key")
    # A sentinel stands in for a client belonging to the HTTP server event loop.
    token = llm.request_client.set(object())
    try:
        target, args = workers[0]
        target(*args)
        assert llm.request_client.get() is not None
    finally:
        llm.request_client.reset(token)
    job = ready(client, body)
    assert job["status"] == "ready"
    assert calls[0]["model"] == job["execution"]["model"] == "example/test-model"
    assert "alice" not in app.state.ai_budget.active


def test_http_report_thread_dispatch_failure_releases_reserved_budget(http_reports, monkeypatch):
    (client, app, _, _), calls, _ = http_reports
    class FailedThread:
        def __init__(self, **_):
            pass
        def start(self):
            raise RuntimeError("dispatch failed")
    monkeypatch.setattr(reports, "Thread", FailedThread)
    body = {"id": str(uuid.uuid4()), "prompt": "금융 보안 보고서를 작성해 주세요.", "research": False}
    assert client.post("/api/report-tasks", json=body).status_code == 500
    failed = client.get(f"/api/report-tasks/{body['id']}").json()
    assert failed["status"] == "failed" and failed["execution"]["completed"] is False
    assert client.post("/api/report-tasks", json=body).json() == failed
    assert "alice" not in app.state.ai_budget.active and not calls
    assert app.state.rewrite_capacity.acquire(blocking=False)
    app.state.rewrite_capacity.release()
