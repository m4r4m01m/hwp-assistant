"""Cached authorization and real worker-thread boundaries; no model or network."""
from queue import SimpleQueue
from threading import BoundedSemaphore, Event, Thread

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.account_settings import AccountSetting, engine_context
from app.accounts import DUMMY_HASH, UserAccount
from app.audit import actor_context
from app.auth import require_access
from app.codex_runtime import allowed
from app.db import Access, Document, make_engine, transaction
from app.rewrite_capacity import ExecutionSlot
from app.schema import migrate


@pytest.mark.parametrize("revoked", [False, True], ids=["demoted", "removed"])
def test_fresh_private_authorization_rechecks_a_cached_grant(tmp_path, monkeypatch, revoked):
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "0")
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "private")
    engine = make_engine("sqlite:///" + str(tmp_path / "grant-fence.db"))
    try:
        migrate(engine, {})
        with transaction(engine) as db:
            db.add(Document(id="synthetic-document", name="합성 문서.hwpx", created_at=1, lease_until=0))
            db.flush()
            db.add(Access(document_id="synthetic-document", user_id="reviewer", role="editor"))
        with Session(engine) as cached:
            document = require_access(cached, "synthetic-document", "reviewer", "editor")
            grant = cached.get(Access, (document.id, "reviewer"))
            assert grant.role == "editor"
            # Retain the identity-map object while another writer revokes access.
            with engine.begin() as connection:
                statement = delete(Access) if revoked else update(Access).values(role="reader")
                connection.execute(statement.where(Access.document_id == document.id, Access.user_id == "reviewer"))
            assert grant.role == "editor"  # Exercise an actually stale cache.
            with pytest.raises(HTTPException) as rejected:
                require_access(cached, document.id, "reviewer", "editor", fresh=True)
            assert rejected.value.status_code == (404 if revoked else 403)
    finally:
        engine.dispose()


def test_background_execution_keeps_request_ai_policy_and_audit_actor_without_leaking(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_CODEX_ALLOWED_USERS", "operator-only")
    monkeypatch.setenv("HWP_CODEX_ACCOUNT_ACCESS", "allowlist")
    engine = make_engine("sqlite:///" + str(tmp_path / "thread-context.db"))
    threads, output = [], SimpleQueue()
    proceed = Event()

    class ObservedThread(Thread):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            threads.append(self)

    def operation():
        if not proceed.wait(5):
            output.put((False, "timeout", False))
            return
        output.put((engine_context.get() is engine, actor_context.get(), allowed("guest")))

    try:
        migrate(engine, {})
        with transaction(engine) as db:
            db.add(UserAccount(user_id="guest", name="합성 체험", password_hash=DUMMY_HASH,
                kind="guest", is_active=True, role="member", approval_status="approved",
                session_revision="synthetic-session-revision", created_at=1))
            db.add(AccountSetting(key="codex_account_access", value="approved_accounts",
                updated_by="synthetic-admin", updated_at=1))
        capacity = BoundedSemaphore(1)
        engine_token = engine_context.set(engine)
        actor_token = actor_context.set("guest")
        try:
            with ExecutionSlot(capacity) as slot:
                slot.reserve()
                slot.start(ObservedThread, operation, ())
        finally:
            actor_context.reset(actor_token)
            engine_context.reset(engine_token)
        # The HTTP request may end before the actual background work proceeds.
        assert engine_context.get() is None and actor_context.get() is None
        proceed.set()
        threads[0].join(timeout=5)
        assert not threads[0].is_alive()
        assert output.get_nowait() == (True, "guest", True)
        assert capacity.acquire(blocking=False)
        capacity.release()
        # The worker must not turn its captured policy into process-wide access.
        assert allowed("guest") is False
        assert engine_context.get() is None and actor_context.get() is None
    finally:
        proceed.set()
        for worker in threads:
            worker.join(timeout=5)
        engine.dispose()
