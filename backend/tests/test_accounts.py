"""Real auth/storage, synthetic identities. No model, password logging or production data."""
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.accounts import AccountPolicy, AttemptLimiter, UserAccount, hash_password, verify_password
from app.account_admin import maintain
from app.auth import COOKIE
from app.db import AuthSession, transaction
from app.main import create_app
from test_storage import package

PASSWORD = "synthetic-password-1!"
TOKENS = {"guest": "synthetic-guest-" + "a" * 32, "owner": "synthetic-owner-" + "b" * 32}


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "1")
    monkeypatch.setenv("HWP_REGISTRATION_ENABLED", "1")
    # Preserve legacy immediate-registration coverage explicitly. New default
    # approval flows are verified separately in test_account_management.py.
    monkeypatch.setenv("HWP_REGISTRATION_REQUIRE_APPROVAL", "0")
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(TOKENS))
    monkeypatch.setenv("HWP_GUEST_PASSWORD", PASSWORD)
    monkeypatch.setenv("HWP_CODEX_USER", "owner")
    monkeypatch.delenv("HWP_CODEX_ALLOWED_USERS", raising=False)
    url, folder = "sqlite:///" + str(tmp_path / "accounts.db"), tmp_path / "files"
    app = create_app(url, folder)
    with TestClient(app, base_url="https://testserver") as client:
        yield client, app, url, folder


def register(client, user="alice", employee="TEST-001", password=PASSWORD):
    return client.post("/api/auth/register", json={"username": user, "password": password,
        "name": "합성 검수자", "employee_number": employee})


def login(client, user="guest", password=PASSWORD):
    return client.post("/api/auth/password-login", json={"username": user, "password": password})


def test_signup_hash_private_profile_session_and_logout(accounts):
    c, app, _, _ = accounts
    policy = c.get("/api/auth/status")
    assert policy.headers["cache-control"] == "no-store"
    assert policy.json() == dict(authentication_required=True, accounts_enabled=True, registration_enabled=True, registration_requires_approval=False, guest_enabled=True, sample_accounts=[], library_access_mode="private", token_login_enabled=True)
    response = register(c)
    assert response.status_code == 200, response.text
    assert response.json() == {"user_id": "alice", "name": "합성 검수자", "kind": "member"}
    for field in ("Secure", "HttpOnly", "SameSite=strict", "Path=/api"):
        assert field in response.headers["set-cookie"]
    assert c.get("/api/auth/me").json()["user_id"] == "alice"
    assert c.get("/api/documents").json() == []
    with transaction(app.state.engine) as db:
        row = db.get(UserAccount, "alice")
        assert row.employee_number == "TEST-001" and row.is_active
        assert PASSWORD not in row.password_hash and verify_password(PASSWORD, row.password_hash)
        assert row.password_hash.startswith("scrypt-v1$") and row.last_login_at
        assert PASSWORD not in str(list(db.scalars(select(AuthSession.credential_hash))))
    assert "employee" not in c.get("/api/auth/me").text
    assert PASSWORD not in response.text + policy.text
    cookie = c.cookies.get(COOKIE)
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/documents").status_code == 401
    assert c.get("/api/documents", headers={"Cookie": f"{COOKIE}={cookie}"}).status_code == 401


def test_restart_keeps_guest_hash_registered_sessions_and_original_document(accounts, monkeypatch):
    c, app, url, folder = accounts
    # Existing token-auth guest retains exactly the same ownership after password login.
    c.headers["Authorization"] = "Bearer " + TOKENS["guest"]
    response = c.post("/api/documents?name=synthetic.hwpx&document_id=11111111-1111-4111-8111-111111111111",
                      content=package(), headers={"Idempotency-Key": "original"})
    assert response.status_code == 200, response.text
    doc = response.json()
    c.headers.pop("Authorization")
    assert login(c).status_code == 200
    assert c.get("/api/documents").json()[0]["id"] == doc["id"]
    with transaction(app.state.engine) as db:
        original = db.get(UserAccount, "guest").password_hash
    assert register(c).status_code == 200
    cookie = c.cookies.get(COOKIE)
    monkeypatch.setenv("HWP_GUEST_PASSWORD", "different-seed-password")
    with TestClient(create_app(url, folder), base_url="https://testserver") as other:
        assert other.get("/api/auth/me", headers={"Cookie": f"{COOKIE}={cookie}"}).json()["user_id"] == "alice"
        assert login(other).status_code == 200
        assert other.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content == package()
    with transaction(app.state.engine) as db:
        assert db.get(UserAccount, "guest").password_hash == original


