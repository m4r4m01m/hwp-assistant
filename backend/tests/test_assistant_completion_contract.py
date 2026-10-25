"""Authoritative conversation/design completions over synthetic real stdio."""
import copy
import json
import os
from pathlib import Path
import sys

import pytest

from app import assistant_runner, document_assistant
from app.codex_runner import CodexSettings
from app.report_rpc import ReportRpc
from test_assistant_design_proposals import designs
from test_collaboration import secured
from test_document_assistant import assistant, create


REAL_RUN_TURN = document_assistant.run_turn
RAW = json.dumps({"answer": "합성 디자인 제안", "rewrite_instruction": None,
    "design_suggestions": [{"kind": "design", "id": "reference-table", "sha256": "b" * 64,
                            "reason": "선택한 표의 합성 서식 후보입니다."}]})
FINAL = {"type": "agentMessage", "id": "answer", "text": RAW, "phase": "final_answer", "delivery": None}


def event(method, **params):
    return {"method": method, "params": {"threadId": "thread", "turnId": "turn", **params}}


def terminal(items=(), status="completed"):
    # Pinned TurnCompletedNotification has threadId + turn, no outer turnId.
    return {"method": "turn/completed", "params": {"threadId": "thread",
        "turn": {"id": "turn", "status": status, "items": list(items)}}}


def install_stdio(tmp_path, monkeypatch, notifications, *, on_received=None):
    fixture = tmp_path / "assistant-events.json"
    fixture.write_text(json.dumps(notifications, ensure_ascii=False), encoding="utf-8")
    sent = []
    instances = []

    class RecordingRpc(ReportRpc):
        def __enter__(self):
            result = super().__enter__()
            instances.append(self)
            return result

        def send(self, request):
            sent.append(copy.deepcopy(request))
            return super().send(request)

        def receive(self, *args, **kwargs):
            result = super().receive(*args, **kwargs)
            if on_received:
                on_received(result)
            return result

    monkeypatch.setattr(assistant_runner, "ReportRpc", RecordingRpc)
    monkeypatch.setattr(assistant_runner, "stream_command", lambda *_: [sys.executable,
        str(Path(__file__).with_name("report_search_rpc_fixture.py")), str(fixture)])
    settings = CodexSettings(sys.executable, "test-model", "", tuple(os.environ.items()), timeout=5)
    return settings, sent, instances


def value_with_design():
    return {"message": "선택한 표의 디자인을 제안해 주세요.", "design_context": {
        "source_sha256": "a" * 64, "target": {"kind": "table", "section": 0, "paragraph": 0, "control": 0}}}


@pytest.mark.parametrize("mode", ["event", "legacy", "legacy-omitted", "summary", "repeated", "commentary-after-final", "foreign-events"])
def test_only_authoritative_final_design_message_finishes_the_same_turn(tmp_path, monkeypatch, mode):
    final = {**FINAL, "phase": None} if mode == "legacy" else FINAL
    if mode == "legacy-omitted":
        final = {key: value for key, value in FINAL.items() if key not in ("phase", "delivery")}
    messages = [] if mode == "summary" else [event("item/completed", item=final)]
    if mode == "commentary-after-final":
        messages.append(event("item/completed", item={**FINAL, "id": "commentary", "phase": "commentary", "text": "{}"}))
    if mode == "foreign-events":
        wrong_thread = event("item/completed", item={**FINAL, "id": "foreign", "text": "{}"})
        wrong_thread["params"]["threadId"] = "another-thread"
        wrong_turn = terminal([{**FINAL, "text": "{}"}])
        wrong_turn["params"]["turn"]["id"] = "another-turn"
        wrong_event_turn = event("item/completed", item={**FINAL, "id": "foreign-turn", "text": "{}"})
        wrong_event_turn["params"]["turnId"] = "another-turn"
        messages.extend([wrong_thread, wrong_turn, wrong_event_turn])
    messages.append(terminal([final] if mode in ("summary", "repeated") else []))
    settings, sent, instances = install_stdio(tmp_path, monkeypatch, messages)
    result, evidence = assistant_runner.codex_answer(value_with_design(), settings, lambda: True)
    assert len(result.design_suggestions) == 1 and result.answer == "합성 디자인 제안"
    assert evidence["executor"] == "codex-app-server" and evidence["web_search_calls"] == 0
    assert sum(request["method"] == "turn/start" for request in sent) == 1
    assert instances[0].process.poll() is not None
    assert all(not reader.is_alive() for reader in instances[0].readers)


