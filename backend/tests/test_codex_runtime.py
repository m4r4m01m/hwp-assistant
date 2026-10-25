"""Synthetic subprocess responses, real quota transactions; never an inference call."""
from dataclasses import replace
import subprocess
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app import codex_runtime as runtime
from app.codex_runner import BACKEND, capture_settings, command
from app.db import transaction
from test_accounts import accounts, register
from test_reports import report_env, ready
from test_collaboration import secured


def test_explicit_allowlist_is_strict_and_signup_does_not_change_it(monkeypatch):
    monkeypatch.setenv("HWP_CODEX_USER", "owner")
    monkeypatch.delenv("HWP_CODEX_ALLOWED_USERS", raising=False)
    assert runtime.allowed("owner") and not runtime.allowed("guest")
    monkeypatch.setenv("HWP_CODEX_ALLOWED_USERS", "owner,reviewer")
    assert runtime.allowed("reviewer") and not runtime.allowed("newly-signed-up")
    for invalid in ("*", "owner,", "owner, bad user"):
        monkeypatch.setenv("HWP_CODEX_ALLOWED_USERS", invalid)
        with pytest.raises(HTTPException): runtime.allowed("owner")


def test_status_check_has_no_model_request_no_secrets_and_cached_auth_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_CODEX_CHECK_AUTH", "1")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(runtime.shutil, "which", lambda *_, **__: "/bin/codex")
    settings = replace(capture_settings(), executable="synthetic-codex")
    calls = []
    def process(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "Logged in using ChatGPT", "")
    monkeypatch.setattr(runtime, "run_process", process)
    monkeypatch.setattr(runtime, "_cache", (None, 0, None))
    assert runtime.readiness(settings)["configured"]
    assert runtime.readiness(settings)["configured"]
    assert len(calls) == 1 and calls[0] == ["synthetic-codex", "login", "status"]
    (tmp_path / "auth.json").write_text("synthetic-marker-not-a-credential")
    assert runtime.readiness(settings)["configured"]
    assert len(calls) == 2
    monkeypatch.setattr(runtime, "run_process", lambda *a, **k: subprocess.CompletedProcess([], 0, "Logged in using an API key", ""))
    monkeypatch.setattr(runtime, "_cache", (None, 0, None))
    assert runtime.readiness(settings)["reason_code"] == "codex_login_required"


def test_container_task_mcp_resolves_actual_backend_and_sanitized_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-inherit")
    monkeypatch.setenv("DATABASE_URL", "do-not-inherit")
    monkeypatch.setenv("HWP_CODEX_MODEL", "configured-model")
    settings = capture_settings()
    assert "OPENAI_API_KEY" not in dict(settings.environment)
    assert "DATABASE_URL" not in dict(settings.environment)
    assert settings.model == "configured-model"
    args = command("codex", "/tmp/synthetic", settings)
    assert f'mcp_servers.hwp_document.env.PYTHONPATH="{BACKEND}"' in args
    assert (BACKEND / "app/rewrite_mcp.py").is_file()
    assert '--ignore-user-config' in args and '--ephemeral' in args


def test_durable_quota_counts_accepted_requests_and_rollback_does_not(accounts, monkeypatch):
    _, app, url, folder = accounts
    monkeypatch.setenv("HWP_CODEX_DAILY_REQUEST_LIMIT", "2")
    with transaction(app.state.engine) as db:
        runtime.reserve_usage(db, "one", "owner")
    with pytest.raises(RuntimeError):
        with transaction(app.state.engine) as db:
            runtime.reserve_usage(db, "rolled-back", "owner")
            raise RuntimeError("synthetic rollback")
    with transaction(app.state.engine) as db:
        runtime.reserve_usage(db, "two", "owner")
    with transaction(app.state.engine) as db:
        assert len(list(db.scalars(select(runtime.CodexUsage)))) == 2
        with pytest.raises(HTTPException) as exc:
            runtime.reserve_usage(db, "three", "owner")
        assert exc.value.status_code == 429
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(url, folder)) as other:
        with transaction(other.app.state.engine) as db, pytest.raises(HTTPException):
            runtime.reserve_usage(db, "after-restart", "owner")


def test_report_idempotency_does_not_spend_daily_quota_again(report_env, monkeypatch):
    import uuid
    (client, app, _, _), calls = report_env
    monkeypatch.setenv("HWP_CODEX_DAILY_REQUEST_LIMIT", "1")
    body = {"id": str(uuid.uuid4()), "prompt": "합성 자료로 보고서를 작성하세요.", "research": False}
    assert client.post("/api/report-tasks", json=body).status_code == 200
    ready(client, body)
    assert client.post("/api/report-tasks", json=body).status_code == 200
    assert client.post("/api/report-tasks", json={**body, "id": str(uuid.uuid4())}).status_code == 429
    assert calls == [body]
    with transaction(app.state.engine) as db:
        assert len(list(db.scalars(select(runtime.CodexUsage)))) == 1
