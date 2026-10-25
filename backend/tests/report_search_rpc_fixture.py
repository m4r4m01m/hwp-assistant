"""Replay synthetic notification shapes over stdio; no Codex/model invocation."""
import json
import sys
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
events = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "initialized":
        continue
    result = {}
    if method == "account/read":
        result = {"account": {"type": "chatgpt"}}
    if method == "config/read":
        result = {"config": {}}
    if method == "thread/start":
        result = {"thread": {"id": "thread"}, "model": "test-model", "modelProvider": "openai",
                  "approvalPolicy": "never", "sandbox": {"type": "readOnly"}, "instructionSources": []}
    if method == "turn/start":
        result = {"turn": {"id": "turn"}}
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
    if method == "turn/start":
        for event in events:
            print(json.dumps(event), flush=True)