def test_duplicate_reserved_and_employee_numbers_cannot_overwrite(accounts):
    c, app, _, _ = accounts
    assert register(c, "Alice", "test-001").status_code == 200
    assert register(c, "alice", "TEST-002", "other-password!").status_code == 409
    assert register(c, "bob", "TEST-001").status_code == 409
    for user in ("guest", "local", "owner"):
        assert register(c, user, "TEST-004").status_code == 409
    with transaction(app.state.engine) as db:
        assert verify_password(PASSWORD, db.get(UserAccount, "alice").password_hash)
        assert db.get(UserAccount, "bob") is None
    assert login(c, "ALICE").status_code == 200


def test_account_limit_and_disabled_registration_fail_closed(accounts, monkeypatch):
    c, _, url, folder = accounts
    monkeypatch.setenv("HWP_REGISTRATION_MAX_USERS", "1")
    with TestClient(create_app(url, folder)) as other:
        assert register(other).status_code == 403  # Existing guest counts.
    monkeypatch.setenv("HWP_REGISTRATION_ENABLED", "0")
    with TestClient(create_app(url, folder)) as other:
        assert register(other).status_code == 403
        assert other.get("/api/auth/status").json()["registration_enabled"] is False


def test_cross_origin_inputs_and_errors_never_echo_secrets(accounts):
    c, _, _, _ = accounts
    for path in ("/api/auth/password-login", "/api/auth/register"):
        response = c.post(path, json={"password": PASSWORD}, headers={"Origin": "https://foreign.invalid"})
        assert response.status_code == 403 and PASSWORD not in response.text
        for raw in ('{"password":"' + PASSWORD + '", "username":"bad\\ud800"}',
                    '{"username":"alice","username":"bob","password":"' + PASSWORD + '"}',
                    '{"username":false,"password":"' + PASSWORD + '"}',
                    '{"username":"alice","password":"' + PASSWORD + '","extra":true}'):
            response = c.post(path, content=raw, headers={"content-type": "application/json"})
            assert response.status_code == 422 and PASSWORD not in response.text
        assert c.post(path, content="x" * 8193, headers={"content-type": "application/json"}).status_code == 413
        assert c.post(path, data={"password": PASSWORD}).status_code == 415
    absent = login(c, "missing")
    wrong = login(c, "guest", "incorrect-password")
    assert absent.status_code == wrong.status_code == 401 and absent.json() == wrong.json()


def test_reset_disable_and_expiry_revoke_password_sessions(accounts):
    c, app, _, _ = accounts
    assert register(c).status_code == 200
    old_cookie = c.cookies.get(COOKIE)
    maintain(app.state.engine, "password", "alice", password="new-synthetic-password")
    assert c.get("/api/auth/me").status_code == 401
    assert login(c, "alice", "new-synthetic-password").status_code == 200
    maintain(app.state.engine, "disable", "alice")
    assert c.get("/api/auth/me").status_code == 401
    assert login(c, "alice", "new-synthetic-password").status_code == 401
    maintain(app.state.engine, "enable", "alice")
    assert c.get("/api/auth/me", headers={"Cookie": f"{COOKIE}={old_cookie}"}).status_code == 401
    assert login(c, "alice", "new-synthetic-password").status_code == 200
    with transaction(app.state.engine) as db:
        for row in db.scalars(select(AuthSession).where(AuthSession.user_id == "alice")):
            row.last_seen_at = time.time() - 3600
    assert c.get("/api/auth/me").status_code == 401


def test_disabled_guest_cannot_bypass_with_static_token(accounts):
    c, app, _, _ = accounts
    maintain(app.state.engine, "disable", "guest")
    assert c.get("/api/auth/status").json()["guest_enabled"] is False
    assert c.post("/api/auth/login", json={"token": TOKENS["guest"]}).status_code == 401
    assert c.get("/api/documents", headers={"Authorization": "Bearer " + TOKENS["guest"]}).status_code == 401


