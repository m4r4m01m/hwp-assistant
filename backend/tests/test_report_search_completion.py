"""Search lifecycle/summary identity fences, using pinned 0.153 item shapes."""
import copy
import json
import os
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError
from app import report_stream
from app.codex_runner import CodexSettings
from app.report_contract import ReportDraft, report_hash
from app.report_errors import ReportGenerationError
from test_reports import DRAFT


SEARCH = {"type": "webSearch", "id": "search", "query": "PRIVATE-SYNTHETIC-QUERY",
          "action": {"type": "search", "query": "PRIVATE-SYNTHETIC-QUERY", "queries": None},
          "results": [{"url": "https://example.invalid/PRIVATE-RESULT"}]}
START = {"type": "webSearch", "id": "search", "query": "", "action": None, "results": None}
ANSWER = {"type": "agentMessage", "id": "answer", "text": json.dumps(DRAFT),
          "phase": "final_answer", "delivery": None}


@pytest.mark.parametrize("research", [False, True])
def test_report_command_exposes_only_web_for_enabled_research(tmp_path, research):
    settings = CodexSettings("codex-pinned", "gpt-6-astra", "", (), timeout=240)
    command = report_stream.stream_command(settings, tmp_path / "instructions.txt", research)
    overrides = dict(part.split("=", 1) for i, part in enumerate(command) if i and command[i - 1] == "-c")
    config = {key: json.loads(value) for key, value in overrides.items()}
    assert config["web_search"] == ("live" if research else "disabled")
    flag = "features.code_mode.direct_only_tool_namespaces"
    assert config.get(flag) == (["web"] if research else None)
    assert (flag in config) is research
    assert config["model"] == "gpt-6-astra" and config["model_provider"] == "openai"
    assert config["approval_policy"] == "never" and config["sandbox_mode"] == "read-only"
    assert command[:4] == ["codex-pinned", "app-server", "--listen", "stdio://"]
    assert command.count("app-server") == 1
    assert command.count("--disable") == len(report_stream.DISABLED)
    assert "unified_exec" in report_stream.DISABLED and "shell_tool" in report_stream.DISABLED


def event(method, **params):
    return {"method": method, "params": {"threadId": "thread", "turnId": "turn", **params}}


def finish(items=None, status="completed", **params):
    notification = event("turn/completed", turn={"id": "turn", "status": status,
                         "items": [copy.deepcopy(ANSWER)] if items is None else items}, **params)
    notification["params"].pop("turnId", None)
    return notification


def run(tmp_path, monkeypatch, notifications, *, research=True, policy="allow_unverified", timeout=5):
    fixture = tmp_path / "notifications.json"
    fixture.write_text(json.dumps(notifications), encoding="utf-8")
    monkeypatch.setattr(report_stream, "stream_command", lambda *_: [sys.executable,
        str(Path(__file__).with_name("report_search_rpc_fixture.py")), str(fixture)])
    value = {"research": research}
    if policy is not None:
        value["research_policy"] = policy
    progress = []
    result = report_stream.generate_stream(value,
        CodexSettings(sys.executable, "test-model", "", tuple(os.environ.items()), timeout=timeout),
        str(tmp_path), "report", "test", lambda state: progress.append(copy.deepcopy(state)), lambda: True)
    assert "PRIVATE" not in json.dumps(result[1]) + json.dumps(progress)
    return *result, progress


