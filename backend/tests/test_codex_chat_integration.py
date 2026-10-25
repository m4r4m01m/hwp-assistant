"""Actual API/DB chat fences with synthetic providers; no model or credential use."""
import copy
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import codex_runtime
from app.ai_requests import AIRequest
from app.db import Access, Context, Document, transaction
from test_collaboration import as_user, context, document, secured, share, thread


@pytest.fixture
def codex_chat(secured, monkeypatch):
    from app.codex_chat_review import CodexBasicProvider
    client, app, _, folder = secured
    monkeypatch.setenv("HWP_CHAT_PROVIDER", "codex")
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    monkeypatch.delenv("HWP_CODEX_ALLOWED_USERS", raising=False)
    monkeypatch.setenv("HWP_CODEX_EXECUTABLE", sys.executable)
    monkeypatch.setenv("HWP_CODEX_CHECK_AUTH", "0")
    monkeypatch.setenv("HWP_CODEX_DAILY_REQUEST_LIMIT", "1")
    app.state.rewrite_capacity = BoundedSemaphore(1)
    doc = document(client)
    tid = thread(client, doc)
    assert context(client, doc, tid).status_code == 200
    calls = []

    async def synthetic(self, ctx, messages, active=lambda: True):
        calls.append((copy.deepcopy(ctx), copy.deepcopy(messages)))
        assert active()
        return "합성 Codex 답변: 확인한 문서 작성 기준을 따릅니다."

    monkeypatch.setattr(CodexBasicProvider, "chat", synthetic)
    return client, app, folder, doc, tid, calls


def question(client, tid, text="확정 사실에 따라 설명해 주세요."):
    response = client.post(f"/api/threads/{tid}/messages", json={"request_id": str(uuid.uuid4()), "content": text})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def submit(client, tid, message):
    return client.post(f"/api/threads/{tid}/chat", json={"message_id": message})


def usage(app):
    with transaction(app.state.engine) as db:
        return [(r.request_key, r.user_id) for r in db.scalars(select(codex_runtime.CodexUsage))]


def test_codex_chat_uses_owned_context_once_without_modifying_document(codex_chat, monkeypatch):
    client, app, folder, doc, tid, calls = codex_chat
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    message = question(client, tid)
    response = submit(client, tid, message)
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "codex"
    assert calls[0][0]["facts"] == "확정 사실"
    assert calls[0][1][-1]["content"] == "확정 사실에 따라 설명해 주세요."
    assert len(calls) == len(usage(app)) == 1
    # A committed result is readback, even after readiness changes or the cap is spent.
    monkeypatch.setattr(codex_runtime, "readiness", lambda _: {"configured": False, "message": "synthetic login unavailable"})
    assert submit(client, tid, message).json() == response.json()
    assert len(calls) == len(usage(app)) == 1
    assert len(client.get(f"/api/threads/{tid}").json()["messages"]) == 2
    assert client.get(f"/api/documents/{doc['id']}").json()["current_version"] == doc["current_version"]
    assert len(client.get(f"/api/documents/{doc['id']}/versions").json()) == 1
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


@pytest.mark.parametrize("reason", ["permission", "login", "capacity", "daily_limit"])
def test_codex_chat_rejected_admission_does_not_call_model_or_claim_question(codex_chat, monkeypatch, reason):
    client, app, _, _, tid, calls = codex_chat
    message = question(client, tid)
    if reason == "permission":
        monkeypatch.setenv("HWP_CODEX_USER", "bob")
    elif reason == "login":
        monkeypatch.setattr(codex_runtime, "readiness", lambda _: {"configured": False, "message": "synthetic login unavailable"})
    elif reason == "capacity":
        assert app.state.rewrite_capacity.acquire(blocking=False)
    else:
        with transaction(app.state.engine) as db:
            codex_runtime.reserve_usage(db, "already-used-by-reports", "alice")
    initial_usage = usage(app)
    try:
        result = submit(client, tid, message)
        assert result.status_code == {"permission": 403, "login": 503, "capacity": 429, "daily_limit": 429}[reason], result.text
        assert not calls and usage(app) == initial_usage
        with transaction(app.state.engine) as db:
            assert list(db.scalars(select(AIRequest))) == []
    finally:
        if reason == "capacity":
            app.state.rewrite_capacity.release()


def test_codex_chat_uncertain_failure_is_charged_once_and_never_automatically_retried(codex_chat, monkeypatch):
    from app.codex_chat_review import CodexBasicProvider
    client, app, _, _, tid, calls = codex_chat
    message = question(client, tid)

    async def failed(self, *args, **kwargs):
        calls.append("failed")
        raise HTTPException(502, "합성 모델 연결 실패")

    monkeypatch.setattr(CodexBasicProvider, "chat", failed)
    assert submit(client, tid, message).status_code == 502
    assert submit(client, tid, message).status_code == 409
    assert calls == ["failed"] and len(usage(app)) == 1
    with transaction(app.state.engine) as db:
        claims = list(db.scalars(select(AIRequest)))
        assert len(claims) == 1 and claims[0].status == "failed"
    assert len(client.get(f"/api/threads/{tid}").json()["messages"]) == 1


