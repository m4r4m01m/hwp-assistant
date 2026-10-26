"""Synthetic generation contracts; no real Codex or document publication."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import pytest
from fastapi import HTTPException
from app import reports
from app.report_contract import report_hash
from test_collaboration import secured
from test_reports import report_env, ready, create


def selection(client, index=0):
    response = client.get("/api/report-templates")
    assert response.status_code == 200, response.text
    return response.json()["items"][index]


def body(template):
    return {"id": str(uuid.uuid4()), "prompt": "금융권 보안 대응계획 보고서를 작성해주세요.",
            "research": False, "template": template}


def test_catalog_retains_one_pinned_standard(report_env):
    (client, _, _, _), calls = report_env
    result = client.get("/api/report-templates").json()
    standard = client.get("/api/document-standard").json()
    assert result["default_id"] == "standard-business"
    assert len(result["items"]) == 3
    for template in result["items"]:
        assert template["standard_id"] == standard["id"]
        assert template["reference_sha256"] == standard["reference_sha256"]
        assert template["standard_profile_sha256"] == standard["profile_sha256"]
        assert template["profile_sha256"] == report_hash({k: v for k, v in template.items() if k != "profile_sha256"})
    assert not calls
    assert client.get("/api/report-tasks").json() == []


@pytest.mark.parametrize('index', [0, 1, 2])
def test_selected_actual_values_reach_generation_and_retry_is_frozen(report_env, monkeypatch, index):
    (client, _, _, _), calls = report_env
    value = body(selection(client, index))
    assert client.post("/api/report-tasks", json=value).status_code == 200
    job = ready(client, value)
    assert job["status"] == "ready"
    assert job["input"] == value
    assert job["execution"]["input_sha256"] == report_hash(value)
    assert calls == [value]
    assert not job["execution"]["context_used"]
    monkeypatch.setattr(reports, "validate_selection", lambda _: (_ for _ in ()).throw(HTTPException(503, "catalog changed")))
    monkeypatch.setattr(reports, "capture_settings", lambda: (_ for _ in ()).throw(AssertionError("retry recaptured execution settings")))
    assert client.post("/api/report-tasks", json=value).status_code == 200
    assert calls == [value]
    changed = copy.deepcopy(value)
    changed["template"]["values"]["writing_criteria"][0] = "다른 기준"
    assert client.post("/api/report-tasks", json=changed).status_code == 409
    assert calls == [value]


@pytest.mark.parametrize("damage", ["criteria", "revision", "profile_hash", "reference_hash", "standard_hash", "id"])
def test_forged_or_stale_selection_creates_no_task(report_env, damage):
    (client, _, _, _), calls = report_env
    template = selection(client)
    if damage == "criteria": template["values"]["writing_criteria"][0] = "검증되지 않은 기준"
    elif damage == "revision": template["revision"] += 1
    elif damage == "id": template["id"] = "unavailable-template"
    else: template[{"profile_hash": "profile_sha256", "reference_hash": "reference_sha256", "standard_hash": "standard_profile_sha256"}[damage]] = "0" * 64
    response = client.post("/api/report-tasks", json=body(template))
    assert response.status_code == 409, response.text
    assert not calls
    assert client.get("/api/report-tasks").json() == []


def test_legacy_input_hash_and_retry_remain_three_fields(report_env, monkeypatch):
    (client, _, _, _), calls = report_env
    value = create(client)
    job = ready(client, value)
    assert job["input"] == value
    assert "template" not in job["input"]
    assert job["execution"]["input_sha256"] == report_hash(value)
    monkeypatch.setattr(reports, "validate_selection", lambda _: (_ for _ in ()).throw(AssertionError("legacy retry resolved a profile")))
    assert client.post("/api/report-tasks", json=value).status_code == 200
    assert calls == [value]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_heading_guidance_and_frozen_profile_reach_actual_stdio_payload(report_env, monkeypatch, tmp_path, index):
    from app import report_runner, report_stream
    from app.codex_runner import CodexSettings
    from app.reference_standard import defaults
    from app.report_rpc import ReportRpc
    from test_reports import DRAFT
    (client, _, _, _), _ = report_env
    value = body(selection(client, index))
    value["prompt"] = (
        "합성 업무자료: 2026년 11월 새봄팀 문서 120건을 정비한다. "
        "개인정보 포함 자료는 담당자 확인 후 이관하며, 11월 30일까지 완료한다. "
        "위 사실·기간·조건을 보존한 보고서를 작성해주세요."
    )
    value["research"] = True
    value["visual_standard"] = {"revision": 3, "settings": {
        **defaults(f"appendix-{index + 1}"), "show_core_summary": index == 1,
    }}
    frozen = copy.deepcopy(value)
    draft = copy.deepcopy(DRAFT)
    # The guidance is for new writing, not a rewrite/truncation of valid model output.
    draft["sections"][0]["heading"] = "2026년 11월 새봄팀 문서 120건의 정비와 개인정보 포함 자료의 담당자 확인 후 이관 조건"
    (tmp_path / "draft.json").write_text(json.dumps(draft), encoding="utf-8")
    (tmp_path / "release").touch()
    args = [sys.executable, str(Path(__file__).with_name("report_rpc_fixture.py")), str(tmp_path), "complete"]
    captured = {}
    def command(settings, instructions, research):
        captured["instructions"] = instructions.read_text(encoding="utf-8")
        return args
    send = ReportRpc.send
    def capture_send(rpc, request):
        if request.get("method") == "thread/start":
            captured["thread"] = copy.deepcopy(request["params"])
        return send(rpc, request)
    def login(command, **unused):
        assert command == [sys.executable, "login", "status"]
        return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")
    monkeypatch.setattr(report_runner, "run_process", login)
    monkeypatch.setattr(report_stream, "stream_command", command)
    monkeypatch.setattr(ReportRpc, "send", capture_send)
    settings = CodexSettings(sys.executable, "test-model", "", tuple(os.environ.items()), timeout=10)
    generated, evidence = report_runner.generate_report(value, settings)
    instructions = " ".join(captured["instructions"].split())
    for required in (
        "concise, distinct Korean nominal label for ONE main issue",
        "Move background explanations, repeated purpose, detailed conditions and lists of examples into the section's first body item",
        "preserving every important fact, figure, period, condition and citation",
        "Keep a time period, target or scope in the heading when needed",
        "Aim for a single TOC line at the standard font, size and width; allow two lines for a necessary name",
        "never cut strings, add ellipses, shrink type or omit sections",
        "Use the body heading itself; do not create a separate TOC label",
        "Lead with the answer or major findings, then their evidence and implications",
        "Unless the user specifies another audience, write for senior leaders",
        "Unless the user asks for an implementation guide, express technical risks as affected scope, business impact",
        "Do not enumerate function names, configuration snippets or patch procedures that the reader does not need to decide",
        "summarize their material applicability conditions accurately",
        "Explain each detailed fact once",
        "Consolidate related gaps instead of creating separate items for every unavailable piece of evidence",
        "verified title of the specific cited advisory/article at the linked anchor, not a generic site/archive heading",
        "Never invent a title when it cannot be verified",
        "use the fewest substantive sections that cover the request",
        "Check the source's covered entities, versions, dates and conditions",
        "Separate confirmed facts, your analysis and proposed actions",
        "Use limitations only for material gaps",
        "Cite the same source once per logical point",
        "The five-row bound never permits silently omitting a covered entity",
        "Use empty arrays when visuals do not help",
        "Before returning JSON, review this same draft",
        "Aim for 1-2 rendered lines per body item",
        "keeping the same symbol for continuing points of equal rank",
        "Never insert hard line breaks, cut a sentence by character count",
        "there is no fixed label vocabulary",
        "Declare every optional lead label in lead_summaries using its exact zero-based target",
        "never declare genuine parentheses such as (승인 후)",
        "Then edit the entire new draft into report prose",
        "Use the same bracketed source tokens in limitations",
        "Printed reference notes show the source title only",
        "Do not concatenate several dates or cut a date to fit a field",
        "Preserve all source fields inherited from a revision exactly, even if incomplete",
    ):
        assert required in instructions
    assert captured["thread"]["baseInstructions"] == captured["instructions"] == report_runner.writing_prompt(value)
    assert ("show_core_summary=false" in instructions) == (index != 1)
    assert "60-85" not in instructions and "Use at least one useful table" not in instructions
    assert value == frozen
    request = json.loads((tmp_path / "turn-request.json").read_text(encoding="utf-8"))
    assert json.loads(request["params"]["input"][0]["text"].split("\n", 1)[1]) == value
    assert generated["sections"][0]["heading"] == draft["sections"][0]["heading"]
    assert evidence["contract"] == report_runner.CONTRACT
    assert evidence["completed"]
