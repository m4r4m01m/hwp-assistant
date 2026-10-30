"""In-flight managed-account revocation with synthetic providers; no model calls."""
import copy
import json
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import document_assistant, reports, rewrite_jobs
from app.account_settings import AccountSetting
from app.accounts import DUMMY_HASH, UserAccount
from app.db import Access, Document, LeaseOwner, ReportTask, make_engine, transaction
from app.hwpx import digest
from app.schema import migrate
from test_reports import DRAFT


@pytest.fixture
def managed_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "1")
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "private")
    monkeypatch.setenv("HWP_CODEX_ALLOWED_USERS", "operator-only")
    engine = make_engine("sqlite:///" + str(tmp_path / "worker-revocation.db"))
    migrate(engine, {})
    with transaction(engine) as db:
        db.add(UserAccount(user_id="guest", name="합성 체험", password_hash=DUMMY_HASH,
            kind="guest", is_active=True, approval_status="approved", role="member",
            session_revision="synthetic-revision", created_at=1))
        db.add(AccountSetting(key="codex_account_access", value="approved_accounts",
            updated_by="synthetic-admin", updated_at=1))
        db.add(Document(id="synthetic-document", name="합성.hwpx", created_at=1,
            lease_token="synthetic-lease", lease_until=time.time() + 60))
        db.flush()
        db.add(Access(document_id="synthetic-document", user_id="guest", role="editor"))
        db.add(LeaseOwner(document_id="synthetic-document", user_id="guest"))
    try:
        yield engine
    finally:
        engine.dispose()


def revoke(engine, change):
    with transaction(engine) as db:
        if change == "disabled":
            db.get(UserAccount, "guest").is_active = False
        else:
            db.get(AccountSetting, "codex_account_access").value = "allowlist"


@pytest.mark.parametrize("change", ["disabled", "policy"])
@pytest.mark.parametrize("stage", ["active", "progress", "final"])
def test_report_never_publishes_after_account_or_codex_revocation(managed_worker, monkeypatch, change, stage):
    engine, task_id = managed_worker, str(uuid.uuid4())
    with transaction(engine) as db:
        db.add(ReportTask(id=task_id, owner_id="guest", input="{}", input_sha256="a" * 64,
            status="running", execution="{}", created_at=time.time()))
    calls = []

    def synthetic_provider(value, settings, progress, active):
        calls.append(True)
        assert active()
        revoke(engine, change)
        if stage == "active":
            assert active() is False
        elif stage == "progress":
            progress({"stage": "writing", "untrusted": "not-to-be-published"})
        # Even a provider ignoring cancellation cannot cross terminal publication.
        return copy.deepcopy(DRAFT), {"completed": True, "executor": "synthetic"}

    monkeypatch.setattr(reports, "generate_report", synthetic_provider)
    reports.run_report(engine, task_id, "guest", {"research": False}, SimpleNamespace(timeout=60))
    with Session(engine) as db:
        row = db.get(ReportTask, task_id)
        assert row.status == "failed" and row.draft is None and row.draft_sha256 is None
        execution = json.loads(row.execution)
        assert execution["completed"] is False
        assert execution["failure"]["code"] == "access_revoked"
        assert "not-to-be-published" not in row.execution
        assert "권한이 변경" in row.error
    assert calls == [True]


@pytest.mark.parametrize("change", ["disabled", "policy"])
def test_revoked_queued_report_never_starts_provider(managed_worker, monkeypatch, change):
    engine, task_id = managed_worker, str(uuid.uuid4())
    with transaction(engine) as db:
        db.add(ReportTask(id=task_id, owner_id="guest", input="{}", input_sha256="a" * 64,
            status="running", execution="{}", created_at=time.time()))
    revoke(engine, change)
    calls = []
    monkeypatch.setattr(reports, "generate_report", lambda *args: calls.append(True))
    reports.run_report(engine, task_id, "guest", {"research": False}, SimpleNamespace(timeout=60))
    assert calls == []
    with Session(engine) as db:
        assert db.get(ReportTask, task_id).status == "failed"


def worker_row(provider):
    return SimpleNamespace(requested_by="guest", base_version=None, status="running",
        execution=json.dumps({"provider": provider, "_lease_sha256": digest(b"synthetic-lease")}), created_at=time.time())


@pytest.mark.parametrize("check", [rewrite_jobs.require_current, document_assistant.require_current], ids=["rewrite", "assistant"])
@pytest.mark.parametrize("change", ["disabled", "policy"])
def test_codex_publication_fence_rechecks_managed_permission(managed_worker, check, change):
    engine, row = managed_worker, worker_row("codex")
    with Session(engine) as db:
        check(db, row, db.get(Document, "synthetic-document"))
    revoke(engine, change)
    with Session(engine) as db:
        with pytest.raises(HTTPException) as rejected:
            check(db, row, db.get(Document, "synthetic-document"))
        assert rejected.value.status_code == (401 if change == "disabled" else 403)