def test_accounts_do_not_automatically_get_documents_or_codex(accounts):
    c, app, _, _ = accounts
    assert login(c).status_code == 200
    guest = c.get("/api/capabilities").json()
    assert not guest["features"]["reports"]["allowed"]
    assert register(c).status_code == 200
    assert c.get("/api/documents").json() == []
    assert not c.get("/api/document-assistant-provider").json()["allowed"]
    assert c.post("/api/report-tasks", json={"id": "11111111-1111-4111-8111-111111111111", "prompt": "합성 보고서를 작성하세요", "research": False}).status_code == 403
    assert c.get("/api/auth/accounts").status_code == 404  # No public directory.


def test_signup_no_automatic_local_development_bypass(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "1")
    monkeypatch.setenv("HWP_REGISTRATION_ENABLED", "1")
    monkeypatch.setenv("HWP_AUTH_TOKENS", "")
    with TestClient(create_app("sqlite:///" + str(tmp_path / "isolated.db"), tmp_path / "files")) as c:
        assert c.get("/api/documents").status_code == 401
        assert register(c).status_code == 202
        assert c.get("/api/auth/me").status_code == 401
        assert c.get("/api/auth/status").json()["authentication_required"] is True
        assert login(c, "alice").status_code == 403


def test_rate_limit_is_bounded_before_hash_and_honors_retry_after(accounts, monkeypatch):
    c, _, _, _ = accounts
    calls = []
    monkeypatch.setattr("app.accounts.verify_password", lambda *_: calls.append(1) or False)
    for _ in range(10):
        assert login(c, "unknown").status_code == 401
    response = login(c, "unknown")
    assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0
    assert len(calls) == 10
    limiter = AttemptLimiter()
    limiter.capacity.acquire(); limiter.capacity.acquire()
    with pytest.raises(HTTPException) as exc:
        limiter.run(lambda: pytest.fail("must not hash"))
    assert exc.value.status_code == 429


def test_hashes_are_salted_and_admin_creation_never_overwrites(accounts):
    c, app, _, _ = accounts
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second and verify_password(PASSWORD, first)
    assert not verify_password(PASSWORD, "invalid")
    maintain(app.state.engine, "create", "owner", password=PASSWORD, name="합성 관리자", employee="TEST-OWNER")
    assert login(c, "owner").status_code == 200
    with pytest.raises(ValueError):
        maintain(app.state.engine, "create", "owner", password=PASSWORD, name="변경", employee="TEST-NEW")
    with transaction(app.state.engine) as db:
        assert db.get(UserAccount, "owner").name == "합성 관리자"


def test_duplicate_signup_is_atomic_across_app_instances(accounts):
    c, _, url, folder = accounts
    with TestClient(create_app(url, folder)) as other, ThreadPoolExecutor(2) as workers:
        one = workers.submit(register, c)
        two = workers.submit(register, other)
        assert sorted((one.result().status_code, two.result().status_code)) == [200, 409]


def test_pre_authorized_identity_cannot_be_claimed_through_signup(accounts, monkeypatch):
    c, _, _, _ = accounts
    monkeypatch.setenv("HWP_CODEX_ALLOWED_USERS", "owner,designated-reviewer")
    monkeypatch.setenv("HWP_LLM_ALLOWED_USERS", "model-operator")
    assert register(c, "designated-reviewer").status_code == 409
    assert register(c, "model-operator").status_code == 409


def test_registered_account_can_receive_document_access_without_env_token(accounts):
    import uuid
    c, _, _, _ = accounts
    assert register(c).status_code == 200
    assert login(c).status_code == 200
    result = c.post("/api/documents?name=synthetic.hwpx&document_id=" + str(uuid.uuid4()),
                    content=package(), headers={"Idempotency-Key": "shared"})
    assert result.status_code == 200
    doc = result.json()
    assert c.put(f"/api/documents/{doc['id']}/access", json={"user_id":"alice","role":"reader"}).status_code == 200
    assert login(c, "alice").status_code == 200
    assert c.get("/api/documents").json()[0]["role"] == "reader"
    assert c.post(f"/api/documents/{doc['id']}/session").status_code == 403
