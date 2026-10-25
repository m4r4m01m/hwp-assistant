"""New policy, authoritative completion and legacy input fences; synthetic stdio only."""
import copy
import json
import os
from pathlib import Path
import sys
import time
import uuid
from threading import Event
import pytest
from app import report_stream, reports
from app.codex_runner import CodexSettings
from app.report_contract import ReportDraft, report_hash
from app.report_errors import ReportGenerationError
from app.report_research import apply_research_policy, policy_prompt
from test_reports import DRAFT, report_env, ready
from test_collaboration import secured, as_user


def stream(tmp_path, monkeypatch, mode, *, policy="allow_unverified", draft=None):
    (tmp_path / "draft.json").write_text(json.dumps(draft or DRAFT), encoding="utf-8")
    (tmp_path / "release").touch()
    arguments = [sys.executable, str(Path(__file__).with_name("report_rpc_fixture.py")), str(tmp_path), mode]
    monkeypatch.setattr(report_stream, "stream_command", lambda *args: arguments)
    value = {"research": True, "prompt": "공개 합성 동향 보고서"}
    if policy is not None:
        value["research_policy"] = policy
    return report_stream.generate_stream(value,
        CodexSettings(sys.executable, "test-model", "", tuple(os.environ.items()), timeout=5),
        str(tmp_path), "report", "test", lambda _: None, lambda: True)


@pytest.mark.parametrize(("mode", "completed", "started", "incomplete", "status"), [
    ("no_research", 0, 0, 0, "unverified"),
    ("foreign_research", 0, 0, 0, "unverified"),
    ("start_only_research", 0, 1, 1, "unverified"),
    ("mixed_research", 1, 2, 1, "unverified"),
    ("complete", 1, 1, 0, "completed"),
])
def test_only_completed_valid_draft_can_return_with_frozen_research_observations(tmp_path, monkeypatch, mode, completed, started, incomplete, status):
    draft, evidence = stream(tmp_path, monkeypatch, mode)
    assert evidence["completed"] is True and evidence["web_search_calls"] == completed
    notice = draft["research_notice"]
    assert notice["status"] == status and notice["completed_calls"] == completed
    assert notice["started_calls"] == started and notice["incomplete_calls"] == incomplete
    assert notice["source_verification"] == "unverified"
    assert ("research_incomplete" in [warning["code"] for warning in notice["warnings"]]) == (status == "unverified")
    assert evidence["research"]["status"] == status and evidence["warnings"] == notice["warnings"]
    assert {key: value for key, value in draft.items() if key != "research_notice"} == ReportDraft.model_validate(DRAFT).model_dump()


@pytest.mark.parametrize("policy", [None, "require_completed"])
def test_missing_or_explicit_strict_policy_keeps_zero_search_failure(tmp_path, monkeypatch, policy):
    with pytest.raises(ReportGenerationError) as caught:
        stream(tmp_path, monkeypatch, "no_research", policy=policy)
    assert caught.value.code == "research_unavailable"


def test_legacy_nonzero_completed_gate_stays_unchanged_with_unfinished_search(tmp_path, monkeypatch):
    draft, evidence = stream(tmp_path, monkeypatch, "mixed_research", policy=None)
    assert evidence["completed"] is True and evidence["web_search_calls"] == 1
    assert "research_notice" not in draft and "research" not in evidence and "warnings" not in evidence


@pytest.mark.parametrize("mode", ["failed", "invalid", "delta_only", "commentary_only", "async_only", "disconnect"])
def test_allowing_unverified_does_not_relax_completion_or_report_validation(tmp_path, monkeypatch, mode):
    with pytest.raises(Exception):
        stream(tmp_path, monkeypatch, mode)


def test_model_cannot_assert_server_owned_provenance(tmp_path, monkeypatch):
    valid, _ = apply_research_policy({"research": True, "research_policy": "allow_unverified"},
        copy.deepcopy(DRAFT), {"completed": True, "web_search_calls": 0})
    with pytest.raises(ReportGenerationError) as caught:
        stream(tmp_path, monkeypatch, "complete", draft=valid)
    assert caught.value.code == "invalid_report"
    schema = ReportDraft.generation_schema()
    assert "research_notice" not in schema["properties"]
    assert "research_notice" not in schema["required"]
    assert "ReportResearchNotice" not in schema["$defs"]


