"""Real HTTP/DB admission; synchronous synthetic workers, zero model calls."""
from types import SimpleNamespace
from threading import BoundedSemaphore
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app import codex_runner, codex_runtime, document_assistant, rewrite
from app.db import ConversationTurn, RewriteTask, transaction
from test_collaboration import secured
from test_document_assistant import assistant


class InlineThread:
    def __init__(self, *, target, args, daemon):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def admission(assistant, monkeypatch):
    c, app, doc, thread, lease, original, _, _ = assistant
    for name in ("HWP_REWRITE_PROVIDER", "HWP_DOCUMENT_ASSISTANT_PROVIDER"):
        monkeypatch.setenv(name, "codex")
    monkeypatch.setenv("HWP_CODEX_EXECUTABLE", "synthetic-codex-never-executed")
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    monkeypatch.setenv("HWP_CODEX_CHECK_AUTH", "0")
    monkeypatch.setenv("HWP_CODEX_DAILY_REQUEST_LIMIT", "1")
    monkeypatch.delenv("HWP_CODEX_ALLOWED_USERS", raising=False)
    app.state.rewrite_capacity = BoundedSemaphore(1)
    calls = []
    monkeypatch.setattr(codex_runner, "run_codex", lambda *args: calls.append("rewrite"))
    monkeypatch.setattr(document_assistant, "run_turn", lambda *args: calls.append("assistant"))
    monkeypatch.setattr(rewrite, "Thread", InlineThread)
    monkeypatch.setattr(document_assistant, "Thread", InlineThread)

    def submit(kind, identifier):
        if kind == "rewrite":
            body = {"id": identifier, "base_version": doc["current_version"], "expected_revision": 0,
                    "selection": original["selection"], "instruction": "합성 문장 다듬기"}
            path = f"/api/threads/{thread}/rewrite-tasks"
        else:
            body = {**original, "id": identifier}
            path = f"/api/threads/{thread}/assistant-turns"
        return c.post(path, json=body, headers={"X-Edit-Session": lease})

    def usage():
        with transaction(app.state.engine) as db:
            return [(row.request_key, row.user_id) for row in db.scalars(select(codex_runtime.CodexUsage))]

    return app, submit, usage, calls


@pytest.mark.parametrize("first", ["rewrite", "assistant"])
def test_one_shared_daily_cap_and_idempotent_retry_across_both_flows(admission, first):
    app, submit, usage, calls = admission
    identifier = str(uuid.uuid4())
    result = submit(first, identifier)
    assert result.status_code == 200, result.text
    assert submit(first, identifier).status_code == 200
    assert calls == [first]
    second = "assistant" if first == "rewrite" else "rewrite"
    # Even the same UUID in a different endpoint is a new generation request,
    # not a way to obtain another uncharged call.
    denied = submit(second, identifier)
    assert denied.status_code == 429 and int(denied.headers["Retry-After"]) > 0
    assert usage() == [(first + ":" + identifier, "alice")]
    with transaction(app.state.engine) as db:
        assert db.get(ConversationTurn if second == "assistant" else RewriteTask, identifier) is None
    assert submit(first, identifier).status_code == 200  # cap does not block readback
    assert calls == [first]


@pytest.mark.parametrize("kind", ["rewrite", "assistant"])
@pytest.mark.parametrize("reason", ["permission", "login", "capacity"])
def test_rejected_admission_never_spends_a_daily_slot(admission, monkeypatch, kind, reason):
    app, submit, usage, calls = admission
    identifier = str(uuid.uuid4())
    if reason == "permission":
        monkeypatch.setenv("HWP_CODEX_USER", "different-owner")
    elif reason == "login":
        monkeypatch.setattr(codex_runtime, "readiness", lambda _: {
            "configured": False, "reason_code": "codex_login_required", "message": "synthetic login required"})
    else:
        assert app.state.rewrite_capacity.acquire(blocking=False)
    try:
        response = submit(kind, identifier)
        assert response.status_code == {"permission": 403, "login": 503, "capacity": 429}[reason], response.text
        assert usage() == [] and calls == []
        with transaction(app.state.engine) as db:
            assert db.get(ConversationTurn if kind == "assistant" else RewriteTask, identifier) is None
    finally:
        if reason == "capacity":
            app.state.rewrite_capacity.release()
    if reason == "capacity":
        assert submit(kind, identifier).status_code == 200
        assert usage() == [(kind + ":" + identifier, "alice")]


@pytest.mark.parametrize("kind", ["rewrite", "assistant"])
def test_dispatch_failure_consumes_one_slot_without_retrying_the_model(admission, monkeypatch, kind):
    _, submit, usage, calls = admission
    class FailedThread(InlineThread):
        def start(self):
            raise RuntimeError("synthetic worker start failure")
    monkeypatch.setattr(rewrite if kind == "rewrite" else document_assistant, "Thread", FailedThread)
    identifier = str(uuid.uuid4())
    response = submit(kind, identifier)
    assert response.status_code == 200 and response.json()["status"] == "failed", response.text
    assert submit(kind, identifier).json()["status"] == "failed"
    assert usage() == [(kind + ":" + identifier, "alice")]
    assert calls == []
    assert submit(kind, str(uuid.uuid4())).status_code == 429


def test_daily_limit_rolls_over_at_exact_utc_boundary_not_server_restart(secured, monkeypatch):
    _, app, _, _ = secured
    monkeypatch.setenv("HWP_CODEX_DAILY_REQUEST_LIMIT", "1")
    now = [86400 * 20000 - 1.0]
    monkeypatch.setattr(codex_runtime, "time", SimpleNamespace(time=lambda: now[0]))
    with transaction(app.state.engine) as db:
        codex_runtime.reserve_usage(db, "yesterday", "alice")
    with transaction(app.state.engine) as db, pytest.raises(HTTPException) as failure:
        codex_runtime.reserve_usage(db, "too-early", "bob")
    assert failure.value.status_code == 429 and failure.value.headers["Retry-After"] == "1"
    now[0] += 1
    with transaction(app.state.engine) as db:
        codex_runtime.reserve_usage(db, "today", "bob")
    with transaction(app.state.engine) as db:
        assert len(list(db.scalars(select(codex_runtime.CodexUsage)))) == 2
        with pytest.raises(HTTPException) as failure:
            codex_runtime.reserve_usage(db, "too-many-today", "alice")
        assert failure.value.status_code == 429