@pytest.mark.parametrize("mutation", ["context", "base", "revoke", "reader"])
def test_late_codex_chat_cannot_append_after_context_base_or_acl_change(codex_chat, monkeypatch, mutation):
    from app.codex_chat_review import CodexBasicProvider
    client, app, _, doc, tid, calls = codex_chat
    # Bob owns this question while Alice remains the document owner/operator.
    share(client, doc)
    monkeypatch.setenv("HWP_CODEX_ALLOWED_USERS", "alice,bob")
    as_user(client, "bob")
    message = question(client, tid)

    async def late(self, *args, **kwargs):
        calls.append(mutation)
        with transaction(app.state.engine) as db:
            if mutation == "context":
                db.get(Context, tid).revision += 1
            elif mutation == "base":
                db.get(Document, doc["id"]).current_version = "synthetic-new-head"
            elif mutation == "revoke":
                db.delete(db.get(Access, (doc["id"], "bob")))
            else:
                db.get(Access, (doc["id"], "bob")).role = "reader"
        return "늦은 합성 응답은 대화에 게시되면 안 됩니다."

    monkeypatch.setattr(CodexBasicProvider, "chat", late)
    result = submit(client, tid, message)
    assert result.status_code == 409, result.text
    assert calls == [mutation] and len(usage(app)) == 1
    as_user(client, "alice")
    assert len(client.get(f"/api/threads/{tid}").json()["messages"]) == 1
    with transaction(app.state.engine) as db:
        assert [r.status for r in db.scalars(select(AIRequest))] == ["failed"]


def test_codex_chat_question_identity_and_reader_acl_reject_before_spending(codex_chat, monkeypatch):
    client, app, _, doc, tid, calls = codex_chat
    message = question(client, tid)
    share(client, doc)
    monkeypatch.setenv("HWP_CODEX_ALLOWED_USERS", "alice,bob")
    as_user(client, "bob")
    assert submit(client, tid, message).status_code == 404
    own_message = question(client, tid)
    as_user(client, "alice")
    share(client, doc, "reader")
    as_user(client, "bob")
    assert submit(client, tid, own_message).status_code == 403
    assert not calls and usage(app) == []


def test_codex_chat_freezes_history_through_requested_question(codex_chat):
    client, _, _, _, tid, calls = codex_chat
    original = question(client, tid, "이 요청의 원래 질문")
    question(client, tid, "뒤에 작성한 다른 질문은 이 응답의 대상이 아님")
    assert submit(client, tid, original).status_code == 200
    assert calls[0][1][-1]["id"] == original
    assert all("뒤에 작성한 다른 질문" not in item["content"] for item in calls[0][1])


def test_explicit_http_chat_never_uses_codex_or_its_daily_cap(codex_chat, monkeypatch):
    from app import collaboration
    client, app, _, _, tid, calls = codex_chat
    monkeypatch.setenv("HWP_CHAT_PROVIDER", "llm")
    monkeypatch.setenv("HWP_CODEX_USER", "nobody")

    class ExplicitHTTP:
        async def chat(self, ctx, messages):
            return "명시적으로 선택한 합성 HTTP 응답"

    monkeypatch.setattr(collaboration, "OpenAICompatible", ExplicitHTTP)
    message = question(client, tid)
    assert submit(client, tid, message).json()["content"] == "명시적으로 선택한 합성 HTTP 응답"
    assert calls == [] and usage(app) == []


def test_concurrent_codex_chat_same_question_never_invokes_model_twice(codex_chat, monkeypatch):
    import asyncio
    from app.codex_chat_review import CodexBasicProvider
    client, app, _, _, tid, calls = codex_chat
    message = question(client, tid)
    entered, release = Event(), Event()

    async def delayed(self, *args, **kwargs):
        calls.append("entered")
        entered.set()
        assert await asyncio.to_thread(release.wait, 5)
        return "합성 동시성 응답"

    monkeypatch.setattr(CodexBasicProvider, "chat", delayed)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(submit, client, tid, message)
        try:
            assert entered.wait(2)
            second = pool.submit(submit, client, tid, message).result(timeout=3)
            assert second.status_code in (409, 429), second.text
            assert calls == ["entered"] and len(usage(app)) == 1
        finally:
            release.set()
        result = first.result(timeout=5)
    assert result.status_code == 200, result.text
    assert submit(client, tid, message).json() == result.json()
    assert calls == ["entered"] and len(usage(app)) == 1
