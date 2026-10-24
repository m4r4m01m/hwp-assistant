"""Synthetic API evidence only: actual engine page/layout proof is E2E-owned."""
import base64
import copy
import io
import json
import uuid
import zipfile
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import standard_samples as samples
from app import visual_standards as visual
from app.db import Document, ReportTask, VisualStandard, transaction
from app.hwpx import digest
from app.main import create_app
from app.reference_report import body_item
from app.reference_standard import defaults, legacy_defaults
from app.report_contract import report_blocks, report_hash
from app.report_hwpx import HP
from test_collaboration import TOKENS, as_user, secured
from test_document_blocks import changed_zip
from test_reports import seed

PATH = "/api/visual-standards/sample-preview"


def prepare(client, settings=None):
    body = {"id": str(uuid.uuid4()), "settings": settings or defaults(), "seed_base64": base64.b64encode(seed()).decode()}
    response = client.post(PATH, json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


def verify_body(stage, *, page=3):
    body = {"context": stage["context"], "engine_output_base64": stage["preview_base64"], "page_count": page}
    if stage["phase"] == "toc":
        body["layout_evidence"] = {"export_sha256": stage["preview_sha256"], "page_count": page, "revision": 2, "epoch": 1,
            "headings": [{**{k: h[k] for k in ("id", "section", "paragraph", "length", "text_sha256")}, "page": page}
                         for h in stage["toc_stage"]["headings"]]}
    return body


def advance(client, stage, *, page=3):
    body = verify_body(stage, page=page)
    response = client.post(PATH + "/verify", json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


def finish(client, stage):
    while stage["phase"] != "ready":
        _, stage = advance(client, stage)
    return stage


def registration(stage):
    return {"id": str(uuid.uuid4()), "name": "합성 업무 표준", "context": stage["context"], "preview_base64": stage["preview_base64"]}


def strings(encoded):
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded))) as archive:
        section = ET.fromstring(archive.read("Contents/section0.xml"))
    return "\n".join("".join(p.itertext()) for p in section.iter(f"{{{HP}}}p"))


@pytest.mark.parametrize("page_style", ["DASH", "PLAIN", "PAGE_TOTAL", "NONE", "ROMAN"])
def test_direct_synthetic_layout_uses_native_control_host_and_character_order(page_style):
    from app.block_hwpx import HH, parsed
    from app.reference_report import native_character
    raw, _ = samples.materialize(seed(), {**defaults(), "page_number_style": page_style}, str(uuid.uuid4()))
    header, section, _, _ = parsed(raw)
    control = section[0].find(f"{{{HP}}}run")
    assert control.find(f"{{{HP}}}t") is None
    assert control.find(f"{{{HP}}}ctrl/{{{HP}}}colPr").get("id") == ""
    for run in section[0].iter(f"{{{HP}}}run"):
        if run.find(f"{{{HP}}}ctrl") is not None:
            assert all(node.text or len(node) for node in run.findall(f"{{{HP}}}t"))
    for char in header.iter(f"{{{HH}}}charPr"):
        expected = copy.deepcopy(char)
        native_character(expected)
        assert ET.tostring(char) == ET.tostring(expected)


@pytest.mark.parametrize("preset", ["appendix-1", "appendix-2", "appendix-3"])
def test_same_validated_report_and_shared_layout_without_ai_or_document_rows(secured, monkeypatch, preset):
    client, app, _, folder = secured
    monkeypatch.setattr("app.reports.generate_report", lambda *_: (_ for _ in ()).throw(AssertionError("AI called")))
    original = {p: digest(p.read_bytes()) for p in visual.metadata.__globals__["ASSETS"].glob("*") if p.is_file()}
    _, stage = prepare(client, defaults(preset))
    assert stage["sample_id"] == samples.SAMPLE_ID and stage["sample_sha256"] == report_hash(samples.SAMPLE)
    assert stage["needs_toc"] == (preset != "appendix-1")
    roles = {r["role"] for r in stage["sample_regions"]}
    assert {"heading", "subheading", "minor_heading", "numbered_4", "numbered_5", "numbered_6", "body", "square", "circle", "dash", "dot", "table", "note", "table_source"} <= roles
    content = strings(stage["preview_base64"])
    for text in (samples.SAMPLE["title"], "20건", "5건", "15건", "2026년 10월 30일", "필수 항목 충족 시", "합성 문서 점검 기준", "합성 검토 기록 기준"):
        assert text in content
    complete = finish(client, stage)
    assert complete["phase"] == "ready" and complete["page_count"] == 3
    if preset != "appendix-1":
        assert complete["verification"]["converged"] and complete["verification"]["pass_index"] == 2
        assert len(complete["verification"]["headings"]) == 4  # three main headings + explicit subordinate
        assert {r["role"] for r in complete["sample_regions"]} >= {"toc_major", "toc_minor"}
        assert all(r["page"] == 3 for r in complete["sample_regions"] if r["role"] in ("heading", "subheading"))
        assert all(r["occurrence"] == 0 for r in complete["sample_regions"] if r["role"] in ("toc_major", "toc_minor"))
    with transaction(app.state.engine) as db:
        for model in (Document, ReportTask, VisualStandard):
            assert db.scalar(select(model)) is None
    assert not list(folder.iterdir())
    assert {p: digest(p.read_bytes()) for p in original} == original


