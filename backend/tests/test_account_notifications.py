"""Synthetic webhooks only: no external requests or credentials."""
import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import account_notifications as notifications
from app.account_admin import maintain
from app.accounts import UserAccount
from app.db import transaction
from app.main import create_app

PASSWORD = "synthetic-approval-password"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "1")
    monkeypatch.setenv("HWP_REGISTRATION_ENABLED", "1")
    monkeypatch.setenv("HWP_REGISTRATION_REQUIRE_APPROVAL", "1")
    monkeypatch.setenv("HWP_AUTH_TOKENS", "")
    monkeypatch.setenv("HWP_MATTERMOST_WEBHOOK_URL", "https://mattermost.example.invalid/hooks/synthetic-secret")
    monkeypatch.delenv("HWP_GUEST_PASSWORD", raising=False)
    app = create_app("sqlite:///" + str(tmp_path / "notifications.db"), tmp_path / "files")
    with TestClient(app) as client:
        yield app, client


def signup(client):
    return client.post("/api/auth/register", json={"username": "requester", "password": PASSWORD,
                       "name": "합성 검수자", "employee_number": "SYNTHETIC-001"})


def test_failed_delivery_preserves_signup_and_bounded_retries(setup, monkeypatch):
    app, client = setup
    calls = []
    async def fail(url, user):
        calls.append(user)
        raise httpx.ConnectError("synthetic-secret-must-not-be-recorded")
    monkeypatch.setattr(notifications, "post_notification", fail)
    response = signup(client)
    assert response.status_code == 202 and "set-cookie" not in response.headers
    assert calls == ["requester"]
    for _ in range(6):
        with transaction(app.state.engine) as db:
            db.get(notifications.RegistrationNotification, "requester").next_attempt_at = 0
        asyncio.run(notifications.deliver_registration_notification(app.state.engine, "requester"))
    assert len(calls) == 5
    with transaction(app.state.engine) as db:
        row = db.get(notifications.RegistrationNotification, "requester")
        assert row.status == "failed" and row.attempts == 5
        assert db.get(UserAccount, "requester").approval_status == "pending"
        assert "secret" not in row.outcome
    assert signup(client).status_code == 409 and len(calls) == 5


def test_success_and_reviewed_request_are_not_sent_again(setup, monkeypatch):
    app, client = setup
    calls = []
    async def success(url, user):
        calls.append(user)
        return True
    monkeypatch.setattr(notifications, "post_notification", success)
    assert signup(client).status_code == 202
    asyncio.run(notifications.deliver_registration_notification(app.state.engine, "requester"))
    assert calls == ["requester"]
    with transaction(app.state.engine) as db:
        row = db.get(notifications.RegistrationNotification, "requester")
        assert row.status == "delivered" and row.delivered_at is not None
        row.status, row.next_attempt_at = "pending", 0
        db.get(UserAccount, "requester").approval_status = "approved"
    asyncio.run(notifications.deliver_registration_notification(app.state.engine, "requester"))
    with transaction(app.state.engine) as db:
        assert db.get(notifications.RegistrationNotification, "requester").status == "superseded"
    assert calls == ["requester"]