@pytest.mark.parametrize("mode", ["summary", "started_summary", "completed_summary", "repeated"])
def test_search_completion_merges_event_and_summary_once(tmp_path, monkeypatch, mode):
    messages = []
    if mode in ("started_summary", "repeated"):
        provisional = {**START, "query": "PRIVATE-PROVISIONAL", "action": {"type": "search", "query": "PRIVATE-PROVISIONAL"}}
        messages.append(event("item/started", item=provisional if mode == "started_summary" else START))
    if mode in ("completed_summary", "repeated"):
        messages.append(event("item/completed", item=SEARCH))
    if mode == "repeated":
        messages.append(event("item/completed", item=SEARCH))
    # Omitted optional results and explicit null have the same identity.
    search = {key: value for key, value in SEARCH.items() if key != "results"} if mode == "summary" else SEARCH
    messages.append(finish([search, {**search, "results": search.get("results")}, ANSWER]))
    draft, evidence, progress = run(tmp_path, monkeypatch, messages)
    started = int(mode in ("started_summary", "repeated"))
    assert evidence["completed"] is True and evidence["web_search_calls"] == 1
    assert evidence["web_search_started"] == started and evidence["web_search_incomplete"] == 0
    assert draft["research_notice"]["status"] == "completed"
    assert draft["research_notice"]["source_verification"] == "unverified"
    assert progress[-1]["research_calls"] == 1


@pytest.mark.parametrize("action", [
    {"type": "search", "query": None, "queries": ["PRIVATE"]},
    {"type": "openPage", "url": "https://example.invalid/PRIVATE"},
    {"type": "findInPage", "url": None, "pattern": "PRIVATE"},
    {"type": "other"},
])
def test_pinned_web_action_variants_need_no_invented_status(tmp_path, monkeypatch, action):
    search = {**SEARCH, "action": action}
    draft, evidence, _ = run(tmp_path, monkeypatch, [finish([search, ANSWER])])
    assert evidence["web_search_calls"] == 1 and draft["research_notice"]["completed_calls"] == 1


@pytest.mark.parametrize("changed", [
    {"query": None}, {"query": {}}, {"results": "invalid"}, {"action": "invalid"},
    {"action": {"type": "open_page", "url": None}},
    {"action": {"type": "search", "query": 42}},
    {"action": {"type": "search", "queries": 1}},
    {"action": {"type": "search", "queries": [True]}},
    {"action": {"type": "openPage", "url": []}},
    {"action": {"type": "findInPage", "url": None, "pattern": False}},
])
@pytest.mark.parametrize("source", ["event", "summary"])
def test_malformed_pinned_search_fields_are_not_completion_evidence(tmp_path, monkeypatch, changed, source):
    search = {**SEARCH, **changed}
    notifications = [finish([search, ANSWER])] if source == "summary" else [event("item/completed", item=search), finish()]
    with pytest.raises(ReportGenerationError, match="protocol_error"):
        run(tmp_path, monkeypatch, notifications)


@pytest.mark.parametrize("started", [False, True])
def test_null_action_history_snapshot_cannot_promote_started_search(tmp_path, monkeypatch, started):
    messages = [event("item/started", item=START)] if started else []
    draft, evidence, _ = run(tmp_path, monkeypatch, messages + [finish([START, ANSWER])])
    assert evidence["web_search_calls"] == 0
    assert evidence["web_search_started"] == evidence["web_search_incomplete"] == 1
    assert draft["research_notice"]["status"] == "unverified"


@pytest.mark.parametrize("source", ["event", "summary", "started"])
def test_research_off_rejects_search_in_every_delivery_scope(tmp_path, monkeypatch, source):
    messages = [finish([SEARCH, ANSWER])] if source == "summary" else [
        event("item/started" if source == "started" else "item/completed", item=SEARCH), finish()]
    with pytest.raises(ReportGenerationError, match="scope_violation"):
        run(tmp_path, monkeypatch, messages, research=False)


@pytest.mark.parametrize("item_id", [None, 1, False, [], {}, "", " \t"])
@pytest.mark.parametrize("source", ["event", "summary"])
def test_search_ids_are_valid_strings_before_deduplication(tmp_path, monkeypatch, item_id, source):
    search = {**SEARCH, "id": item_id}
    messages = [finish([search, ANSWER])] if source == "summary" else [event("item/completed", item=search), finish()]
    with pytest.raises(ReportGenerationError, match="protocol_error"):
        run(tmp_path, monkeypatch, messages)


