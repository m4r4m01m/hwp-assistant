"""Explicit test-only HTTP fixture. No model, Codex, upstream calls, or document writes.

Run only on loopback: python -m uvicorn mock_local_provider:app --app-dir backend/tests --host 127.0.0.1 --port <test port>
"""
import asyncio
import hashlib
import json
from fastapi import FastAPI, Request, HTTPException

app = FastAPI(title="SYNTHETIC MOCK provider — not a language model")
calls = []
controls = {"delay": 0, "failure": "", "hold": False}
response_gate = asyncio.Event()


@app.get("/v1/models")
def models():
    return {"data": [{"id": "hwp-synthetic-fixture", "object": "model"}]}


@app.post("/controls")
async def configure(request: Request):
    value = await request.json()
    controls.update(delay=min(max(float(value.get("delay", 0)), 0), 300), failure=value.get("failure", ""), hold=bool(value.get("hold", False)))
    if controls["hold"]:
        response_gate.clear()
    return {"mock": True, **controls}


@app.post("/release")
async def release():
    response_gate.set()
    return {"mock": True, "released": True}


@app.get("/proof")
def proof():
    return {"mock": True, "actual_model_calls": 0, "calls": calls}


@app.post("/v1/chat/completions")
async def generate(request: Request):
    value = await request.json()
    if value.get("model") != "hwp-synthetic-fixture":
        raise HTTPException(400, "Only the synthetic fixture model is supported")
    payload = json.loads(value["messages"][1]["content"])
    call = {"model": value["model"], "input": payload, "finished": False,
            "input_sha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    calls.append(call)
    settings = dict(controls)
    call["held"] = settings["hold"]
    if settings["hold"]:
        await asyncio.wait_for(response_gate.wait(), 15)
    await asyncio.sleep(settings["delay"])
    call["finished"] = True
    if settings["failure"] == "http":
        raise HTTPException(503, "Synthetic provider failure")
    if payload.get("_assistant_contract") == "live-document-conversation-v1":
        # Deliberately fixed language: this verifies transport/context contracts,
        # never actual model quality or reasoning about the document.
        content = json.dumps({
            "answer": "MOCK 문서 대화 응답: " + payload["message"],
            "rewrite_instruction": "사실과 부정 표현을 유지하며 업무 문체로 다듬어 주세요."
                if payload.get("selection") and "다듬" in payload["message"] else None,
        }, ensure_ascii=False)
        if settings["failure"] == "json":
            content = '{"answer":'
        return {"model": "hwp-synthetic-fixture", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}
    original = payload["selected_paragraph"]
    candidates = [{"replacement": original + " 확인했습니다.", "reason": "MOCK 시험 후보 1"},
                  {"replacement": original + " 검토했습니다.", "reason": "MOCK 시험 후보 2"}]
    if settings["failure"] == "page-growth":
        candidates[0]["replacement"] = "가😀" * 2000
    content = json.dumps({"candidates": candidates}, ensure_ascii=False)
    if settings["failure"] == "json":
        content = '{"candidates":['
    return {"model": "hwp-synthetic-fixture", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}
