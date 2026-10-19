"""Review/revision request boundaries with mock generation, not model quality proof."""
import copy
import json
import uuid
from threading import Event

import pytest

from app import reports
from app.db import ReportTask, transaction
from app.report_contract import ReportDraft, canonical, report_hash
from app.report_runner import PROMPT, writing_prompt
from app.visual_standards import VisualSelection
from test_collaboration import as_user, secured
from test_reports import DRAFT, approve, create, ready, report_env


def revision(job, feedback="운영팀의 확인 절차와 완료 기준을 보완해 주세요."):
    return {"id": str(uuid.uuid4()), "draft_sha256": job["draft_sha256"], "feedback": feedback}


def test_revision_freezes_reviewed_draft_selections_and_cover_without_touching_source(report_env):
    (client, app, _, _), calls = report_env
    visual = client.get("/api/visual-standards").json()["presets"][1]
    visual = {key: visual[key] for key in VisualSelection.model_fields}
    source_input = {"id": str(uuid.uuid4()), "prompt": "자료 수집 운영 보고서를 작성해 주세요.", "research": False,
                    "template": client.get("/api/report-templates").json()["items"][0], "visual_standard": visual,
                    "cover": {"date": "2026. 10. 20.", "department": "운영팀"}}
    assert client.post("/api/report-tasks", json=source_input).status_code == 200
    source = ready(client, source_input)
    with transaction(app.state.engine) as db:
        row = db.get(ReportTask, source["id"])
        execution = json.loads(row.execution)
        execution["preview"] = {"id": "frozen-preview", "plan": [{"text": "보존할 미리보기"}]}
        row.execution = canonical(execution)
        before = (row.input, row.draft, row.draft_sha256, row.execution, row.approval)
    payload = revision(source)
    response = client.post(f"/api/report-tasks/{source['id']}/revise", json=payload)
    assert response.status_code == 200, response.text
    revised = ready(client, payload)
    assert revised["status"] == "ready" and revised["id"] != source["id"]
    assert revised["preview_id"] is None and revised["approval"] is None
    assert revised["execution"]["revision_of"] == source["id"]
    assert revised["execution"]["reviewed_draft_sha256"] == source["draft_sha256"]
    expected = {**source_input, "id": payload["id"], "revision": {
        "source_task_id": source["id"], "draft_sha256": source["draft_sha256"],
        "feedback": payload["feedback"], "draft": source["draft"]}}
    assert revised["input"] == calls[-1] == expected
    assert revised["execution"]["input_sha256"] == report_hash(expected)
    with transaction(app.state.engine) as db:
        row = db.get(ReportTask, source["id"])
        assert (row.input, row.draft, row.draft_sha256, row.execution, row.approval) == before
    assert client.get("/api/documents").json() == []
    again = revision(revised, "두 번째 절을 더 간결하게 다듬어 주세요.")
    assert client.post(f"/api/report-tasks/{revised['id']}/revise", json=again).status_code == 200
    latest = ready(client, again)
    assert latest["input"]["revision"]["draft"] == revised["draft"]
    assert latest["input"]["revision"]["source_task_id"] == revised["id"]
    assert "revision" not in latest["input"]["revision"]["draft"]


def test_revision_response_loss_retry_is_frozen_and_different_input_conflicts(report_env, monkeypatch):
    (client, _, _, _), calls = report_env
    source = ready(client, create(client))
    payload = revision(source)
    url = f"/api/report-tasks/{source['id']}/revise"
    assert client.post(url, json=payload).status_code == 200
    result = ready(client, payload)
    count = len(calls)
    monkeypatch.setattr(reports, "capture_settings", lambda: (_ for _ in ()).throw(AssertionError("recaptured settings")))
    assert client.post(url, json=payload).json() == result
    assert len(calls) == count
    assert client.post(url, json={**payload, "feedback": "다른 내용으로 보완해 주세요."}).status_code == 409
    assert client.post(url, json={**payload, "draft_sha256": "0" * 64}).status_code == 409
    assert client.post(f"/api/report-tasks/{uuid.uuid4()}/revise", json=payload).status_code == 409
    assert client.post(url, json={**payload, "id": source["id"]}).status_code == 409


def test_revision_rejects_stale_hash_unknown_owner_and_empty_feedback(report_env, monkeypatch):
    (client, _, _, _), calls = report_env
    source = ready(client, create(client))
    payload = revision(source)
    url = f"/api/report-tasks/{source['id']}/revise"
    assert client.post(url, json={**payload, "draft_sha256": "f" * 64}).status_code == 409
    for feedback in ("", "  \n\t", "x" * 6001, "잘못된\x00내용"):
        assert client.post(url, json={**payload, "feedback": feedback}).status_code == 422
    assert client.post(url, json={**payload, "draft": source["draft"]}).status_code == 422
    as_user(client, "bob")
    assert client.post(url, json=payload).status_code == 403
    monkeypatch.setenv("HWP_CODEX_USER", "bob")
    assert client.post(url, json=payload).status_code == 404
    assert len(calls) == 1


