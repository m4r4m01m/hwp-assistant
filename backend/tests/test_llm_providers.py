"""Offline provider contract tests. All tokens, models and responses are synthetic."""
import asyncio
import copy
import json
import os
from dataclasses import FrozenInstanceError

import httpx
import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import llm
from app.llm import OpenAICompatible, completion_text, strict_response_schema


@pytest.fixture(autouse=True)
def clean_llm_environment(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("LLM_"):
            monkeypatch.delenv(name)


def configure(monkeypatch, provider="openrouter", **overrides):
    values = {"LLM_PROVIDER": provider, "LLM_MODEL": "synthetic/pinned-model",
              "LLM_API_KEY": "synthetic-key-never-log", "LLM_ALLOW_EXTERNAL": "true"}
    values.update(overrides)
    for key, value in values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return OpenAICompatible()


def completed(content='{"replacement":"명확한 문장","reason":"명확성"}', **message):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content, **message}}]}


@pytest.mark.parametrize("provider,url,limit_key", [
    ("openrouter", "https://openrouter.ai/api/v1/chat/completions", "max_tokens"),
    ("nvidia", "https://integrate.api.nvidia.com/v1/chat/completions", "max_tokens"),
    ("openai", "https://api.openai.com/v1/chat/completions", "max_completion_tokens"),
])
def test_hosted_presets_send_one_authenticated_bounded_request(monkeypatch, provider, url, limit_key):
    client = configure(monkeypatch, provider, LLM_MAX_OUTPUT_TOKENS="512", LLM_TEMPERATURE="omit")
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=completed())
    monkeypatch.setattr(llm, "pooled_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    assert asyncio.run(client.review("원문" )).replacement == "명확한 문장"
    assert len(calls) == 1
    request = calls[0]
    body = json.loads(request.content)
    assert request.method == "POST" and str(request.url) == url
    assert request.headers["authorization"] == "Bearer synthetic-key-never-log"
    assert body["model"] == "synthetic/pinned-model" and body[limit_key] == 512
    assert "temperature" not in body and "response_format" not in body
    assert ("max_tokens" in body) != ("max_completion_tokens" in body)
    if provider == "openrouter":
        assert body["provider"] == {"allow_fallbacks": False, "require_parameters": True, "data_collection": "deny"}
    else:
        assert "provider" not in body


@pytest.mark.parametrize("provider", ["openrouter", "nvidia", "openai"])
def test_hosted_requires_both_consent_and_key_without_network(monkeypatch, provider):
    def forbidden():
        pytest.fail("configuration loading must not make a provider call")
    monkeypatch.setattr(llm, "pooled_client", forbidden)
    with pytest.raises(HTTPException) as result:
        configure(monkeypatch, provider, LLM_ALLOW_EXTERNAL="false")
    assert result.value.status_code == 503
    with pytest.raises(HTTPException) as result:
        configure(monkeypatch, provider, LLM_ALLOW_EXTERNAL="true", LLM_API_KEY="")
    assert result.value.status_code == 503


@pytest.mark.parametrize("provider,base", [
    ("openrouter", "http://openrouter.ai/api/v1"),
    ("openrouter", "https://openrouter.ai/api/v1/another-path"),
    ("openrouter", "https://openrouter.ai.attacker.test/api/v1"),
    ("openrouter", "https://openrouter.ai:444/api/v1"),
    ("nvidia", "https://api.nvidia.com/v1"),
    ("openai", "https://api.openai.com/v1?api_key=synthetic-secret"),
    ("openai", "https://name:synthetic-secret@api.openai.com/v1"),
    ("openai", "https://api.openai.com/v1#synthetic-secret"),
    ("openai", "https://api.openai.com\n/v1"),
    ("openai-compatible", "https://api.openai.com/v1"),
    ("openai-compatible", "https://API.OPENAI.COM./v1"),
    ("openai-compatible", "https://openrouter.ai/api/v1"),
    ("openai-compatible", "https://integrate.api.nvidia.com/v1"),
])
def test_preset_cannot_retarget_credentials_or_bypass_explicit_selection(monkeypatch, provider, base):
    with pytest.raises(HTTPException) as result:
        configure(monkeypatch, provider, LLM_BASE_URL=base)
    assert result.value.status_code == 503
    assert "synthetic-secret" not in str(result.value)


@pytest.mark.parametrize("settings", [
    {"LLM_ALLOW_EXTERNAL": "yes"}, {"LLM_TIMEOUT_SECONDS": "nan"},
    {"LLM_TIMEOUT_SECONDS": "241"}, {"LLM_MAX_OUTPUT_TOKENS": "255"},
    {"LLM_MAX_OUTPUT_TOKENS": "32769"}, {"LLM_RESPONSE_FORMAT": "automatic"},
    {"LLM_TEMPERATURE": "nan"}, {"LLM_TEMPERATURE": "-1"},
    {"LLM_TOKEN_LIMIT_PARAMETER": "arbitrary"}, {"LLM_MODEL": "two model ids"},
    {"LLM_MODEL": "openrouter/auto"}, {"LLM_MODEL": "openrouter/free"},
    {"LLM_MODEL": "openrouter/auto-beta"}, {"LLM_MODEL": "openrouter/auto:free"},
    {"LLM_MODEL": "@preset/dynamic-model"},
    {"LLM_MODEL": "vendor/model@preset/dynamic-model"},
    {"LLM_MODEL": "vendor/model:online"}, {"LLM_MODEL": "vendor/model:online:free"},
    {"LLM_MODEL": "vendor/model:free:ONLINE"},
    {"LLM_API_KEY": "synthetic\nsecret"}, {"LLM_OPENROUTER_PROVIDERS": "first,,second"},
    {"LLM_OPENROUTER_ZDR": "maybe"},
])
def test_invalid_execution_configuration_fails_before_sending(monkeypatch, settings):
    with pytest.raises(HTTPException) as result:
        configure(monkeypatch, **settings)
    assert result.value.status_code == 503
    assert "synthetic\nsecret" not in str(result.value)


def test_custom_endpoint_private_declaration_and_external_opt_in(monkeypatch):
    local = configure(monkeypatch, "openai-compatible", LLM_BASE_URL="http://home-llm:8080/v1",
                      LLM_ALLOW_EXTERNAL="false", LLM_API_KEY="")
    assert local.metadata()["endpoint_scope"] == "operator-configured"
    assert local.metadata()["external"] is False
    with pytest.raises(HTTPException):
        configure(monkeypatch, "openai-compatible", LLM_BASE_URL="http://service.example/v1")
    remote = configure(monkeypatch, "openai-compatible", LLM_BASE_URL="https://service.example/v1")
    assert remote.metadata()["external"] is True


@pytest.mark.parametrize("provider", ["openrouter", "nvidia", "openai"])
def test_hosted_model_list_is_configured_selection_not_connection_probe(monkeypatch, provider):
    client = configure(monkeypatch, provider)
    async def forbidden(*args):
        pytest.fail("listing the configured model must not use credentials or make a provider request")
    monkeypatch.setattr(OpenAICompatible, "request", forbidden)
    assert asyncio.run(client.models()) == ["synthetic/pinned-model"]
    assert "synthetic-key-never-log" not in repr(client)
    assert "synthetic-key-never-log" not in json.dumps(client.metadata())
    assert client.base not in json.dumps(client.metadata())


@pytest.mark.parametrize("mode", ["text", "json_object", "json_schema"])
def test_structured_mode_is_opt_in_and_does_not_change_plain_chat(monkeypatch, mode):
    client = configure(monkeypatch, "nvidia", LLM_RESPONSE_FORMAT=mode)
    bodies = []
    async def request(self, method, route, body):
        bodies.append(body)
        return completed()
    monkeypatch.setattr(OpenAICompatible, "request", request)
    asyncio.run(client.review("원문"))
    asyncio.run(client.chat({}, [{"role": "user", "content": "질문"}]))
    assert "response_format" not in bodies[1]
    if mode == "text":
        assert "response_format" not in bodies[0]
    else:
        assert bodies[0]["response_format"]["type"] == mode
    if mode == "json_schema":
        schema = bodies[0]["response_format"]["json_schema"]
        assert schema["strict"] is True and schema["name"] == "suggestion"
        assert schema["schema"]["additionalProperties"] is False


def test_strict_schema_preserves_nullable_contract_nested_refs_and_input():
    class Item(BaseModel):
        model_config = ConfigDict(extra="forbid")
        default: str = ""  # A field named default must not be confused with the schema keyword.
        note: str | None = None
    class Output(BaseModel):
        model_config = ConfigDict(extra="forbid")
        items: list[Item] = Field(default_factory=list)
    original = Output.model_json_schema()
    frozen = copy.deepcopy(original)
    normalized = strict_response_schema(original)
    assert original == frozen
    assert normalized["required"] == ["items"] and normalized["additionalProperties"] is False
    item = normalized["$defs"]["Item"]
    assert item["required"] == ["default", "note"] and item["additionalProperties"] is False
    assert "default" in item["properties"]
    assert "default" not in item["properties"]["default"]
    assert "default" not in item["properties"]["note"]
    assert {part["type"] for part in item["properties"]["note"]["anyOf"]} == {"string", "null"}


def test_openrouter_frozen_routing_has_no_fallback_or_secret_metadata(monkeypatch):
    client = configure(monkeypatch, LLM_OPENROUTER_PROVIDERS="test-upstream", LLM_OPENROUTER_ZDR="true",
                       LLM_RESPONSE_FORMAT="json_schema")
    fingerprint = client.configuration_fingerprint()
    changed_key = configure(monkeypatch, LLM_OPENROUTER_PROVIDERS="test-upstream", LLM_OPENROUTER_ZDR="true",
                            LLM_RESPONSE_FORMAT="json_schema", LLM_API_KEY="synthetic-rotated-key")
    assert changed_key.configuration_fingerprint() == fingerprint
    changed_route = configure(monkeypatch, LLM_OPENROUTER_PROVIDERS="different-upstream", LLM_OPENROUTER_ZDR="true",
                              LLM_RESPONSE_FORMAT="json_schema")
    assert changed_route.configuration_fingerprint() != fingerprint
    bodies = []
    async def request(self, method, route, body):
        bodies.append(body)
        assert self.key == "synthetic-key-never-log"
        return completed()
    monkeypatch.setattr(OpenAICompatible, "request", request)
    asyncio.run(client.review("원문"))
    assert bodies[0]["provider"] == {"allow_fallbacks": False, "require_parameters": True,
                                     "data_collection": "deny", "only": ["test-upstream"], "zdr": True}
    with pytest.raises(FrozenInstanceError):
        client.model = "different-model"


@pytest.mark.parametrize("status,expected", [(401, 502), (403, 502), (400, 502), (404, 502),
                                           (422, 502), (429, 429), (500, 502), (503, 502)])
def test_provider_failures_are_redacted_and_never_retried(monkeypatch, status, expected):
    client = configure(monkeypatch)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "17"},
                              json={"error": "synthetic-key-never-log and private document content"})
    monkeypatch.setattr(llm, "pooled_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    with pytest.raises(HTTPException) as result:
        asyncio.run(client.review("private document content"))
    assert result.value.status_code == expected and len(calls) == 1
    assert "synthetic-key-never-log" not in str(result.value)
    assert "private document content" not in str(result.value)
    if status == 429:
        assert result.value.headers == {"Retry-After": "17"}


def test_redirect_does_not_forward_provider_key(monkeypatch):
    client = configure(monkeypatch)
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://other.example/collect"})
    monkeypatch.setattr(llm, "pooled_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=False, trust_env=False))
    with pytest.raises(HTTPException):
        asyncio.run(client.review("원문"))
    assert calls == ["https://openrouter.ai/api/v1/chat/completions"]


@pytest.mark.parametrize("finish", [None, "length", "tool_calls", "content_filter"])
@pytest.mark.parametrize("operation", ["review", "rewrite", "chat"])
def test_incomplete_output_is_rejected_even_when_content_is_valid(monkeypatch, finish, operation):
    client = configure(monkeypatch)
    value = completed()
    value["choices"][0]["finish_reason"] = finish
    async def request(*args):
        return value
    monkeypatch.setattr(OpenAICompatible, "request", request)
    args = {"review": ("원문",), "rewrite": ("원문", "간결하게"), "chat": ({}, [])}[operation]
    with pytest.raises(HTTPException) as result:
        asyncio.run(getattr(client, operation)(*args))
    assert result.value.status_code == 502


@pytest.mark.parametrize("message", [{"refusal": "declined"}, {"tool_calls": [{"id": "call"}]},
                                     {"function_call": {"name": "tool"}}, {"content": []}, {"content": "  "}])
def test_non_text_response_never_becomes_a_document_proposal(message):
    with pytest.raises(ValueError):
        completion_text(completed(**message))
