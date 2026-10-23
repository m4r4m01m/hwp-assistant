"""Authentication boundaries, revocation and restart persistence; synthetic credentials only."""
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session
from starlette.datastructures import URL

from app import auth
from app.auth import COOKIE, LoginRateLimiter, session_hash
from app.db import AuthSession, Document, Version, make_engine, transaction
from app.main import create_app

TOKENS = {"alice": "alice-test-" + "a" * 32, "bob": "bob-test-" + "b" * 32}


@pytest.fixture
def secured_app(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(TOKENS))
    monkeypatch.delenv("HWP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("HWP_LEGACY_OWNER", raising=False)
    for key in ("HWP_AUTH_SESSION_TTL_SECONDS", "HWP_AUTH_SESSION_IDLE_SECONDS", "HWP_AUTH_SESSION_MAX_PER_USER"):
        monkeypatch.delenv(key, raising=False)
    url, folder = f"sqlite:///{tmp_path}/auth.db", tmp_path / "files"
    app = create_app(url, folder)
    # This route deliberately has no endpoint-level user read: middleware must protect it.
    @app.get("/api/auth-boundary-test")
    def protected():
        return {"ok": True}
    return app, url, folder


def login(client, user="alice"):
    result = client.post("/api/auth/login", json={"token": TOKENS[user]})
    assert result.status_code == 200, result.text
    return client.cookies.get(COOKIE), result


def copied_cookie(token):
    return {"Cookie": f"{COOKIE}={token}"}


def test_browser_session_is_random_hashed_and_cookie_is_protected(secured_app):
    app, _, _ = secured_app
    with TestClient(app, base_url="https://testserver") as client:
        token, response = login(client)
        assert token != TOKENS["alice"] and auth.SESSION_PATTERN.fullmatch(token)
        assert TOKENS["alice"] not in response.text + response.headers["set-cookie"]
        for flag in ("HttpOnly", "SameSite=strict", "Secure", "Path=/api", "Max-Age=28800"):
            assert flag in response.headers["set-cookie"]
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/auth/me").json()["user_id"] == "alice"
        with Session(app.state.engine) as db:
            row = db.scalar(select(AuthSession))
            assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
            assert row.user_id == "alice"
            assert row.expires_at - row.created_at == 28800
            assert row.revoked_at is None
            assert token not in vars(row).values()
            assert TOKENS["alice"] not in vars(row).values()


def test_logout_revokes_a_copied_session_and_keeps_bearer_automation(secured_app):
    app, _, _ = secured_app
    with TestClient(app) as client:
        token, _ = login(client)
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me", headers=copied_cookie(token)).status_code == 401
        with Session(app.state.engine) as db:
            assert db.get(AuthSession, session_hash(token)).revoked_at is not None
        assert client.post("/api/auth/logout", headers=copied_cookie(token)).status_code == 200
        assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + TOKENS["alice"]}).json()["user_id"] == "alice"
        # The old login secret is no longer a browser session; nor is a session an automation token.
        assert client.get("/api/auth/me", headers=copied_cookie(TOKENS["alice"])).status_code == 401
        assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_relogin_rotates_only_the_current_browser_session(secured_app):
    app, _, _ = secured_app
    with TestClient(app) as client:
        first, _ = login(client)
        client.cookies.clear()  # simulate a second device, without a prior browser session
        second, _ = login(client)
        replacement, _ = login(client)
        assert len({first, second, replacement}) == 3
        assert client.get("/api/auth/me", headers=copied_cookie(second)).status_code == 401
        assert client.get("/api/auth/me", headers=copied_cookie(first)).status_code == 200
        assert client.get("/api/auth/me", headers=copied_cookie(replacement)).status_code == 200


