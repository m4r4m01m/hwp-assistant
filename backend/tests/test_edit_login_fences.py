import json
import time
import uuid
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Access, Document, transaction
from test_storage import create, package
from test_auth_sessions import TOKENS, login


def test_same_account_login_scopes_are_distinct_and_logout_releases_only_own(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(TOKENS))
    app = create_app(f"sqlite:///{tmp_path}/test.db", tmp_path / "files")
    with TestClient(app) as a, TestClient(app) as b:
        login(a); login(b)
        d, token = create(a); path = f"/api/documents/{d['id']}/session"
        context = a.get("/api/auth/me").json()["auth_context"]
        assert b.put(path, headers={"X-Edit-Session": token}).status_code == 409
        response = b.get("/api/documents", headers={"X-Auth-Context": context})
        assert response.status_code == 409
        assert "set-cookie" not in response.headers
        assert b.post("/api/auth/logout").status_code == 200
        assert a.put(path, headers={"X-Edit-Session": token}).status_code == 200
        assert a.post("/api/auth/logout").status_code == 200
        login(b)
        assert b.post(path).status_code == 200


def test_expired_exact_lease_can_reconnect_but_replaced_one_cannot(tmp_path):
    app = create_app(f"sqlite:///{tmp_path}/test.db", tmp_path / "files")
    with TestClient(app) as c:
        d, token = create(c); path = f"/api/documents/{d['id']}/session"
        with transaction(app.state.engine) as db: db.get(Document, d["id"]).lease_until = time.time() - 1
        assert c.put(path, headers={"X-Edit-Session": token}).status_code == 409
        assert c.put(path, params={"reconnect": True}, headers={"X-Edit-Session": token}).status_code == 200
        with transaction(app.state.engine) as db: db.get(Document, d["id"]).lease_until = time.time() - 1
        replacement = c.post(path).json()["token"]
        assert replacement != token
        assert c.put(path, headers={"X-Edit-Session": token}).status_code == 409
        assert c.delete(path, headers={"X-Edit-Session": token}).status_code == 200
        assert c.put(path, headers={"X-Edit-Session": replacement}).status_code == 200


def test_coedit_members_cannot_cross_login_and_logout_preserves_other_member(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(TOKENS))
    app = create_app(f"sqlite:///{tmp_path}/test.db", tmp_path / "files")
    with TestClient(app) as a, TestClient(app) as b:
        login(a); login(b)
        d, token = create(a); session = f"/api/documents/{d['id']}/session"; path = f"/api/documents/{d['id']}/coedit"
        a.delete(session, headers={"X-Edit-Session": token})
        first = a.post(path, json={"request_id": str(uuid.uuid4())}).json()["token"]
        second = b.post(path, json={"request_id": str(uuid.uuid4())}).json()["token"]
        assert b.put(path, headers={"X-Coedit-Session": first}).status_code == 409
        assert a.post("/api/auth/logout").status_code == 200
        state = b.put(path, headers={"X-Coedit-Session": second})
        assert state.status_code == 200 and len(state.json()["participants"]) == 1
        assert b.post(session).status_code == 409
        b.post("/api/auth/logout"); login(b)
        assert b.post(session).status_code == 200


def test_coedit_rechecks_revoked_editor_access_and_reader_cannot_join(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(TOKENS))
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "private")
    app = create_app(f"sqlite:///{tmp_path}/test.db", tmp_path / "files")
    with TestClient(app) as a, TestClient(app) as b:
        login(a); login(b, "bob")
        d, token = create(a); path = f"/api/documents/{d['id']}/coedit"
        a.delete(f"/api/documents/{d['id']}/session", headers={"X-Edit-Session":token})
        with transaction(app.state.engine) as db: db.add(Access(document_id=d["id"], user_id="bob", role="reader"))
        assert b.post(path, json={"request_id":str(uuid.uuid4())}).status_code == 403
        with transaction(app.state.engine) as db: db.get(Access, (d["id"], "bob")).role = "editor"
        participant = b.post(path, json={"request_id":str(uuid.uuid4())}).json()["token"]
        with transaction(app.state.engine) as db: db.get(Access, (d["id"], "bob")).role = "reader"
        assert b.put(path, headers={"X-Coedit-Session":participant}).status_code == 403
        assert b.post(path + "/changes", params={"base_version":d["current_version"], "request_id":str(uuid.uuid4())}, content=package("권한 회수"), headers={"X-Coedit-Session":participant}).status_code == 403
