"""Synthetic transport/concurrency checks; no real model or production server calls."""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import codex_runner, llm
from app.ai_requests import AIRequest
from app.db import Access, Conversation, transaction
from app.hwpx import digest
from app.main import create_app
from test_collaboration import secured, document, thread, context, TOKENS
from test_storage import env, create, save


def configure_http(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LLM_MODEL", "synthetic-model")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-key-never-exposed")


def test_capabilities_are_authenticated_cheap_and_honest(secured, monkeypatch):
    c, _, _, _ = secured
    configure_http(monkeypatch)
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "llm")
    monkeypatch.setenv("HWP_LLM_ALLOWED_USERS", "alice")
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    settings = codex_runner.capture_settings()
    monkeypatch.setattr(codex_runner, "capture_settings", lambda: replace(settings, executable=None))
    async def forbidden(*args, **kwargs):
        raise AssertionError("Capability discovery must not contact a model")
    monkeypatch.setattr(llm.OpenAICompatible, "models", forbidden)
    result = c.get("/api/capabilities")
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    features = result.json()["features"]
    assert features["chat"]["configured"] and features["chat"]["allowed"]
    assert features["chat"]["reachable"] is None
    assert features["reports"]["reason_code"] == "codex_not_installed"
    assert features["reports"]["supports_stream"] is True
    assert "synthetic-key" not in result.text and "127.0.0.1" not in result.text
    c.headers["Authorization"] = "Bearer " + TOKENS["bob"]
    assert c.get("/api/capabilities").json()["features"]["chat"]["allowed"] is False
    assert c.get("/api/ai/models").status_code == 403
    c.headers.pop("Authorization")
    assert c.get("/api/capabilities").status_code == 401


def test_missing_model_does_not_block_document_library(env, monkeypatch):
    c, _, _, _ = env
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    result = c.get("/api/capabilities").json()["features"]
    assert result["chat"]["configured"] is False
    assert result["chat"]["reachable"] is None
    assert c.get("/api/documents").status_code == 200


def test_slow_persistence_does_not_stall_other_requests(env, monkeypatch):
    c, _, _, _ = env
    doc, token = create(c)
    started, release = Event(), Event()
    from app.main import describe_changes
    def slow_diff(*args):
        started.set()
        assert release.wait(5)
        return describe_changes(*args)
    monkeypatch.setattr("app.main.describe_changes", slow_diff)
    with ThreadPoolExecutor(2) as pool:
        saving = pool.submit(save, c, doc, token)
        try:
            assert started.wait(3)
            # Health must finish while the save's file/diff stage is still blocked.
            assert pool.submit(c.get, "/api/health").result(timeout=2).status_code == 200
            assert not saving.done()
        finally:
            release.set()
        assert saving.result(timeout=3).status_code == 200


def question(c):
    doc = document(c)
    tid = thread(c, doc)
    assert context(c, doc, tid).status_code == 200
    message = c.post(f"/api/threads/{tid}/messages", json={"request_id": "same-question", "content": "합성 질문"}).json()
    return f"/api/threads/{tid}/chat", {"message_id": message["id"]}


def test_same_question_is_reserved_before_model_call_across_app_instances(secured, monkeypatch):
    c, _, url, folder = secured
    path, body = question(c)
    started, release, calls = Event(), Event(), []
    class Provider:
        async def chat(self, *_):
            calls.append(1); started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return "합성 답변"
    monkeypatch.setattr("app.collaboration.OpenAICompatible", Provider)
    with TestClient(create_app(url, folder)) as other, ThreadPoolExecutor(1) as pool:
        other.headers["Authorization"] = "Bearer " + TOKENS["alice"]
        first = pool.submit(c.post, path, json=body)
        try:
            assert started.wait(3)
            assert other.post(path, json=body).status_code == 409
        finally:
            release.set()
        response = first.result(timeout=3)
        assert response.status_code == 200
        assert other.post(path, json=body).json() == response.json()
    assert len(calls) == 1


def test_failed_or_restarted_chat_is_not_silently_billed_again(secured, monkeypatch):
    c, _, url, folder = secured
    path, body = question(c)
    calls = []
    class Provider:
        async def chat(self, *_):
            calls.append(1)
            raise HTTPException(504, "synthetic uncertain response")
    monkeypatch.setattr("app.collaboration.OpenAICompatible", Provider)
    assert c.post(path, json=body).status_code == 504
    with TestClient(create_app(url, folder)) as restarted:
        restarted.headers["Authorization"] = "Bearer " + TOKENS["alice"]
        assert restarted.post(path, json=body).status_code == 409
    assert len(calls) == 1


def test_completed_chat_retries_do_not_need_a_free_slot_or_user_budget(secured, monkeypatch):
    c, app, _, _ = secured
    path, body = question(c)
    calls = []
    class Provider:
        async def chat(self, *_):
            calls.append(1)
            return "이미 저장된 합성 답변"
    monkeypatch.setattr("app.collaboration.OpenAICompatible", Provider)
    completed = c.post(path, json=body)
    assert completed.status_code == 200
    held = 0
    while app.state.rewrite_capacity.acquire(blocking=False):
        held += 1
    try:
        with app.state.ai_budget.reserve("alice"):
            before = list(app.state.ai_budget.recent["alice"])
            replay = c.post(path, json=body)
            assert replay.status_code == 200 and replay.json() == completed.json()
            assert list(app.state.ai_budget.recent["alice"]) == before
    finally:
        for _ in range(held):
            app.state.rewrite_capacity.release()
    assert len(calls) == 1


