"""Real account/storage policy receipts using synthetic files, no model or network."""
import asyncio
import json
import sqlite3
import sys
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.account_admin import maintain
from app.account_settings import account_codex_allowed, engine_context
from app.accounts import UserAccount, hash_password, verify_password
from app.audit import AuditEvent, actor_context, record_event
from app.codex_runtime import allowed
from app.db import Access, Document, make_engine, transaction
from app.main import create_app
from app.schema import migrate
from test_accounts import PASSWORD, TOKENS, accounts, login, register
from test_storage import create, package, save


@pytest.fixture
def policy_app(accounts, monkeypatch):
    _, original, url, folder = accounts
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "shared")
    maintain(original.state.engine, "create", "admin", password=PASSWORD,
             name="합성 관리자", employee="TEST-ADMIN", role="admin")
    maintain(original.state.engine, "create", "alice", password=PASSWORD,
             name="합성 개인회원", employee="TEST-ALICE")
    app = create_app(url, folder)
    with TestClient(app, base_url="https://testserver") as client:
        yield client, app, url, folder


def mark_legacy_shared(engine, document):
    """Model an existing shared document from before private-by-default uploads."""
    from app.document_library import PersonalDocument
    with transaction(engine) as db:
        marker = db.get(PersonalDocument, document["id"])
        assert marker is not None
        db.delete(marker)


def receipts(engine, doc_id):
    with Session(engine) as db:
        return [{key: getattr(row, key) for key in ("id", "actor_id", "action", "document_id", "version_id", "document_name", "details")}
                for row in db.scalars(select(AuditEvent).where(AuditEvent.document_id == doc_id).order_by(AuditEvent.id))]


def test_shared_library_preserves_grants_and_attributes_file_actions_to_each_actor(policy_app):
    client, app, _, _ = policy_app
    assert login(client).status_code == 200
    original_bytes = package("SYNTHETIC-CONTENT-NOT-FOR-AUDIT")
    document, lease = create(client, original_bytes)
    mark_legacy_shared(app.state.engine, document)
    doc_id, first_version = document["id"], document["current_version"]
    assert client.delete(f"/api/documents/{doc_id}/session", headers={"X-Edit-Session": lease}).status_code == 200
    with Session(app.state.engine) as db:
        initial_grants = [(row.user_id, row.role) for row in db.scalars(select(Access).where(Access.document_id == doc_id))]
    assert initial_grants == [("guest", "owner")]

    assert login(client, "alice").status_code == 200
    listed = client.get("/api/documents").json()
    assert listed[0]["id"] == doc_id and listed[0]["role"] == "owner"
    assert client.patch(f"/api/documents/{doc_id}", json={"name": "합성 감사 문서"}).status_code == 200
    lease = client.post(f"/api/documents/{doc_id}/session").json()["token"]
    edited_bytes = package("SYNTHETIC-EDITED-CONTENT")
    edit_key = str(uuid.uuid4())
    edited = save(client, document, lease, edited_bytes, key=edit_key)
    assert edited.status_code == 200, edited.text
    second_version = edited.json()["id"]
    retry = save(client, document, lease, edited_bytes, key=edit_key)
    assert retry.status_code == 200 and retry.json()["id"] == second_version
    restore_key = str(uuid.uuid4())
    restored = client.post(f"/api/documents/{doc_id}/versions/{first_version}/restore",
        params={"base_version": second_version}, headers={"X-Edit-Session": lease, "Idempotency-Key": restore_key})
    assert restored.status_code == 200, restored.text
    third_version = restored.json()["id"]
    assert third_version not in (first_version, second_version)
    download = client.get(f"/api/documents/{doc_id}/versions/{third_version}/file")
    assert download.status_code == 200 and download.content == original_bytes
    with Session(app.state.engine) as db:
        assert [(row.user_id, row.role) for row in db.scalars(select(Access).where(Access.document_id == doc_id))] == initial_grants
    events = receipts(app.state.engine, doc_id)
    assert [(row["actor_id"], row["action"]) for row in events] == [
        ("guest", "document.upload"), ("alice", "document.rename"),
        ("alice", "document.modify"), ("alice", "document.restore"), ("alice", "document.download")]
    assert [row["version_id"] for row in events if row["action"] in ("document.upload", "document.modify", "document.restore")] == [first_version, second_version, third_version]
    assert events[0]["document_name"] == "문서.hwpx"
    assert events[-1]["document_name"] == "합성 감사 문서.hwpx"
    serialized = json.dumps(events, ensure_ascii=False)
    for secret in (PASSWORD, TOKENS["guest"], "SYNTHETIC-CONTENT-NOT-FOR-AUDIT", "SYNTHETIC-EDITED-CONTENT", "TEST-ALICE"):
        assert secret not in serialized


