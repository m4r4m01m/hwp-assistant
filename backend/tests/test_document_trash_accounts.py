"""Real account/auth/policy fixtures; shared management never changes ownership."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.account_admin import maintain
from app.accounts import UserAccount
from app.audit import AuditEvent
from app.db import Access, Document, DocumentTrash, transaction
from app.library_access import document_management_scope
from test_accounts import PASSWORD, TOKENS, accounts, login, register
from test_collaboration import document
from test_document_trash import trash
from test_storage import package, save


@pytest.fixture
def shared_accounts(accounts, monkeypatch):
    c, app, _, folder = accounts
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "shared")
    # Preserve an existing non-password identity's ownership and two versions.
    c.headers["Authorization"] = "Bearer " + TOKENS["owner"]
    doc = document(c)
    # This fixture represents documents already shared before private defaults.
    from app.document_library import PersonalDocument
    with transaction(app.state.engine) as db:
        db.delete(db.get(PersonalDocument, doc['id']))
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    assert save(c, doc, token, package("공용 휴지통 보존 시험")).status_code == 200
    assert c.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": token}).status_code == 200
    doc = c.get("/api/documents").json()[0]
    versions = c.get(f"/api/documents/{doc['id']}/versions").json()
    files = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    c.headers.pop("Authorization")
    assert register(c).status_code == 200
    maintain(app.state.engine, "create", "admin", password=PASSWORD,
             name="합성 관리자", employee="TRASH-ADMIN", role="admin")
    yield c, app, doc, versions, files, folder


@pytest.mark.parametrize("user", ["alice", "guest", "admin"])
def test_shared_approved_users_manage_anothers_document_and_keep_history(shared_accounts, user):
    c, app, doc, versions, files, folder = shared_accounts
    assert login(c, user).status_code == 200
    item = c.get("/api/documents").json()[0]
    assert item["role"] == "owner" and item["can_trash"] is True
    key = str(uuid.uuid4())
    moved = trash(c, doc, key)
    assert moved.status_code == 200, moved.text
    assert trash(c, doc, key).json() == moved.json()
    receipt = moved.json()["trash_id"]
    assert c.get("/api/documents").json() == []
    # Another approved account sees the shared trash and can restore it.
    other = "guest" if user != "guest" else "alice"
    assert login(c, other).status_code == 200
    assert [row["trash_id"] for row in c.get("/api/document-trash").json()] == [receipt]
    assert trash(c, doc, key).status_code == 409  # Receipt IDs remain actor-bound.
    restored = c.post(f"/api/document-trash/{receipt}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["can_trash"] is True
    assert c.post(f"/api/document-trash/{receipt}/restore").status_code == 200
    assert c.get(f"/api/documents/{doc['id']}/versions").json() == versions
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == files
    with Session(app.state.engine) as db:
        assert [(a.user_id, a.role) for a in db.scalars(select(Access).where(Access.document_id == doc["id"]))] == [("owner", "owner")]
        assert db.get(DocumentTrash, receipt).deleted_by == user
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.document_id == doc["id"],
            AuditEvent.action.in_(["document.trash", "document.restore_from_trash"]))
            .order_by(AuditEvent.id)))
        assert [(event.action, event.actor_id) for event in events] == [
            ("document.trash", user), ("document.restore_from_trash", other)]
    # Old restore/delete receipts cannot replay a later trash lifecycle.
    latest = trash(c, doc).json()["trash_id"]
    assert c.post(f"/api/document-trash/{receipt}/restore").status_code == 409
    assert c.post(f"/api/document-trash/{latest}/restore").status_code == 200
    assert login(c, user).status_code == 200
    assert trash(c, doc, key).status_code == 409


def test_shared_active_edit_lease_still_blocks_deletion(shared_accounts):
    c, app, doc, *_ = shared_accounts
    assert login(c, "guest").status_code == 200
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    assert login(c, "admin").status_code == 200
    assert trash(c, doc).status_code == 409
    with Session(app.state.engine) as db:
        assert db.get(Document, doc["id"]).lease_token == token
        assert db.scalar(select(DocumentTrash)) is None


@pytest.mark.parametrize("setting,value", [("HWP_LIBRARY_ACCESS_MODE", "private"), ("HWP_ACCOUNTS_ENABLED", "0")])
def test_changed_policy_rechecks_delete_list_and_restore(shared_accounts, monkeypatch, setting, value):
    c, app, doc, *_ = shared_accounts
    assert login(c, "alice").status_code == 200
    assert c.get("/api/documents").json()[0]["can_trash"] is True
    with monkeypatch.context() as changed:
        changed.setenv(setting, value)
        assert c.get("/api/documents").json() == []
        assert trash(c, doc).status_code == 404
    moved = trash(c, doc)
    assert moved.status_code == 200
    receipt = moved.json()["trash_id"]
    with monkeypatch.context() as changed:
        changed.setenv(setting, value)
        assert c.get("/api/document-trash").json() == []
        assert trash(c, doc, receipt).status_code == 404
        assert c.post(f"/api/document-trash/{receipt}/restore").status_code == 404
    assert c.post(f"/api/document-trash/{receipt}/restore").status_code == 200


@pytest.mark.parametrize("change", [{"is_active": False}, {"approval_status": "pending"}])
def test_changed_account_status_revokes_shared_management(shared_accounts, change):
    c, app, doc, *_ = shared_accounts
    assert login(c, "alice").status_code == 200
    moved = trash(c, doc)
    assert moved.status_code == 200
    receipt = moved.json()["trash_id"]
    with transaction(app.state.engine) as db:
        account = db.get(UserAccount, "alice")
        for key, value in change.items():
            setattr(account, key, value)
    for response in (c.get("/api/documents"), c.get("/api/document-trash"), trash(c, doc, receipt),
                     c.post(f"/api/document-trash/{receipt}/restore")):
        assert response.status_code == 401
    with Session(app.state.engine) as db:
        assert db.get(DocumentTrash, receipt).restored_at is None
        assert not db.scalar(select(Document.id).where(Document.id == doc["id"], document_management_scope(db, "alice")))


def test_private_admin_and_editor_have_no_management_override(shared_accounts, monkeypatch):
    c, app, doc, *_ = shared_accounts
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "private")
    with transaction(app.state.engine) as db:
        db.add(Access(document_id=doc["id"], user_id="admin", role="editor"))
    assert login(c, "admin").status_code == 200
    assert c.get("/api/documents").json()[0]["can_trash"] is False
    assert trash(c, doc).status_code == 403
    c.headers["Authorization"] = "Bearer " + TOKENS["owner"]
    receipt = trash(c, doc).json()["trash_id"]
    c.headers.pop("Authorization")
    assert c.get("/api/document-trash").json() == []
    assert c.post(f"/api/document-trash/{receipt}/restore").status_code == 403
    c.headers["Authorization"] = "Bearer " + TOKENS["owner"]
    assert c.post(f"/api/document-trash/{receipt}/restore").status_code == 200


def test_non_account_token_does_not_gain_shared_management(shared_accounts):
    c, app, doc, *_ = shared_accounts
    assert login(c, "guest").status_code == 200
    other = document(c)
    receipt = trash(c, other).json()["trash_id"]
    c.headers["Authorization"] = "Bearer " + TOKENS["owner"]
    assert [row["id"] for row in c.get("/api/documents").json()] == [doc["id"]]
    assert c.get("/api/document-trash").json() == []
    assert trash(c, other).status_code == 404
    assert c.post(f"/api/document-trash/{receipt}/restore").status_code == 404


def test_management_policy_refreshes_a_cached_account(shared_accounts):
    _, app, doc, *_ = shared_accounts
    with Session(app.state.engine) as db:
        cached = db.get(UserAccount, "alice")
        assert db.scalar(select(Document.id).where(document_management_scope(db, "alice"))) == doc["id"]
        with transaction(app.state.engine) as writer:
            writer.get(UserAccount, "alice").is_active = False
        assert not db.scalar(select(Document.id).where(document_management_scope(db, "alice")))
        assert cached.is_active is False
