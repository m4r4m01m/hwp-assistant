import asyncio
import pytest
import httpx
from fastapi import HTTPException
from app.llm import OpenAICompatible


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("LLM_MODEL", "test-only-model")
    return OpenAICompatible()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        '{"replacement":"","reason":"x"}',
        '{"replacement":"x","reason":"x","command":"bad"}',
    ],
)
def test_invalid_mock_output_rejected(provider, content, monkeypatch):
    async def request(*args):
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(OpenAICompatible, "request", request)
    with pytest.raises(HTTPException) as e:
        asyncio.run(provider.review("원문"))
    assert e.value.status_code == 502


def test_mock_valid_output_and_stateless_prompt(provider, monkeypatch):
    requests = []

    async def request(*args):
        requests.append(args[3])
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"replacement":"개선 문장","reason":"명확성"}'
                    }
                }
            ]
        }

    monkeypatch.setattr(OpenAICompatible, "request", request)
    assert asyncio.run(provider.review("원문")).replacement == "개선 문장"
    asyncio.run(provider.review("두 번째"))
    assert len(requests[1]["messages"]) == 2
    assert "원문" not in requests[1]["messages"][1]["content"]


@pytest.mark.parametrize(
    "error,status",
    [(httpx.ConnectError("offline"), 502), (httpx.ReadTimeout("slow"), 504)],
)
def test_mock_transport_failures(provider, monkeypatch, error, status):
    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(httpx.AsyncClient, "request", fail)
    with pytest.raises(HTTPException) as result:
        asyncio.run(provider.models())
    assert result.value.status_code == status