@pytest.mark.parametrize("expired_field", ["expires_at", "last_seen_at"])
def test_server_expiry_rejects_replayed_cookie(secured_app, expired_field):
    app, _, _ = secured_app
    with TestClient(app) as client:
        token, _ = login(client)
        with transaction(app.state.engine) as db:
            row = db.get(AuthSession, session_hash(token))
            setattr(row, expired_field, time.time() - (1801 if expired_field == "last_seen_at" else 1))
        response = client.get("/api/auth/me", headers=copied_cookie(token))
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert "Max-Age=0" in response.headers["set-cookie"]


def test_session_survives_restart_but_credential_rotation_revokes_it(secured_app, monkeypatch):
    app, url, folder = secured_app
    with TestClient(app) as client:
        token, _ = login(client)
    with TestClient(create_app(url, folder)) as restarted:
        assert restarted.get("/api/auth/me", headers=copied_cookie(token)).json()["user_id"] == "alice"
    rotated = {**TOKENS, "alice": "rotated-test-" + "c" * 32}
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(rotated))
    with TestClient(create_app(url, folder)) as restarted:
        assert restarted.get("/api/auth/me", headers=copied_cookie(token)).status_code == 401
        assert restarted.get("/api/auth/me", headers={"Authorization": "Bearer " + TOKENS["alice"]}).status_code == 401
        assert restarted.get("/api/auth/me", headers={"Authorization": "Bearer " + rotated["alice"]}).status_code == 200


def test_session_activity_is_throttled_and_does_not_extend_absolute_expiry(secured_app, monkeypatch):
    app, _, _ = secured_app
    clock = [1000.0]
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: clock[0]))
    writes = []
    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("UPDATE auth_sessions"):
            writes.append(statement)
    with TestClient(app) as client:
        token, _ = login(client)
        event.listen(app.state.engine, "before_cursor_execute", record)
        try:
            for value in (1000, 1059, 1060, 1061):
                clock[0] = value
                assert client.get("/api/auth/me").status_code == 200
            assert len(writes) == 1
            with Session(app.state.engine) as db:
                row = db.get(AuthSession, session_hash(token))
                assert row.last_seen_at == 1060
                assert row.expires_at == 29800
        finally:
            event.remove(app.state.engine, "before_cursor_execute", record)


def test_session_limit_revokes_oldest_device(secured_app, monkeypatch):
    _, url, folder = secured_app
    monkeypatch.setenv("HWP_AUTH_SESSION_MAX_PER_USER", "2")
    app = create_app(url, folder)
    with TestClient(app) as client:
        sessions = []
        for _ in range(3):
            client.cookies.clear()
            sessions.append(login(client)[0])
        assert client.get("/api/auth/me", headers=copied_cookie(sessions[0])).status_code == 401
        for token in sessions[1:]:
            assert client.get("/api/auth/me", headers=copied_cookie(token)).status_code == 200


def test_repeated_logins_prune_revoked_session_hashes(secured_app, monkeypatch):
    _, url, folder = secured_app
    monkeypatch.setenv("HWP_AUTH_SESSION_MAX_PER_USER", "2")
    app = create_app(url, folder)
    with TestClient(app) as client:
        for _ in range(8):
            client.cookies.clear()
            login(client)
        with Session(app.state.engine) as db:
            rows = list(db.scalars(select(AuthSession)))
            assert len(rows) <= 3
            assert sum(row.revoked_at is None for row in rows) == 2


def test_api_auth_uses_scope_path_even_if_reconstructed_url_path_is_wrong(secured_app, monkeypatch):
    app, _, _ = secured_app
    # Independent of the dependency patch: the application must not use URL.path as its gate.
    monkeypatch.setattr(URL, "path", property(lambda _self: "/api/health"))
    with TestClient(app) as client:
        for path in ("/api", "/api/unknown", "/api/auth-boundary-test"):
            result = client.get(path)
            assert result.status_code == 401, result.text
            assert result.headers["cache-control"] == "no-store"
        assert client.get("/api/auth-boundary-test", headers={"Authorization": "Bearer " + TOKENS["alice"]}).status_code == 200