def test_composition_and_numbering_settings_apply_to_new_plan_only():
    original = copy.deepcopy(samples.SAMPLE)
    settings = defaults()
    before = report_blocks(original, concise=True, rich=True, visual_settings=settings)
    frozen = copy.deepcopy(before)
    changed = {**settings, "show_core_summary": False, "body_parenthetical_labels": False,
               "numbered_1_marker": "1)", "numbered_2_marker": "가)", "numbered_3_marker": "(1)",
               "numbered_4_marker": "(가)", "numbered_5_marker": "①", "numbered_6_marker": "㉮"}
    after = report_blocks(original, concise=True, rich=True, visual_settings=changed)
    text = "\n".join(b["text"] for b in after)
    assert "핵심 요약" not in text and "담당자의 확인 기준을 통일함" not in text
    assert all(f"({label})" not in text for label in ("요약", "현황", "조치", "방안", "일정", "결론", "핵심"))
    assert "(필수 항목 충족 시)" in text and "20건" in text and "15건" in text
    assert "1) 정비 필요성" in text and "가) 적용 범위" in text and "(1) 검토 대상" in text
    assert original == samples.SAMPLE and before == frozen
    for marker in ("가)", "(가)"):
        value, role = body_item("나. 범위", {**settings, "numbered_2_marker": marker}, [0] * 4, apply_numbered_markers=True)
        assert value == marker.replace("가", "나") + " 범위" and role == "subheading"
    assert body_item("각. 원문", settings, [0] * 4, apply_numbered_markers=True) == ("각. 원문", "subheading")
    for ordinal, original_marker, role in ((2, "가.", "subheading"), (3, "(1)", "minor_heading"),
            (4, "(가)", "numbered_4"), (5, "①", "numbered_5"), (6, "㉮", "numbered_6")):
        changed_marker = f"제1-{ordinal}"
        value, actual_role = body_item(original_marker + " 기준", {**settings, f"numbered_{ordinal}_marker": changed_marker}, [0] * 4, apply_numbered_markers=True)
        assert value == changed_marker + " 기준" and actual_role == role


@pytest.mark.parametrize("settings", [visual.LEGACY_DEFAULTS, visual.DEFAULTS, legacy_defaults()])
def test_legacy_settings_can_use_same_sample_without_adding_new_policy(secured, settings):
    client, _, _, _ = secured
    body, stage = prepare(client, settings)
    complete = finish(client, stage)
    response = client.post("/api/visual-standards/sample-register", json=registration(complete))
    assert response.status_code == 200, response.text
    stored = response.json()
    expected = visual.PreviewVisual.model_validate({k: body[k] for k in ("settings", "seed_base64")})
    assert stored["settings"] == expected.settings.model_dump(mode="json")
    assert "preview_layout_policy" not in stored["settings"]


