"""Safe execution-only diagnostics survive completed synthetic report turns."""
import copy
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

from app import report_stream, reports
from app.codex_runner import CodexSettings
from app.report_contract import report_hash
from app.report_errors import retain_upstream_details
from test_collaboration import secured
from test_reports import DRAFT, ready, report_env


SAFE = {"protocol_error": "httpConnectionFailed", "http_status": 503}
PRIVATE = "SYNTHETIC-PRIVATE-ERROR-QUERY-AND-CREDENTIAL-MARKER"
KNOWN = {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 503,
    "message": PRIVATE}}, "message": PRIVATE, "additionalDetails": PRIVATE,
    "query": PRIVATE, "authorization": PRIVATE}
UNKNOWN = {"codexErrorInfo": {"unknownPrivateVariant": {"httpStatusCode": 403}},
    "message": PRIVATE}


@pytest.mark.parametrize("unknown", [None, "private error text", {}, UNKNOWN])
def test_unknown_notice_retains_only_prior_allowlisted_diagnostic(unknown):
    previous = {**SAFE, "message": PRIVATE, "query": PRIVATE}
    assert retain_upstream_details(previous, unknown) == SAFE
    assert previous["message"] == PRIVATE  # Caller dictionaries are not mutated.


@pytest.mark.parametrize("previous", [None, "private", {},
    {"protocol_error": "unknown", "http_status": 503},
    {"protocol_error": ["httpConnectionFailed"], "http_status": 503}])
def test_unknown_notice_does_not_create_or_preserve_undocumented_diagnostic(previous):
    assert retain_upstream_details(previous, UNKNOWN) == {}


@pytest.mark.parametrize("status", [True, False, "503", 99, 600, None, {}, []])
def test_diagnostic_status_requires_documented_integer_http_range(status):
    error = {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": status,
        "message": PRIVATE}}, "additionalDetails": PRIVATE}
    assert retain_upstream_details({}, error) == {"protocol_error": "httpConnectionFailed"}


def test_known_new_error_replaces_old_kind_and_status_as_one_observation():
    assert retain_upstream_details(SAFE, {"codexErrorInfo": "rateLimitExceeded",
        "message": PRIVATE}) == {"protocol_error": "rateLimitExceeded"}
    assert retain_upstream_details({}, KNOWN) == SAFE


def install_rpc(monkeypatch, errors, *, error_thread="thread", error_turn="turn"):
    """Use the pinned request/result shapes without a process or model call."""
    calls = []
    raw = json.dumps(DRAFT, ensure_ascii=False)

    class Rpc:
        def __init__(self, *args, **kwargs):
            self.events = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send(self, request):
            method = request["method"]
            calls.append(method)
            if method == "initialized":
                return
            result = {}
            if method == "account/read":
                result = {"account": {"type": "chatgpt"}}
            elif method == "config/read":
                result = {"config": {}}
            elif method == "thread/start":
                result = {"thread": {"id": "thread"}, "model": "test-model",
                    "modelProvider": "openai", "approvalPolicy": "never",
                    "sandbox": {"type": "readOnly"}, "instructionSources": []}
            elif method == "turn/start":
                result = {"turn": {"id": "turn"}}
            self.events.append({"id": request["id"], "result": result})
            if method == "turn/start":
                for error in errors:
                    self.events.append({"method": "error", "params": {
                        "threadId": error_thread, "turnId": error_turn, "willRetry": True,
                        "error": copy.deepcopy(error)}})
                self.events.extend([
                    {"method": "item/completed", "params": {"threadId": "thread",
                        "turnId": "turn", "item": {"type": "agentMessage", "id": "answer",
                            "phase": "final_answer", "text": raw}}},
                    {"method": "turn/completed", "params": {"threadId": "thread",
                        "turn": {"id": "turn", "status": "completed", "items": []}}},
                ])

        def receive(self, **kwargs):
            return self.events.pop(0)

    monkeypatch.setattr(report_stream, "ReportRpc", Rpc)
    return calls


def generate(tmp_path, value, progress=lambda _: None, active=lambda: True):
    return report_stream.generate_stream(value,
        CodexSettings(sys.executable, "test-model", "", tuple(os.environ.items()), timeout=5),
        str(tmp_path), "report", "test", progress, active)


