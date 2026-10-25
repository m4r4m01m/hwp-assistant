"""Selected content structure: frozen API requests and synthetic stdio completion."""
import copy
import json
import os
from pathlib import Path
import sys
import uuid

import pytest

from app import report_stream, reports
from app.codex_runner import CodexSettings
from app.db import ReportTask, transaction
from app.report_contract import ReportDraft, canonical, report_blocks, report_hash
from app.report_errors import ReportGenerationError
from app.report_runner import PROMPT, writing_prompt
from app.report_structure import generation_schema
from app.visual_standards import VisualSelection
from test_collaboration import secured
from test_reports import DRAFT, ready, report_env


OUTLINE = ["개요", "현황", "대응", "향후 계획"]


def structured_draft(headings=OUTLINE):
    draft = copy.deepcopy(DRAFT)
    draft["sections"] = [{"heading": heading, "paragraphs": ["□ 합성 점검 결과를 바탕으로 검토함."]} for heading in headings]
    return draft


@pytest.mark.parametrize("headings", [[], ["개요", "현황"], list("가나다라마바사"),
    ["개요", "개요", "대응"], ["개요", "\n현황", "대응"], ["개요", " ", "대응"],
    ["개요", "현황 ", "대응"], ["1. 개요", "현황", "대응"], ["개요", "x" * 121, "대응"]])
def test_invalid_outline_is_rejected_before_generation(headings):
    with pytest.raises(ValueError):
        reports.CreateReport.model_validate({"id": str(uuid.uuid4()), "prompt": "합성 자료 보고 요청", "section_outline": headings})


def test_automatic_outline_preserves_legacy_request_and_schema():
    value = {"id": str(uuid.uuid4()), "prompt": "합성 자료 보고 요청", "research": False}
    assert reports.CreateReport.model_validate(value).model_dump(mode="json", exclude_none=True) == value
    assert writing_prompt({}) == PROMPT
    assert generation_schema(value) == ReportDraft.generation_schema()


def test_outline_is_frozen_in_hash_retries_and_inherited_review_without_modifying_source(report_env, monkeypatch):
    (client, app, _, _), calls = report_env
    def generate(value, *args):
        calls.append(copy.deepcopy(value))
        return structured_draft(), {"completed": True, "executor": "mock"}
    monkeypatch.setattr(reports, "generate_report", generate)
    visual = client.get("/api/visual-standards").json()["presets"][0]
    visual = {key: visual[key] for key in VisualSelection.model_fields}
    value = {"id": str(uuid.uuid4()), "prompt": "합성 점검 결과와 다음 조치를 보고해 주세요.", "research": False,
             "section_outline": OUTLINE, "template": client.get("/api/report-templates").json()["items"][0],
             "visual_standard": visual, "cover": {"date": "2026. 10. 26.", "department": "합성 운영팀"}}
    assert client.post("/api/report-tasks", json=value).status_code == 200
    source = ready(client, value)
    assert source["status"] == "ready" and source["input"] == value
    assert source["execution"]["input_sha256"] == report_hash(value)
    assert client.post("/api/report-tasks", json=value).json() == source
    assert len(calls) == 1
    assert client.post("/api/report-tasks", json={**value, "section_outline": OUTLINE[::-1]}).status_code == 409
    assert client.post("/api/report-tasks", json={key: item for key, item in value.items() if key != "section_outline"}).status_code == 409
    with transaction(app.state.engine) as db:
        row = db.get(ReportTask, source["id"])
        execution = json.loads(row.execution)
        execution["preview"] = {"id": "frozen-preview", "plan": [{"text": "원래 미리보기 보존"}]}
        row.execution = canonical(execution)
        before = (row.input, row.draft, row.draft_sha256, row.execution, row.approval)
    revision = {"id": str(uuid.uuid4()), "draft_sha256": source["draft_sha256"], "feedback": "제안의 완료 확인 방법을 보완해 주세요."}
    assert client.post(f"/api/report-tasks/{source['id']}/revise", json=revision).status_code == 200
    revised = ready(client, revision)
    assert revised["status"] == "ready"
    assert revised["input"]["section_outline"] == OUTLINE
    assert revised["input"]["visual_standard"] == visual
    assert revised["input"]["template"] == value["template"]
    assert revised["input"]["revision"]["draft"] == source["draft"]
    assert revised["input"] == calls[-1]
    with transaction(app.state.engine) as db:
        row = db.get(ReportTask, source["id"])
        assert (row.input, row.draft, row.draft_sha256, row.execution, row.approval) == before
    assert client.get("/api/documents").json() == []