@pytest.mark.parametrize("mode", ["commentary", "async", "delta", "later-nonjson", "replayed-old-json",
    "summary-commentary-conflict", "summary-async-conflict", "summary-phase-and-delivery-conflict",
    "commentary-to-final-same-id", "type-conflict", "failed", "interrupted", "terminal-tool"])
def test_commentary_partial_failed_or_conflicting_design_messages_never_become_final(tmp_path, monkeypatch, mode):
    messages = []
    items = []
    if mode == "delta":
        messages.append(event("item/agentMessage/delta", itemId="answer", delta=RAW))
    elif mode in ("commentary", "async"):
        item = {**FINAL, **({"phase": "commentary"} if mode == "commentary" else {"delivery": "async"})}
        messages.append(event("item/completed", item=item))
        items = [item]
    elif mode in ("later-nonjson", "replayed-old-json"):
        messages.extend([event("item/completed", item=FINAL),
                         event("item/completed", item={**FINAL, "id": "later", "text": "최종 비JSON 합성 응답"})])
        items = [FINAL] if mode == "replayed-old-json" else []
    elif mode == "commentary-to-final-same-id":
        messages.append(event("item/completed", item={**FINAL, "phase": "commentary"}))
        items = [FINAL]
    else:
        messages.append(event("item/completed", item=FINAL))
        if mode.startswith("summary-"):
            items = [{**FINAL, **({"phase": "commentary"} if "commentary" in mode else {"delivery": "async"})}]
            if mode == "summary-phase-and-delivery-conflict":
                items[0].update(phase="commentary", delivery="async")
        elif mode == "type-conflict":
            items = [{"type": "reasoning", "id": "answer", "summary": [], "content": []}]
        elif mode == "terminal-tool":
            items = [{"type": "webSearch", "id": "forbidden"}]
    messages.append(terminal(items, status=mode if mode in ("failed", "interrupted") else "completed"))
    settings, sent, _ = install_stdio(tmp_path, monkeypatch, messages)
    with pytest.raises(RuntimeError):
        assistant_runner.codex_answer(value_with_design(), settings, lambda: True)
    assert sum(request["method"] == "turn/start" for request in sent) == 1


@pytest.mark.parametrize("source", ["event", "summary", "delta"])
@pytest.mark.parametrize("invalid_id", ["absent", None, "", False, []])
def test_incomplete_item_identity_cannot_supply_a_design_answer(tmp_path, monkeypatch, source, invalid_id):
    item = {**FINAL, "id": invalid_id}
    if invalid_id == "absent":
        item.pop("id")
    if source == "summary":
        messages = [terminal([item])]
    elif source == "delta":
        params = {} if invalid_id == "absent" else {"itemId": invalid_id}
        messages = [event("item/agentMessage/delta", delta=RAW, **params), terminal([FINAL])]
    else:
        messages = [event("item/completed", item=item), terminal()]
    settings, _, _ = install_stdio(tmp_path, monkeypatch, messages)
    with pytest.raises(RuntimeError):
        assistant_runner.codex_answer(value_with_design(), settings, lambda: True)


@pytest.mark.parametrize("missing", ["threadId", "turnId"])
def test_unattributed_message_cannot_supply_this_turns_final(tmp_path, monkeypatch, missing):
    message = event("item/completed", item=FINAL)
    message["params"].pop(missing)
    settings, _, _ = install_stdio(tmp_path, monkeypatch, [message, terminal()])
    with pytest.raises(RuntimeError):
        assistant_runner.codex_answer(value_with_design(), settings, lambda: True)


