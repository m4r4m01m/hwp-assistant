"""Failure provenance/fences through actual stdio, with synthetic model events."""
import copy
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
import uuid
from threading import Event
import pytest
from pydantic import ValidationError
from app import report_stream, reports
from app.codex_runner import CodexSettings
from app.report_contract import ReportDraft
from app.report_errors import ReportGenerationError, failure_details, upstream_details
from app.report_rpc import ReportRpc
from app.db import ReportTask, transaction
from test_reports import DRAFT, report_env, ready
from test_collaboration import secured, as_user


@pytest.mark.parametrize(("mode", "code"), [
    ("no_research", "research_unavailable"),
    ("start_only_research", "research_unavailable"),
    ("foreign_research", "research_unavailable"),
    ("commentary_only", "incomplete_output"),
    ("async_only", "incomplete_output"),
    ("delta_only", "incomplete_output"),
    ("upstream_failed", "model_failed"),
])
def test_completed_partial_or_unresearched_output_never_becomes_draft(tmp_path, monkeypatch, mode, code):
    (tmp_path / "draft.json").write_text(json.dumps(DRAFT), encoding="utf-8")
    (tmp_path / "release").touch()
    arguments = [sys.executable, str(Path(__file__).with_name("report_rpc_fixture.py")), str(tmp_path), mode]
    monkeypatch.setattr(report_stream, "stream_command", lambda *args: arguments)
    snapshots = []
    with pytest.raises(ReportGenerationError) as caught:
        report_stream.generate_stream({"research": True, "prompt": "합성 동향 보고서"},
            CodexSettings(sys.executable, "test-model", "", tuple(os.environ.items()), timeout=5),
            str(tmp_path), "report", "test", lambda value: snapshots.append(copy.deepcopy(value)), lambda: True)
    assert caught.value.code == code
    assert any(value["blocks"] for value in snapshots)  # What the user saw is still display-only.
    if code == "research_unavailable":
        assert snapshots[-1]["research_calls"] == 0
    if mode == "upstream_failed":
        assert failure_details(caught.value) == {"code": "model_failed", "protocol_error": "httpConnectionFailed", "http_status": 503}
    assert "PRIVATE" not in str(caught.value) + json.dumps(failure_details(caught.value))


@pytest.mark.parametrize("mode", ["retry_complete", "summary_only", "legacy_phase", "commentary_after_final"])
def test_authoritative_completed_message_can_finish_same_turn(tmp_path, monkeypatch, mode):
    (tmp_path / "draft.json").write_text(json.dumps(DRAFT), encoding="utf-8")
    (tmp_path / "release").touch()
    arguments = [sys.executable, str(Path(__file__).with_name("report_rpc_fixture.py")), str(tmp_path), mode]
    monkeypatch.setattr(report_stream, "stream_command", lambda *args: arguments)
    draft, evidence = report_stream.generate_stream({"research": True},
        CodexSettings(sys.executable, "test-model", "", tuple(os.environ.items()), timeout=5),
        str(tmp_path), "report", "test", lambda _: None, lambda: True)
    assert draft == ReportDraft.model_validate(DRAFT).model_dump()
    assert evidence["completed"] and evidence["web_search_calls"] == 1
    assert "PRIVATE" not in json.dumps(evidence)


@pytest.mark.parametrize("failure", ["research_unavailable", "timeout", "invalid_report", "unknown"])
def test_api_failure_preserves_partial_owner_idempotency_and_no_save(report_env, monkeypatch, failure):
    (client, _, _, _), _ = report_env
    calls = []
    def generate(value, settings, progress, active):
        calls.append(value)
        progress({"stage": "writing", "research_calls": 0, "blocks": [{"kind": "title", "text": "합성 수신 내용"}], "timings": {}})
        if failure == "invalid_report":
            ReportDraft.model_validate({**DRAFT, "summary": "PRIVATE-INPUT" * 100})
        if failure == "unknown":
            raise RuntimeError("PRIVATE-ERROR token=must-not-be-persisted")
        raise ReportGenerationError(failure)
    monkeypatch.setattr(reports, "generate_report", generate)
    body = {"id": str(uuid.uuid4()), "prompt": "합성 동향 보고서 작성", "research": True}
    assert client.post("/api/report-tasks", json=body).status_code == 200
    task = ready(client, body)
    code = "internal_error" if failure == "unknown" else failure
    assert task["status"] == "failed" and task["draft"] is None and task["preview_id"] is None
    assert task["execution"]["failure"]["code"] == code
    assert task["execution"]["failure"]["stage"] == "writing"
    assert task["execution"]["failure"]["research_calls"] == 0
    assert task["execution"]["progress"]["blocks"][0]["text"] == "합성 수신 내용"
    if failure == "invalid_report":
        assert task["execution"]["failure"]["validation_paths"] == ["summary"]
    assert "PRIVATE" not in json.dumps(task)
    assert client.post("/api/report-tasks", json=body).json() == task and len(calls) == 1
    url = "/api/report-tasks/" + body["id"]
    assert client.post(url + "/preview", json={"id": str(uuid.uuid4()), "draft_sha256": "0" * 64, "seed_base64": "eA=="}).status_code in (409, 422)
    assert client.get("/api/documents").json() == []
    as_user(client, "bob")
    assert client.get(url).status_code == 404