def test_idempotent_upload_and_failed_publication_never_add_success_receipts(policy_app, monkeypatch):
    client, app, _, _ = policy_app
    assert login(client).status_code == 200
    doc_id, key = str(uuid.uuid4()), str(uuid.uuid4())
    def upload():
        return client.post("/api/documents", params={"document_id": doc_id, "name": "합성.hwpx"},
            content=package(), headers={"Idempotency-Key": key})
    first, second = upload(), upload()
    assert first.status_code == second.status_code == 200
    assert first.json()["current_version"] == second.json()["current_version"]
    assert len(receipts(app.state.engine, doc_id)) == 1
    lease = client.post(f"/api/documents/{doc_id}/session").json()["token"]
    assert save(client, first.json(), "wrong-lease").status_code == 409
    assert len(receipts(app.state.engine, doc_id)) == 1
    # Fail at real DB commit: the mutation and its receipt must roll back together.
    def fail(_connection):
        raise RuntimeError("synthetic commit failure")
    event.listen(app.state.engine, "commit", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            save(client, first.json(), lease)
    finally:
        event.remove(app.state.engine, "commit", fail)
    assert len(receipts(app.state.engine, doc_id)) == 1
    assert client.get("/api/documents").json()[0]["current_version"] == first.json()["current_version"]


def test_audit_reader_is_admin_only_bounded_cursor_and_read_only(policy_app):
    client, app, _, _ = policy_app
    assert login(client).status_code == 200
    for _ in range(3):
        create(client)
    assert client.get("/api/admin/audit").status_code == 403
    assert login(client, "admin").status_code == 200
    with Session(app.state.engine) as db:
        before = list(db.scalars(select(AuditEvent.id)))
    first = client.get("/api/admin/audit?actor=guest&action=document.upload&limit=2")
    assert first.status_code == 200 and len(first.json()["items"]) == 2
    cursor = first.json()["next_before"]
    assert cursor == first.json()["items"][-1]["id"]
    second = client.get(f"/api/admin/audit?actor=guest&action=document.upload&limit=2&before={cursor}")
    assert len(second.json()["items"]) == 1 and second.json()["next_before"] is None
    assert set(row["id"] for row in first.json()["items"]).isdisjoint(row["id"] for row in second.json()["items"])
    assert all(row["actor_id"] == "guest" and row["action"] == "document.upload" for row in first.json()["items"])
    assert client.get("/api/admin/audit?limit=101").status_code == 422
    assert client.delete("/api/admin/audit").status_code == 405
    with Session(app.state.engine) as db:
        assert list(db.scalars(select(AuditEvent.id))) == before
    for action in ("update", "delete"):
        with pytest.raises(ValueError, match="append-only"):
            with transaction(app.state.engine) as db:
                row = db.get(AuditEvent, before[0])
                if action == "update":
                    row.actor_id = "tampered"
                else:
                    db.delete(row)


def test_private_mode_remains_isolated_without_rewriting_shared_history(policy_app, monkeypatch):
    client, app, url, folder = policy_app
    assert login(client).status_code == 200
    document, _ = create(client)
    mark_legacy_shared(app.state.engine, document)
    assert login(client, "alice").status_code == 200
    assert client.get("/api/documents").json()[0]["id"] == document["id"]
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "private")
    with TestClient(create_app(url, folder), base_url="https://testserver") as private:
        assert login(private, "alice").status_code == 200
        assert private.get("/api/documents").json() == []
        assert private.get(f"/api/documents/{document['id']}/versions").status_code == 404
        assert login(private).status_code == 200
        assert private.get("/api/documents").json()[0]["id"] == document["id"]