def test_api_freezes_explicit_policy_and_retries_without_second_model_call(report_env, monkeypatch):
    (client, _, _, _), _ = report_env
    calls = []
    def generate(value, *_):
        calls.append(copy.deepcopy(value))
        return copy.deepcopy(DRAFT), {"completed": True, "web_search_calls": 0}
    monkeypatch.setattr(reports, "generate_report", generate)
    body = {"id": str(uuid.uuid4()), "research": True, "research_policy": "allow_unverified", "prompt": "원래 요청을 변경하지 않는 합성 보고서"}
    assert client.post("/api/report-tasks", json=body).status_code == 200
    task = ready(client, body)
    assert task["status"] == "ready" and task["error"] == ""
    assert task["input"] == body and calls == [body]
    assert task["draft_sha256"] == report_hash(task["draft"])
    assert task["execution"]["research"]["status"] == "unverified"
    assert task["approval"] is None and task["preview_id"] is None
    assert client.get("/api/documents").json() == []
    assert client.post("/api/report-tasks", json=body).json() == task and len(calls) == 1
    assert client.post("/api/report-tasks", json={**body, "research_policy": "require_completed"}).status_code == 409
    assert client.post("/api/report-tasks", json={**body, "research_policy": "invalid"}).status_code == 422
    as_user(client, "bob")
    assert client.get("/api/report-tasks/" + body["id"]).status_code == 404


def test_legacy_input_hash_and_draft_shape_remain_exact(report_env):
    (client, _, _, _), calls = report_env
    body = {"id": str(uuid.uuid4()), "research": False, "prompt": "기존 요청 형태 합성 보고서"}
    assert client.post("/api/report-tasks", json=body).status_code == 200
    task = ready(client, body)
    assert task["input"] == body and calls == [body]
    assert task["execution"]["input_sha256"] == report_hash(body)
    assert "research_notice" not in task["draft"]
    assert "research" not in task["execution"] or task["execution"]["research"] is False
    assert "warnings" not in task["execution"]
    assert client.post("/api/report-tasks", json=body).json() == task


def test_cancelled_new_policy_task_never_promotes_late_valid_output(report_env, monkeypatch):
    (client, _, _, _), _ = report_env
    entered, release, exited = Event(), Event(), Event()
    def generate(value, settings, progress, active):
        progress({"stage": "writing", "blocks": [{"kind": "title", "text": "실제 합성 부분 출력"}], "research_calls": 0})
        entered.set()
        try:
            assert release.wait(5)
            return copy.deepcopy(DRAFT), {"completed": True, "web_search_calls": 0}
        finally:
            exited.set()
    monkeypatch.setattr(reports, "generate_report", generate)
    body = {"id": str(uuid.uuid4()), "research": True, "research_policy": "allow_unverified", "prompt": "취소 경계 합성 보고서"}
    try:
        assert client.post("/api/report-tasks", json=body).status_code == 200
        assert entered.wait(2)
        url = "/api/report-tasks/" + body["id"]
        partial = client.get(url).json()
        assert partial["draft"] is None and partial["status"] == "running"
        cancelled = client.post(url + "/cancel").json()
        release.set()
        assert exited.wait(2)
        time.sleep(.05)
        assert client.get(url).json() == cancelled
        assert client.get("/api/documents").json() == []
    finally:
        release.set()


def test_policy_instruction_is_new_request_only_and_does_not_change_user_prompt():
    prompt = "기존 고정 작성 지침"
    value = {"research": True, "prompt": "원본 사용자 주제", "research_policy": "allow_unverified"}
    before = copy.deepcopy(value)
    assert policy_prompt({}, prompt) == prompt
    assert policy_prompt({"research_policy": "require_completed"}, prompt) == prompt
    assert "explicitly allows an unverified draft" in policy_prompt(value, prompt)
    assert value == before