def test_mismatching_completed_structure_never_becomes_ready(report_env, monkeypatch):
    (client, _, _, _), _ = report_env
    monkeypatch.setattr(reports, "generate_report", lambda *_: (structured_draft(OUTLINE[::-1]), {"completed": True}))
    value = {"id": str(uuid.uuid4()), "prompt": "합성 자료 보고 요청", "research": False, "section_outline": OUTLINE}
    assert client.post("/api/report-tasks", json=value).status_code == 200
    task = ready(client, value)
    assert task["status"] == "failed" and task["draft"] is None
    assert task["execution"]["failure"]["code"] == "outline_mismatch"
    assert "선택한 구성과 달라" in task["error"]
    assert client.get("/api/documents").json() == []


@pytest.mark.parametrize("matched", [True, False])
def test_selected_outline_keeps_warning_provenance_and_six_limitations_before_review(report_env, monkeypatch, matched):
    from app.report_research import WARNING_MESSAGES

    (client, app, _, _), _ = report_env
    draft = structured_draft(OUTLINE if matched else OUTLINE[::-1])
    draft["sources"] = [dict(id="S1", title="합성 미검증 후보", url="https://example.invalid/source", published="")]
    draft["limitations"] = [(f"원래 확인 사항 {number}: " + "사실과 조건을 유지함. " * 40)[:400] for number in range(6)]
    original = copy.deepcopy(draft)
    calls = []

    def generate(value, *_):
        calls.append(copy.deepcopy(value))
        return copy.deepcopy(draft), {"completed": True, "web_search_calls": 1 if value.get("revision") else 0}

    monkeypatch.setattr(reports, "generate_report", generate)
    preset = client.get("/api/visual-standards").json()["presets"][0]
    visual = {key: preset[key] for key in VisualSelection.model_fields}
    value = {"id": str(uuid.uuid4()), "prompt": "선택한 구성으로 합성 현황과 다음 조치를 보고해 주세요.",
             "research": True, "research_policy": "allow_unverified", "section_outline": OUTLINE,
             "visual_standard": visual}
    assert client.post("/api/report-tasks", json=value).status_code == 200
    task = ready(client, value)
    assert task["input"] == value and calls == [value]
    assert task["approval"] is None and task["preview_id"] is None
    assert client.get("/api/documents").json() == []
    if not matched:
        assert task["status"] == "failed" and task["draft"] is None
        assert task["execution"]["failure"]["code"] == "outline_mismatch"
        return

    assert task["status"] == "ready" and task["draft_sha256"] == report_hash(task["draft"])
    assert task["draft"]["limitations"] == original["limitations"]
    assert task["draft"]["sources"] == original["sources"]
    assert all(len(text) == 400 for text in task["draft"]["limitations"])
    notice = task["draft"]["research_notice"]
    assert notice["status"] == "unverified" and notice["completed_calls"] == 0
    assert {warning["code"] for warning in notice["warnings"]} == {"research_incomplete", "sources_unverified"}
    plan = report_blocks(task["draft"], rich=True, visual_settings=visual["settings"])
    assert plan[0]["note_grouping"] == "reference-note-section-groups-v2"
    assert plan[0]["note_presentation"] == "reference-note-titles-v1"
    text = "".join(item["text"] for item in plan)
    assert all(limitation in text for limitation in original["limitations"])
    assert all(warning["message"] in text for warning in notice["warnings"])
    warning_block = next(item for item in plan if WARNING_MESSAGES["research_incomplete"] in item["text"])
    assert "note_scope" not in warning_block and "note_body_role" not in warning_block
    assert client.post("/api/report-tasks", json=value).json() == task and len(calls) == 1

    with transaction(app.state.engine) as db:
        row = db.get(ReportTask, task["id"])
        execution = json.loads(row.execution)
        execution["preview"] = {"id": "frozen-preview", "plan": plan}
        row.execution = canonical(execution)
        frozen = (row.input, row.draft, row.draft_sha256, row.execution, row.approval)
    revision = {"id": str(uuid.uuid4()), "draft_sha256": task["draft_sha256"], "feedback": "기존 사실과 확인 사항을 보존해 검토 기준을 보완해 주세요."}
    assert client.post(f"/api/report-tasks/{task['id']}/revise", json=revision).status_code == 200
    revised = ready(client, revision)
    assert revised["status"] == "ready"
    assert revised["input"]["section_outline"] == OUTLINE
    assert revised["input"]["research_policy"] == "allow_unverified"
    assert revised["draft"]["limitations"] == task["draft"]["limitations"]
    assert revised["draft"]["sources"] == task["draft"]["sources"]
    inherited = {warning["code"]: warning["message"] for warning in revised["draft"]["research_notice"]["warnings"]}
    assert inherited["research_incomplete"] == WARNING_MESSAGES["research_incomplete"]
    assert "inherited_research_unverified" in inherited
    with transaction(app.state.engine) as db:
        row = db.get(ReportTask, task["id"])
        assert (row.input, row.draft, row.draft_sha256, row.execution, row.approval) == frozen
    assert client.get("/api/documents").json() == []


