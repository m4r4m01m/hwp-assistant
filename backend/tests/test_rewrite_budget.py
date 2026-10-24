"""Real shared user/global admission with synthetic inference and authenticated APIs."""
import json
import threading
import uuid
from threading import BoundedSemaphore

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db import RewriteTask, transaction
from app.llm import OpenAICompatible
from test_collaboration import secured
from test_local_rewrite import local_jobs, submit, execute, completion


def fresh(job):
    return {**job[5], "id": str(uuid.uuid4())}


def test_duplicate_confirmation_bypasses_active_and_minute_budget(local_jobs):
    _, app, _, _, _, original, queued, calls = local_jobs
    app.state.ai_budget.limit = 1
    assert submit(local_jobs).status_code == 200
    assert app.state.ai_budget.active == {"alice"}
    assert submit(local_jobs).status_code == 200 and len(queued) == 1
    next_request = fresh(local_jobs)
    assert submit(local_jobs, next_request).status_code == 429
    assert len(app.state.ai_budget.recent["alice"]) == 1
    with transaction(app.state.engine) as db:
        assert db.get(RewriteTask, next_request["id"]) is None
    assert execute(local_jobs)["status"] == "ready"
    assert not app.state.ai_budget.active and len(calls) == 1
    assert submit(local_jobs).json()["id"] == original["id"]
    assert submit(local_jobs, next_request).status_code == 429
    assert len(queued) == 1 and len(app.state.ai_budget.recent["alice"]) == 1


def test_global_capacity_rejection_does_not_spend_user_quota(local_jobs):
    _, app, _, _, _, body, queued, _ = local_jobs
    app.state.rewrite_capacity = BoundedSemaphore(0)
    assert submit(local_jobs).status_code == 429
    assert not app.state.ai_budget.active and not app.state.ai_budget.recent.get("alice")
    assert not queued
    with transaction(app.state.engine) as db:
        assert db.get(RewriteTask, body["id"]) is None
    app.state.rewrite_capacity = BoundedSemaphore(1)
    assert submit(local_jobs).status_code == 200
    execute(local_jobs)
    assert not app.state.ai_budget.active


def test_rewrite_shares_user_budget_with_other_http_ai_routes(local_jobs):
    c, app, doc, _, lease, _, queued, calls = local_jobs
    with app.state.ai_budget.reserve("alice"):
        assert submit(local_jobs).status_code == 429 and not queued
        assert len(app.state.ai_budget.recent["alice"]) == 1
    assert submit(local_jobs).status_code == 200
    review = c.post("/api/ai/review", json={"document_id": doc["id"],
        "base_version": doc["current_version"], "original": "원문", "revision": 0,
        "target": local_jobs[5]["selection"]["selection"]["target"]}, headers={"X-Edit-Session": lease})
    assert review.status_code == 429 and not calls
    assert len(app.state.ai_budget.recent["alice"]) == 2
    execute(local_jobs)


def test_cancellation_does_not_release_user_until_captured_worker_exits(local_jobs):
    c, app, _, _, _, body, queued, calls = local_jobs
    assert submit(local_jobs).status_code == 200
    assert c.post(f"/api/rewrite-tasks/{body['id']}/cancel").json()["status"] == "cancelled"
    assert app.state.ai_budget.active == {"alice"}
    next_request = fresh(local_jobs)
    assert submit(local_jobs, next_request).status_code == 429
    assert submit(local_jobs).json()["status"] == "cancelled" and len(queued) == 1
    execute(local_jobs)
    assert not app.state.ai_budget.active and not calls
    assert submit(local_jobs, next_request).status_code == 200
    execute(local_jobs, 1)
    assert not app.state.ai_budget.active


def test_ready_result_keeps_user_admission_until_worker_cleanup(local_jobs, monkeypatch):
    from app.local_rewrite import run_local_rewrite
    c, app, _, tid, _, _, queued, _ = local_jobs
    ready, release = threading.Event(), threading.Event()
    errors = []
    def held(*args):
        run_local_rewrite(*args)
        ready.set()
        assert release.wait(5)
    monkeypatch.setattr("app.local_rewrite.run_local_rewrite", held)
    assert submit(local_jobs).status_code == 200
    def work():
        try:
            queued[0][0](*queued[0][1])
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=work)
    worker.start()
    try:
        assert ready.wait(5)
        assert c.get(f"/api/threads/{tid}/rewrite-tasks").json()[0]["status"] == "ready"
        assert app.state.ai_budget.active == {"alice"}
        assert submit(local_jobs).json()["status"] == "ready"
        assert submit(local_jobs, fresh(local_jobs)).status_code == 429
    finally:
        release.set()
        worker.join(5)
    assert not errors and not worker.is_alive() and not app.state.ai_budget.active
    assert submit(local_jobs, fresh(local_jobs)).status_code == 200
    execute(local_jobs, 1)


