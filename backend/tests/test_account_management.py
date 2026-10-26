"""Approval and role boundaries on real auth/SQLite, synthetic accounts only."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.account_admin import maintain
from app.accounts import AccountPolicy, UserAccount
from app.auth import COOKIE
from app.db import transaction
from app.main import create_app
from test_accounts import PASSWORD, TOKENS, accounts, login, register


@pytest.fixture
def managed(accounts, monkeypatch):
    _, original, url, folder = accounts
    monkeypatch.setenv("HWP_REGISTRATION_REQUIRE_APPROVAL", "1")
    maintain(original.state.engine, "create", "admin", password=PASSWORD,
             name="합성 관리자", employee="TEST-ADMIN", role="admin")
    app = create_app(url, folder)
    with TestClient(app, base_url="https://testserver") as client:
        yield client, app, url, folder


def admin(client):
    assert login(client, "admin").status_code == 200


def account(client, user):
    response = client.get("/api/admin/accounts")
    assert response.status_code == 200, response.text
    return next(row for row in response.json()["items"] if row["user_id"] == user)


def act(client, user, action, **data):
    return client.post(f"/api/admin/accounts/{user}/{action}", json=data)


def approve(client, user="alice"):
    admin(client)
    row = account(client, user)
    response = act(client, user, "approve", expected_revision=row["revision"])
    assert response.status_code == 200, response.text
    return response


def test_new_signup_defaults_to_review_and_never_issues_session(managed, monkeypatch):
    client, app, _, _ = managed
    monkeypatch.delenv("HWP_REGISTRATION_REQUIRE_APPROVAL")
    assert AccountPolicy.from_env().require_approval
    response = register(client)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending"
    assert "set-cookie" not in response.headers
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/documents").status_code == 401
    assert login(client, "alice", "incorrect-password").status_code == 401
    assert login(client, "alice").status_code == 403
    assert "승인" in login(client, "alice").text
    with transaction(app.state.engine) as db:
        row = db.get(UserAccount, "alice")
        assert (row.approval_status, row.role, row.is_active) == ("pending", "member", False)
    assert PASSWORD not in response.text


def test_pending_or_member_cannot_list_or_manage_accounts(managed):
    client, _, _, _ = managed
    assert register(client).status_code == 202
    assert client.get("/api/admin/accounts").status_code == 401
    assert login(client).status_code == 200
    assert client.get("/api/admin/accounts").status_code == 403
    for action in ("approve", "reject", "enable", "disable", "password"):
        assert act(client, "alice", action, **({"password": PASSWORD} if action == "password" else {})).status_code == 403


def test_admin_id_reserved_and_extra_role_cannot_elevate_signup(managed):
    client, _, _, _ = managed
    assert register(client, "admin", "TEST-TAKEOVER").status_code == 409
    assert client.post("/api/auth/register", json={"username": "mallory", "password": PASSWORD,
        "name": "합성", "employee_number": "TEST-MALLORY", "role": "admin"}).status_code == 422
    assert login(client, "admin").status_code == 200
    assert client.get("/api/auth/me").json()["is_admin"]


def test_approval_requires_admin_and_new_login_and_stale_review_is_rejected(managed):
    client, _, _, _ = managed
    assert register(client).status_code == 202
    admin(client)
    pending = account(client, "alice")
    response = act(client, "alice", "approve", expected_revision=pending["revision"])
    assert response.status_code == 200, response.text
    assert response.json()["approval_status"] == "approved"
    assert response.json()["reviewed_by"] == "admin"
    assert "set-cookie" not in response.headers
    assert act(client, "alice", "reject", expected_revision=pending["revision"]).status_code == 409
    assert login(client, "alice").status_code == 200
    assert not client.get("/api/auth/me").json()["is_admin"]
    assert client.get("/api/documents").json() == []
    assert client.get("/api/admin/accounts").status_code == 403


def test_rejection_retains_identity_and_cannot_be_bypassed_by_enable(managed):
    client, app, _, _ = managed
    assert register(client).status_code == 202
    admin(client)
    response = act(client, "alice", "reject")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert act(client, "alice", "enable").status_code == 409
    assert act(client, "alice", "approve").status_code == 409
    assert register(client, "alice", "TEST-NEW").status_code == 409
    assert login(client, "alice").status_code == 401
    with transaction(app.state.engine) as db:
        assert db.get(UserAccount, "alice") is not None


def test_disable_password_reset_and_reenable_never_revive_old_sessions(managed):
    client, _, _, _ = managed
    assert register(client).status_code == 202
    approve(client)
    assert login(client, "alice").status_code == 200
    old = client.cookies.get(COOKIE)
    admin(client)
    assert act(client, "alice", "disable").status_code == 200
    assert client.get("/api/auth/me", headers={"Cookie": f"{COOKIE}={old}"}).status_code == 401
    admin(client)  # An expired-cookie response deliberately clears this client's cookie jar.
    assert act(client, "alice", "enable").status_code == 200
    assert client.get("/api/auth/me", headers={"Cookie": f"{COOKIE}={old}"}).status_code == 401
    admin(client)
    reset = act(client, "alice", "password", password="new-synthetic-passphrase")
    assert reset.status_code == 200, reset.text
    assert "new-synthetic-passphrase" not in reset.text
    assert login(client, "alice").status_code == 401
    assert login(client, "alice", "new-synthetic-passphrase").status_code == 200


def test_last_active_admin_protected_in_web_and_operator_paths(managed):
    client, app, _, _ = managed
    admin(client)
    assert act(client, "admin", "disable").status_code == 409
    with pytest.raises(HTTPException) as exc:
        maintain(app.state.engine, "disable", "admin")
    assert exc.value.status_code == 409
    assert client.get("/api/auth/me").status_code == 200


def test_concurrent_admin_disable_cannot_remove_all_administrators(managed):
    client, app, _, _ = managed
    maintain(app.state.engine, "create", "other-admin", password=PASSWORD,
             name="합성 관리자 2", employee="TEST-ADMIN-2", role="admin")
    admin(client)
    first = client.cookies.get(COOKIE)
    assert login(client, "other-admin").status_code == 200
    second = client.cookies.get(COOKIE)
    def disable(pair):
        target, cookie = pair
        return client.post(f"/api/admin/accounts/{target}/disable", json={},
                           headers={"Cookie": f"{COOKIE}={cookie}"}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(disable, [("other-admin", first), ("admin", second)]))
    assert codes.count(200) == 1 and all(code in (200, 401, 403, 409) for code in codes)
    with transaction(app.state.engine) as db:
        assert sum(row.is_active for row in db.scalars(select(UserAccount).where(UserAccount.role == "admin"))) == 1


def test_role_never_inferred_from_admin_name_and_only_operator_can_promote(accounts, monkeypatch):
    _, old, url, folder = accounts
    maintain(old.state.engine, "create", "admin", password=PASSWORD,
             name="합성 일반회원", employee="TEST-ORDINARY")
    with TestClient(create_app(url, folder), base_url="https://testserver") as client:
        assert login(client, "admin").status_code == 200
        assert not client.get("/api/auth/me").json()["is_admin"]
        assert client.get("/api/admin/accounts").status_code == 403
        maintain(old.state.engine, "promote-admin", "admin")
        assert client.get("/api/auth/me").status_code == 401
        assert login(client, "admin").status_code == 200
        assert client.get("/api/admin/accounts").status_code == 200


def test_disabled_legacy_token_path_and_operator_pending_bypass_fail(managed):
    client, app, _, _ = managed
    maintain(app.state.engine, "create", "owner", password=PASSWORD,
             name="합성 기존 ID", employee="TEST-OWNER")
    admin(client)
    assert act(client, "owner", "disable").status_code == 200
    assert client.get("/api/documents", headers={"Authorization": "Bearer " + TOKENS["owner"]}).status_code == 401
    assert register(client, "alice").status_code == 202
    with pytest.raises(ValueError):
        maintain(app.state.engine, "enable", "alice")


def test_management_lists_bounded_views_without_passwords_or_employee_numbers(managed):
    client, _, _, _ = managed
    assert register(client).status_code == 202
    admin(client)
    response = client.get("/api/admin/accounts?status=pending&limit=1&offset=0")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["user_id"] == "alice"
    for secret in (PASSWORD, "TEST-001", "password_hash", "session_revision"):
        assert secret not in response.text
    assert client.get("/api/admin/accounts?limit=201").status_code == 422
    assert client.get("/api/admin/accounts?status=unknown").status_code == 422
    assert act(client, "alice", "password", password=PASSWORD, role="admin").status_code == 422


def test_admin_role_not_assignable_to_guest_or_pending_member(managed):
    _, app, _, _ = managed
    with pytest.raises(ValueError):
        maintain(app.state.engine, "create", "guest", password=PASSWORD, role="admin")
    with pytest.raises(ValueError):
        maintain(app.state.engine, "promote-admin", "guest")


def test_operator_account_audit_reserves_retired_identity(managed):
    client, app, _, _ = managed
    maintain(app.state.engine, "create", "retired-member", password=PASSWORD,
             name="합성 이전 계정", employee="TEST-RETIRED")
    # Simulate a historic external retirement. Management UI itself never deletes.
    with transaction(app.state.engine) as db:
        db.delete(db.get(UserAccount, "retired-member"))
    assert register(client, "retired-member", "TEST-RECLAIM").status_code == 409
