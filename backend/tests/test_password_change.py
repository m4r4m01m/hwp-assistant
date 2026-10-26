"""Synthetic own-password changes; no production credentials or accounts."""
import json
import secrets
import time

import pytest
from sqlalchemy import select

from app.accounts import UserAccount, verify_password
from app.auth import COOKIE
from app.audit import AuditEvent
from app.db import AuthSession, transaction
from test_accounts import accounts, login, register, PASSWORD, TOKENS

NEW = "synthetic-changed-password-2!"


def change(client, **extra):
    return client.post("/api/auth/password-change", json={
        "current_password": PASSWORD, "new_password": NEW, **extra})


def test_change_rotates_cookie_revokes_other_sessions_and_preserves_identity(accounts):
    client, app, _, _ = accounts
    assert register(client).status_code == 200
    first = client.cookies.get(COOKIE)
    assert login(client, "alice").status_code == 200
    second = client.cookies.get(COOKIE)
    assert client.get("/api/auth/me").json()["can_change_password"] is True
    with transaction(app.state.engine) as db:
        before = db.get(UserAccount, "alice")
        original_hash, original_revision = before.password_hash, before.session_revision
    response = change(client)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert client.cookies.get(COOKIE) not in (first, second)
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/api"):
        assert flag in response.headers["set-cookie"]
    assert client.get("/api/auth/me").json()["user_id"] == "alice"
    for old in (first, second):
        assert client.get("/api/documents", headers={"Cookie": f"{COOKIE}={old}"}).status_code == 401
    assert login(client, "alice", PASSWORD).status_code == 401
    assert login(client, "alice", NEW).status_code == 200
    with transaction(app.state.engine) as db:
        row = db.get(UserAccount, "alice")
        assert row.password_hash != original_hash and row.session_revision != original_revision
        assert verify_password(NEW, row.password_hash)
        assert row.name == "합성 검수자" and row.employee_number == "TEST-001" and row.kind == "member"
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.action == "account.password_change")))
        assert len(events) == 1 and events[0].actor_id == events[0].target_id == "alice"
        assert events[0].details == "{}"


def test_wrong_current_password_and_target_injection_do_not_change_credentials(accounts):
    client, app, _, _ = accounts
    assert register(client).status_code == 200
    assert change(client, current_password="synthetic-wrong-password").status_code == 400
    assert change(client, user_id="guest").status_code == 422
    assert change(client, new_password=PASSWORD).status_code == 422
    assert change(client, new_password="short").status_code == 422
    assert client.post("/api/auth/password-change", json={"current_password": PASSWORD, "new_password": NEW}, headers={"Origin": "https://outside.example"}).status_code == 403
    assert login(client, "alice", PASSWORD).status_code == 200
    with transaction(app.state.engine) as db:
        assert not list(db.scalars(select(AuditEvent).where(AuditEvent.action == "account.password_change")))


def test_guest_and_token_logins_cannot_change_password(accounts):
    client, app, _, _ = accounts
    assert change(client).status_code == 401
    assert login(client).status_code == 200
    assert client.get("/api/auth/me").json()["can_change_password"] is False
    assert change(client).status_code == 403
    # An operator-owned personal ID may also have a static token, but that
    # credential must not acquire a password-change capability.
    from app.account_admin import maintain
    maintain(app.state.engine, "create", "owner", password=PASSWORD, name="합성 소유자", employee="TEST-OWNER")
    assert client.post("/api/auth/login", json={"token": TOKENS["owner"]}).status_code == 200
    assert client.get("/api/auth/me").json()["can_change_password"] is False
    assert change(client).status_code == 403
    assert login(client, "owner").status_code == 200
    client.headers["Authorization"] = "Bearer " + TOKENS["owner"]
    assert client.get("/api/auth/me").json()["can_change_password"] is False
    assert change(client).status_code == 403


@pytest.mark.parametrize("action", ["reset", "logout", "disable", "expire"])
def test_state_change_during_hashing_rejects_stale_update(accounts, monkeypatch, action):
    client, app, _, _ = accounts
    assert register(client).status_code == 200
    import app.accounts as module
    original_hash = module.hash_password

    def interleaved(value):
        encoded = original_hash(value)
        with transaction(app.state.engine) as db:
            row = db.get(UserAccount, "alice")
            if action == "reset":
                row.session_revision = secrets.token_hex(32)
            elif action == "disable":
                row.is_active = False
            else:
                for session in db.scalars(select(AuthSession).where(AuthSession.user_id == "alice")):
                    if action == "logout":
                        session.revoked_at = time.time()
                    else:
                        session.expires_at = time.time() - 1
        return encoded

    monkeypatch.setattr(module, "hash_password", interleaved)
    assert change(client).status_code == 409
    with transaction(app.state.engine) as db:
        assert verify_password(PASSWORD, db.get(UserAccount, "alice").password_hash)
        assert not list(db.scalars(select(AuditEvent).where(AuditEvent.action == "account.password_change")))


def test_lost_cookie_response_recovers_with_new_password(accounts):
    client, app, _, _ = accounts
    assert register(client).status_code == 200
    # Simulate response loss by discarding its new cookie after the committed request.
    old = client.cookies.get(COOKIE)
    assert change(client).status_code == 200
    assert client.get("/api/documents", headers={"Cookie": f"{COOKIE}={old}"}).status_code == 401
    client.cookies.clear()
    assert login(client, "alice", NEW).status_code == 200
    assert client.get("/api/auth/me").json()["can_change_password"] is True


def test_input_errors_and_rate_limit_never_echo_passwords(accounts, monkeypatch):
    client, app, _, _ = accounts
    assert register(client).status_code == 200
    secret = "synthetic-secret-for-redaction"
    invalid = client.post("/api/auth/password-change", content=json.dumps({"current_password": secret, "new_password": "short"}), headers={"Content-Type": "application/json"})
    assert invalid.status_code == 422 and secret not in invalid.text
    calls = []
    monkeypatch.setattr("app.accounts.verify_password", lambda *_: calls.append(1) or False)
    responses = [change(client) for _ in range(12)]
    assert responses[-1].status_code == 429 and "Retry-After" in responses[-1].headers
    assert 1 <= len(calls) <= 10
    assert all(PASSWORD not in result.text and NEW not in result.text for result in responses)