@pytest.mark.parametrize("check", [rewrite_jobs.require_current, document_assistant.require_current], ids=["rewrite", "assistant"])
def test_codex_policy_does_not_change_existing_http_provider_fence(managed_worker, check):
    revoke(managed_worker, "policy")
    with Session(managed_worker) as db:
        check(db, worker_row("llm"), db.get(Document, "synthetic-document"))


def test_disabled_account_feature_keeps_legacy_report_worker_contract(managed_worker, monkeypatch):
    engine, task_id = managed_worker, str(uuid.uuid4())
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "0")
    revoke(engine, "disabled")
    with transaction(engine) as db:
        db.add(ReportTask(id=task_id, owner_id="guest", input="{}", input_sha256="a" * 64,
            status="running", execution="{}", created_at=time.time()))
    monkeypatch.setattr(reports, "generate_report", lambda *args: (copy.deepcopy(DRAFT), {"completed": True}))
    reports.run_report(engine, task_id, "guest", {"research": False}, SimpleNamespace(timeout=60))
    with Session(engine) as db:
        assert db.get(ReportTask, task_id).status == "ready"


# Import the established real-account fixture; worker execution remains synthetic.
from test_account_audit_policy import policy_app, mark_legacy_shared
from test_accounts import accounts, login
from test_collaboration import document, snapshot, thread


def test_shared_member_without_explicit_grant_can_create_and_poll_assistant_and_rewrite(policy_app, monkeypatch):
    from app import rewrite
    from app.db import ConversationTurn, RewriteTask
    client, app, _, _ = policy_app
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "llm")
    monkeypatch.setenv("HWP_LLM_ALLOWED_USERS", "")
    queued = []

    class QueuedThread:
        def __init__(self, *, target, args, daemon):
            self.target, self.args = target, args
        def start(self):
            queued.append((self.target, self.args))

    monkeypatch.setattr(document_assistant, "Thread", QueuedThread)
    monkeypatch.setattr(rewrite, "Thread", QueuedThread)
    monkeypatch.setattr(document_assistant, "configured_provider", lambda: ("llm", SimpleNamespace(model="synthetic")))
    monkeypatch.setattr(rewrite, "OpenAICompatible", lambda: SimpleNamespace(model="synthetic", timeout=60, base="http://example.invalid"))
    monkeypatch.setattr(document_assistant, "run_turn", lambda *args: None)
    monkeypatch.setattr("app.local_rewrite.run_local_rewrite", lambda *args: None)
    assert login(client).status_code == 200
    original = document(client)
    mark_legacy_shared(app.state.engine, original)
    conversation = thread(client, original)
    assert login(client, "alice").status_code == 200
    with Session(app.state.engine) as db:
        assert db.get(Access, (original["id"], "alice")) is None
    lease = client.post(f"/api/documents/{original['id']}/session").json()["token"]
    text = "합성 공유 문서 질문"
    turn_id = str(uuid.uuid4())
    turn = client.post(f"/api/threads/{conversation}/assistant-turns", json={
        "id": turn_id, "base_version": original["current_version"], "message": "요약해 주세요",
        "document_context": {"text": text, "sha256": digest(text.encode()), "truncated": False, "revision": 1, "epoch": 1},
        "selection": None}, headers={"X-Edit-Session": lease})
    assert turn.status_code == 200, turn.text
    assert turn.json()["can_cancel"] is True
    assert client.get(f"/api/assistant-turns/{turn_id}").json()["requested_by"] == "alice"
    assert client.get(f"/api/threads/{conversation}/assistant-turns").json()[0]["can_cancel"] is True
    assert client.post(f"/api/assistant-turns/{turn_id}/cancel").json()["status"] == "cancelled"
    selection = snapshot()
    selection["selection"]["target"] = {"kind": "body_paragraph", "section": 0, "paragraph": 0,
        "charOffset": 0, "length": len(selection["evidence"]["text"])}
    rewrite_id = str(uuid.uuid4())
    result = client.post(f"/api/threads/{conversation}/rewrite-tasks", json={"id": rewrite_id,
        "base_version": original["current_version"], "expected_revision": 0, "selection": selection,
        "instruction": "뜻을 유지해 다듬어 주세요"}, headers={"X-Edit-Session": lease})
    assert result.status_code == 200, result.text
    listed = client.get(f"/api/threads/{conversation}/rewrite-tasks")
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["id"] == rewrite_id
    with Session(app.state.engine) as db:
        assert db.get(Access, (original["id"], "alice")) is None
        assert db.get(ConversationTurn, turn_id).requested_by == "alice"
        assert db.get(RewriteTask, rewrite_id).requested_by == "alice"
    # Run only the captured no-op targets so owned admission/budgets are released.
    for target, args in queued:
        target(*args)