def test_shared_library_blocks_disabled_pending_and_old_legacy_tokens(policy_app, monkeypatch):
    client, app, url, folder = policy_app
    assert login(client).status_code == 200
    document, _ = create(client)
    assert login(client, "alice").status_code == 200
    maintain(app.state.engine, "disable", "alice")
    assert client.get("/api/documents").status_code == 401
    assert login(client, "alice").status_code == 401
    maintain(app.state.engine, "disable", "guest")
    assert client.get("/api/documents", headers={"Authorization": "Bearer " + TOKENS["guest"]}).status_code == 401
    monkeypatch.setenv("HWP_REGISTRATION_REQUIRE_APPROVAL", "1")
    with TestClient(create_app(url, folder), base_url="https://testserver") as other:
        assert register(other, "pending-user", "TEST-PENDING").status_code == 202
        assert login(other, "pending-user").status_code == 403
        assert other.get(f"/api/documents/{document['id']}/versions").status_code == 401


def test_approved_account_codex_setting_applies_immediately_to_guest_and_members(policy_app, monkeypatch):
    from app import codex_runner
    # This is an admission-policy test. An operator's installed CLI/auth/config
    # must not determine whether its synthetic provider is locally configured.
    settings = codex_runner.CodexSettings(sys.executable, "synthetic-no-inference",
                                         "http://127.0.0.1:8000", ())
    monkeypatch.setattr(codex_runner, "capture_settings", lambda: settings)
    monkeypatch.setenv("HWP_CODEX_CHECK_AUTH", "0")
    monkeypatch.setenv("HWP_DOCUMENT_ASSISTANT_PROVIDER", "codex")
    client, app, _, _ = policy_app
    assert login(client).status_code == 200
    # Report/document-assistant provider checks execute in a worker thread.
    assert not client.get("/api/document-assistant-provider").json()["allowed"]
    assert client.patch("/api/admin/settings", json={"codex_account_access": "approved_accounts"}).status_code == 403
    assert login(client, "admin").status_code == 200
    assert client.patch("/api/admin/settings", json={"codex_account_access": "approved_accounts"}).status_code == 200
    assert login(client).status_code == 200
    assert client.get("/api/document-assistant-provider").json()["allowed"]
    assert login(client, "alice").status_code == 200
    assert client.get("/api/document-assistant-provider").json()["allowed"]
    assert account_codex_allowed("alice", app.state.engine) is True
    assert account_codex_allowed("unknown-account", app.state.engine) is None
    assert login(client, "admin").status_code == 200
    assert client.patch("/api/admin/settings", json={"codex_account_access": "allowlist"}).status_code == 200
    assert login(client).status_code == 200
    assert not client.get("/api/document-assistant-provider").json()["allowed"]
    assert account_codex_allowed("guest", app.state.engine) is None


def test_codex_setting_never_grants_pending_disabled_and_context_propagates_to_threads(policy_app):
    client, app, _, _ = policy_app
    assert login(client, "admin").status_code == 200
    assert client.patch("/api/admin/settings", json={"codex_account_access": "approved_accounts"}).status_code == 200
    maintain(app.state.engine, "disable", "alice")
    assert account_codex_allowed("alice", app.state.engine) is False
    with transaction(app.state.engine) as db:
        row = db.get(UserAccount, "alice")
        row.is_active, row.approval_status = True, "pending"  # Fail closed even inconsistent external data.
    assert account_codex_allowed("alice", app.state.engine) is False
    async def in_worker():
        token = engine_context.set(app.state.engine)
        try:
            assert await run_in_threadpool(allowed, "guest") is True
            assert await run_in_threadpool(allowed, "alice") is False
        finally:
            engine_context.reset(token)
    asyncio.run(in_worker())
    assert engine_context.get() is None


def test_additive_legacy_account_migration_preserves_hash_active_state_and_never_promotes_admin(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    encoded = hash_password(PASSWORD)
    with sqlite3.connect(path) as old:
        old.execute("CREATE TABLE user_accounts (user_id VARCHAR(80) PRIMARY KEY, password_hash VARCHAR(256) NOT NULL, name VARCHAR(80) NOT NULL, employee_number VARCHAR(32) UNIQUE, kind VARCHAR(12) NOT NULL, is_active BOOLEAN NOT NULL, session_revision VARCHAR(64) NOT NULL, created_at FLOAT NOT NULL, last_login_at FLOAT)")
        old.executemany("INSERT INTO user_accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            ("admin", encoded, "합성 기존 일반 계정", "TEST-OLD", "member", True, "legacy-synthetic-revision", 100.0, None),
            ("guest", encoded, "합성 비활성 체험", None, "guest", False, "legacy-guest-revision", 101.0, 102.0)])
    engine = make_engine("sqlite:///" + str(path))
    try:
        migrate(engine, {})
        migrate(engine, {})  # Repeated startup is idempotent.
        with Session(engine) as db:
            admin, guest = db.get(UserAccount, "admin"), db.get(UserAccount, "guest")
            assert admin.role == guest.role == "member"
            assert admin.approval_status == guest.approval_status == "approved"
            assert admin.is_active and not guest.is_active
            assert admin.password_hash == guest.password_hash == encoded
            assert admin.session_revision == "legacy-synthetic-revision"
            assert admin.employee_number == "TEST-OLD" and guest.last_login_at == 102.0
            assert verify_password(PASSWORD, admin.password_hash)
            assert admin.reviewed_by is None and admin.reviewed_at is None
    finally:
        engine.dispose()


