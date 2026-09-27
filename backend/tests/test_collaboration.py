import base64
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import create_app
from app.db import Access, Context, Conversation, Document, Version, transaction, make_engine
from app.hwpx import digest
from test_storage import package, save

TOKENS = {"alice": "alice-" + "a" * 32, "bob": "bob-" + "b" * 32, "eve": "eve-" + "e" * 32}


@pytest.fixture
def secured(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(TOKENS))
    url = f"sqlite:///{tmp_path}/test.db"
    app = create_app(url, tmp_path / "files")
    with TestClient(app, raise_server_exceptions=False) as c:
        c.headers["Authorization"] = "Bearer " + TOKENS["alice"]
        yield c, app, url, tmp_path / "files"


def as_user(c, name):
    c.headers["Authorization"] = "Bearer " + TOKENS[name]


def document(c):
    r = c.post("/api/documents", params={"name": "권한.hwpx", "document_id": str(uuid.uuid4())},
               content=package(), headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    return r.json()


def share(c, doc, role="editor"):
    r = c.put(f"/api/documents/{doc['id']}/access", json={"user_id": "bob", "role": role})
    assert r.status_code == 200, r.text


def thread(c, doc, visibility="document"):
    r = c.post(f"/api/documents/{doc['id']}/threads",
               json={"id": str(uuid.uuid4()), "title": "검토", "visibility": visibility})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def snapshot():
    return {"revision": 3, "state": {"documentEpoch": 1, "changeSeq": 3, "documentSha256": "a" * 64},
            "selection": {"editable": True, "target": {"sectionIndex": 0, "paragraphIndex": 1}},
            "evidence": {"text": "원문", "textSha256": digest("원문".encode()),
                         "formatSha256": "b" * 64, "adjacentContextSha256": "c" * 64}}


def context(c, doc, tid, revision=0):
    return c.put(f"/api/threads/{tid}/context", json={"expected_revision": revision,
                 "base_version": doc["current_version"], "purpose": "검토 목적", "facts": "확정 사실", "selection": snapshot()})


def test_document_readers_see_other_authors_and_legacy_private_threads(secured):
    c, app, _, _ = secured
    doc = document(c)
    shared, private = thread(c, doc), thread(c, doc, "private")
    with transaction(app.state.engine) as db:
        db.get(Conversation, private).visibility = "private"  # preserved legacy row
    assert context(c, doc, shared).status_code == 200
    share(c, doc, "reader")
    as_user(c, "bob")
    assert [d["id"] for d in c.get("/api/documents").json()] == [doc["id"]]
    assert c.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content == package()
    assert [t["id"] for t in c.get(f"/api/documents/{doc['id']}/threads").json()] == [shared, private]
    assert c.get(f"/api/threads/{private}").status_code == 200
    assert c.get(f"/api/threads/{shared}").json()["context"]["facts"] == "확정 사실"
    assert c.post(f"/api/documents/{doc['id']}/session").status_code == 403
    assert context(c, doc, shared, 1).status_code == 403
    assert c.post(f"/api/threads/{shared}/messages", json={"content": "x", "request_id": "x"}).status_code == 403
    assert c.get(f"/api/documents/{doc['id']}/access").status_code == 403
    as_user(c, "eve")
    assert c.get("/api/documents").json() == []
    for path in [f"/api/documents/{doc['id']}", f"/api/threads/{shared}",
                 f"/api/documents/{doc['id']}/versions", f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file"]:
        assert c.get(path).status_code == 404
    c.headers.pop("Authorization")
    assert c.get("/api/documents", headers={"X-User-ID": "alice"}).status_code == 401


def test_editor_lease_is_bound_to_identity_and_revocation_is_immediate(secured):
    c, _, _, _ = secured
    doc = document(c)
    share(c, doc)
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    as_user(c, "bob")
    assert save(c, doc, token).status_code == 409
    assert c.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": token}).status_code == 409
    private = thread(c, doc, "private")
    as_user(c, "alice")
    assert c.get(f"/api/threads/{private}").status_code == 200
    c.delete(f"/api/documents/{doc['id']}/access/bob")
    as_user(c, "bob")
    assert c.get(f"/api/threads/{private}").status_code == 404
    assert save(c, doc, token).status_code == 404
    as_user(c, "alice")
    assert save(c, doc, token).status_code == 409


def test_context_cas_message_idempotency_restart_and_stale_base(secured):
    c, app, url, folder = secured
    doc = document(c)
    tid = thread(c, doc)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: context(c, doc, tid), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    msg = {"request_id": "same", "content": "결정 기록"}
    a = c.post(f"/api/threads/{tid}/messages", json=msg)
    b = c.post(f"/api/threads/{tid}/messages", json=msg)
    assert a.json()["id"] == b.json()["id"]
    assert c.post(f"/api/threads/{tid}/messages", json={**msg, "content": "other"}).status_code == 409
    with TestClient(create_app(url, folder), headers={"Authorization": "Bearer " + TOKENS["alice"]}) as other:
        persisted = other.get(f"/api/threads/{tid}").json()
        assert persisted["context"]["revision"] == 1
        assert persisted["context"]["selection"] == snapshot()
        assert [m["content"] for m in persisted["messages"]] == ["결정 기록"]
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    assert save(c, doc, token).status_code == 200
    assert c.get(f"/api/threads/{tid}").json()["context"]["stale"]
    assert context(c, doc, tid, 1).status_code == 409


def test_context_commit_failure_and_proposal_fences(secured, monkeypatch):
    c, app, _, _ = secured
    doc = document(c)
    tid = thread(c, doc)
    from sqlalchemy import event
    def fail(_):
        raise RuntimeError("injected commit failure")
    event.listen(app.state.engine, "commit", fail)
    assert context(c, doc, tid).status_code == 500
    event.remove(app.state.engine, "commit", fail)
    assert c.get(f"/api/threads/{tid}").json()["context"]["revision"] == 0
    assert context(c, doc, tid).status_code == 200
    proposal = {"context_revision": 1, "replacement": "수정안", "reason": "명확성"}
    p = c.post(f"/api/threads/{tid}/proposals", json=proposal)
    assert p.status_code == 200
    assert c.get(f"/api/documents/{doc['id']}/versions").json()[0]["id"] == doc["current_version"]
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    path = f"/api/threads/{tid}/proposals/{p.json()['id']}/check"
    assert c.post(path, headers={"X-Edit-Session": token}).status_code == 200
    context(c, doc, tid, 1)
    assert c.post(path, headers={"X-Edit-Session": token}).status_code == 409
    assert c.post(f"/api/threads/{tid}/proposals", json=proposal).status_code == 409


def test_mock_chat_context_is_app_owned_and_rechecks_revoke(secured, monkeypatch):
    c, app, _, _ = secured
    doc = document(c)
    tid = thread(c, doc)
    context(c, doc, tid)
    share(c, doc)
    as_user(c, "bob")
    message = c.post(f"/api/threads/{tid}/messages", json={"request_id": "question", "content": "어떻게 쓸까요?"}).json()
    class Provider:
        async def chat(self, ctx, messages):
            assert ctx["facts"] == "확정 사실"
            assert messages[-1]["content"] == "어떻게 쓸까요?"
            with transaction(app.state.engine) as db:
                db.delete(db.get(Access, (doc["id"], "bob")))
            return "MOCK answer"
    monkeypatch.setattr("app.collaboration.OpenAICompatible", Provider)
    assert c.post(f"/api/threads/{tid}/chat", json={"message_id": message["id"]}).status_code == 404
    as_user(c, "alice")
    assert len(c.get(f"/api/threads/{tid}").json()["messages"]) == 1


def test_auth_cookie_cross_origin_and_logout(secured):
    c, _, _, _ = secured
    c.headers.pop("Authorization")
    assert c.post("/api/auth/login", json={"token": TOKENS["alice"]}, headers={"Origin": "https://evil.example"}).status_code == 403
    response = c.post("/api/auth/login", json={"token": TOKENS["alice"]})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    assert c.get("/api/auth/me").json()["user_id"] == "alice"
    c.post("/api/auth/logout")
    assert c.get("/api/documents").status_code == 401


def patch(before, after):
    return {"base_sha256": digest(before), "prefix": 0, "remove": len(before),
            "removed_sha256": digest(before), "inserted_base64": base64.b64encode(after).decode(), "result_sha256": digest(after)}


def test_delta_exact_bytes_retries_conflicts_and_entry_history(secured):
    c, _, _, _ = secured
    doc = document(c)
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    delta = patch(package(), package("변경"))
    path = f"/api/documents/{doc['id']}/versions?base_version={doc['current_version']}&cause=web-edit"
    headers = {"X-Edit-Session": token, "Idempotency-Key": "delta", "Content-Type": "application/vnd.hwp.delta+json"}
    assert c.post(path, json={**delta, "base_sha256": "f" * 64}, headers=headers).status_code == 409
    assert c.post(path, json={**delta, "removed_sha256": "f" * 64}, headers=headers).status_code == 409
    assert c.post(path, json={**delta, "result_sha256": "f" * 64}, headers=headers).status_code == 422
    first = c.post(path, json=delta, headers=headers)
    assert first.status_code == 200, first.text
    assert c.post(path, json=delta, headers=headers).json()["id"] == first.json()["id"]
    assert c.post(path, json=delta, headers={**headers, "Idempotency-Key": "other"}).status_code == 409
    assert c.get(f"/api/documents/{doc['id']}/versions/{first.json()['id']}/file").content == package("변경")
    changes = c.get(f"/api/documents/{doc['id']}/versions/{first.json()['id']}/changes").json()
    assert changes["base_sha256"] == digest(package())
    assert [e["path"] for e in changes["entries"]] == ["Contents/section0.xml"]
    assert c.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content == package()


def test_templates_inherit_permissions_pin_version_and_apply_real_bytes(secured):
    c, _, _, _ = secured
    doc = document(c)
    registered = c.post("/api/templates", json={"document_id": doc["id"], "version_id": doc["current_version"],
                       "name": "회의록 양식", "description": "회의 결정 기록", "tags": ["회의"]})
    assert registered.status_code == 200
    tid = registered.json()["id"]
    share(c, doc, "reader")
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    save(c, doc, token)
    as_user(c, "bob")
    assert c.get("/api/templates?q=회의").json()[0]["version_id"] == doc["current_version"]
    body = {"document_id": str(uuid.uuid4()), "name": "새 회의록", "request_id": "apply-once"}
    result = c.post(f"/api/templates/{tid}/apply", json=body)
    assert result.status_code == 200, result.text
    new = result.json()
    assert c.post(f"/api/templates/{tid}/apply", json=body).json() == new
    assert c.get(f"/api/documents/{new['id']}/versions/{new['current_version']}/file").content == package()
    as_user(c, "alice")
    assert c.get(f"/api/documents/{new['id']}").status_code == 404
    c.delete(f"/api/documents/{doc['id']}/access/bob")
    as_user(c, "bob")
    assert c.get("/api/templates").json() == []
    assert c.post(f"/api/templates/{tid}/apply", json={**body, "document_id": str(uuid.uuid4())}).status_code == 404
    assert c.get(f"/api/documents/{new['id']}").status_code == 200  # already-created independent copy


def test_additive_migration_preserves_001_and_requires_explicit_legacy_owner(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/old.db"
    engine = make_engine(url)
    Document.__table__.create(engine)
    Version.__table__.create(engine)
    with transaction(engine) as db:
        db.add(Document(id="legacy", name="원본", current_version=None, created_at=1, lease_until=0))
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(TOKENS))
    monkeypatch.delenv("HWP_LEGACY_OWNER", raising=False)
    with pytest.raises(ValueError, match="HWP_LEGACY_OWNER"):
        with TestClient(create_app(url, tmp_path / "files")):
            pass
    monkeypatch.setenv("HWP_LEGACY_OWNER", "alice")
    with TestClient(create_app(url, tmp_path / "files"), headers={"Authorization": "Bearer " + TOKENS["alice"]}) as c:
        assert c.get("/api/documents").json()[0]["name"] == "원본"
    engine.dispose()
