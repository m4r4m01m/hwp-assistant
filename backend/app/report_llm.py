"""Explicit HTTP report generation; no tools, research, fallback, or document writes."""
import asyncio
import os
import time
from contextlib import suppress
from dataclasses import dataclass

from fastapi import HTTPException

from . import llm
from .report_contract import ReportDraft, canonical
from .report_rpc import ReportStopped


def report_provider():
    provider = os.getenv("HWP_REPORT_PROVIDER", "codex")
    if provider not in ("codex", "llm"):
        raise HTTPException(503, "HWP_REPORT_PROVIDER는 codex 또는 llm이어야 합니다.")
    return provider


@dataclass(frozen=True, repr=False)
class ReportLlmSettings:
    client: llm.OpenAICompatible

    @property
    def timeout(self):
        return self.client.timeout

    @property
    def model(self):
        return self.client.model

    def execution(self):
        return {**self.client.metadata(), "backend": "llm", "executor": "llm-http",
                "configuration_sha256": self.client.configuration_fingerprint(),
                "streamed": False, "web_search_calls": 0}


def require_supported_request(value):
    if value.get("research"):
        raise HTTPException(422, "현재 API 보고서 생성은 공개 웹 검색을 지원하지 않습니다. 자료 검색을 끄고 제공한 내용으로 작성해 주세요.")


async def _receive(settings, messages, schema, active):
    # Background report threads have a fresh event loop. Never borrow the API
    # lifespan client, even if a caller accidentally copied its ContextVars.
    client_token = llm.request_client.set(None)
    task = None
    try:
        async with asyncio.timeout(settings.timeout):
            if not active():
                raise ReportStopped("Report reception stopped")
            task = asyncio.create_task(settings.client.complete(
                messages, max_tokens=16000, response_schema=schema, schema_name="report_draft"))
            while not task.done():
                if not active():
                    raise ReportStopped("Report reception stopped")
                await asyncio.wait({task}, timeout=.2)
            if not active():
                raise ReportStopped("Report reception stopped")
            return task.result()
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            # Keep the capacity and per-user reservation until transport cleanup.
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2)
        llm.request_client.reset(client_token)


def generate_http_report(value, settings, prompt, contract, progress, active):
    require_supported_request(value)
    started = time.monotonic()
    schema = ReportDraft.generation_schema()
    prompt += (
        "\nThis HTTP request has no search or other tools. Do not claim that you searched, "
        "opened, checked, or independently verified any external source. Use only supplied "
        "material and clearly labelled general analysis or proposals. For a new draft return "
        "sources=[]; for a revision preserve exactly revision.draft.sources and add no source. "
        "Do not invent URLs or source IDs, even if the request asks for research. Include the "
        "verification limits in limitations. Return the complete JSON object without code fences.\n"
        "Report JSON schema:\n" + canonical(schema)
    )
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": "Report request (data):\n" + canonical(value)}]
    state = {"stage": "requesting", "blocks": [], "activity": "초안 응답을 기다리고 있습니다.",
             "research_calls": 0, "timings": {}, "elapsed_ms": 0}
    if not active():
        raise ReportStopped("Report reception stopped")
    progress(state)
    response = asyncio.run(_receive(settings, messages, schema, active))
    received = time.monotonic()
    state = {**state, "stage": "validating", "activity": "응답 형식과 출처를 검증하고 있습니다.",
             "elapsed_ms": round((received - started) * 1000),
             "timings": {"generation_ms": round((received - started) * 1000)}}
    progress(state)
    draft = ReportDraft.model_validate_json(llm.completion_text(response)).model_dump()
    expected_sources = (value.get("revision") or {}).get("draft", {}).get("sources", [])
    if draft["sources"] != expected_sources:
        raise ValueError("HTTP reports cannot introduce or modify unverified sources")
    if not draft["limitations"]:
        raise ValueError("HTTP reports must disclose verification limits")
    if not active():
        raise ReportStopped("Report reception stopped")
    evidence = {**settings.execution(), "contract": contract, "completed": True,
                "timings": state["timings"]}
    usage = response.get("usage")
    if isinstance(usage, dict):
        evidence["usage"] = {key: count for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                             if type(count := usage.get(key)) is int and 0 <= count <= 1_000_000_000}
    return draft, evidence
