"""Controlled ordering: synthetic providers/CLI, real DB and authenticated APIs."""
import json
import subprocess
import threading
import time
import uuid
from contextlib import nullcontext

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.orm import Session
from app.db import Access, Document, LeaseOwner, RewriteCandidate, RewriteTask, transaction
from app.llm import OpenAICompatible
from app.rewrite_jobs import fail_task, lock_task, require_current
from test_collaboration import secured, share, as_user
from test_local_rewrite import local_jobs, submit, execute, completion
from test_rewrite import job, ready, approve, output_and_receipt
from test_collaboration import patch
from test_storage import package


@pytest.fixture(autouse=True)
def explicit_test_capacity(monkeypatch):
    # Proposed policy, deliberately configured here, not a measured production capacity.
    monkeypatch.setenv("HWP_REWRITE_MAX_ACTIVE", "2")


def history(job):
    return job[0].get(f"/api/threads/{job[3]}/rewrite-tasks").json()


@pytest.fixture
def isolate_global_capacity(local_jobs, monkeypatch):
    # These cases independently exercise physical global slots with two workers
    # for one fixture user. Real shared per-user admission is covered by
    # test_rewrite_budget.py, including its interaction with these global slots.
    monkeypatch.setattr(local_jobs[1].state.ai_budget, "reserve", lambda _user: nullcontext())


@pytest.mark.usefixtures("isolate_global_capacity")
def test_cancel_and_reissue_keeps_physical_execution_capacity_until_worker_returns(local_jobs):
    c, _, _, _, _, body, queued, _ = local_jobs
    for _ in range(2):
        assert submit(local_jobs).status_code == 200
        assert c.post(f"/api/rewrite-tasks/{body['id']}/cancel").status_code == 200
        body = {**body, "id": str(uuid.uuid4())}
        local_jobs = (*local_jobs[:5], body, *local_jobs[6:])
    # Logical cancellation must not let an unlimited number of still-live workers accumulate.
    rejected = submit(local_jobs)
    assert rejected.status_code == 429, rejected.text
    assert len(queued) == 2 and len(history(local_jobs)) == 2
    target, args = queued[0]
    target(*args)  # Cancelled before transport: returns and releases exactly one slot.
    assert submit(local_jobs).status_code == 200
    assert len(queued) == 3
    for target, args in queued[1:]:
        target(*args)


@pytest.mark.parametrize("setting", ["model", "endpoint"])
def test_codex_dispatch_freezes_model_and_endpoint_at_task_acceptance(local_jobs, tmp_path, monkeypatch, setting):
    c, _, _, _, _, _, queued, _ = local_jobs
    config = tmp_path / "codex-fixture"
    config.mkdir()
    (config / "config.toml").write_text('model = "accepted-model"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(config))
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "codex")
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    monkeypatch.setenv("HWP_CODEX_EXECUTABLE", "synthetic-cli")
    monkeypatch.setenv("HWP_CODEX_API_URL", "http://127.0.0.1:10001")
    calls = []

    def process(args, **kwargs):
        calls.append((args, kwargs["env"]["HWP_API_URL"]))
        if args[1:3] == ["login", "status"]:
            return subprocess.CompletedProcess(args, 0, "Logged in using ChatGPT", "")
        return subprocess.CompletedProcess(args, 1, "", "synthetic stop, no model call")

    monkeypatch.setattr("app.codex_runner.run_process", process)
    assert submit(local_jobs).status_code == 200
    (config / "config.toml").write_text('model = "later-model"\n', encoding="utf-8")
    monkeypatch.setenv("HWP_CODEX_API_URL", "http://127.0.0.1:10002")
    task = execute(local_jobs)
    if setting == "model":
        assert task["execution"]["model"] == "accepted-model"
        assert calls[1][0][calls[1][0].index("--model") + 1] == "accepted-model"
    else:
        assert all(endpoint == "http://127.0.0.1:10001" for _, endpoint in calls)
    assert submit(local_jobs).status_code == 200
    assert len(queued) == 1 and len(calls) == 2
    assert submit(local_jobs, {**local_jobs[5], "instruction": "different"}).status_code == 409
    assert submit(local_jobs, {**local_jobs[5], "id": str(uuid.uuid4())}).status_code == 200
    target, args = queued[1]
    target(*args)
    if setting == "model":
        assert calls[-1][0][calls[-1][0].index("--model") + 1] == "later-model"
    else:
        assert all(endpoint == "http://127.0.0.1:10002" for _, endpoint in calls[2:])
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "llm")
    assert submit(local_jobs).status_code == 200 and len(queued) == 2


