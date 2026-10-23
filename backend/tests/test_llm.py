import asyncio
import pytest
import httpx
from fastapi import HTTPException
from app import llm
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

    monkeypatch.setattr(llm, "pooled_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fail)))
    with pytest.raises(HTTPException) as result:
        asyncio.run(provider.models())
    assert result.value.status_code == status


@pytest.mark.parametrize("declared", [None, str(3 * 1024 * 1024)])
def test_oversized_model_body_stops_reading_and_does_not_close_shared_pool(provider, declared):
    class LargeBody(httpx.AsyncByteStream):
        chunks, closed = 0, False
        async def __aiter__(self):
            for _ in range(10):
                self.chunks += 1
                yield b"x" * (1024 * 1024)
        async def aclose(self):
            self.closed = True
    body = LargeBody()
    calls = []
    def respond(request):
        calls.append(1)
        return (httpx.Response(200, stream=body, headers={"Content-Length": declared} if declared else {}) if len(calls) == 1 else
                httpx.Response(200, json={"data": [{"id": "synthetic-model"}]}))
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            marker = llm.request_client.set(client)
            try:
                with pytest.raises(HTTPException) as result:
                    await provider.models()
                assert result.value.status_code == 502
                assert body.chunks == (0 if declared else 3) and body.closed
                assert not client.is_closed
                assert await provider.models() == ["synthetic-model"]
            finally:
                llm.request_client.reset(marker)
    asyncio.run(check())


@pytest.mark.parametrize("cancel", [False, True])
def test_model_stream_is_closed_on_total_timeout_or_cancellation(provider, monkeypatch, cancel):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0.05")
    provider = OpenAICompatible()
    closed = []
    async def check():
        started = asyncio.Event()
        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"{"
                started.set()
                await asyncio.sleep(10)
            async def aclose(self):
                closed.append(True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, stream=SlowBody()))) as client:
            marker = llm.request_client.set(client)
            try:
                task = asyncio.create_task(provider.models())
                await started.wait()
                if cancel:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(HTTPException) as result:
                        await task
                    assert result.value.status_code == 504
                assert closed == [True] and not client.is_closed
            finally:
                llm.request_client.reset(marker)
    asyncio.run(check())