@pytest.mark.parametrize("host", ["testserver/harmless?x=", "testserver?x=", "testserver#x", "testserver@evil.example", "testserver:70000", "[broken::ip]", "testserver.."])
def test_malformed_host_never_reaches_unprotected_endpoint(secured_app, host):
    app, _, _ = secured_app
    with TestClient(app) as client:
        result = client.get("/api/auth-boundary-test", headers={"Host": host, "Authorization": "Bearer " + TOKENS["alice"]})
        assert result.status_code == 400


def test_duplicate_host_is_rejected(secured_app):
    app, _, _ = secured_app
    with TestClient(app) as client:
        assert client.get("/api/health", headers=[("Host", "testserver"), ("Host", "evil.example")]).status_code == 400


def test_trusted_hosts_and_cross_origin_write_validation(secured_app, monkeypatch):
    _, url, folder = secured_app
    monkeypatch.setenv("HWP_ALLOWED_HOSTS", "example.test,*.trusted.test,127.0.0.1,[::1]")
    with TestClient(create_app(url, folder), base_url="https://example.test") as client:
        assert client.get("/api/health").status_code == 200
        for host in ("sub.trusted.test", "127.0.0.1:8000", "[::1]:8000"):
            assert client.get("/api/health", headers={"Host": host}).status_code == 200
        for host in ("evil.example", "trusted.test", "sub.trusted.test.evil.example"):
            assert client.get("/api/health", headers={"Host": host}).status_code == 400
        assert client.post("/api/auth/login", json={"token": TOKENS["alice"]}, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/auth/login", json={"token": TOKENS["alice"]}, headers={"Origin": "https://example.test"}).status_code == 200
        assert client.post("/api/auth/logout", headers={"Origin": "null"}).status_code == 403


@pytest.mark.parametrize("value", ["*", "https://example.test", "example.test:443", "*.test", "example.test/extra"])
def test_invalid_trusted_host_configuration_fails_early(secured_app, monkeypatch, value):
    _, url, folder = secured_app
    monkeypatch.setenv("HWP_ALLOWED_HOSTS", value)
    with pytest.raises(ValueError, match="HWP_ALLOWED_HOSTS"):
        create_app(url, folder)


def test_explicit_allowed_hosts_configuration_ignores_process_environment(monkeypatch):
    monkeypatch.setenv("HWP_ALLOWED_HOSTS", "process.example")
    assert auth.allowed_hosts() == ("process.example",)
    assert auth.allowed_hosts("") == ()
    assert auth.allowed_hosts("explicit.example,127.0.0.1") == ("explicit.example", "127.0.0.1")


def test_login_failure_limit_blocks_before_checking_credentials_and_recovers(secured_app):
    app, _, _ = secured_app
    clock = [1000.0]
    app.state.login_limiter = LoginRateLimiter(max_failures=2, clock=lambda: clock[0])
    with TestClient(app) as client:
        for value in ("forged-client-1", "forged-client-2"):
            # App code never trusts the submitted forwarding header as a limiter identity.
            result = client.post("/api/auth/login", json={"token": "invalid-test-" + "z" * 32},
                                 headers={"X-Forwarded-For": value})
            assert result.status_code == 401
        blocked = client.post("/api/auth/login", json={"token": TOKENS["alice"]})
        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == "60"
        assert blocked.headers["cache-control"] == "no-store"
        with Session(app.state.engine) as db:
            assert list(db.scalars(select(AuthSession))) == []
        clock[0] += 60
        login(client)


def test_login_limiter_isolates_clients_and_success_resets_prior_failures():
    limiter = LoginRateLimiter(max_failures=2)
    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            limiter.authenticate("client-1", "bad-token", TOKENS)
        assert exc.value.status_code == 401
    assert limiter.authenticate("client-2", TOKENS["alice"], TOKENS) == "alice"
    with pytest.raises(HTTPException) as exc:
        limiter.authenticate("client-1", TOKENS["alice"], TOKENS)
    assert exc.value.status_code == 429
    for _ in range(3):
        with pytest.raises(HTTPException) as exc:
            limiter.authenticate("client-2", "bad-token", TOKENS)
        assert exc.value.status_code == 401
        assert limiter.authenticate("client-2", TOKENS["alice"], TOKENS) == "alice"


@pytest.mark.parametrize("shape", ["token", "nested_token", "body"])
def test_lone_surrogate_login_is_rejected_without_echo_or_server_error(secured_app, shape):
    app, _, _ = secured_app
    invalid = "malformed-test-" + "x" * 24 + "\ud800"
    value = {"token": invalid} if shape == "token" else {"token": [invalid]} if shape == "nested_token" else invalid
    payload = json.dumps(value).encode("ascii")
    with TestClient(app) as client:
        result = client.post("/api/auth/login", content=payload, headers={"Content-Type": "application/json"})
        assert result.status_code == 422
        assert "malformed-test" not in result.text
        assert result.headers["cache-control"] == "no-store"
        login(client)


def test_direct_auth_unicode_failure_is_counted_by_the_limiter():
    limiter = LoginRateLimiter(max_failures=2)
    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            limiter.authenticate("same-client", "invalid-test-" + "x" * 24 + "\ud800", TOKENS)
        assert exc.value.status_code == 401 and exc.value.detail == auth.LOGIN_REQUIRED
    with pytest.raises(HTTPException) as exc:
        limiter.authenticate("same-client", TOKENS["alice"], TOKENS)
    assert exc.value.status_code == 429


def test_configured_credentials_require_utf8_but_preserve_valid_unicode(monkeypatch):
    invalid = {"alice": "invalid-test-" + "x" * 24 + "\ud800"}
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(invalid))
    with pytest.raises(ValueError, match="valid UTF-8"):
        auth.credentials()
    valid = {"alice": "합성-유니코드-" + "가" * 24}
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(valid))
    users = auth.credentials()
    assert auth.authenticate(valid["alice"], users) == "alice"