def test_publication_lock_refreshes_preloaded_task_and_document(local_jobs):
    _, app, doc, _, _, body, _, _ = local_jobs
    assert submit(local_jobs).status_code == 200
    with Session(app.state.engine) as stale:
        cached_doc = stale.get(Document, doc["id"])
        cached_task = stale.get(RewriteTask, body["id"])
        with transaction(app.state.engine) as current:
            current.get(Document, doc["id"]).lease_token = "replacement-lease"
        task, locked = lock_task(stale, body["id"])
        assert locked is cached_doc and task is cached_task
        assert locked.lease_token == "replacement-lease"
        with pytest.raises(HTTPException) as error:
            require_current(stale, task, locked)
        assert error.value.status_code == 409
        with transaction(app.state.engine) as current:
            current.get(RewriteTask, body["id"]).status = "cancelled"
        assert lock_task(stale, body["id"])[0].status == "cancelled"


@pytest.mark.parametrize("preloaded", [False, True])
@pytest.mark.parametrize("changed", ["lease", "access", "owner"])
def test_publication_checks_latest_authority_in_clean_sessions(local_jobs, preloaded, changed):
    _, app, doc, _, _, body, _, _ = local_jobs
    assert submit(local_jobs).status_code == 200
    with Session(app.state.engine) as session:
        held = []
        if preloaded:
            held = [session.get(Document, doc["id"]), session.get(RewriteTask, body["id"]),
                    session.get(Access, (doc["id"], "alice")), session.get(LeaseOwner, doc["id"])]
        with transaction(app.state.engine) as other:
            if changed == "lease":
                other.get(Document, doc["id"]).lease_token = "changed"
            elif changed == "access":
                other.delete(other.get(Access, (doc["id"], "alice")))
            else:
                other.get(LeaseOwner, doc["id"]).user_id = "bob"
        task, locked = lock_task(session, body["id"])
        with pytest.raises(HTTPException):
            require_current(session, task, locked)
        assert held or not preloaded


def test_lock_helper_refuses_dirty_session_without_flushing_or_discarding(local_jobs):
    _, app, doc, _, _, body, _, _ = local_jobs
    assert submit(local_jobs).status_code == 200
    with Session(app.state.engine) as session:
        document = session.get(Document, doc["id"])
        original = document.name
        document.name = "pending caller edit"
        with pytest.raises(RuntimeError, match="clean Session"):
            lock_task(session, body["id"])
        assert document.name == "pending caller edit" and document in session.dirty
        with Session(app.state.engine) as other:
            assert other.get(Document, doc["id"]).name == original


@pytest.mark.parametrize("change", ["cancel", "expire", "release_reissue", "renew", "unrelated_acl", "revoke_regrant", "downgrade_regrant"])
def test_event_ordered_authority_transitions_do_not_revive_pending_work(local_jobs, monkeypatch, change):
    c, app, doc, tid, lease, body, queued, _ = local_jobs
    if change in ("revoke_regrant", "downgrade_regrant"):
        share(c, doc, "editor")
        assert c.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": lease}).status_code == 200
        as_user(c, "bob")
        lease = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
        local_jobs = (*local_jobs[:4], lease, *local_jobs[5:])
    entered, release = threading.Event(), threading.Event()
    failures = []

    async def transport(*_):
        entered.set()
        assert release.wait(5), "bounded model response barrier"
        return completion()

    monkeypatch.setattr(OpenAICompatible, "request", transport)
    assert submit(local_jobs).status_code == 200
    def work():
        try:
            queued[0][0](*queued[0][1])
        except BaseException as exc:
            failures.append(exc)
    worker = threading.Thread(target=work)
    worker.start()
    try:
        assert entered.wait(5)
        session_url = f"/api/documents/{doc['id']}/session"
        if change == "cancel":
            assert c.post(f"/api/rewrite-tasks/{body['id']}/cancel").status_code == 200
        elif change == "expire":
            with transaction(app.state.engine) as db:
                db.get(RewriteTask, body["id"]).created_at = time.time() - 601
            assert history(local_jobs)[0]["status"] == "expired"
        elif change == "release_reissue":
            assert c.delete(session_url, headers={"X-Edit-Session": lease}).status_code == 200
            assert c.post(session_url).status_code == 200
        elif change == "renew":
            assert c.put(session_url, headers={"X-Edit-Session": lease}).status_code == 200
        elif change == "unrelated_acl":
            assert c.put(f"/api/documents/{doc['id']}/access", json={"user_id": "eve", "role": "reader"}).status_code == 200
            assert c.put(session_url, headers={"X-Edit-Session": lease}).status_code == 200
        else:
            as_user(c, "alice")
            if change == "revoke_regrant":
                assert c.delete(f"/api/documents/{doc['id']}/access/bob").status_code == 200
            else:
                share(c, doc, "reader")
            share(c, doc, "editor")
            as_user(c, "bob")
            assert c.post(session_url).status_code == 200
        release.set()
        worker.join(5)
        assert not worker.is_alive() and not failures
        task = history(local_jobs)[0]
        valid = change in ("renew", "unrelated_acl")
        assert task["status"] == ("ready" if valid else "cancelled" if change == "cancel" else "failed")
        assert len(task["candidates"]) == (2 if valid else 0)
        assert task["application"] is None
        assert len(c.get(f"/api/documents/{doc['id']}/versions").json()) == 1
    finally:
        release.set()
        worker.join(5)