@pytest.mark.parametrize("case, expected", [("reader", 403), ("revoked", 404), ("other_author", 404)])
def test_completed_chat_still_checks_current_access_and_question_author(secured, monkeypatch, case, expected):
    c, app, _, _ = secured
    path, body = question(c)
    class Provider:
        async def chat(self, *_):
            return "권한 확인용 합성 답변"
    monkeypatch.setattr("app.collaboration.OpenAICompatible", Provider)
    assert c.post(path, json=body).status_code == 200
    thread_id = path.split("/")[3]
    with transaction(app.state.engine) as db:
        doc_id = db.get(Conversation, thread_id).document_id
        access = db.get(Access, (doc_id, "alice"))
        if case == "reader":
            access.role = "reader"
        elif case == "revoked":
            db.delete(access)
        else:
            db.add(Access(document_id=doc_id, user_id="bob", role="editor"))
    if case == "other_author":
        c.headers["Authorization"] = "Bearer " + TOKENS["bob"]
    assert c.post(path, json=body).status_code == expected


def test_http_ai_and_background_jobs_share_capacity(env, monkeypatch):
    c, app, _, _ = env
    configure_http(monkeypatch)
    held = 0
    while app.state.rewrite_capacity.acquire(blocking=False):
        held += 1
    try:
        assert c.get("/api/ai/models").status_code == 429
    finally:
        for _ in range(held):
            app.state.rewrite_capacity.release()


def test_chat_capacity_rejection_preserves_quota_and_request_retry(secured, monkeypatch):
    client, app, _, _ = secured
    path, body = question(client)
    thread_id = path.split("/")[3]
    app.state.ai_budget.limit = 1
    calls = []
    class Provider:
        async def chat(self, *_):
            calls.append(1)
            return "용량 해제 후 생성한 합성 답변"
    monkeypatch.setattr("app.collaboration.OpenAICompatible", Provider)
    held = 0
    while app.state.rewrite_capacity.acquire(blocking=False):
        held += 1
    try:
        assert client.post(path, json=body).status_code == 429
        assert not calls and not app.state.ai_budget.recent
        assert "alice" not in app.state.ai_budget.active
        with transaction(app.state.engine) as db:
            assert db.get(AIRequest, digest((thread_id + ":" + body["message_id"]).encode())) is None
    finally:
        for _ in range(held):
            app.state.rewrite_capacity.release()

    # No durable execution claim or minute quota was consumed by failed admission.
    completed = client.post(path, json=body)
    assert completed.status_code == 200, completed.text
    assert calls == [1] and len(app.state.ai_budget.recent["alice"]) == 1
    assert client.post(path, json=body).json() == completed.json()
    message = client.post(f"/api/threads/{thread_id}/messages", json={
        "request_id": "capacity-follow-up", "content": "두 번째 합성 질문"}).json()
    assert client.post(path, json={"message_id": message["id"]}).status_code == 429
    assert calls == [1] and "alice" not in app.state.ai_budget.active
    available = 0
    while app.state.rewrite_capacity.acquire(blocking=False):
        available += 1
    try:
        assert available == held
    finally:
        for _ in range(available):
            app.state.rewrite_capacity.release()


def test_telemetry_has_real_timings_without_document_names_tokens_or_query(secured, caplog):
    c, _, _, _ = secured
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        response = c.get("/api/documents?q=secret-document-name", headers={"X-Request-ID": "untrusted-input"})
    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32 and request_id != "untrusted-input"
    assert float(response.headers["Server-Timing"].split("=")[1]) >= 0
    rows = [json.loads(r.message.split("hwp_request ")[1]) for r in caplog.records if "hwp_request " in r.message]
    assert rows[-1]["route"] == "/api/documents" and rows[-1]["request_id"] == request_id
    assert "secret-document-name" not in caplog.text and TOKENS["alice"] not in caplog.text


def test_api_transport_reuses_pool_but_worker_loops_own_their_clients(monkeypatch):
    configure_http(monkeypatch)
    created, seen = [], []
    def factory():
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={"data": [{"id": "synthetic-model"}]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
        created.append(client)
        return client
    monkeypatch.setattr(llm, "pooled_client", factory)
    async def api_calls():
        async with factory() as client:
            marker = llm.request_client.set(client)
            try:
                await llm.OpenAICompatible().models()
                await llm.OpenAICompatible().models()
                assert len(created) == 1
            finally:
                llm.request_client.reset(marker)
    asyncio.run(api_calls())
    asyncio.run(llm.OpenAICompatible().models())
    asyncio.run(llm.OpenAICompatible().models())
    assert len(created) == 3 and all(client.is_closed for client in created)
    assert len(seen) == 4


def test_total_model_deadline_bounds_trickling_transport(monkeypatch):
    configure_http(monkeypatch)
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0.02")
    async def slow(request):
        await asyncio.sleep(10)
        return httpx.Response(200, json={"data": []})
    monkeypatch.setattr(llm, "pooled_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    with pytest.raises(HTTPException) as error:
        asyncio.run(llm.OpenAICompatible().models())
    assert error.value.status_code == 504
