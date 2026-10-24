"""Hosted discovery is authenticated metadata, never a live model probe."""
import os

import pytest

from app import llm
from test_collaboration import secured, TOKENS
from test_storage import create


@pytest.fixture
def hosted(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("LLM_"):
            monkeypatch.delenv(name)
    for name, value in {
        "LLM_PROVIDER": "openrouter", "LLM_MODEL": "synthetic/pinned-model",
        "LLM_API_KEY": "synthetic-secret-do-not-expose", "LLM_ALLOW_EXTERNAL": "true",
        "HWP_LLM_ALLOWED_USERS": "alice", "HWP_REWRITE_PROVIDER": "llm",
        "HWP_REPORT_PROVIDER": "llm",
    }.items():
        monkeypatch.setenv(name, value)


def test_hosted_model_configuration_does_not_claim_connectivity(secured, hosted, monkeypatch):
    client, app, _, _ = secured
    async def forbidden(*args, **kwargs):
        raise AssertionError("Metadata discovery must not contact a model")
    monkeypatch.setattr(llm.OpenAICompatible, "request", forbidden)
    # Metadata lookup remains available while this user has a generation running.
    with app.state.ai_budget.reserve("alice"):
        response = client.get("/api/ai/models")
    assert response.status_code == 200
    assert response.json() == {
        "models": ["synthetic/pinned-model"], "configured_model": "synthetic/pinned-model",
        "model_configured": True, "model_available": None, "connectivity": "unchecked",
        "provider": "openrouter", "model": "synthetic/pinned-model", "external": True,
        "endpoint_scope": "external",
    }
    assert "synthetic-secret" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_report_capabilities_match_http_permissions_and_missing_search(secured, hosted):
    client, _, _, _ = secured
    result = client.get("/api/capabilities")
    report = result.json()["features"]["reports"]
    assert report["configured"] and report["allowed"]
    assert report["backend"] == "llm" and report["provider"] == "openrouter"
    assert report["supports_stream"] is False and report["supports_research"] is False
    assert report["reachable"] is None
    assert "synthetic-secret" not in result.text and "https://" not in result.text
    client.headers["Authorization"] = "Bearer " + TOKENS["bob"]
    assert client.get("/api/capabilities").json()["features"]["reports"]["allowed"] is False
    assert client.get("/api/ai/models").status_code == 403
    client.headers.pop("Authorization")
    assert client.get("/api/capabilities").status_code == 401


def test_hosted_opt_in_failure_keeps_document_features_available(secured, hosted, monkeypatch):
    client, _, _, _ = secured
    monkeypatch.setenv("LLM_ALLOW_EXTERNAL", "false")
    result = client.get("/api/capabilities").json()["features"]
    assert result["chat"]["configured"] is False
    assert result["reports"]["configured"] is False
    assert client.get("/api/ai/models").status_code == 503
    assert client.get("/api/documents").status_code == 200


def test_unknown_report_backend_is_disabled_without_contacting_provider(secured, hosted, monkeypatch):
    client, _, _, _ = secured
    monkeypatch.setenv("HWP_REPORT_PROVIDER", "automatic-fallback")
    report = client.get("/api/capabilities").json()["features"]["reports"]
    assert report["configured"] is False and report["allowed"] is False
    assert report["reason_code"] == "invalid_configuration"


@pytest.mark.parametrize("operation", ["models", "review"])
def test_global_capacity_rejection_does_not_spend_user_quota(secured, hosted, monkeypatch, operation):
    client, app, _, _ = secured
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LLM_ALLOW_EXTERNAL", "false")
    app.state.ai_budget.limit = 1
    calls = []

    async def models(provider):
        calls.append("models")
        return [provider.model]

    async def review(_provider, _original):
        calls.append("review")
        return llm.Suggestion(replacement="합성 수정안", reason="합성 확인")

    monkeypatch.setattr(llm.OpenAICompatible, "models", models)
    monkeypatch.setattr(llm.OpenAICompatible, "review", review)
    if operation == "models":
        request = lambda: client.get("/api/ai/models")
    else:
        document, lease = create(client)
        payload = {"document_id": document["id"], "base_version": document["current_version"],
                   "revision": 0, "original": "원본", "target": {"sectionIndex": 0, "paragraphIndex": 0}}
        request = lambda: client.post("/api/ai/review", json=payload, headers={"X-Edit-Session": lease})

    held = 0
    while app.state.rewrite_capacity.acquire(blocking=False):
        held += 1
    try:
        assert request().status_code == 429
        assert not calls and not app.state.ai_budget.recent
        assert "alice" not in app.state.ai_budget.active
    finally:
        for _ in range(held):
            app.state.rewrite_capacity.release()

    # The rejected admission has not consumed this user's sole minute allowance.
    accepted = request()
    assert accepted.status_code == 200, accepted.text
    assert calls == [operation] and len(app.state.ai_budget.recent["alice"]) == 1
    assert "alice" not in app.state.ai_budget.active
    assert request().status_code == 429 and calls == [operation]
    # A user-quota rejection also releases its temporary global reservation.
    released = 0
    while app.state.rewrite_capacity.acquire(blocking=False):
        released += 1
    try:
        assert released == held
    finally:
        for _ in range(released):
            app.state.rewrite_capacity.release()
