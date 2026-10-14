"""Codex launcher contract only. No login, CLI execution, MCP model calls, or paid API."""
import json
import subprocess
import pytest
from app.codex_runner import command, summarize, run_codex
from test_collaboration import secured
from test_rewrite import job


def test_command_preserves_task_scoped_mcp_and_restricted_tool_surface(tmp_path, monkeypatch):
    config = tmp_path / "codex-config"
    config.mkdir()
    (config / "config.toml").write_text('model = "configured-test-model"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(config))
    args = command("codex-fixture", tmp_path / "empty")
    assert args[:2] == ["codex-fixture", "exec"]
    assert "--ignore-user-config" in args and "--ephemeral" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[args.index("--model") + 1] == "configured-test-model"
    disabled = [args[index + 1] for index, value in enumerate(args[:-1]) if value == "--disable"]
    assert {"shell_tool", "unified_exec", "apps", "plugins", "browser_use", "computer_use", "multi_agent"} <= set(disabled)
    config_args = [args[index + 1] for index, value in enumerate(args[:-1]) if value == "-c"]
    enabled = next(value for value in config_args if value.startswith("mcp_servers.hwp_document.enabled_tools="))
    assert json.loads(enabled.split("=", 1)[1]) == ["read_rewrite_task", "submit_rewrite_candidates"]
    assert args[-1] == "-"


@pytest.mark.parametrize("kind", ["command_execution", "file_change", "web_search"])
def test_prohibited_executor_activity_is_not_success_evidence(kind):
    with pytest.raises(RuntimeError):
        summarize(json.dumps({"type": "item.completed", "item": {"type": kind}}), "test-secret")


def test_evidence_contains_tool_results_and_redacts_execution_token():
    events = [{"type": "thread.started", "thread_id": "synthetic-thread"},
              {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "hwp_document",
                                                  "tool": "read_rewrite_task", "status": "completed"}},
              {"type": "error", "message": "bad test-secret"}]
    evidence = summarize("\n".join(json.dumps(item) for item in events), "test-secret")
    assert evidence["completed"] is False
    assert evidence["mcp_tools"][0]["tool"] == "read_rewrite_task"
    assert "test-secret" not in json.dumps(evidence)


def test_codex_timeout_keeps_selected_model_without_a_fallback(job, tmp_path, monkeypatch):
    client, app, doc, conversation, _, body, token = job
    config = tmp_path / "fake-codex"
    config.mkdir()
    (config / "config.toml").write_text('model = "configured-timeout-fixture"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(config))
    monkeypatch.setenv("HWP_CODEX_EXECUTABLE", "never-executed-fixture")
    calls = []
    def fake_process(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["login", "status"]:
            return subprocess.CompletedProcess(args, 0, "Logged in using ChatGPT", "")
        assert args[1] == "exec"
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr("app.codex_runner.run_process", fake_process)
    run_codex(app.state.engine, body["id"], token)
    task = client.get(f"/api/threads/{conversation}/rewrite-tasks").json()[0]
    assert task["status"] == "failed" and task["candidates"] == []
    assert task["execution"]["model"] == "configured-timeout-fixture"
    assert task["execution"]["model_source"] == "configured"
    assert task["execution"]["completed"] is False and len(calls) == 2
    assert len(client.get(f"/api/documents/{doc['id']}/versions").json()) == 1
