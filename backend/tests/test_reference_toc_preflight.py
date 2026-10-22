"""Reject an overfull new TOC before fenced staging; frozen previews are reused."""
import base64
import copy
import uuid

import pytest

from app.report_contract import ReportDraft
from app.report_toc import POLICY
from test_collaboration import secured
from test_reports import report_env, ready, seed


def hierarchy_draft(*, include_limitations):
    return {
        "title": "합성 문서 계층 검수", "summary": "제목 연결을 검증하는 합성 자료임.",
        "sections": [{"heading": f"검토 영역 {index + 1}", "paragraphs": [
            text for number, marker in enumerate("가나다라", 1)
            for text in (f"{marker}. 검토 항목 {number}", f"□ 영역 {index + 1}의 항목 {number}를 검토함.")
        ]} for index in range(6)],
        "sources": [], "limitations": ["실제 회사 자료가 아닌 합성 시험임."] if include_limitations else [],
    }


def new_request(client, preset):
    visual = next(item for item in client.get("/api/visual-standards").json()["presets"]
                  if item["settings"]["preset_id"] == preset)
    visual = {key: visual[key] for key in ("id", "revision", "name", "settings", "settings_sha256", "base_standard", "preview_sha256")}
    task = {"id": str(uuid.uuid4()), "prompt": "제공된 합성 자료로 업무보고서를 작성해 주세요.", "research": False,
            "visual_standard": visual, "template": client.get("/api/report-templates").json()["items"][0]}
    response = client.post("/api/report-tasks", json=task)
    assert response.status_code == 200, response.text
    job = ready(client, task)
    assert job["status"] == "ready", job
    return f"/api/report-tasks/{task['id']}/preview", {
        "id": str(uuid.uuid4()), "draft_sha256": job["draft_sha256"], "seed_base64": base64.b64encode(seed()).decode()
    }, job


@pytest.mark.parametrize("preset", ["appendix-2", "appendix-3"])
def test_overfull_reference_toc_fails_before_scaffold_and_preserves_draft(report_env, monkeypatch, preset):
    (client, _, _, folder), calls = report_env
    draft = hierarchy_draft(include_limitations=True)
    ReportDraft.model_validate(draft)  # 6 sections, 8 paragraphs each are within the draft contract.
    monkeypatch.setattr("test_reports.DRAFT", copy.deepcopy(draft))
    url, value, before = new_request(client, preset)
    monkeypatch.setattr("app.report_preview.scaffold", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("created staging before TOC preflight")))

    response = client.post(url, json=value)
    assert response.status_code == 422, response.text
    assert "1~30" in response.json()["detail"] and "31개" in response.json()["detail"]
    assert "본문에 유지" in response.json()["detail"]
    after = client.get(url.removesuffix("/preview")).json()
    assert after["draft"] == before["draft"] and after["draft_sha256"] == before["draft_sha256"]
    assert client.get(url).status_code == 404
    assert len(calls) == 1 and client.get("/api/documents").json() == [] and not list(folder.iterdir())


@pytest.mark.parametrize("preset", ["appendix-2", "appendix-3"])
def test_thirty_headings_are_supported_and_frozen_retries_skip_preflight(report_env, monkeypatch, preset):
    (client, _, _, _), _ = report_env
    monkeypatch.setattr("test_reports.DRAFT", hierarchy_draft(include_limitations=False))
    url, value, _ = new_request(client, preset)
    response = client.post(url, json=value)
    assert response.status_code == 200, response.text
    frozen = response.json()
    assert frozen["toc_policy"] == POLICY
    assert sum(item["kind"] == "heading" or item.get("toc_level") == 2 for item in frozen["plan"]) == 30
    for function in ("heading_level", "report_blocks", "scaffold"):
        monkeypatch.setattr(f"app.report_preview.{function}", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("recomputed frozen preview")))
    assert client.post(url, json=value).json() == frozen
    assert client.get(url).json() == frozen


def test_appendix1_has_no_toc_limit_and_keeps_all_content(report_env, monkeypatch):
    (client, _, _, _), _ = report_env
    monkeypatch.setattr("test_reports.DRAFT", hierarchy_draft(include_limitations=True))
    url, value, _ = new_request(client, "appendix-1")
    monkeypatch.setattr("app.report_preview.heading_level", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("applied TOC limit to appendix 1")))
    response = client.post(url, json=value)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert "toc_policy" not in preview
    assert sum(item["kind"] == "heading" or item.get("toc_level") == 2 for item in preview["plan"]) == 31