def test_published_success_survives_late_cancel_and_failure(local_jobs):
    c, app, _, _, _, body, _, _ = local_jobs
    assert submit(local_jobs).status_code == 200
    before = execute(local_jobs)
    assert before["status"] == "ready"
    assert c.post(f"/api/rewrite-tasks/{body['id']}/cancel").status_code == 409
    fail_task(app.state.engine, body["id"], "late synthetic exception")
    assert history(local_jobs)[0] == before


@pytest.mark.usefixtures("isolate_global_capacity")
def test_event_held_workers_keep_slots_after_logical_cancel(local_jobs, monkeypatch):
    c, _, _, _, _, body, queued, _ = local_jobs
    entered = [threading.Event(), threading.Event()]
    releases = [threading.Event(), threading.Event()]
    workers, errors = [], []
    count_lock = threading.Lock()
    calls = []
    async def transport(*_):
        with count_lock:
            index = len(calls)
            calls.append(index)
        entered[index].set()
        assert releases[index].wait(5)
        return completion()
    monkeypatch.setattr(OpenAICompatible, "request", transport)
    def work(index):
        try:
            queued[index][0](*queued[index][1])
        except BaseException as exc:
            errors.append(exc)
    try:
        for index in range(2):
            request = {**body, "id": str(uuid.uuid4())}
            assert submit(local_jobs, request).status_code == 200
            worker = threading.Thread(target=work, args=(index,)); workers.append(worker); worker.start()
            assert entered[index].wait(5)
            assert c.post(f"/api/rewrite-tasks/{request['id']}/cancel").status_code == 200
            assert submit(local_jobs, request).json()["status"] == "cancelled"
        fresh = {**body, "id": str(uuid.uuid4())}
        assert submit(local_jobs, fresh).status_code == 429 and len(queued) == 2
        releases[0].set(); workers[0].join(5)
        assert not workers[0].is_alive() and not errors
        assert submit(local_jobs, fresh).status_code == 200
        assert submit(local_jobs, {**body, "id": str(uuid.uuid4())}).status_code == 429
        assert c.post(f"/api/rewrite-tasks/{fresh['id']}/cancel").status_code == 200
        queued[2][0](*queued[2][1])
    finally:
        for release in releases: release.set()
        for worker in workers: worker.join(5)
    assert not errors and all(not worker.is_alive() for worker in workers)