def test_conflicting_outer_terminal_turn_does_not_replace_the_nested_fence(tmp_path, monkeypatch):
    wrong = terminal([{**FINAL, "text": "{}"}])
    wrong["params"]["turnId"] = "another-turn"
    settings, _, _ = install_stdio(tmp_path, monkeypatch, [wrong, terminal([FINAL])])
    result, _ = assistant_runner.codex_answer(value_with_design(), settings, lambda: True)
    assert len(result.design_suggestions) == 1


def test_final_active_check_rejects_cancellation_after_terminal_was_received(tmp_path, monkeypatch):
    active = [True]
    def received(message):
        if message.get("method") == "turn/completed":
            active[0] = False
    settings, _, instances = install_stdio(tmp_path, monkeypatch, [terminal([FINAL])], on_received=received)
    with pytest.raises(RuntimeError, match="수신이 중단"):
        assistant_runner.codex_answer(value_with_design(), settings, lambda: active[0])
    assert instances[0].process.poll() is not None


@pytest.mark.parametrize("with_design", [False, True])
def test_strict_output_schema_and_request_preserve_optional_design_contract(tmp_path, monkeypatch, with_design):
    value = value_with_design() if with_design else {"message": "합성 질문"}
    original = copy.deepcopy(value)
    raw = RAW if with_design else json.dumps({"answer": "합성 답변", "rewrite_instruction": None})
    settings, sent, _ = install_stdio(tmp_path, monkeypatch, [terminal([{**FINAL, "text": raw}])])
    result, _ = assistant_runner.codex_answer(value, settings, lambda: True)
    request = next(item["params"] for item in sent if item["method"] == "turn/start")
    schema = request["outputSchema"]
    assert set(schema["properties"]) == set(schema["required"]) == ({"answer", "rewrite_instruction", "design_suggestions"} if with_design else {"answer", "rewrite_instruction"})
    assert all("default" not in prop for prop in schema["properties"].values())
    if with_design:
        assert schema["properties"]["design_suggestions"]["maxItems"] == 3
        assert schema["$defs"]["DesignSuggestion"]["additionalProperties"] is False
        assert len(result.design_suggestions) == 1
    else:
        assert "$defs" not in schema and result.design_suggestions == []
    assert json.loads(request["input"][0]["text"].split("\n", 1)[1]) == original == value


@pytest.mark.parametrize("mode", ["commentary-replay", "invalid-schema"])
def test_invalid_design_cannot_publish_or_leak_model_text_through_real_worker_completion(designs, tmp_path, monkeypatch, mode):
    fixture, body, _, _ = designs
    client, app, doc, _, _, _, calls, folder = fixture
    original = {path.name: path.read_bytes() for path in folder.iterdir()}
    private = "SYNTHETIC-PRIVATE-MODEL-ANSWER"
    invalid = json.dumps({"answer": {"private": private}, "rewrite_instruction": None})
    messages = [event("item/completed", item={**FINAL, "phase": "commentary"}),
        event("item/completed", item={**FINAL, "id": "last", "text": "최종 비JSON 합성 응답"}), terminal()]
    if mode == "invalid-schema":
        messages = [terminal([{**FINAL, "text": invalid}])]
    settings, sent, _ = install_stdio(tmp_path, monkeypatch, messages)
    monkeypatch.setattr(document_assistant, "configured_provider", lambda: ("codex", settings))
    monkeypatch.setattr(document_assistant, "codex_allowed", lambda _: True)
    monkeypatch.setattr(document_assistant, "require_codex_ready", lambda _: None)
    assert create(fixture, body).status_code == 200
    assert len(calls) == 1
    REAL_RUN_TURN(*calls[0])
    task = client.get(f"/api/assistant-turns/{body['id']}").json()
    assert task["status"] == "failed" and task["answer"] is None and task["design_proposals"] == []
    assert task["execution"]["completed"] is False
    assert private not in json.dumps(task)
    assert create(fixture, body).json() == task and len(calls) == 1
    assert sum(request["method"] == "turn/start" for request in sent) == 1
    assert len(client.get(f"/api/documents/{doc['id']}/versions").json()) == 1
    assert original == {path.name: path.read_bytes() for path in folder.iterdir()}
