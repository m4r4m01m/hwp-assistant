"""Frozen warning provenance and HWPX/save fences, using synthetic completed drafts."""
import base64
import copy
import io
import json
import uuid
import zipfile
from xml.etree import ElementTree as ET

import pytest

from app import reports
from app.db import ReportTask, transaction
from app.report_contract import ReportDraft, report_blocks, report_hash
from app.report_hwpx import HP, paragraphs
from app.report_research import WARNING_MESSAGES, apply_research_policy
from test_collaboration import as_user, secured
from test_report_preview import render
from test_reports import DRAFT, ready, report_env, seed


def saturated():
    value = copy.deepcopy(DRAFT)
    value["title"] = "합성 자료 조회 미완료 보고서"
    value["summary"] = "확정 수치는 200원이며 기준일은 2026년 10월 25일임. [S1]"
    value["sources"] = [{"id": f"S{i}", "title": f"미확인 후보 {i}",
                         "url": f"https://example.invalid/candidate/{i}", "published": "2026-10-25"}
                        for i in range(1, 11)]
    value["sections"][0]["paragraphs"] = ["원래 인용과 사실은 보존함. " + " ".join(f"[S{i}]" for i in range(1, 11))]
    value["limitations"] = [(f"합성 확인 사항 {i}: " + "확인할 원래 사실과 조건을 보존함. " * 30)[:400] for i in range(6)]
    return ReportDraft.model_validate(value).model_dump()


def warned(value=None, *, calls=0, source=None):
    request = {"research": True, "research_policy": "allow_unverified"}
    if source is not None:
        request["revision"] = {"draft": source}
    return apply_research_policy(request, copy.deepcopy(value or saturated()),
                                 {"completed": True, "web_search_calls": calls})


def request_body():
    return {"id": str(uuid.uuid4()), "prompt": "합성 자료를 바탕으로 경고 보고서를 작성해 주세요.",
            "research": True, "research_policy": "allow_unverified"}


def prepare_warning(client, body):
    job = ready(client, body)
    assert job["status"] == "ready", job
    payload = {"id": str(uuid.uuid4()), "draft_sha256": job["draft_sha256"],
               "seed_base64": base64.b64encode(seed()).decode()}
    url = f"/api/report-tasks/{body['id']}/preview"
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    return job, url, payload, response.json()


def test_saturated_original_limits_sources_and_facts_survive_warning_hash_and_render():
    original = saturated()
    before = copy.deepcopy(original)
    result, evidence = warned(original)
    assert original == before
    assert len(result["limitations"]) == 6 and all(len(text) == 400 for text in result["limitations"])
    for field in ("title", "summary", "sections", "sources", "limitations", "emphasis"):
        assert result[field] == original[field]
    assert len(result["sources"]) == 10
    notice = result["research_notice"]
    assert notice["status"] == "unverified" and notice["completed_calls"] == 0
    assert notice["source_verification"] == "unverified"
    assert {w["code"] for w in notice["warnings"]} == {"research_incomplete", "sources_unverified"}
    assert evidence["warnings"] == notice["warnings"]
    assert ReportDraft.model_validate(result).model_dump() == result
    assert report_hash(result) != report_hash(original)
    changed = copy.deepcopy(result)
    changed["research_notice"]["warnings"][0]["message"] += " 추가 확인 필요."
    assert report_hash(changed) != report_hash(result)
    text = "".join(block["text"] for block in report_blocks(result))
    for limitation in original["limitations"]:
        assert limitation in text
    for source in original["sources"]:
        assert source["url"] in text
    for warning in notice["warnings"]:
        assert warning["message"] in text
        assert text.index(warning["message"]) > text.index(original["limitations"][-1])


def test_successful_revision_keeps_prior_unverified_provenance_without_rewriting_sources():
    source, _ = warned()
    snapshot = copy.deepcopy(source)
    revised, evidence = warned(saturated(), calls=2, source=source)
    assert source == snapshot
    assert revised["sources"] == source["sources"]
    assert revised["summary"] == source["summary"]
    assert revised["research_notice"]["completed_calls"] == 2
    codes = {warning["code"] for warning in revised["research_notice"]["warnings"]}
    assert {"research_incomplete", "sources_unverified", "inherited_research_unverified"} <= codes
    assert revised["research_notice"]["source_verification"] == "unverified"
    assert evidence["warnings"] == revised["research_notice"]["warnings"]
    again, _ = apply_research_policy({"research": True, "research_policy": "allow_unverified", "revision": {"draft": source}},
                                     revised, {"completed": True, "web_search_calls": 2})
    assert again == revised  # Revalidation must not duplicate messages or grow warnings.


