"""Provider selection and displayed capability must agree, without inference."""
import sys
from dataclasses import replace

import pytest

from app import codex_runner, codex_runtime
from test_collaboration import secured


@pytest.mark.parametrize("kind", ["chat", "review"])
def test_codex_capability_and_connection_use_codex_acl_not_http(secured, monkeypatch, kind):
    client, _, _, _ = secured
    monkeypatch.setenv("HWP_" + kind.upper() + "_PROVIDER", "codex")
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    monkeypatch.delenv("HWP_CODEX_ALLOWED_USERS", raising=False)
    monkeypatch.setenv("HWP_LLM_ALLOWED_USERS", "bob")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    settings = replace(codex_runner.capture_settings(), executable=sys.executable)
    monkeypatch.setattr(codex_runner, "capture_settings", lambda: settings)
    state = {"configured": True, "reason_code": None, "message": "합성 ChatGPT 로그인 확인"}
    monkeypatch.setattr(codex_runtime, "readiness", lambda _: state)
    capability = client.get("/api/capabilities").json()["features"][kind]
    connection = next(item for item in client.get("/api/ai-connection").json()["features"] if item["id"] == kind)
    assert capability["provider"] == connection["provider"] == "codex"
    assert capability["configured"] and connection["configured"]
    assert capability["allowed"] and connection["allowed"]
    assert capability["reachable"] is None
    # The HTTP model-list route remains explicitly HTTP, separate from Codex readiness.
    assert client.get("/api/ai/models").status_code == 403
    state.update(configured=False, reason_code="codex_login_required", message="합성 서버 로그인 필요")
    assert client.get("/api/capabilities").json()["features"][kind]["reason_code"] == "codex_login_required"
    monkeypatch.setenv("HWP_CODEX_USER", "bob")
    state.update(configured=True, reason_code=None)
    assert not client.get("/api/capabilities").json()["features"][kind]["allowed"]


@pytest.mark.parametrize("kind", ["chat", "review"])
def test_explicit_http_and_legacy_default_stay_http_despite_available_codex(secured, monkeypatch, kind):
    client, _, _, _ = secured
    monkeypatch.delenv("HWP_" + kind.upper() + "_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    before = client.get("/api/capabilities").json()["features"][kind]
    assert before["provider"] == "llm" and before["reason_code"] == "llm_not_configured"
    monkeypatch.setenv("HWP_" + kind.upper() + "_PROVIDER", "llm")
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LLM_MODEL", "synthetic-http-model")
    configured = client.get("/api/capabilities").json()["features"][kind]
    assert configured["provider"] == "llm" and configured["configured"]
    monkeypatch.setenv("HWP_" + kind.upper() + "_PROVIDER", "unsupported")
    invalid = client.get("/api/capabilities").json()["features"][kind]
    assert invalid["provider"] == "unknown" and invalid["reason_code"] == "invalid_configuration"
    assert not invalid["configured"] and not invalid["allowed"]