def test_running_or_cancelled_draft_cannot_be_revised(report_env, monkeypatch):
    (client, _, _, _), _ = report_env
    entered, release, finished = Event(), Event(), Event()
    def delayed(*_):
        entered.set()
        release.wait(5)
        finished.set()
        return copy.deepcopy(DRAFT), {}
    monkeypatch.setattr(reports, "generate_report", delayed)
    try:
        body = create(client)
        assert entered.wait(2)
        payload = {"id": str(uuid.uuid4()), "draft_sha256": "0" * 64, "feedback": "보완 요청"}
        url = f"/api/report-tasks/{body['id']}"
        assert client.post(url + "/revise", json=payload).status_code == 409
        assert client.post(url + "/cancel").json()["status"] == "cancelled"
        assert client.post(url + "/revise", json=payload).status_code == 409
    finally:
        release.set()
        assert finished.wait(2)


def test_revising_an_approved_draft_never_changes_approval_or_existing_blobs(report_env):
    (client, _, _, folder), _ = report_env
    body = create(client)
    _, approved = approve(client, body)
    source = ready(client, body)
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    payload = revision(source)
    assert client.post(f"/api/report-tasks/{source['id']}/revise", json=payload).status_code == 200
    assert ready(client, payload)["status"] == "ready"
    assert client.get(f"/api/report-tasks/{source['id']}/approved").json() == approved
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before
    assert len(client.get("/api/documents").json()) == 1


@pytest.mark.parametrize("change", ["drop", "rebind", "retain"])
def test_revision_never_discards_or_rebinds_existing_source_ids(report_env, monkeypatch, change):
    (client, _, _, _), _ = report_env
    draft = copy.deepcopy(DRAFT)
    draft["sources"] = [{"id": "S1", "title": "제공 자료", "url": "https://example.com/supplied", "published": "2026-10-20"}]
    draft["sections"][0]["paragraphs"] = ["기준일은 2026년 10월 20일임. [S1]"]
    monkeypatch.setattr(reports, "generate_report", lambda *_: (copy.deepcopy(draft), {}))
    source = ready(client, create(client))
    revised = copy.deepcopy(draft)
    revised["sections"][1]["paragraphs"] = ["운영팀은 확인 후 완료 결과를 공유함."]
    if change == "drop":
        revised["sources"] = []
        revised["sections"][0]["paragraphs"] = ["기준일은 2026년 10월 20일임."]
    elif change == "rebind":
        revised["sources"][0]["url"] = "https://example.com/different"
    monkeypatch.setattr(reports, "generate_report", lambda *_: (revised, {}))
    payload = revision(source)
    assert client.post(f"/api/report-tasks/{source['id']}/revise", json=payload).status_code == 200
    result = ready(client, payload)
    assert result["status"] == ("ready" if change == "retain" else "failed")
    if change != "retain":
        assert "기존 출처가 달라져" in result["error"] and "원래 초안은 보존" in result["error"]
    assert client.get(f"/api/report-tasks/{source['id']}").json()["draft"] == source["draft"]
    assert client.get("/api/documents").json() == []


def test_cover_is_optional_plain_input_and_legacy_requests_keep_exact_hash():
    body = {"id": str(uuid.uuid4()), "prompt": "합성 보고서를 작성해 주세요.", "research": False}
    assert reports.CreateReport.model_validate(body).model_dump(mode="json", exclude_none=True) == body
    explicit = {**body, "cover": {"date": "2026. 10. 20.", "department": "업무지원팀"}}
    assert reports.CreateReport.model_validate(explicit).model_dump(mode="json", exclude_none=True) == explicit
    assert reports.ReportCover().model_dump() == {"date": "", "department": ""}
    for cover in ({"date": "bad\nline"}, {"department": "x" * 101}, {"date": "x" * 61}, {"department": " "}):
        with pytest.raises(ValueError):
            reports.CreateReport.model_validate({**body, "cover": cover})


def test_revision_writing_rules_are_sent_for_all_reference_presets_and_legacy():
    from app.reference_standard import defaults
    assert writing_prompt({}) == PROMPT
    for preset in (None, "appendix-1", "appendix-2", "appendix-3"):
        value = {"revision": {"feedback": "완료 기준 보완", "draft": ReportDraft.model_validate(DRAFT).model_dump()}}
        if preset:
            value["visual_standard"] = {"revision": 3, "settings": defaults(preset)}
        prompt = writing_prompt(value)
        assert "revision.draft" in prompt and "revision.feedback" in prompt
        assert "Keep every existing source entry exactly" in prompt
        assert "Preserve important facts, figures, dates, conditions" in prompt
        assert "complete revised ReportDraft" in prompt
        if preset:
            assert "at the selected 14pt body size" in prompt
            assert "Frozen document-writing rules" in prompt
