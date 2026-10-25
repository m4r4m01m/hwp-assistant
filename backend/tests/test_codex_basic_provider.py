"""Real bounded stdio/cancellation with a synthetic child; zero model calls."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from threading import BoundedSemaphore, Event

import pytest
from fastapi import HTTPException

from app import codex_chat_review as basic
from app.codex_runner import CodexSettings
from app.rewrite_capacity import ExecutionSlot


@pytest.fixture
def rpc(tmp_path, monkeypatch):
    (tmp_path / "response.json").write_text('{"answer":"합성 답변"}', encoding="utf-8")
    mode = ["valid"]
    monkeypatch.setattr(basic, "stream_command", lambda settings, instructions, research:
        [sys.executable, str(Path(__file__).with_name("codex_basic_rpc_fixture.py")), str(tmp_path), mode[0]])
    settings = CodexSettings(sys.executable, "synthetic-model", "http://127.0.0.1:8000", (), timeout=3)
    return tmp_path, mode, basic.CodexBasicProvider(settings)


def ask(provider, active=lambda: True):
    return asyncio.run(provider.chat({"facts": "합성 사실"}, [{"role": "user", "content": "합성 질문"}], active=active))


def assert_child_reaped(folder):
    pid = int((folder / "pid").read_text())
    if os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


@pytest.mark.parametrize("mode", ["valid", "legacy", "summary-only", "duplicate-summary", "commentary-after-final", "retry-notice"])
def test_only_authoritative_completed_chat_answers_are_returned(rpc, mode):
    folder, selected, provider = rpc
    selected[0] = mode
    assert ask(provider) == "합성 답변"
    turn = json.loads((folder / "turn-request.json").read_text())
    assert turn["params"]["outputSchema"]["required"] == ["answer"]
    assert turn["params"]["outputSchema"]["additionalProperties"] is False
    assert turn["params"]["input"][0]["text"].endswith(
        basic.canonical({"app_context": {"facts": "합성 사실"}, "conversation": [{"role": "user", "content": "합성 질문"}]}))
    assert_child_reaped(folder)


@pytest.mark.parametrize("mode", ["commentary-only", "async-only", "delta-only", "nonjson-after-final", "failed",
    "tool-request", "tool-item", "web-item", "terminal-tool", "oversize-delta", "conflicting-summary", "nonjson-after-final-summary",
    "summary-commentary-conflict", "summary-async-conflict", "summary-phase-and-async-conflict", "commentary-then-final-same-id"])
def test_partial_async_failed_or_out_of_scope_output_is_rejected_without_raw_error(rpc, mode):
    folder, selected, provider = rpc
    selected[0] = mode
    with pytest.raises(HTTPException) as failure:
        ask(provider)
    assert failure.value.status_code == 502
    assert "SECRET-MUST-NOT-ECHO" not in str(failure.value.detail)
    assert_child_reaped(folder)


@pytest.mark.parametrize("mode", ["bad-account", "bad-config", "bad-thread"])
def test_login_route_and_readonly_execution_contract_are_enforced(rpc, mode):
    folder, selected, provider = rpc
    selected[0] = mode
    with pytest.raises(HTTPException) as failure:
        ask(provider)
    assert failure.value.status_code == 503
    assert "SECRET-MUST-NOT-ECHO" not in str(failure.value.detail)
    assert not (folder / "turn-request.json").exists()
    assert_child_reaped(folder)


def test_review_uses_strict_suggestion_schema_without_applying_anything(rpc):
    folder, _, provider = rpc
    (folder / "response.json").write_text('{"replacement":"합성 수정안","reason":"합성 이유"}', encoding="utf-8")
    result = asyncio.run(provider.review("합성 원문"))
    assert result.model_dump() == {"replacement": "합성 수정안", "reason": "합성 이유"}
    turn = json.loads((folder / "turn-request.json").read_text())
    assert turn["params"]["outputSchema"]["required"] == ["replacement", "reason"]
    assert "text_to_review" in turn["params"]["input"][0]["text"]
    assert_child_reaped(folder)


def test_repeated_terminal_item_does_not_double_count_valid_escaped_output(rpc):
    folder, selected, provider = rpc
    selected[0] = "duplicate-summary"
    # Legal 12000-character text can occupy >64KB as escaped model JSON. The
    # same completed item appears in its event and the authoritative turn summary.
    (folder / "response.json").write_text(json.dumps({"answer": "가" * 12000}), encoding="utf-8")
    assert ask(provider) == "가" * 12000
    assert_child_reaped(folder)


@pytest.mark.parametrize("raw", ['{"answer":""}', '{"answer":1}', '{"answer":"a","answer":"b"}',
    '{"answer":"SECRET-MUST-NOT-ECHO\\ud800"}', '{"answer":"text","unexpected":true}',
    '{"answer":"x\\u0000"}', '{"answer":NaN}'])
def test_invalid_model_json_never_echoes_content(rpc, raw):
    folder, _, provider = rpc
    (folder / "response.json").write_text(raw, encoding="utf-8")
    with pytest.raises(HTTPException) as failure:
        ask(provider)
    assert failure.value.status_code == 502
    assert "SECRET-MUST-NOT-ECHO" not in str(failure.value.detail)


def test_deadline_kills_the_owned_child_and_does_not_retry(rpc):
    folder, selected, provider = rpc
    selected[0] = "wait"
    provider.settings = CodexSettings(sys.executable, "synthetic-model", "http://127.0.0.1:8000", (), timeout=.15)
    started = time.monotonic()
    with pytest.raises(HTTPException) as failure:
        ask(provider)
    assert failure.value.status_code == 504
    assert time.monotonic() - started < 4
    assert_child_reaped(folder)


@pytest.mark.parametrize("configured, expected", [(240, 210), (3, 3), (.15, .15)])
def test_basic_transport_deadline_leaves_ingress_headroom_and_preserves_shorter_limits(rpc, monkeypatch, configured, expected):
    folder, _, provider = rpc
    provider.settings = CodexSettings(sys.executable, "synthetic-model", "http://127.0.0.1:8000", (), timeout=configured)
    deadlines = []

    def capture_deadline(*args, **kwargs):
        deadlines.append(kwargs["timeout"])
        # Capture the actual transport boundary without waiting 210 seconds or
        # starting a model. Real deadline/process cleanup is exercised above.
        raise TimeoutError("synthetic deadline capture")

    monkeypatch.setattr(basic, "ReportRpc", capture_deadline)
    with pytest.raises(HTTPException) as failure:
        ask(provider)
    assert failure.value.status_code == 504
    assert deadlines == [expected]
    assert not (folder / "pid").exists()


def test_inactive_request_never_starts_a_process(rpc):
    folder, _, provider = rpc
    with pytest.raises(HTTPException) as failure:
        ask(provider, lambda: False)
    assert failure.value.status_code == 409
    assert not (folder / "pid").exists()


def test_repeated_async_cancellation_waits_for_cli_cleanup_before_releasing_capacity(rpc, monkeypatch):
    folder, selected, provider = rpc
    selected[0] = "wait"
    cleanup_started, cleanup_release = Event(), Event()
    original = basic.ReportRpc

    class SlowCleanup(original):
        def __exit__(self, *args):
            try:
                super().__exit__(*args)
            finally:
                cleanup_started.set()
                assert cleanup_release.wait(3)

    monkeypatch.setattr(basic, "ReportRpc", SlowCleanup)
    capacity = BoundedSemaphore(1)

    async def exercise():
        async def admitted():
            with ExecutionSlot(capacity) as slot:
                slot.reserve()
                await provider.chat({}, [{"role": "user", "content": "합성 질문"}])

        task = asyncio.create_task(admitted())
        deadline = time.monotonic() + 3
        while not (folder / "started").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        task.cancel()
        assert await asyncio.to_thread(cleanup_started.wait, 3)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        assert not capacity.acquire(blocking=False)
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert capacity.acquire(blocking=False)
        capacity.release()

    asyncio.run(exercise())
    assert_child_reaped(folder)


def test_disconnected_request_cancels_stdio_and_no_result_escapes(rpc):
    folder, selected, provider = rpc
    selected[0] = "wait"

    class Request:
        disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    request = Request()

    async def exercise():
        async with basic.request_active(request) as active:
            task = asyncio.create_task(provider.chat({}, [{"role": "user", "content": "합성 질문"}], active=active))
            deadline = time.monotonic() + 3
            while not (folder / "started").exists():
                assert time.monotonic() < deadline
                await asyncio.sleep(.01)
            request.disconnected = True
            with pytest.raises(HTTPException) as failure:
                await task
            assert failure.value.status_code == 409

    asyncio.run(exercise())
    assert_child_reaped(folder)


def test_provider_selection_is_explicit_and_default_http_is_preserved(monkeypatch):
    for kind in ("chat", "review"):
        variable = "HWP_" + kind.upper() + "_PROVIDER"
        monkeypatch.delenv(variable, raising=False)
        assert basic.provider_name(kind) == "llm"
        monkeypatch.setenv(variable, "codex")
        assert basic.provider_name(kind) == "codex"
        monkeypatch.setenv(variable, "invalid-private-value")
        with pytest.raises(HTTPException) as failure:
            basic.provider_name(kind)
        assert "invalid-private-value" not in str(failure.value.detail)
    sentinel = object()
    assert basic.capture_provider("llm", lambda: sentinel) is sentinel
