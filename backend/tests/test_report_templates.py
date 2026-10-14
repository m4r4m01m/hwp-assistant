"""Synthetic generation contracts; no real Codex or document publication."""
import copy
import json
import os
from pathlib import Path
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


def test_selected_actual_values_reach_generation_and_retry_is_frozen(report_env, monkeypatch):
    (client, _, _, _), calls = report_env
    value = body(selection(client, 1))
    assert client.post("/api/report-tasks", json=value).status_code == 200
    job = ready(client, value)
    assert job["status"] == "ready"
    assert job["input"] == value
    assert job["execution"]["input_sha256"] == report_hash(value)
    assert calls == [value]
    assert not job["execution"]["context_used"]
    monkeypatch.setattr(reports, "validate_selection", lambda _: (_ for _ in ()).throw(HTTPException(503, "catalog changed")))
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


def test_frozen_profile_reaches_actual_stdio_turn_payload(report_env, monkeypatch, tmp_path):
    from app import report_stream
    from app.codex_runner import CodexSettings
    from test_reports import DRAFT
    (client, _, _, _), _ = report_env
    value = body(selection(client, 2))
    value["research"] = True
    (tmp_path / "draft.json").write_text(json.dumps(DRAFT), encoding="utf-8")
    (tmp_path / "release").touch()
    args = [sys.executable, str(Path(__file__).with_name("report_rpc_fixture.py")), str(tmp_path), "complete"]
    monkeypatch.setattr(report_stream, "stream_command", lambda *unused: args)
    settings = CodexSettings(sys.executable, "test-model", "", tuple(os.environ.items()), timeout=10)
    _, evidence = report_stream.generate_stream(value, settings, str(tmp_path), "synthetic", "test", lambda _: None, lambda: True)
    request = json.loads((tmp_path / "turn-request.json").read_text(encoding="utf-8"))
    assert json.loads(request["params"]["input"][0]["text"].split("\n", 1)[1]) == value
    assert evidence["completed"]