@pytest.mark.parametrize("failure", ["task_commit", "dispatch", "parse", "publication", "success"])
@pytest.mark.usefixtures("isolate_global_capacity")
def test_slot_released_once_after_all_exit_paths(local_jobs, monkeypatch, failure):
    _, _, _, _, _, body, queued, _ = local_jobs
    fired = False
    def fail_commit(session):
        nonlocal fired
        kind = RewriteTask if failure == "task_commit" else RewriteCandidate
        if not fired and any(isinstance(row, kind) for row in session.new):
            fired = True
            raise RuntimeError("synthetic commit failure")
    class BrokenThread:
        def __init__(self, **_): pass
        def start(self): raise OSError("synthetic dispatch failure")
    original_thread = __import__("app.rewrite", fromlist=["Thread"]).Thread
    original_request = OpenAICompatible.request
    if failure in ("task_commit", "publication"):
        event.listen(Session, "before_commit", fail_commit)
    if failure == "dispatch": monkeypatch.setattr("app.rewrite.Thread", BrokenThread)
    if failure == "parse":
        async def invalid(*_): return {"choices": []}
        monkeypatch.setattr(OpenAICompatible, "request", invalid)
    try:
        result = submit(local_jobs)
        assert result.status_code == (500 if failure == "task_commit" else 200)
        if failure not in ("task_commit", "dispatch"):
            task = execute(local_jobs)
            assert task["status"] == ("ready" if failure == "success" else "failed")
    finally:
        if failure in ("task_commit", "publication"): event.remove(Session, "before_commit", fail_commit)
        monkeypatch.setattr("app.rewrite.Thread", original_thread)
        monkeypatch.setattr(OpenAICompatible, "request", original_request)
    start = len(queued)
    for _ in range(2):
        assert submit(local_jobs, {**body, "id": str(uuid.uuid4())}).status_code == 200
    assert submit(local_jobs, {**body, "id": str(uuid.uuid4())}).status_code == 429
    for target, args in queued[start:]: target(*args)


def test_codex_and_llm_share_capacity_until_ready_worker_cleanup_finishes(local_jobs, monkeypatch):
    from app.local_rewrite import run_local_rewrite
    _, _, _, _, _, body, queued, _ = local_jobs
    ready_event, release = threading.Event(), threading.Event()
    errors = []
    def held(*args):
        run_local_rewrite(*args)
        ready_event.set()
        assert release.wait(5)
    monkeypatch.setattr("app.local_rewrite.run_local_rewrite", held)
    assert submit(local_jobs).status_code == 200
    def work():
        try: queued[0][0](*queued[0][1])
        except BaseException as exc: errors.append(exc)
    worker = threading.Thread(target=work); worker.start()
    try:
        assert ready_event.wait(5)
        assert history(local_jobs)[0]["status"] == "ready"
        monkeypatch.setenv("HWP_REWRITE_PROVIDER", "codex")
        monkeypatch.setenv("HWP_CODEX_USER", "alice")
        assert submit(local_jobs, {**body, "id": str(uuid.uuid4())}).status_code == 200
        assert submit(local_jobs).status_code == 200  # Existing ID needs no free slot.
        assert submit(local_jobs, {**body, "id": str(uuid.uuid4())}).status_code == 429
        release.set(); worker.join(5)
        assert not worker.is_alive() and not errors
        assert submit(local_jobs, {**body, "id": str(uuid.uuid4())}).status_code == 200
        assert len(queued) == 3
    finally:
        release.set(); worker.join(5)
        # Cancel before running captured Codex workers: never invoke a real CLI.
        for target, args in queued[1:]:
            assert local_jobs[0].post(f"/api/rewrite-tasks/{args[1]}/cancel").status_code == 200
            target(*args)


@pytest.mark.usefixtures("isolate_global_capacity")
def test_unexpected_runner_exception_still_releases_slot(local_jobs, monkeypatch):
    def broken(*_): raise RuntimeError("synthetic runner failure")
    monkeypatch.setattr("app.local_rewrite.run_local_rewrite", broken)
    assert submit(local_jobs).status_code == 200
    with pytest.raises(RuntimeError, match="synthetic runner"):
        execute(local_jobs)
    for _ in range(2):
        assert submit(local_jobs, {**local_jobs[5], "id": str(uuid.uuid4())}).status_code == 200
    assert submit(local_jobs, {**local_jobs[5], "id": str(uuid.uuid4())}).status_code == 429
    for target, args in local_jobs[6][1:]:
        with pytest.raises(RuntimeError): target(*args)


def test_default_codex_unknown_model_stays_unknown_and_specs_are_immutable(local_jobs, tmp_path, monkeypatch):
    from dataclasses import FrozenInstanceError
    from app.codex_runner import command
    monkeypatch.delenv("HWP_REWRITE_PROVIDER")
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert local_jobs[0].get("/api/rewrite-provider").json()["provider"] == "codex"
    assert submit(local_jobs).status_code == 200
    spec = local_jobs[6][0][1][3]
    assert spec.model is None
    with pytest.raises(FrozenInstanceError): spec.model = "changed"
    (tmp_path / "config.toml").write_text('model = "later-model"\n', encoding="utf-8")
    assert "--model" not in command("synthetic", tmp_path, spec)
    task = history(local_jobs)[0]
    assert task["execution"]["model"] is None
    assert task["execution"]["model_source"] == "CLI default; ID not confirmed"
    assert task["execution"]["context_used"] is False
    assert "_endpoint" not in task["execution"]
    assert submit(local_jobs).status_code == 200 and len(local_jobs[6]) == 1