def test_login_failure_memory_is_bounded_and_expired_buckets_are_removed():
    clock = [1000.0]
    limiter = LoginRateLimiter(max_clients=3, clock=lambda: clock[0])
    for index in range(10):
        with pytest.raises(HTTPException):
            limiter.authenticate(f"client-{index}", "never-store-this-token", TOKENS)
        assert len(limiter.failures) <= 3
    assert all(isinstance(key, bytes) and len(key) == 32 for key in limiter.failures)
    assert all(list(value) == [1000.0] for value in limiter.failures.values())
    clock[0] += 60
    assert limiter.authenticate("new-client", TOKENS["alice"], TOKENS) == "alice"
    assert not limiter.failures


def test_concurrent_login_failures_cannot_race_past_the_limit():
    limiter = LoginRateLimiter(max_failures=2)
    def attempt(_):
        try:
            limiter.authenticate("same-client", "invalid-test-token", TOKENS)
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(attempt, range(20)))
    assert results.count(401) == 2
    assert results.count(429) == 18


def test_session_table_migration_is_additive(secured_app, monkeypatch):
    _, url, folder = secured_app
    engine = make_engine(url)
    Document.__table__.create(engine)
    Version.__table__.create(engine)
    with transaction(engine) as db:
        db.add(Document(id="old-document", name="original", current_version=None, created_at=123, lease_until=0))
    engine.dispose()
    monkeypatch.setenv("HWP_LEGACY_OWNER", "alice")
    app = create_app(url, folder)
    with TestClient(app) as client:
        assert "auth_sessions" in inspect(app.state.engine).get_table_names()
        login(client)
        with Session(app.state.engine) as db:
            row = db.get(Document, "old-document")
            assert (row.name, row.created_at, row.current_version) == ("original", 123, None)