def test_retiring_legacy_tokens_rejects_bearer_login_and_old_token_cookie_but_keeps_password_cookie(accounts, monkeypatch):
    from app.auth import COOKIE
    client, _, url, folder = accounts
    issued = client.post("/api/auth/login", json={"token": TOKENS["guest"]})
    assert issued.status_code == 200
    token_cookie = client.cookies.get(COOKIE)
    client.cookies.clear()  # A separate browser's password session must coexist.
    assert login(client).status_code == 200
    password_cookie = client.cookies.get(COOKIE)
    assert token_cookie != password_cookie
    monkeypatch.setenv("HWP_LEGACY_TOKEN_ACCESS_ENABLED", "0")
    with TestClient(create_app(url, folder), base_url="https://testserver") as retired:
        status = retired.get("/api/auth/status").json()
        assert status["token_login_enabled"] is False
        assert retired.post("/api/auth/login", json={"token": TOKENS["guest"]}).status_code == 403
        assert retired.get("/api/documents", headers={"Authorization": "Bearer " + TOKENS["guest"]}).status_code == 401
        assert retired.get("/api/auth/me", headers={"Cookie": f"{COOKIE}={token_cookie}"}).status_code == 401
        assert retired.get("/api/documents", headers={"Cookie": f"{COOKIE}={token_cookie}"}).status_code == 401
        accepted = retired.get("/api/auth/me", headers={"Cookie": f"{COOKIE}={password_cookie}"})
        assert accepted.status_code == 200 and accepted.json()["user_id"] == "guest"
        assert login(retired).status_code == 200
        assert retired.get("/api/auth/me").status_code == 200


def test_legacy_token_retirement_requires_password_accounts(accounts, monkeypatch):
    _, _, url, folder = accounts
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "0")
    monkeypatch.setenv("HWP_REGISTRATION_ENABLED", "0")
    monkeypatch.setenv("HWP_LEGACY_TOKEN_ACCESS_ENABLED", "0")
    with pytest.raises(ValueError, match="Disabling legacy token access requires password accounts"):
        create_app(url, folder)


def test_account_features_off_hide_admin_routes_even_for_existing_admin_identity(policy_app, monkeypatch):
    from app.auth import COOKIE
    client, _, url, folder = policy_app
    assert login(client, "admin").status_code == 200
    admin_cookie = client.cookies.get(COOKIE)
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "0")
    monkeypatch.setenv("HWP_REGISTRATION_ENABLED", "0")
    with TestClient(create_app(url, folder), base_url="https://testserver") as disabled:
        for relative in ("accounts", "audit", "settings"):
            assert disabled.get("/api/admin/" + relative, headers={"Cookie": f"{COOKIE}={admin_cookie}"}).status_code == 404
        assert disabled.patch("/api/admin/settings", json={"codex_account_access": "approved_accounts"}).status_code == 404


def test_shared_mode_rejects_personal_grant_mutations_without_changing_historical_access(policy_app):
    client, app, _, _ = policy_app
    assert login(client).status_code == 200
    document, _ = create(client)
    mark_legacy_shared(app.state.engine, document)
    assert login(client, "alice").status_code == 200
    doc_id = document["id"]
    assert client.put(f"/api/documents/{doc_id}/access", json={"user_id": "alice", "role": "reader"}).status_code == 409
    assert client.delete(f"/api/documents/{doc_id}/access/guest").status_code == 409
    with Session(app.state.engine) as db:
        assert [(row.user_id, row.role) for row in db.scalars(select(Access).where(Access.document_id == doc_id))] == [("guest", "owner")]