def test_completed_warning_draft_retains_safe_retry_diagnostic_outside_draft_hash(tmp_path, monkeypatch):
    value = {"research": True, "research_policy": "allow_unverified", "prompt": "합성 공개자료 보고서"}
    install_rpc(monkeypatch, [])
    without_error, without_evidence = generate(tmp_path, value)
    assert "upstream_diagnostics" not in without_evidence
    calls = install_rpc(monkeypatch, [KNOWN, UNKNOWN])
    draft, evidence = generate(tmp_path, value)
    assert calls.count("turn/start") == 1  # Same turn, no automatic second inference.
    assert evidence["completed"] is True and evidence["upstream_diagnostics"] == SAFE
    assert draft["research_notice"]["status"] == "unverified"
    assert draft["research_notice"]["completed_calls"] == 0
    assert any(warning["code"] == "research_incomplete" for warning in draft["research_notice"]["warnings"])
    assert draft == without_error and report_hash(draft) == report_hash(without_error)
    assert PRIVATE not in json.dumps({"draft": draft, "execution": evidence})


def test_warning_api_persists_safe_execution_record_and_idempotent_ready_result(report_env, tmp_path, monkeypatch):
    (client, _, _, _), _ = report_env
    calls = install_rpc(monkeypatch, [KNOWN, UNKNOWN])
    monkeypatch.setattr(reports, "generate_report", lambda value, settings, progress, active:
        generate(tmp_path, value, progress, active))
    body = {"id": str(uuid.uuid4()), "research": True, "research_policy": "allow_unverified",
        "prompt": "합성 자료 조회 진단 보고서"}
    assert client.post("/api/report-tasks", json=body).status_code == 200
    task = ready(client, body)
    assert task["status"] == "ready" and task["error"] == ""
    assert task["execution"]["upstream_diagnostics"] == SAFE
    assert task["execution"]["research"]["status"] == "unverified"
    assert task["draft_sha256"] == report_hash(task["draft"])
    assert "upstream_diagnostics" not in task["draft"]
    assert task["approval"] is None and task["preview_id"] is None
    assert PRIVATE not in json.dumps(task)
    assert client.post("/api/report-tasks", json=body).json() == task
    assert calls.count("turn/start") == 1
    assert client.get("/api/documents").json() == []


@pytest.mark.parametrize(("errors", "expected"), [([UNKNOWN], None), ([KNOWN], SAFE)])
def test_legacy_draft_hash_stays_exact_and_only_known_notice_adds_execution_diagnostic(tmp_path, monkeypatch, errors, expected):
    value = {"research": False, "prompt": "기존 형태 합성 보고서"}
    install_rpc(monkeypatch, [])
    baseline, _ = generate(tmp_path, value)
    install_rpc(monkeypatch, errors)
    draft, evidence = generate(tmp_path, value)
    assert draft == baseline and report_hash(draft) == report_hash(baseline)
    assert "research_notice" not in draft
    if expected is None:
        assert "upstream_diagnostics" not in evidence
    else:
        assert evidence["upstream_diagnostics"] == expected
    assert PRIVATE not in json.dumps(evidence)


@pytest.mark.parametrize(("thread", "turn"), [("foreign", "turn"), ("thread", "foreign")])
def test_another_thread_or_turn_cannot_supply_this_reports_upstream_diagnostic(tmp_path, monkeypatch, thread, turn):
    install_rpc(monkeypatch, [KNOWN], error_thread=thread, error_turn=turn)
    draft, evidence = generate(tmp_path, {"research": True, "research_policy": "allow_unverified",
        "prompt": "합성 요청별 진단 분리"})
    assert evidence["completed"] is True and "upstream_diagnostics" not in evidence
    assert draft["research_notice"]["status"] == "unverified"


def test_actual_stdio_completed_turn_keeps_allowlisted_retry_diagnostic(tmp_path, monkeypatch):
    (tmp_path / "draft.json").write_text(json.dumps(DRAFT), encoding="utf-8")
    (tmp_path / "release").touch()
    arguments = [sys.executable, str(Path(__file__).with_name("report_rpc_fixture.py")), str(tmp_path), "retry_complete"]
    monkeypatch.setattr(report_stream, "stream_command", lambda *args: arguments)
    draft, evidence = generate(tmp_path, {"research": True, "research_policy": "allow_unverified",
        "prompt": "합성 실제 stdio 진단"})
    assert evidence["completed"] is True and evidence["upstream_diagnostics"] == SAFE
    assert "PRIVATE-MODEL-ERROR" not in json.dumps(evidence)
    assert "PRIVATE-DETAILS" not in json.dumps(evidence)
    assert "upstream_diagnostics" not in draft
