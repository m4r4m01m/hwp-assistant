"""Configured synthetic cards require real passwords and retain principal ACLs."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.account_admin import maintain
from app.accounts import AccountPolicy, UserAccount
from app.db import AuthSession, transaction
from app.main import create_app
from test_accounts import PASSWORD, accounts, login, register
from test_storage import package


@pytest.fixture
def sample_accounts(accounts, monkeypatch):
    _, app, url, folder = accounts
    for i, user in enumerate(("sample-a", "sample-b", "private-member")):
        maintain(app.state.engine, "create", user, password=PASSWORD,
                 name="비공개 프로필 " + str(i), employee="SYNTHETIC-" + str(i))
    monkeypatch.setenv("HWP_SAMPLE_ACCOUNTS", json.dumps([
        {"user_id": "sample-b", "label": "합성 검토자"},
        {"user_id": "not-created", "label": "아직 만들지 않은 예시"},
        {"user_id": "sample-a", "label": "합성 작성자"},
    ]))
    with TestClient(create_app(url, folder), base_url="https://testserver") as client:
        yield client, app, url, folder


def test_public_sample_cards_are_explicit_and_do_not_create_accounts_or_sessions(sample_accounts):
    client, app, _, _ = sample_accounts
    with transaction(app.state.engine) as db:
        before = {row.user_id: (row.password_hash, row.name, row.employee_number)
                  for row in db.scalars(select(UserAccount))}
    response = client.get("/api/auth/status")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["sample_accounts"] == [
        {"user_id": "sample-b", "label": "합성 검토자"},
        {"user_id": "sample-a", "label": "합성 작성자"},
    ]
    for private in (PASSWORD, "비공개 프로필", "SYNTHETIC-", "private-member", "not-created", "password_hash"):
        assert private not in response.text
    assert "set-cookie" not in response.headers
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/auth/accounts").status_code == 401
    with transaction(app.state.engine) as db:
        assert {row.user_id: (row.password_hash, row.name, row.employee_number)
                for row in db.scalars(select(UserAccount))} == before
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_missing_configured_identity_cannot_be_claimed_with_public_signup(sample_accounts):
    client, app, _, _ = sample_accounts
    assert register(client, "not-created", "TEST-CLAIM").status_code == 409
    with transaction(app.state.engine) as db:
        assert db.get(UserAccount, "not-created") is None
    assert register(client, "unlisted-new", "TEST-NEW").status_code == 200
    assert "unlisted-new" not in client.get("/api/auth/status").text


def test_disabled_retired_and_nonmember_sample_accounts_are_hidden(sample_accounts):
    client, app, _, _ = sample_accounts
    maintain(app.state.engine, "disable", "sample-b")
    assert client.get("/api/auth/status").json()["sample_accounts"] == [
        {"user_id": "sample-a", "label": "합성 작성자"}]
    with transaction(app.state.engine) as db:
        db.get(UserAccount, "sample-a").kind = "guest"
    assert client.get("/api/auth/status").json()["sample_accounts"] == []
    # A removed/retired row is never recreated by reading the public status.
    with transaction(app.state.engine) as db:
        db.delete(db.get(UserAccount, "sample-a"))
    assert client.get("/api/auth/status").json()["sample_accounts"] == []
    assert login(client, "sample-a").status_code == 401


def test_sample_login_requires_password_and_keeps_document_ownership_and_ai_acl(sample_accounts):
    client, _, _, _ = sample_accounts
    assert login(client, "sample-a", "incorrect-synthetic-password").status_code == 401
    assert client.get("/api/documents").status_code == 401
    assert login(client, "sample-a").status_code == 200
    assert client.get("/api/auth/accounts").status_code == 404
    document = client.post("/api/documents?name=synthetic-sample.hwpx&document_id=" + str(uuid.uuid4()),
        content=package(), headers={"Idempotency-Key": "sample-original"})
    assert document.status_code == 200, document.text
    document = document.json()
    file_path = f"/api/documents/{document['id']}/versions/{document['current_version']}/file"
    assert client.get(file_path).content == package()
    assert not client.get("/api/document-assistant-provider").json()["allowed"]
    assert client.post("/api/report-tasks", json={"id": str(uuid.uuid4()), "prompt": "합성 보고서", "research": False}).status_code == 403
    assert login(client, "sample-b").status_code == 200
    assert client.get("/api/documents").json() == []
    assert client.get(f"/api/documents/{document['id']}").status_code == 404
    assert client.get(file_path).status_code == 404
    assert login(client, "sample-a").status_code == 200
    assert client.get(file_path).content == package()


def test_unconfigured_sample_cards_are_empty_and_restart_preserves_identity(accounts, monkeypatch):
    client, app, url, folder = accounts
    assert client.get("/api/auth/status").json()["sample_accounts"] == []
    maintain(app.state.engine, "create", "sample-a", password=PASSWORD,
             name="원래 프로필", employee="TEST-ORIGINAL")
    with transaction(app.state.engine) as db:
        before = db.get(UserAccount, "sample-a").password_hash
    monkeypatch.setenv("HWP_SAMPLE_ACCOUNTS", '[{"user_id":"sample-a","label":"공개 합성 라벨"}]')
    with TestClient(create_app(url, folder), base_url="https://testserver") as restarted:
        assert restarted.get("/api/auth/status").json()["sample_accounts"] == [
            {"user_id": "sample-a", "label": "공개 합성 라벨"}]
        assert login(restarted, "sample-a").status_code == 200
    with transaction(app.state.engine) as db:
        row = db.get(UserAccount, "sample-a")
        assert (row.password_hash, row.name, row.employee_number) == (before, "원래 프로필", "TEST-ORIGINAL")


@pytest.mark.parametrize("raw", [
    "not-json", "null", "{}", '"string"',
    '[{"user_id":"guest","label":"합성"}]',
    '[{"user_id":"local","label":"합성"}]',
    '[{"user_id":"Sample-A","label":"합성"}]',
    '[{"user_id":"sample-a ","label":"합성"}]',
    '[{"user_id":"sample-a","label":" 합성 "}]',
    '[{"user_id":"sample-a","label":""}]',
    '[{"user_id":"sample-a","label":"bad\\u0000label"}]',
    '[{"user_id":"sample-a","label":"bad\\ud800label"}]',
    '[{"user_id":"sample-a","label":"합성","password":"MUST-NOT-ECHO"}]',
    '[{"user_id":"sample-a","label":"합성","label":"MUST-NOT-ECHO"}]',
    '[{"user_id":"sample-a","label":"합성"},{"user_id":"sample-a","label":"중복"}]',
    json.dumps([{"user_id": "sample-" + str(i), "label": "합성"} for i in range(9)]),
    " " * 4097,
    "[" * 1500 + "]" * 1500,
])
def test_invalid_sample_configuration_fails_closed_without_echo(monkeypatch, raw):
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "1")
    monkeypatch.setenv("HWP_SAMPLE_ACCOUNTS", raw)
    with pytest.raises(ValueError, match="HWP_SAMPLE_ACCOUNTS") as exc:
        AccountPolicy.from_env()
    assert "MUST-NOT-ECHO" not in str(exc.value)


def test_sample_configuration_requires_password_accounts(monkeypatch):
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "0")
    monkeypatch.setenv("HWP_REGISTRATION_ENABLED", "0")
    monkeypatch.setenv("HWP_SAMPLE_ACCOUNTS", '[{"user_id":"sample-a","label":"합성"}]')
    with pytest.raises(ValueError, match="HWP_SAMPLE_ACCOUNTS"):
        AccountPolicy.from_env()
    monkeypatch.setenv("HWP_SAMPLE_ACCOUNTS", "[]")
    assert AccountPolicy.from_env().sample_accounts == ()
