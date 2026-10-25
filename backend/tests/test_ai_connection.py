"""Onboarding contracts with synthetic RPC replies; never run a CLI or model here."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import ai_connection, codex_runner, codex_runtime
from app.db import AIConnectionSetup, Document, ReportTask, Version, transaction
from app.main import create_app
from test_collaboration import TOKENS
from test_storage import create, save


@pytest.fixture
def local(tmp_path, monkeypatch):
    for name in ("HWP_AUTH_TOKENS", "HWP_LLM_ALLOWED_USERS", "LLM_PROVIDER", "LLM_API_KEY",
                 "HWP_DOCUMENT_ASSISTANT_PROVIDER", "HWP_LEGACY_OWNER", "HWP_ACCOUNTS_ENABLED",
                 "HWP_REGISTRATION_ENABLED", "HWP_CODEX_ALLOWED_USERS", "HWP_CODEX_CHECK_AUTH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HWP_LOCAL_AI_SETUP", "1")
    monkeypatch.setenv("HWP_CODEX_USER", "local")
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "codex")
    monkeypatch.setenv("HWP_CODEX_EXECUTABLE", sys.executable)
    settings = codex_runner.CodexSettings(sys.executable, "synthetic-model", "http://127.0.0.1:8000", ())
    monkeypatch.setattr(codex_runner, "capture_settings", lambda: settings)
    # Missing a mock must fail locally, never launch a real account check.
    monkeypatch.setattr(ai_connection, "ReportRpc", lambda *a, **k: pytest.fail("Unexpected RPC process"))
    url, folder = f"sqlite:///{tmp_path}/test.db", tmp_path / "files"
    app = create_app(url, folder)
    with TestClient(app) as client:
        yield client, app, url, folder


def fake_rpc(monkeypatch, *, account=None, failure=None, server_request=None):
    calls, instances = [], []

    class RPC:
        def __init__(self, args, **kwargs):
            self.args, self.options, self.closed = args, kwargs, False
            self.pending = None
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def send(self, value):
            calls.append(value)
            self.pending = value

        def receive(self):
            if self.pending["method"] == "account/read":
                if failure:
                    raise failure
                if server_request:
                    return server_request
                return {"id": self.pending["id"], "result": {"account": account}}
            return {"id": self.pending["id"], "result": {}}

    monkeypatch.setattr(ai_connection, "ReportRpc", RPC)
    return calls, instances


def test_get_is_cheap_has_no_database_writes_and_reports_actual_providers(local):
    client, app, _, _ = local
    statements = []
    def record(_conn, _cursor, statement, *_args):
        statements.append(statement.strip().split()[0].upper())
    event.listen(app.state.engine, "before_cursor_execute", record)
    try:
        result = client.get("/api/ai-connection")
    finally:
        event.remove(app.state.engine, "before_cursor_execute", record)
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    body = result.json()
    assert body["deployment_mode"] == "local" and body["can_configure"] and body["needs_setup"]
    assert body["choice"] is None and body["codex"]["auth_state"] == "unchecked"
    assert body["codex"]["checked_at"] is None
    features = {value["id"]: value for value in body["features"]}
    assert set(features) == {"reports", "rewrite", "document_assistant", "chat", "review"}
    assert features["reports"]["provider"] == "codex" and features["reports"]["configured"]
    assert features["document_assistant"]["provider"] == "codex"
    assert features["chat"]["provider"] == "llm" and not features["chat"]["configured"]
    assert set(statements) <= {"SELECT"}
    assert sys.executable not in result.text and "synthetic-model" not in result.text


def test_explicit_check_reads_saved_account_only_and_short_caches(local, monkeypatch):
    client, app, _, _ = local
    calls, instances = fake_rpc(monkeypatch, account={"type": "chatgpt", "email": "private@example.invalid", "planType": "private-plan"})
    result = client.post("/api/ai-connection/check", json={})
    assert result.status_code == 200
    value = result.json()["codex"]
    assert value["installed"] and value["auth_state"] == "chatgpt" and isinstance(value["checked_at"], float)
    assert "실제 모델 이용 가능 여부는 실행 시" in value["message"]
    assert "private" not in result.text and sys.executable not in result.text
    assert [call["method"] for call in calls] == ["initialize", "initialized", "account/read"]
    assert calls[-1]["params"] == {"refreshToken": False}
    assert len(instances) == 1 and instances[0].closed
    assert instances[0].options["timeout"] == 10
    assert instances[0].args[1:4] == ["app-server", "--listen", "stdio://"]
    assert "shell_tool" in instances[0].args and "plugins" in instances[0].args
    assert client.post("/api/ai-connection/check", json={}).json() == result.json()
    assert len(instances) == 1
    with Session(app.state.engine) as db:
        assert list(db.scalars(select(AIConnectionSetup))) == []
        assert list(db.scalars(select(ReportTask))) == []


@pytest.mark.parametrize("account,state", [(None, "signed_out"), ({"type": "apiKey", "key": "secret"}, "unsupported"),
                                           ({"type": "futureAuth"}, "unsupported")])
def test_signed_out_and_unsupported_accounts_do_not_enable_setup(local, monkeypatch, account, state):
    client = local[0]
    calls, instances = fake_rpc(monkeypatch, account=account)
    result = client.post("/api/ai-connection/check", json={})
    assert result.json()["codex"]["auth_state"] == state
    assert "secret" not in result.text and instances[0].closed
    assert client.post("/api/ai-connection/setup", json={"choice": "codex"}).status_code == 409
    assert [call["method"] for call in calls] == ["initialize", "initialized", "account/read"]


@pytest.mark.parametrize("failure,state", [(TimeoutError("private credential path"), "timeout"),
                                           (RuntimeError("private credential path"), "error")])
def test_probe_failure_is_safe_closed_and_not_persisted(local, monkeypatch, failure, state):
    client, app, _, _ = local
    _, instances = fake_rpc(monkeypatch, failure=failure)
    result = client.post("/api/ai-connection/check", json={})
    assert result.json()["codex"]["auth_state"] == state
    assert "private" not in result.text and instances[0].closed
    with Session(app.state.engine) as db:
        assert list(db.scalars(select(AIConnectionSetup))) == []


def test_unexpected_tool_or_auth_request_is_rejected_without_reply(local, monkeypatch):
    client = local[0]
    calls, instances = fake_rpc(monkeypatch, server_request={"id": 999, "method": "account/chatgptAuthTokens/refresh", "params": {}})
    assert client.post("/api/ai-connection/check", json={}).json()["codex"]["auth_state"] == "error"
    assert len(calls) == 3 and instances[0].closed


def test_missing_executable_never_spawns_and_all_statuses_agree(local, monkeypatch):
    client = local[0]
    missing = "missing-executable-for-onboarding-fixture"
    monkeypatch.setenv("HWP_CODEX_EXECUTABLE", missing)
    monkeypatch.setattr(codex_runner, "capture_settings", lambda: codex_runner.CodexSettings(missing, None, "http://127.0.0.1", ()))
    result = client.post("/api/ai-connection/check", json={})
    assert result.json()["codex"]["auth_state"] == "not_installed"
    assert not result.json()["codex"]["installed"]
    assert all(not item["configured"] for item in result.json()["features"])
    assert not client.get("/api/rewrite-provider").json()["configured"]
    assert not client.get("/api/document-assistant-provider").json()["configured"]
    assert not client.get("/api/capabilities").json()["features"]["reports"]["configured"]
    assert client.post("/api/ai-connection/setup", json={"choice": "later"}).json()["choice"] == "later"


@pytest.mark.parametrize("choice", ["codex", "later"])
def test_choice_committed_idempotent_survives_restart_and_changes_no_provider(local, monkeypatch, choice):
    client, app, url, folder = local
    initial_features = client.get("/api/capabilities").json()
    if choice == "codex":
        fake_rpc(monkeypatch, account={"type": "chatgpt"})
        assert client.post("/api/ai-connection/check", json={}).status_code == 200
    result = client.post("/api/ai-connection/setup", json={"choice": choice})
    assert result.status_code == 200 and result.json()["choice"] == choice
    assert not result.json()["needs_setup"]
    with Session(app.state.engine) as db:
        receipt = db.get(AIConnectionSetup, "local")
        timestamp = receipt.updated_at
        assert receipt.choice == choice
    assert client.post("/api/ai-connection/setup", json={"choice": choice}).status_code == 200
    with Session(app.state.engine) as db:
        assert db.get(AIConnectionSetup, "local").updated_at == timestamp
    with TestClient(create_app(url, folder)) as restarted:
        body = restarted.get("/api/ai-connection").json()
        assert body["choice"] == choice and not body["needs_setup"]
        assert body["codex"]["auth_state"] == "unchecked"
        assert restarted.get("/api/capabilities").json() == initial_features
        assert restarted.post("/api/ai-connection/setup", json={"choice": "codex"}).status_code == 409


def test_fresh_success_required_and_invalidated_when_executable_changes(local, monkeypatch):
    client, app, _, _ = local
    assert client.post("/api/ai-connection/setup", json={"choice": "codex"}).status_code == 409
    fake_rpc(monkeypatch, account={"type": "chatgpt"})
    client.post("/api/ai-connection/check", json={})
    check = app.state.ai_connection_check
    check.checked_monotonic -= 301
    assert client.get("/api/ai-connection").json()["codex"]["auth_state"] == "unchecked"
    assert client.post("/api/ai-connection/setup", json={"choice": "codex"}).status_code == 409
    client.post("/api/ai-connection/check", json={})
    monkeypatch.setenv("HWP_CODEX_EXECUTABLE", "different-unavailable-executable")
    assert client.post("/api/ai-connection/setup", json={"choice": "codex"}).status_code == 409


@pytest.mark.parametrize("setting", ["0", "", "true"])
def test_server_mode_is_read_only_even_at_localhost(local, monkeypatch, setting):
    client, app, _, _ = local
    monkeypatch.setenv("HWP_LOCAL_AI_SETUP", setting)
    body = client.get("/api/ai-connection").json()
    assert body["deployment_mode"] == "server" and not body["can_configure"] and not body["needs_setup"]
    assert body["choice"] is None and body["codex"]["auth_state"] == "unchecked"
    assert client.post("/api/ai-connection/check", json={}).status_code == 403
    assert client.post("/api/ai-connection/setup", json={"choice": "later"}).status_code == 403


def test_owner_mismatch_cannot_check_or_acknowledge(local, monkeypatch):
    client = local[0]
    monkeypatch.setenv("HWP_CODEX_USER", "someone-else")
    assert not client.get("/api/ai-connection").json()["can_configure"]
    assert client.post("/api/ai-connection/check", json={}).status_code == 403
    assert client.post("/api/ai-connection/setup", json={"choice": "later"}).status_code == 403


def test_authenticated_server_even_codex_owner_cannot_probe_host(local, monkeypatch, tmp_path):
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(TOKENS))
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    app = create_app(f"sqlite:///{tmp_path}/secured.db", tmp_path / "secured-files")
    with TestClient(app) as client:
        assert client.get("/api/ai-connection").status_code == 401
        client.headers["Authorization"] = "Bearer " + TOKENS["alice"]
        body = client.get("/api/ai-connection").json()
        assert body["deployment_mode"] == "server" and not body["can_configure"]
        assert client.post("/api/ai-connection/check", json={}).status_code == 403
        assert client.post("/api/ai-connection/setup", json={"choice": "codex"}).status_code == 403


@pytest.mark.parametrize("path,payload", [("check", {"executable": "arbitrary"}), ("setup", {"choice": "other"}),
                                          ("setup", {"choice": "later", "api_key": "secret"})])
def test_only_bounded_setup_payload_is_accepted(local, path, payload):
    assert local[0].post("/api/ai-connection/" + path, json=payload).status_code == 422


def test_cross_origin_check_and_setup_are_denied(local):
    client = local[0]
    assert client.post("/api/ai-connection/check", json={}, headers={"Origin": "https://other.invalid"}).status_code == 403
    assert client.post("/api/ai-connection/setup", json={"choice": "later"}, headers={"Origin": "https://other.invalid"}).status_code == 403


def test_failed_database_commit_never_acknowledges_setup(local):
    client, app, _, _ = local
    def reject_commit(_session):
        raise RuntimeError("synthetic commit failure")
    event.listen(Session, "before_commit", reject_commit)
    try:
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            client.post("/api/ai-connection/setup", json={"choice": "later"})
    finally:
        event.remove(Session, "before_commit", reject_commit)
    assert client.get("/api/ai-connection").json()["needs_setup"]
    with Session(app.state.engine) as db:
        assert db.get(AIConnectionSetup, "local") is None


def test_invalid_provider_value_is_not_reflected_in_status(local, monkeypatch):
    monkeypatch.setenv("HWP_REWRITE_PROVIDER", "private-invalid-provider")
    result = local[0].get("/api/ai-connection")
    assert "private-invalid-provider" not in result.text
    features = {feature["id"]: feature for feature in result.json()["features"]}
    assert features["rewrite"]["provider"] == "unknown" and not features["rewrite"]["configured"]
    assert features["document_assistant"]["provider"] == "unknown"


def test_probe_concurrency_bounded_without_blocking_status_or_later(local, monkeypatch):
    client = local[0]
    entered, release = Event(), Event()
    def blocked(_settings):
        entered.set()
        assert release.wait(5)
        return "chatgpt"
    monkeypatch.setattr(ai_connection, "probe_account", blocked)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(client.post, "/api/ai-connection/check", json={})
        try:
            assert entered.wait(3)
            assert client.post("/api/ai-connection/check", json={}).status_code == 409
            assert client.get("/api/ai-connection").status_code == 200
            assert client.post("/api/ai-connection/setup", json={"choice": "later"}).status_code == 200
        finally:
            release.set()
        assert first.result(timeout=3).json()["codex"]["auth_state"] == "chatgpt"


def test_setup_preserves_documents_history_blobs_and_pending_report(local, monkeypatch):
    client, app, _, folder = local
    doc, lease = create(client)
    assert save(client, doc, lease).status_code == 200
    with transaction(app.state.engine) as db:
        db.add(ReportTask(id="frozen-report", owner_id="local", input="frozen-input", input_sha256="a" * 64,
                          status="draft", draft="frozen-draft", draft_sha256="b" * 64, execution="{}", error="", created_at=1))
    before = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
    documents = client.get("/api/documents").json()
    versions = client.get(f"/api/documents/{doc['id']}/versions").json()
    fake_rpc(monkeypatch, account={"type": "chatgpt"})
    client.post("/api/ai-connection/check", json={})
    assert client.post("/api/ai-connection/setup", json={"choice": "codex"}).status_code == 200
    assert client.post("/api/ai-connection/setup", json={"choice": "later"}).status_code == 200
    assert client.get("/api/documents").json() == documents
    assert client.get(f"/api/documents/{doc['id']}/versions").json() == versions
    assert {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()} == before
    with Session(app.state.engine) as db:
        report = db.get(ReportTask, "frozen-report")
        assert (report.status, report.input, report.draft, report.execution) == ("draft", "frozen-input", "frozen-draft", "{}")
        assert len(list(db.scalars(select(Document)))) == 1
        assert len(list(db.scalars(select(Version)))) == 2


@pytest.mark.parametrize("allowlist,configured,allowed", [
    ("owner,local", True, True),
    ("owner", True, False),
    ("owner,", False, False),
])
def test_all_connection_screens_preserve_server_codex_allowlist(local, monkeypatch, allowlist, configured, allowed):
    client = local[0]
    # The authenticated local identity differs from the legacy single-owner name.
    monkeypatch.setenv("HWP_CODEX_USER", "owner")
    monkeypatch.setenv("HWP_CODEX_ALLOWED_USERS", allowlist)
    capabilities = client.get("/api/capabilities").json()["features"]
    connection = client.get("/api/ai-connection").json()
    features = {item["id"]: item for item in connection["features"]}
    assert not connection["can_configure"]
    for name in ("reports", "rewrite", "document_assistant"):
        assert capabilities[name]["configured"] is configured
        assert capabilities[name]["allowed"] is allowed
        assert features[name]["configured"] is configured
        assert features[name]["allowed"] is allowed
    for path in ("/api/rewrite-provider", "/api/document-assistant-provider"):
        status = client.get(path).json()
        assert status["configured"] is configured
        assert status["allowed"] is allowed


def test_all_connection_screens_preserve_server_login_readiness(local, monkeypatch):
    client = local[0]
    message = "synthetic server ChatGPT login required"
    monkeypatch.setattr(codex_runtime, "readiness", lambda _: {
        "configured": False, "reason_code": "codex_login_required", "message": message})
    capabilities = client.get("/api/capabilities").json()["features"]
    connection = client.get("/api/ai-connection").json()
    features = {item["id"]: item for item in connection["features"]}
    for name in ("reports", "rewrite", "document_assistant"):
        assert not capabilities[name]["configured"]
        assert capabilities[name]["reason_code"] == "codex_login_required"
        assert not features[name]["configured"] and features[name]["message"] == message
    for path in ("/api/rewrite-provider", "/api/document-assistant-provider"):
        status = client.get(path).json()
        assert not status["configured"] and status["reason"] == message


def test_password_only_server_never_probes_host_even_for_codex_owner(local, monkeypatch, tmp_path):
    from app.account_admin import maintain

    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "1")
    monkeypatch.setenv("HWP_CODEX_USER", "owner")
    monkeypatch.setattr(ai_connection, "probe_account", lambda _: pytest.fail("Server must not probe local onboarding"))
    app = create_app(f"sqlite:///{tmp_path}/password-server.db", tmp_path / "password-server-files")
    with TestClient(app, base_url="https://testserver") as client:
        assert not app.state.users and app.state.account_policy.enabled
        assert client.get("/api/ai-connection").status_code == 401
        assert client.post("/api/ai-connection/check", json={}).status_code == 401
        assert client.post("/api/ai-connection/setup", json={"choice": "later"}).status_code == 401
        password = "synthetic-owner-password-1!"
        maintain(app.state.engine, "create", "owner", password=password,
                 name="합성 관리자", employee="TEST-AI-OWNER")
        assert client.post("/api/auth/password-login", json={"username": "owner", "password": password}).status_code == 200
        body = client.get("/api/ai-connection").json()
        assert body["deployment_mode"] == "server" and not body["can_configure"] and not body["needs_setup"]
        assert body["choice"] is None and body["codex"]["auth_state"] == "unchecked"
        assert client.post("/api/ai-connection/check", json={}).status_code == 403
        for choice in ("codex", "later"):
            assert client.post("/api/ai-connection/setup", json={"choice": choice}).status_code == 403
        with Session(app.state.engine) as db:
            assert list(db.scalars(select(AIConnectionSetup))) == []
            assert list(db.scalars(select(codex_runtime.CodexUsage))) == []


def test_local_onboarding_never_consumes_or_resets_codex_daily_usage(local, monkeypatch):
    client, app, _, _ = local
    monkeypatch.setenv("HWP_CODEX_DAILY_REQUEST_LIMIT", "1")
    with transaction(app.state.engine) as db:
        codex_runtime.reserve_usage(db, "already-accepted", "local")
    fake_rpc(monkeypatch, account={"type": "chatgpt"})
    assert client.post("/api/ai-connection/check", json={}).status_code == 200
    for choice in ("codex", "later"):
        assert client.post("/api/ai-connection/setup", json={"choice": choice}).status_code == 200
    with Session(app.state.engine) as db:
        rows = list(db.scalars(select(codex_runtime.CodexUsage)))
        assert [(row.request_key, row.user_id) for row in rows] == [("already-accepted", "local")]
    assert not app.state.ai_budget.active