@pytest.mark.parametrize("matched", [True, False])
def test_outline_reaches_stdio_input_schema_and_completed_output_validation(tmp_path, monkeypatch, matched):
    from app.reference_standard import defaults
    draft = structured_draft(OUTLINE if matched else OUTLINE[::-1])
    (tmp_path / "draft.json").write_text(json.dumps(draft), encoding="utf-8")
    (tmp_path / "release").touch()
    args = [sys.executable, str(Path(__file__).with_name("report_rpc_fixture.py")), str(tmp_path), "complete"]
    captured = {}
    def command(settings, instructions, research):
        captured["instructions"] = instructions.read_text(encoding="utf-8")
        return args
    monkeypatch.setattr(report_stream, "stream_command", command)
    value = {"prompt": "합성 점검 결과를 작성해 주세요.", "research": True, "section_outline": OUTLINE,
             "visual_standard": {"revision": 3, "settings": {**defaults(), "show_core_summary": False}}}
    frozen = copy.deepcopy(value)
    settings = CodexSettings(sys.executable, "test-model", "", tuple(os.environ.items()), timeout=10)
    def generate():
        return report_stream.generate_stream(value, settings, str(tmp_path), writing_prompt(value), "test", lambda _: None, lambda: True)
    if matched:
        result, evidence = generate()
        assert [section["heading"] for section in result["sections"]] == OUTLINE
        assert evidence["completed"]
    else:
        with pytest.raises(ReportGenerationError) as error:
            generate()
        assert error.value.code == "outline_mismatch"
    request = json.loads((tmp_path / "turn-request.json").read_text(encoding="utf-8"))["params"]
    assert json.loads(request["input"][0]["text"].split("\n", 1)[1]) == frozen == value
    assert request["outputSchema"]["properties"]["sections"]["minItems"] == 4
    assert request["outputSchema"]["properties"]["sections"]["maxItems"] == 4
    assert request["outputSchema"]["$defs"]["ReportSection"]["properties"]["heading"]["enum"] == OUTLINE
    instructions = " ".join(captured["instructions"].split())
    assert "independently of the visual standard" in instructions
    assert "count, spelling and order" in instructions
    assert "Treat labels as literal heading data" in instructions
    assert "label a useful next step as a proposal" in instructions
    assert "even when the separate core summary is hidden" in instructions