def test_safe_diagnostics_ignore_exception_inputs_unknown_fields_and_upstream_prose():
    with pytest.raises(ValidationError) as caught:
        ReportDraft.model_validate({**DRAFT, "PRIVATE-KEY": "PRIVATE-VALUE", "summary": "PRIVATE-SUMMARY" * 100})
    diagnostic = failure_details(caught.value)
    assert diagnostic == {"code": "invalid_report", "validation_paths": ["summary"]}
    assert upstream_details({"message": "PRIVATE", "codexErrorInfo": {"PRIVATE": {"httpStatusCode": 503}}}) == {}
    assert upstream_details({"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": "503", "message": "PRIVATE"}}}) == {"protocol_error": "httpConnectionFailed"}
    assert failure_details(subprocess.TimeoutExpired(["PRIVATE"], 1)) == {"code": "timeout"}


@pytest.mark.parametrize("expiry", ["worker", "poll"])
def test_expired_report_is_diagnosed_once_and_late_completion_cannot_revive(report_env, monkeypatch, expiry):
    (client, app, _, _), _ = report_env
    entered, release, exited = Event(), Event(), Event()
    def generate(value, settings, progress, active):
        progress({"stage": "writing", "research_calls": 0, "blocks": [], "timings": {}})
        with transaction(app.state.engine) as db:
            row = db.get(ReportTask, value["id"])
            row.created_at = time.time() - (settings.timeout + 31 if expiry == "worker" else 301)
        entered.set()
        try:
            assert release.wait(5)
            return copy.deepcopy(DRAFT), {"completed": True}
        finally:
            exited.set()
    monkeypatch.setattr(reports, "generate_report", generate)
    body = {"id": str(uuid.uuid4()), "prompt": "합성 만료 보고서", "research": False}
    try:
        assert client.post("/api/report-tasks", json=body).status_code == 200
        assert entered.wait(2)
        if expiry == "poll":
            first = ready(client, body)
            assert first["execution"]["failure"]["code"] == "timeout"
        release.set()
        assert exited.wait(2)
        task = ready(client, body)
        assert task["status"] == "failed" and task["draft"] is None
        assert task["execution"]["failure"] == {"code": "timeout", "stage": "writing", "research_calls": 0}
        assert task["execution"]["completed"] is False and task["execution"]["completed_at"] > 0
        assert client.get("/api/documents").json() == []
    finally:
        release.set()


@pytest.mark.skipif(os.name == "nt", reason="POSIX inherited descriptor regression; Windows uses a private Job")
def test_timeout_cleanup_does_not_wait_for_escaped_descendant_pipe_eof(tmp_path):
    code = ('import json,subprocess,sys,time; '
            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(8)"],start_new_session=True); '
            'print(json.dumps({"child_pid":p.pid}),flush=True); time.sleep(8)')
    child_pid = None
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            with ReportRpc([sys.executable, "-c", code], environment=dict(os.environ), cwd=str(tmp_path), timeout=.25) as rpc:
                child_pid = rpc.receive()["child_pid"]
                rpc.receive()
        assert time.monotonic() - started < 2
        assert rpc.process.poll() is not None
        assert all(not reader.is_alive() for reader in rpc.readers)
    finally:
        # Terminate only the deliberately escaped synthetic process this test
        # created. The transport itself never enumerates unrelated processes.
        if child_pid is not None:
            try: os.kill(child_pid, signal.SIGTERM)
            except ProcessLookupError: pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX nonblocking incremental UTF-8 transport")
def test_stdio_split_utf8_and_unterminated_final_event_are_decoded(tmp_path):
    raw = json.dumps({"text": "합성😀"}, ensure_ascii=False).encode("utf-8")
    code = (f'import os,time; data={raw!r}; '
            '[os.write(1,data[i:i+1]) for i in range(len(data))]')
    with ReportRpc([sys.executable, "-c", code], environment=dict(os.environ), cwd=str(tmp_path), timeout=2) as rpc:
        assert rpc.receive() == {"text": "합성😀"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor range regression")
def test_stdio_handles_descriptors_above_select_fd_setsize(tmp_path):
    import resource
    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] < 1200:
        pytest.skip("OS descriptor limit below test requirement")
    descriptors = []
    try:
        for _ in range(1050):
            descriptors.append(os.open(os.devnull, os.O_RDONLY))
        code = 'import sys,json; print(json.dumps(json.loads(sys.stdin.readline())),flush=True)'
        with ReportRpc([sys.executable, "-c", code], environment=dict(os.environ), cwd=str(tmp_path), timeout=2) as rpc:
            assert rpc.process.stdin.fileno() > 1024 and rpc.process.stdout.fileno() > 1024
            rpc.send({"synthetic": "높은 파일 번호"})
            assert rpc.receive() == {"synthetic": "높은 파일 번호"}
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