def test_cancelled_live_http_call_keeps_user_admission_until_response_cleanup(local_jobs, monkeypatch):
    c, app, _, tid, _, body, queued, _ = local_jobs
    entered, release = threading.Event(), threading.Event()
    errors = []
    async def held_request(*args):
        entered.set()
        assert release.wait(5)
        return completion()
    monkeypatch.setattr(OpenAICompatible, "request", held_request)
    assert submit(local_jobs).status_code == 200
    def work():
        try:
            queued[0][0](*queued[0][1])
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=work)
    worker.start()
    try:
        assert entered.wait(5)
        assert c.post(f"/api/rewrite-tasks/{body['id']}/cancel").status_code == 200
        assert app.state.ai_budget.active == {"alice"}
        assert submit(local_jobs, fresh(local_jobs)).status_code == 429
    finally:
        release.set()
        worker.join(5)
    assert not errors and not worker.is_alive() and not app.state.ai_budget.active
    task = c.get(f"/api/threads/{tid}/rewrite-tasks").json()[0]
    assert task["status"] == "cancelled" and not task["candidates"]


@pytest.mark.parametrize("failure", ["commit", "dispatch", "worker"])
def test_failed_dispatch_or_worker_frees_both_admissions(local_jobs, monkeypatch, failure):
    _, app, _, _, _, body, queued, calls = local_jobs
    def fail_commit(session):
        if any(isinstance(row, RewriteTask) for row in session.new):
            raise RuntimeError("synthetic commit failure")
    class BrokenThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            raise RuntimeError("synthetic dispatch failure")
    def broken_worker(*args):
        raise RuntimeError("synthetic worker failure")
    if failure == "commit":
        event.listen(Session, "before_commit", fail_commit)
    elif failure == "dispatch":
        monkeypatch.setattr("app.rewrite.Thread", BrokenThread)
    else:
        monkeypatch.setattr("app.local_rewrite.run_local_rewrite", broken_worker)
    try:
        response = submit(local_jobs)
        assert response.status_code == (500 if failure == "commit" else 200)
        if failure == "dispatch":
            assert response.json()["status"] == "failed"
            assert submit(local_jobs).json()["status"] == "failed"
        if failure == "worker":
            with pytest.raises(RuntimeError, match="synthetic worker failure"):
                execute(local_jobs)
    finally:
        if failure == "commit":
            event.remove(Session, "before_commit", fail_commit)
    assert not app.state.ai_budget.active and not calls
    acquired = []
    try:
        for _ in range(2):
            acquired.append(app.state.rewrite_capacity.acquire(blocking=False))
        assert acquired == [True, True]
        assert not app.state.rewrite_capacity.acquire(blocking=False)
    finally:
        for owned in acquired:
            if owned:
                app.state.rewrite_capacity.release()


def test_codex_legacy_path_does_not_use_http_user_budget(local_jobs, monkeypatch):
    _, app, _, _, _, _, queued, _ = local_jobs
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "codex")
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    calls = []
    monkeypatch.setattr("app.codex_runner.run_codex", lambda *args: calls.append(args))
    with app.state.ai_budget.reserve("alice"):
        assert submit(local_jobs).status_code == 200
        queued[0][0](*queued[0][1])
        assert app.state.ai_budget.active == {"alice"}
        assert len(app.state.ai_budget.recent["alice"]) == 1 and len(calls) == 1
    assert not app.state.ai_budget.active


def test_rewrite_metadata_keeps_backend_discriminator_and_safe_provider(local_jobs, monkeypatch):
    c, _, _, tid, _, _, _, _ = local_jobs
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.setenv("LLM_ALLOW_EXTERNAL", "true")
    info = c.get("/api/rewrite-provider").json()
    assert info["provider"] == "llm" and info["llm_provider"] == "openrouter"
    assert info["external"] is True and info["endpoint_scope"] == "external"
    assert submit(local_jobs).status_code == 200
    task = execute(local_jobs)
    metadata = task["execution"]
    assert metadata["provider"] == "llm" and metadata["llm_provider"] == "openrouter"
    assert metadata["external"] is True and "secret-must-not-appear" not in json.dumps(task)
    monkeypatch.setenv("HWP_LLM_ALLOWED_USERS", "bob")
    denied = c.get("/api/rewrite-provider").json()
    assert denied["allowed"] is False and "Codex" not in denied["reason"]