def test_warning_preview_manual_save_reopen_keeps_exact_bytes_and_original_draft(report_env, monkeypatch):
    (client, app, _, folder), _ = report_env
    draft, evidence = warned()
    monkeypatch.setattr(reports, "generate_report", lambda *args: (copy.deepcopy(draft), copy.deepcopy(evidence)))
    body = request_body()
    assert client.post("/api/report-tasks", json=body).status_code == 200
    job, url, payload, plan = prepare_warning(client, body)
    assert job["draft_sha256"] == report_hash(job["draft"])
    assert job["approval"] is None and client.get("/api/documents").json() == []
    assert list(folder.iterdir()) == []
    before = copy.deepcopy(job)
    _, rendered = render(client, url, plan)
    raw = base64.b64decode(rendered["layout_base64"])
    root, _ = paragraphs(raw)
    content = "".join(root.itertext())
    for warning in draft["research_notice"]["warnings"]:
        assert warning["message"] in content
    # Manual edits are approved as visible bytes; the server must not rewrite
    # them or regenerate/append a new notice while publishing the document.
    root[3].find(f"{{{HP}}}run/{{{HP}}}t").text += " 사용자가 직접 검토한 합성 수정"
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as target:
        for member in source.infolist():
            target.writestr(member, ET.tostring(root) if member.filename == "Contents/section0.xml" else source.read(member))
    save = {"id": str(uuid.uuid4()), "preview_id": plan["id"], "layout_sha256": rendered["layout_sha256"],
            "output_base64": base64.b64encode(output.getvalue()).decode()}
    result = client.post(url + "/save", json=save)
    assert result.status_code == 200, result.text
    assert client.post(url + "/save", json=save).json() == result.json()
    document = result.json()
    path = f"/api/documents/{document['id']}/versions/{document['current_version']}/file"
    assert client.get(path).content == output.getvalue()
    assert len(client.get(f"/api/documents/{document['id']}/versions").json()) == 1
    current = client.get(f"/api/report-tasks/{body['id']}").json()
    assert current["draft"] == before["draft"] and current["draft_sha256"] == before["draft_sha256"]
    assert current["execution"]["warnings"] == before["execution"]["warnings"]
    assert len(list(folder.iterdir())) == 1
    as_user(client, "bob")
    assert client.get(url).status_code == 404 and client.post(url + "/save", json=save).status_code == 404


def test_legacy_draft_preview_and_hash_are_not_retrofitted_by_new_policy(report_env, monkeypatch):
    (client, _, _, _), _ = report_env
    legacy = saturated()
    before_hash = report_hash(legacy)
    before_blocks = report_blocks(legacy)
    warned(legacy)  # Applying a NEW policy to another copy must not alter the source.
    monkeypatch.setattr(reports, "generate_report", lambda *args: (copy.deepcopy(legacy), {"completed": True}))
    body = {"id": str(uuid.uuid4()), "prompt": "기존 합성 보고서를 작성해 주세요.", "research": False}
    assert client.post("/api/report-tasks", json=body).status_code == 200
    job, url, payload, plan = prepare_warning(client, body)
    assert job["input"] == body and "research_notice" not in job["draft"]
    assert job["draft_sha256"] == before_hash and report_blocks(job["draft"]) == before_blocks
    assert "warnings" not in job["execution"]
    text = "".join(p["text"] for p in plan["plan"])
    assert all(message not in text for message in WARNING_MESSAGES.values())
    assert client.post(url, json=payload).json() == plan


def test_warning_revision_never_modifies_frozen_source_preview_or_source_entries(report_env, monkeypatch):
    (client, app, _, _), _ = report_env
    draft, evidence = warned()
    monkeypatch.setattr(reports, "generate_report", lambda *args: (copy.deepcopy(draft), copy.deepcopy(evidence)))
    body = request_body()
    assert client.post("/api/report-tasks", json=body).status_code == 200
    source, url, _, plan = prepare_warning(client, body)
    with transaction(app.state.engine) as db:
        row = db.get(ReportTask, body["id"])
        frozen = (row.input, row.input_sha256, row.draft, row.draft_sha256, row.execution, row.approval)
    generated = []

    def revise(value, *args):
        generated.append(copy.deepcopy(value))
        return apply_research_policy(value, saturated(), {"completed": True, "web_search_calls": 1})

    monkeypatch.setattr(reports, "generate_report", revise)
    payload = {"id": str(uuid.uuid4()), "draft_sha256": source["draft_sha256"], "feedback": "원래 출처와 사실을 보존해 간결하게 보완해 주세요."}
    response = client.post(f"/api/report-tasks/{body['id']}/revise", json=payload)
    assert response.status_code == 200, response.text
    child = ready(client, payload)
    assert child["status"] == "ready" and child["input"]["research_policy"] == "allow_unverified"
    assert child["input"]["revision"]["draft"] == source["draft"]
    assert child["draft"]["sources"] == source["draft"]["sources"]
    assert "inherited_research_unverified" in {w["code"] for w in child["draft"]["research_notice"]["warnings"]}
    assert child["approval"] is None and child["preview_id"] is None
    assert client.get(url).json() == plan
    assert client.post(f"/api/report-tasks/{body['id']}/revise", json=payload).json() == child
    assert len(generated) == 1
    with transaction(app.state.engine) as db:
        row = db.get(ReportTask, body["id"])
        assert (row.input, row.input_sha256, row.draft, row.draft_sha256, row.execution, row.approval) == frozen
    assert client.get("/api/documents").json() == []