@pytest.mark.parametrize("changed", [
    {"query": "different"}, {"action": {"type": "openPage", "url": None}},
    {"results": []}, {"type": "agentMessage", "text": "{}"},
])
def test_conflicting_same_id_completion_fails_instead_of_counting(tmp_path, monkeypatch, changed):
    with pytest.raises(ReportGenerationError, match="protocol_error"):
        run(tmp_path, monkeypatch, [event("item/completed", item=SEARCH), finish([{**SEARCH, **changed}, ANSWER])])


@pytest.mark.parametrize("item_id", ["search", None, 1, [], ""])
def test_delta_id_cannot_collide_with_search_or_bypass_string_identity(tmp_path, monkeypatch, item_id):
    with pytest.raises(ReportGenerationError, match="protocol_error"):
        run(tmp_path, monkeypatch, [event("item/completed", item=SEARCH),
            event("item/agentMessage/delta", itemId=item_id, delta=ANSWER["text"]), finish()])


@pytest.mark.parametrize("delta", [None, 1, []])
def test_nontext_delta_is_protocol_error(tmp_path, monkeypatch, delta):
    with pytest.raises(ReportGenerationError, match="protocol_error"):
        run(tmp_path, monkeypatch, [event("item/agentMessage/delta", itemId="answer", delta=delta), finish()])


@pytest.mark.parametrize("foreign", ["threadId", "turnId", "nested", "missing_thread", "missing_nested"])
def test_foreign_or_unattributed_summary_cannot_contribute_search_or_final(tmp_path, monkeypatch, foreign):
    wrong = finish([SEARCH, {**ANSWER, "text": "{}"}])
    if foreign == "nested":
        wrong["params"]["turn"]["id"] = "other"
    elif foreign == "missing_nested":
        wrong["params"]["turn"].pop("id")
    elif foreign.startswith("missing_"):
        wrong["params"].pop("threadId")
    else:
        wrong["params"][foreign] = "other"
    draft, evidence, _ = run(tmp_path, monkeypatch, [wrong, finish()])
    assert evidence["web_search_calls"] == 0 and draft["research_notice"]["status"] == "unverified"


@pytest.mark.parametrize("status", ["failed", "interrupted", "inProgress"])
def test_noncompleted_turn_cannot_promote_summary_search_or_valid_draft(tmp_path, monkeypatch, status):
    with pytest.raises(ReportGenerationError, match="model_failed"):
        run(tmp_path, monkeypatch, [finish([SEARCH, ANSWER], status=status)])


@pytest.mark.parametrize("final", ["delta", "invalid", "commentary", "async", "nonjson", "replayed_old_json"])
def test_search_completion_never_relaxes_authoritative_final_validation(tmp_path, monkeypatch, final):
    messages = [event("item/completed", item=SEARCH)]
    items = []
    if final == "delta":
        messages.append(event("item/agentMessage/delta", itemId="answer", delta=ANSWER["text"]))
    elif final == "replayed_old_json":
        messages += [event("item/completed", item=ANSWER), event("item/completed", item={**ANSWER, "id": "later", "text": "not JSON"})]
        items = [ANSWER]
    else:
        changed = {"text": "{}"} if final == "invalid" else {"phase": "commentary"} if final == "commentary" else {"delivery": "async"} if final == "async" else {"text": "not JSON"}
        items = [{**ANSWER, **changed}]
    with pytest.raises((ReportGenerationError, ValidationError)):
        run(tmp_path, monkeypatch, messages + [finish(items)])


def test_legacy_completed_summary_uses_original_draft_bytes_without_notice(tmp_path, monkeypatch):
    draft, evidence, _ = run(tmp_path, monkeypatch, [finish([SEARCH, ANSWER])], policy=None)
    original = ReportDraft.model_validate(DRAFT).model_dump()
    assert draft == original and report_hash(draft) == report_hash(original)
    assert "research_notice" not in draft and "research" not in evidence


def test_search_summary_without_completed_turn_times_out_without_draft(tmp_path, monkeypatch):
    messages = [event("item/completed", item=SEARCH), event("item/completed", item=ANSWER)]
    with pytest.raises(TimeoutError):
        run(tmp_path, monkeypatch, messages, timeout=.15)
