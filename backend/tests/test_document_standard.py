import base64
import copy
import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

from app import document_standard
from app.hwpx import digest
from app.report_contract import report_blocks
from app.report_hwpx import scaffold
from test_reports import DRAFT, report_env, create, ready, approve, synthetic_output, seed
from test_collaboration import secured

HP, HH = document_standard.HP, document_standard.HH


def test_bundled_reference_and_visible_api_do_not_depend_on_document_db(report_env):
    (client, _, _, _), _ = report_env
    assert client.get("/api/documents").json() == []
    standard = client.get("/api/document-standard").json()
    response = client.get(standard["reference_url"])
    assert response.status_code == 200
    assert digest(response.content) == standard["reference_sha256"]
    assert standard["automatic"] is True
    data, plan = scaffold(seed(), report_blocks(DRAFT))
    signature = document_standard.format_signature(data, plan)
    title = signature["paragraphs"][0]
    assert ("topBorder", "SOLID", "1.0 mm", "#437FC1") in title["borders"]
    assert ("bottomBorder", "SOLID", "1.0 mm", "#437FC1") in title["borders"]
    body = next(s for s, p in zip(signature["paragraphs"], plan) if p["kind"] == "body")
    assert body["height"] == "1400" and body["line"] == "160"
    assert ("left", "1400") in body["margins"]
    assert ("hangul", "휴먼명조") in body["fonts"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_changed_reference_blocks_new_approval_without_document(report_env, monkeypatch, tmp_path, corrupt):
    (client, _, _, _), _ = report_env
    body = create(client); job = ready(client, body)
    profile, reference = document_standard.load_standard()
    if corrupt:
        import json
        (tmp_path / "profile.json").write_text(json.dumps(profile))
        (tmp_path / "reference.hwpx").write_bytes(reference + b"changed")
    monkeypatch.setattr(document_standard, "ASSETS", tmp_path)
    import uuid
    response = client.post(f"/api/report-tasks/{body['id']}/approve", json={"id": str(uuid.uuid4()), "draft_sha256": job["draft_sha256"], "seed_base64": base64.b64encode(seed()).decode()})
    assert response.status_code == 503
    assert client.get("/api/documents").json() == []


def test_approved_standard_survives_config_loss_and_rejects_format_tampering(report_env, monkeypatch, tmp_path):
    (client, _, _, _), _ = report_env
    body = create(client); value, plan = approve(client, body)
    assert plan["document_standard"]["reference_sha256"] == document_standard.load_standard()[0]["reference_sha256"]
    # Once approved, the saved scaffold is the frozen source of formatting truth.
    monkeypatch.setattr(document_standard, "ASSETS", tmp_path)
    url = f"/api/report-tasks/{body['id']}"
    assert client.post(url + "/approve", json=value).json() == plan
    token = client.post(f"/api/documents/{plan['document_id']}/session").json()["token"]
    payload = {**synthetic_output(plan), "token": token}
    raw = base64.b64decode(payload["output_base64"])
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        header = ET.fromstring(source.read("Contents/header.xml"))
        border = next(b for b in header.iter(f"{{{HH}}}borderFill") if b.get("id") == "7")
        border.find(f"{{{HH}}}bottomBorder").set("color", "#000000")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as dest:
            for entry in source.infolist(): dest.writestr(entry, ET.tostring(header) if entry.filename == "Contents/header.xml" else source.read(entry))
    damaged = {**payload, "output_base64": base64.b64encode(out.getvalue()).decode()}
    assert client.post(url + "/save", json=damaged).status_code == 422
    assert len(client.get(f"/api/documents/{plan['document_id']}/versions").json()) == 1
    assert client.post(url + "/save", json=payload).status_code == 200


def test_long_body_is_preserved_and_paginated_for_standard_font():
    value = copy.deepcopy(DRAFT)
    value["summary"] = "가" * 700
    value["sections"] = [{"heading": "긴 절", "paragraphs": ["나" * 800, "다" * 700]} for _ in range(6)]
    blocks = report_blocks(value)
    bodies = [b for b in blocks if b["kind"] == "body"]
    assert all(len(b["text"]) <= 380 for b in bodies)
    assert "".join(b["text"] for b in bodies).startswith(value["summary"] + "".join(p for s in value["sections"] for p in s["paragraphs"]))
    assert sum(b["page_break"] for b in blocks) > len(value["sections"])
    assert len(blocks) <= 60


def test_outline_hanging_indents_and_continuation_preserve_approved_text():
    value = copy.deepcopy(DRAFT)
    value["summary"] = "□ (요약) " + "사실과 제안을 구분해 검토할 필요. " * 22
    value["sections"] = [{"heading": "주요 내용", "paragraphs": [
        "□ (차이점) 정보유출과 업무 중단의 통제 목표가 다름",
        "ㅇ 각각 데이터 보호와 복구 능력의 증거를 확인할 필요",
        "- 담당자가 검증 범위와 확인 일정을 지정할 것을 제안함",
    ]} for _ in range(3)]
    blocks = report_blocks(value)
    assert "".join(b["text"] for b in blocks if b["kind"] == "body").startswith(value["summary"])
    data, plan = scaffold(seed(), blocks)
    signatures = document_standard.format_signature(data, plan)["paragraphs"]
    checked = set()
    for block, signature in zip(plan, signatures):
        name = block.get("style_kind")
        if not name: continue
        checked.add(name)
        assert signature["height"] == "1500"
        assert signature["alignment"] == "LEFT" and signature["line"] == "160"
        margins = dict(signature["margins"])
        left, intent = {"square": (0, -2250), "circle": (1000, -2250),
                        "dash": (2000, -1250), "square_continuation": (2250, 0)}[name]
        assert (margins["left"], margins["intent"]) == (str(left), str(intent))
    assert checked == {"square", "square_continuation", "circle", "dash"}