def test_webhook_never_follows_redirects_or_sends_private_profile(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    seen = []
    real_client = httpx.AsyncClient
    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(302, headers={"location": "https://other.invalid/steal"})
    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return real_client(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(notifications.httpx, "AsyncClient", client)
    assert asyncio.run(notifications.post_notification("https://mattermost.example.invalid/hooks/synthetic", "name@all")) is False
    assert len(seen) == 1
    assert set(seen[0]) == {"text"} and "＠all" in seen[0]["text"] and "@all" not in seen[0]["text"]
    assert "mattermost.example.invalid" not in caplog.text and "/hooks/" not in caplog.text
    logging.getLogger("httpx").info("synthetic unrelated transport still logs")
    assert "synthetic unrelated transport still logs" in caplog.text


@pytest.mark.parametrize("url", ["https://name:secret@mattermost.invalid/hooks/key", "https://mattermost.invalid/hooks/key?secret=x",
                                    "https://mattermost.invalid/hooks/key#secret", "file:///hooks/secret", "http://mattermost/hooks/key"])
def test_unsafe_webhook_configuration_is_not_used(monkeypatch, url):
    monkeypatch.setenv("HWP_MATTERMOST_WEBHOOK_URL", url)
    monkeypatch.delenv("HWP_MATTERMOST_ALLOW_HTTP", raising=False)
    assert notifications.webhook_url() is None


def test_internal_http_is_explicit_operator_opt_in(monkeypatch):
    monkeypatch.setenv("HWP_MATTERMOST_WEBHOOK_URL", "http://mattermost/hooks/synthetic")
    monkeypatch.setenv("HWP_MATTERMOST_ALLOW_HTTP", "1")
    assert notifications.webhook_url() == "http://mattermost/hooks/synthetic"


def test_background_bookkeeping_error_does_not_break_committed_signup(setup, monkeypatch):
    _, client = setup
    async def broken(*_):
        raise RuntimeError("synthetic-private-url")
    monkeypatch.setattr(notifications, "deliver_registration_notification", broken)
    assert signup(client).status_code == 202


def test_admin_can_requeue_failed_notifications_without_exposing_webhook(setup, monkeypatch):
    app, client = setup
    async def fail(*_):
        return False
    monkeypatch.setattr(notifications, "post_notification", fail)
    assert signup(client).status_code == 202
    maintain(app.state.engine, "create", "admin", password=PASSWORD, name="합성 관리자", employee="ADMIN-TEST", role="admin")
    assert client.post("/api/admin/notifications/retry").status_code == 401
    assert client.post("/api/auth/password-login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    with transaction(app.state.engine) as db:
        row = db.get(notifications.RegistrationNotification, "requester")
        row.status, row.attempts = "failed", 5
    value = client.get("/api/admin/settings")
    assert value.json()["mattermost"] == {"configured": True, "pending": 0, "failed": 1}
    assert "synthetic-secret" not in value.text and "https://" not in value.text
    assert client.post("/api/admin/notifications/retry").json() == {"queued": 1}
    with transaction(app.state.engine) as db:
        row = db.get(notifications.RegistrationNotification, "requester")
        assert row.status == "pending" and row.attempts == 0


def test_dispatcher_recovers_exhausted_lease_then_survives_poll_error(setup, monkeypatch):
    app, client = setup
    async def fail(*_):
        return False
    monkeypatch.setattr(notifications, "post_notification", fail)
    assert signup(client).status_code == 202
    with transaction(app.state.engine) as db:
        row = db.get(notifications.RegistrationNotification, "requester")
        row.attempts, row.next_attempt_at = 5, 0
    async def stop(_):
        raise asyncio.CancelledError()
    monkeypatch.setattr(notifications.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(notifications.notification_loop(app.state.engine))
    with transaction(app.state.engine) as db:
        assert db.get(notifications.RegistrationNotification, "requester").status == "failed"


@pytest.mark.parametrize("path", [
    "/hooks/synthetic-token",
    "/mattermost/hooks/synthetic-token",
    "/company/mattermost/hooks/synthetic-token",
])
def test_https_webhook_accepts_root_and_deployment_prefix_without_rewriting(monkeypatch, path):
    url = "https://mattermost.example.invalid" + path
    monkeypatch.setenv("HWP_MATTERMOST_WEBHOOK_URL", url)
    monkeypatch.delenv("HWP_MATTERMOST_ALLOW_HTTP", raising=False)
    assert notifications.webhook_url() == url


@pytest.mark.parametrize("path", ["/mattermost/hooks/synthetic-token", "/company/mattermost/hooks/synthetic-token"])
def test_http_prefixed_webhook_still_requires_explicit_opt_in(monkeypatch, path):
    url = "http://mattermost.example.invalid" + path
    monkeypatch.setenv("HWP_MATTERMOST_WEBHOOK_URL", url)
    for setting in (None, "0"):
        if setting is None:
            monkeypatch.delenv("HWP_MATTERMOST_ALLOW_HTTP", raising=False)
        else:
            monkeypatch.setenv("HWP_MATTERMOST_ALLOW_HTTP", setting)
        assert notifications.webhook_url() is None
    monkeypatch.setenv("HWP_MATTERMOST_ALLOW_HTTP", "1")
    assert notifications.webhook_url() == url


@pytest.mark.parametrize("url", [
    "https://name:synthetic-secret@mattermost.example.invalid/company/mattermost/hooks/synthetic-token",
    "https://mattermost.example.invalid/company/mattermost/hooks/synthetic-token?secret=synthetic",
    "https://mattermost.example.invalid/company/mattermost/hooks/synthetic-token#synthetic-secret",
    "https://mattermost.example.invalid/company/mattermost/incoming/synthetic-token",
    "https://mattermost.example.invalid/company/mattermost/hooks",
    "https://mattermost.example.invalid/company/mattermost/hooks/",
    "https://mattermost.example.invalid/hooks/",
    "https://mattermost.example.invalid/hooks/synthetic-token/extra",
])
def test_prefixed_webhook_retains_credential_and_endpoint_fences(monkeypatch, url):
    monkeypatch.setenv("HWP_MATTERMOST_WEBHOOK_URL", url)
    monkeypatch.delenv("HWP_MATTERMOST_ALLOW_HTTP", raising=False)
    assert notifications.webhook_url() is None


def capture_synthetic_sender(monkeypatch):
    """Exercise the real sender through HTTPX's in-memory transport only."""
    seen = []
    real_client = httpx.AsyncClient
    def handle(request):
        seen.append({"url": str(request.url), "path": request.url.path,
                     "method": request.method, "payload": json.loads(request.content)})
        return httpx.Response(200, text="ok")
    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return real_client(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(notifications.httpx, "AsyncClient", client)
    return seen


@pytest.mark.parametrize("prefix", ["/mattermost", "/company/mattermost"])
def test_prefixed_signup_delivers_real_sender_without_cookie_or_secret_exposure(setup, monkeypatch, caplog, prefix):
    import logging
    app, client = setup
    url = "https://mattermost.example.invalid" + prefix + "/hooks/subpath-synthetic-token"
    monkeypatch.setenv("HWP_MATTERMOST_WEBHOOK_URL", url)
    caplog.set_level(logging.DEBUG)
    seen = capture_synthetic_sender(monkeypatch)
    response = signup(client)
    assert response.status_code == 202 and "set-cookie" not in response.headers
    assert client.get("/api/auth/me").status_code == 401
    assert len(seen) == 1
    assert seen[0]["url"] == url and seen[0]["path"] == prefix + "/hooks/subpath-synthetic-token"
    assert seen[0]["method"] == "POST"
    assert set(seen[0]["payload"]) == {"text"}
    payload = json.dumps(seen[0]["payload"], ensure_ascii=False)
    assert "requester" in payload and PASSWORD not in payload and "SYNTHETIC-001" not in payload and "합성 검수자" not in payload
    with transaction(app.state.engine) as db:
        row = db.get(notifications.RegistrationNotification, "requester")
        assert row.status == "delivered" and row.attempts == 1 and row.delivered_at is not None
        assert db.get(UserAccount, "requester").approval_status == "pending"
        assert url not in row.outcome and "subpath-synthetic-token" not in row.outcome
    maintain(app.state.engine, "create", "admin", password=PASSWORD,
             name="합성 관리자", employee="TEST-ADMIN", role="admin")
    assert client.post("/api/auth/password-login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    settings = client.get("/api/admin/settings")
    assert settings.status_code == 200
    assert settings.json()["mattermost"] == {"configured": True, "pending": 0, "failed": 0}
    for secret in (url, "subpath-synthetic-token", PASSWORD, "SYNTHETIC-001"):
        assert secret not in settings.text + caplog.text


def test_dispatcher_delivers_existing_prefixed_pending_without_recreating_signup(setup, monkeypatch):
    app, client = setup
    url = "https://mattermost.example.invalid/company/mattermost/hooks/existing-synthetic-token"
    monkeypatch.setenv("HWP_MATTERMOST_WEBHOOK_URL", url)
    current_validator = notifications.webhook_url
    seen = capture_synthetic_sender(monkeypatch)
    # Reproduce a pending outbox written by the old root-only validator. Neither
    # the signup nor notification row is recreated after accepting the prefix.
    monkeypatch.setattr(notifications, "webhook_url", lambda: None)
    assert signup(client).status_code == 202
    with transaction(app.state.engine) as db:
        row = db.get(notifications.RegistrationNotification, "requester")
        assert row.status == "pending" and row.attempts == 0
        original_created_at = row.created_at
        original_password_hash = db.get(UserAccount, "requester").password_hash
    assert seen == []
    monkeypatch.setattr(notifications, "webhook_url", current_validator)
    async def stop(_):
        raise asyncio.CancelledError()
    monkeypatch.setattr(notifications.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(notifications.notification_loop(app.state.engine))
    assert len(seen) == 1 and seen[0]["url"] == url
    assert seen[0]["path"] == "/company/mattermost/hooks/existing-synthetic-token"
    with transaction(app.state.engine) as db:
        row = db.get(notifications.RegistrationNotification, "requester")
        assert row.status == "delivered" and row.attempts == 1 and row.created_at == original_created_at
        account = db.get(UserAccount, "requester")
        assert account.password_hash == original_password_hash and account.approval_status == "pending"


@pytest.mark.parametrize("url", [
    "https://@mattermost.example.invalid/hooks/synthetic-token",
    "https://@mattermost.example.invalid/company/mattermost/hooks/synthetic-token",
    "https://mattermost.example.invalid/hooks/.",
    "https://mattermost.example.invalid/hooks/..",
    "https://mattermost.example.invalid/company/mattermost/hooks/.",
    "https://mattermost.example.invalid/company/mattermost/hooks/..",
])
def test_empty_userinfo_and_normalizing_hook_identifiers_are_rejected(monkeypatch, url):
    monkeypatch.setenv("HWP_MATTERMOST_WEBHOOK_URL", url)
    assert notifications.webhook_url() is None