def test_local_model_endpoint_key_timeout_fixed_at_acceptance(local_jobs, monkeypatch):
    from dataclasses import FrozenInstanceError
    assert submit(local_jobs).status_code == 200
    spec = local_jobs[6][0][1][2]
    accepted = (spec.base, spec.model, spec.key, spec.timeout)
    for name, value in {"LLM_BASE_URL": "http://127.0.0.1:9999/v1", "LLM_MODEL": "later",
                        "LLM_API_KEY": "later-synthetic-key", "LLM_TIMEOUT_SECONDS": "7"}.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(FrozenInstanceError): spec.base = "changed"
    calls = []
    async def observe(self, *_):
        calls.append((self.base, self.model, self.key, self.timeout))
        return completion()
    monkeypatch.setattr(OpenAICompatible, "request", observe)
    execute(local_jobs)
    assert calls == [accepted]
    assert submit(local_jobs).status_code == 200 and len(calls) == 1
    assert submit(local_jobs, {**local_jobs[5], "id": str(uuid.uuid4())}).status_code == 200
    local_jobs[6][1][0](*local_jobs[6][1][1])
    assert calls[1] == ("http://127.0.0.1:9999/v1", "later", "later-synthetic-key", 7)
    assert all(secret not in json.dumps(history(local_jobs)) for secret in (accepted[2], "later-synthetic-key"))


@pytest.mark.parametrize("value", ["0", "65", "2.5", "bad"])
def test_invalid_capacity_fails_configuration(value, monkeypatch):
    from app.rewrite_capacity import configured_capacity
    monkeypatch.setenv("HWP_REWRITE_MAX_ACTIVE", value)
    with pytest.raises(ValueError, match="HWP_REWRITE_MAX_ACTIVE"): configured_capacity()


@pytest.mark.parametrize("change,status", [("revoke", 404), ("downgrade", 403)])
def test_committed_save_retries_recheck_current_access_without_rollback(job, change, status):
    c, _, doc, tid, lease, body, _ = job
    candidate = ready(job)
    share(c, doc)
    assert c.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": lease}).status_code == 200
    as_user(c, "bob")
    lease = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    bob_job = (*job[:4], lease, *job[5:])
    application = approve(bob_job, candidate)
    data, receipt = output_and_receipt(bob_job, application)
    headers = {"X-Edit-Session": lease}
    receipt_path = f"/api/applications/{application}/receipt"
    assert c.post(receipt_path, json=receipt, headers=headers).status_code == 200
    save_path = f"/api/documents/{doc['id']}/versions?base_version={doc['current_version']}&cause=web-edit"
    headers.update({"Idempotency-Key": application, "X-Application-Id": application,
                    "Content-Type": "application/vnd.hwp.delta+json"})
    saved = c.post(save_path, json=patch(package(), data), headers=headers)
    assert saved.status_code == 200
    as_user(c, "alice")
    if change == "revoke":
        assert c.delete(f"/api/documents/{doc['id']}/access/bob").status_code == 200
    else: share(c, doc, "reader")
    before = c.get(f"/api/documents/{doc['id']}/applications").json()
    as_user(c, "bob")
    assert c.post(f"/api/threads/{tid}/rewrite-tasks", json=body, headers=headers).status_code == status
    assert c.post(f"/api/rewrite-tasks/{body['id']}/approve", json={"id": application, "candidate_id": candidate}, headers=headers).status_code == status
    assert c.post(receipt_path, json=receipt, headers=headers).status_code == status
    assert c.post(save_path, json=patch(package(), data), headers=headers).status_code == status
    as_user(c, "alice")
    assert c.get(f"/api/documents/{doc['id']}/applications").json() == before
    assert before[0]["status"] == "saved" and before[0]["saved_version"] == saved.json()["id"]
    assert len(c.get(f"/api/documents/{doc['id']}/versions").json()) == 2
    assert c.get(f"/api/documents/{doc['id']}/versions/{saved.json()['id']}/file").content == data
    assert c.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content == package()
