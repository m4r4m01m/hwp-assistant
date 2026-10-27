"""Synthetic isolated storage: trash fences must preserve every saved version."""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Access, Document, DocumentTrash, Version, transaction
from app.schema import migrate
from test_collaboration import secured, as_user, document, share, thread
from test_storage import package, save


def trash(c, doc, key=None):
    return c.delete(f"/api/documents/{doc['id']}", headers={"Idempotency-Key": key or str(uuid.uuid4())})


def test_trash_hides_read_edit_share_and_restore_preserves_history(secured):
    c, app, _, folder = secured
    doc = document(c)
    tid = thread(c, doc)
    share(c, doc)
    lease = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    second = save(c, doc, lease, package("합성 수정본"))
    assert second.status_code == 200
    assert c.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": lease}).status_code == 200
    before = c.get(f"/api/documents/{doc['id']}/versions").json()
    all_bytes = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
    template = c.post("/api/templates", json={"name": "합성 양식", "description": "휴지통 시험", "tags": [],
        "document_id": doc["id"], "version_id": doc["current_version"]})
    assert template.status_code == 200, template.text
    moved = trash(c, doc)
    assert moved.status_code == 200, moved.text
    receipt = moved.json()["trash_id"]
    assert c.get("/api/documents").json() == []
    assert c.get("/api/templates").json() == []
    assert c.get("/api/template-market").json()["items"] == []
    assert c.get(f"/api/template-market/{template.json()['id']}/file").status_code == 404
    assert c.get("/api/document-trash").json()[0]["trash_id"] == receipt
    for who in ("alice", "bob"):
        as_user(c, who)
        for path in (f"/api/documents/{doc['id']}", f"/api/documents/{doc['id']}/versions",
                     f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file",
                     f"/api/documents/{doc['id']}/threads", f"/api/threads/{tid}", f"/api/documents/{doc['id']}/access"):
            assert c.get(path).status_code == 404, path
        assert c.post(f"/api/documents/{doc['id']}/session").status_code == 404
        assert c.patch(f"/api/documents/{doc['id']}", json={"name": "숨김 문서 변경"}).status_code == 404
    assert c.get("/api/document-trash").json() == []
    assert c.post(f"/api/document-trash/{receipt}/restore").status_code == 403
    as_user(c, "alice")
    assert {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()} == all_bytes
    restored = c.post(f"/api/document-trash/{receipt}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["current_version"] == second.json()["id"]
    assert c.get(f"/api/documents/{doc['id']}/versions").json() == before
    assert len(c.get("/api/templates").json()) == 1
    assert len(c.get("/api/template-market").json()["items"]) == 1
    assert c.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content == package()
    assert c.get(f"/api/documents/{doc['id']}/versions/{second.json()['id']}/file").content == package("합성 수정본")
    with Session(app.state.engine) as db:
        assert len(list(db.scalars(select(Version).where(Version.document_id == doc["id"])))) == 2
        assert db.get(Access, (doc["id"], "bob")).role == "editor"


def test_trash_requires_owner_and_no_active_edit_lease(secured):
    c, app, _, _ = secured
    doc = document(c)
    assert c.get("/api/documents").json()[0]["can_trash"] is True
    share(c, doc, "editor")
    as_user(c, "bob")
    assert c.get("/api/documents").json()[0]["can_trash"] is False
    assert trash(c, doc).status_code == 403
    lease = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    as_user(c, "alice")
    assert trash(c, doc).status_code == 409
    with Session(app.state.engine) as db:
        assert db.get(Document, doc["id"]).lease_token == lease
        assert db.scalar(select(DocumentTrash)) is None
    with transaction(app.state.engine) as db:
        db.get(Document, doc["id"]).lease_until = time.time() - 1
    assert trash(c, doc).status_code == 200
    as_user(c, "eve")
    assert trash(c, doc).status_code == 404


def test_trash_retry_and_old_restore_cannot_replay_lifecycle(secured):
    c, app, _, _ = secured
    doc = document(c)
    key = str(uuid.uuid4())
    first = trash(c, doc, key)
    assert first.status_code == 200, first.text
    assert trash(c, doc, key).json() == first.json()
    assert trash(c, doc).status_code == 409
    receipt = first.json()["trash_id"]
    assert c.post(f"/api/document-trash/{receipt}/restore").status_code == 200
    assert c.post(f"/api/document-trash/{receipt}/restore").status_code == 200
    assert trash(c, doc, key).status_code == 409
    second = trash(c, doc).json()["trash_id"]
    assert c.post(f"/api/document-trash/{receipt}/restore").status_code == 409
    assert c.post(f"/api/document-trash/{second}/restore").status_code == 200
    migrate(app.state.engine, {"alice": "unused"})
    migrate(app.state.engine, {"alice": "unused"})
    assert c.get("/api/documents").json()[0]["id"] == doc["id"]


def test_trash_and_edit_session_race_share_document_lock(secured):
    c, app, _, _ = secured
    doc = document(c)
    barrier = Barrier(2)
    def request_session():
        barrier.wait()
        return c.post(f"/api/documents/{doc['id']}/session")
    def request_trash():
        barrier.wait()
        return trash(c, doc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        session = pool.submit(request_session)
        deletion = pool.submit(request_trash)
        status = session.result().status_code, deletion.result().status_code
    assert status in {(200, 409), (404, 200)}
    with Session(app.state.engine) as db:
        doc_row = db.get(Document, doc["id"])
        removed = db.scalar(select(DocumentTrash).where(DocumentTrash.document_id == doc["id"]))
        assert not (removed and doc_row.lease_token and doc_row.lease_until > time.time())
