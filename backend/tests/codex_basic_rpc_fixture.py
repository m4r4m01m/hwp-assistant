"""Synthetic app-server child: no credentials, real documents, network or AI."""
import json
import os
import sys
import time
from pathlib import Path

folder, mode = Path(sys.argv[1]), sys.argv[2]
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
(folder / "pid").write_text(str(os.getpid()), encoding="ascii")


def send(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def event(method, **params):
    send({"method": method, "params": {"threadId": "synthetic-thread", "turnId": "synthetic-turn", **params}})


for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "initialized":
        continue
    result = {}
    if method == "account/read":
        result = {"account": {"type": "apiKey" if mode == "bad-account" else "chatgpt"}}
    if method == "config/read":
        result = {"config": {"mcp_servers": {"synthetic_disabled": {}}}}
        if mode == "bad-config":
            result["config"]["openai_base_url"] = "https://private.invalid/SECRET-MUST-NOT-ECHO"
    if method == "thread/start":
        assert request["params"]["config"] == {"mcp_servers.synthetic_disabled.enabled": False}
        assert request["params"]["ephemeral"] is True
        assert request["params"]["sandbox"] == "read-only"
        result = {"thread": {"id": "synthetic-thread"}, "model": "synthetic-model", "modelProvider": "openai",
                  "approvalPolicy": "always" if mode == "bad-thread" else "never",
                  "sandbox": {"type": "readOnly"}, "instructionSources": []}
    if method == "turn/start":
        (folder / "turn-request.json").write_text(json.dumps(request), encoding="utf-8")
        result = {"turn": {"id": "synthetic-turn"}}
    send({"id": request["id"], "result": result})
    if method != "turn/start":
        continue
    (folder / "started").touch()
    if mode == "wait":
        while True:
            time.sleep(.01)
    raw = (folder / "response.json").read_text(encoding="utf-8")
    if mode == "tool-request":
        send({"id": 999, "method": "item/tool/call", "params": {"message": "SECRET-MUST-NOT-ECHO"}})
    if mode == "tool-item":
        event("item/started", item={"id": "tool", "type": "mcpToolCall"})
    if mode == "web-item":
        event("item/completed", item={"id": "web", "type": "webSearch"})
    if mode == "retry-notice":
        event("error", willRetry=True, error={"message": "SECRET-MUST-NOT-ECHO"})
    event("item/agentMessage/delta", itemId="answer", delta="x" * 131073 if mode == "oversize-delta" else raw)
    # Foreign-thread completions cannot become this request's result.
    send({"method": "item/completed", "params": {"threadId": "foreign-thread", "turnId": "synthetic-turn",
          "item": {"id": "foreign", "type": "agentMessage", "text": '{"answer":"WRONG"}', "phase": "final_answer"}}})
    item = {"id": "answer", "type": "agentMessage", "text": raw,
            "phase": "commentary" if mode in {"commentary-only", "commentary-then-final-same-id"} else None if mode == "legacy" else "final_answer",
            "delivery": "async" if mode == "async-only" else None}
    if mode not in {"delta-only", "summary-only"}:
        event("item/completed", item=item)
    if mode in {"nonjson-after-final", "nonjson-after-final-summary"}:
        event("item/completed", item={**item, "id": "later", "text": "The old JSON was not my final answer."})
    if mode == "commentary-after-final":
        event("item/completed", item={**item, "id": "later", "text": "Commentary", "phase": "commentary"})
    terminal = [item] if mode in {"summary-only", "duplicate-summary", "nonjson-after-final-summary"} else [{"type": "commandExecution"}] if mode == "terminal-tool" else []
    if mode == "conflicting-summary":
        terminal = [{**item, "text": '{"answer":"conflicting completed value"}'}]
    if mode in {"summary-commentary-conflict", "summary-async-conflict", "summary-phase-and-async-conflict", "commentary-then-final-same-id"}:
        terminal = [{**item,
            "phase": "final_answer" if mode == "commentary-then-final-same-id" else "commentary" if "commentary" in mode or "phase-and" in mode else item["phase"],
            "delivery": "async" if "async" in mode else item["delivery"]}]
    event("turn/completed", turn={"id": "synthetic-turn", "status": "failed" if mode == "failed" else "completed",
          "items": terminal, "error": {"message": "SECRET-MUST-NOT-ECHO"} if mode == "failed" else None})