def test_unverified_registration_and_owner_tamper_expiry_base_hash_are_rejected(secured):
    client, app, _, folder = secured
    _, stage = prepare(client)
    assert client.post("/api/visual-standards/sample-register", json=registration(stage)).status_code == 409
    body = verify_body(stage)
    as_user(client, "bob")
    assert client.post(PATH + "/verify", json=body).status_code == 404
    as_user(client, "alice")
    assert client.post(PATH + "/verify", json=body | {"context": stage["context"] + "0"}).status_code == 409
    state = samples.unseal(stage["context"], app.state.standard_sample_key, "alice")
    expired = samples.sign({**state, "expires": 1}, app.state.standard_sample_key)
    assert client.post(PATH + "/verify", json=body | {"context": expired}).status_code == 409
    bad_base = copy.deepcopy(state)
    bad_base["base_standard"]["reference_sha256"] = "0" * 64
    assert client.post(PATH + "/verify", json=body | {"context": samples.sign(bad_base, app.state.standard_sample_key)}).status_code == 409
    assert not list(folder.iterdir())
    assert client.post(PATH, content=b"{}", headers={"content-length": str(samples.MAX_JSON + 1)}).status_code == 413


def test_engine_source_extra_resource_and_changed_prose_are_rejected(secured):
    client, _, _, _ = secured
    _, stage = prepare(client)
    raw = base64.b64decode(stage["preview_base64"])
    body = verify_body(stage)
    extra = changed_zip(raw, {"unexpected.bin": b"not allowed"})
    assert client.post(PATH + "/verify", json=body | {"engine_output_base64": base64.b64encode(extra).decode()}).status_code == 422
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        section = archive.read("Contents/section0.xml").replace("문서 관리 개선 계획".encode(), "다른 보고서 제목".encode())
    changed = changed_zip(raw, {"Contents/section0.xml": section})
    assert client.post(PATH + "/verify", json=body | {"engine_output_base64": base64.b64encode(changed).decode()}).status_code == 422


def test_actual_toc_evidence_and_three_pass_limit_with_frozen_retry(secured):
    client, _, _, _ = secured
    _, layout = prepare(client, defaults("appendix-2"))
    body, first = advance(client, layout)
    assert client.post(PATH + "/verify", json=body).json() == first
    assert client.post(PATH + "/verify", json=body | {"page_count": 4}).status_code == 409
    missing = verify_body(first)
    missing.pop("layout_evidence")
    assert client.post(PATH + "/verify", json=missing).status_code == 422
    invalid = verify_body(first)
    invalid["layout_evidence"]["export_sha256"] = "0" * 64
    assert client.post(PATH + "/verify", json=invalid).status_code == 422
    invalid = verify_body(first)
    invalid["layout_evidence"]["headings"][0]["text_sha256"] = "0" * 64
    assert client.post(PATH + "/verify", json=invalid).status_code == 422
    _, second = advance(client, first, page=3)
    assert second["toc_stage"]["pass_index"] == 2 and set(second["toc_stage"]["pages"].values()) == {3}
    _, third = advance(client, second, page=4)
    assert third["toc_stage"]["pass_index"] == 3 and set(third["toc_stage"]["pages"].values()) == {4}
    assert client.post(PATH + "/verify", json=verify_body(third, page=5)).status_code == 422
    assert client.post("/api/visual-standards/sample-register", json=registration(third)).status_code == 409


def test_register_immutable_blob_commit_and_retry_after_restart(secured):
    client, app, url, folder = secured
    _, stage = prepare(client)
    complete = finish(client, stage)
    body = registration(complete)
    response = client.post("/api/visual-standards/sample-register", json=body)
    assert response.status_code == 200, response.text
    stored = response.json()
    assert stored["preview_sha256"] == complete["preview_sha256"]
    assert client.get(f"/api/visual-standards/{body['id']}/file").content == base64.b64decode(complete["preview_base64"])
    with transaction(app.state.engine) as db:
        assert len(list(db.scalars(select(VisualStandard)))) == 1
        assert db.scalar(select(Document)) is None and db.scalar(select(ReportTask)) is None
    restarted = create_app(url, folder)
    with TestClient(restarted) as other:
        other.headers["Authorization"] = "Bearer " + TOKENS["alice"]
        assert other.post("/api/visual-standards/sample-register", json=body).json() == stored
        assert other.post("/api/visual-standards/sample-register", json=body | {"name": "다른 이름"}).status_code == 409
        assert other.post("/api/visual-standards/sample-register", json=body | {"id": str(uuid.uuid4())}).status_code == 409
    assert len(list(folder.iterdir())) == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_both_builtin_identity_sets_stay_reserved(secured, legacy):
    client, _, _, _ = secured
    _, stage = prepare(client)
    body = registration(finish(client, stage))
    body["id"] = visual.builtin_presets(legacy=legacy)[0]["id"]
    assert client.post("/api/visual-standards/sample-register", json=body).status_code == 409
