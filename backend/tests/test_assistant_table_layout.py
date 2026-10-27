import copy
import base64
import io
import zipfile

import pytest
from fastapi import HTTPException

from app.assistant_table_layout import TableLayout, apply_layout, targets, validated_layout
from app.block_hwpx import HP, Semantic, serialize_block_xml
from app.document_rewrite import verify_rewrite_output
from app.hwpx import digest
from report_explanatory_fixture import EXPLANATORY_DRAFT
from test_reference_body_spacing import generated
from test_document_assistant import assistant, create
from test_collaboration import secured, as_user, share
from app import document_assistant


def sample():
    source, _ = generated(draft=EXPLANATORY_DRAFT, settings={})
    context = dict(source_sha256=digest(source), paragraphs=[], preserved={}, unavailable="", table_targets=targets(source))
    return source, context


def test_parent_half_and_caption_gap_preserve_content_and_other_tables():
    source, context = sample()
    assert len(context["table_targets"]) == 1  # diagram, title and TOC are excluded
    before = Semantic(source)
    layout = dict(parent_half=True, caption_gap_mm=2.0)
    output = apply_layout(source, context, layout)
    after = Semantic(output)
    result = targets(output)[0]
    assert result["left_mm"] == result["parent_half_mm"] > 0
    assert result["caption_gap_mm"] == 2
    assert result["width_mm"] < context["table_targets"][0]["width_mm"]
    assert list(before.section.itertext()) == list(after.section.itertext())
    changed = {context["table_targets"][0]["paragraph"], context["table_targets"][0]["caption_paragraph"]}
    assert len(before.section) == len(after.section)
    for i, paragraph in enumerate(before.section):
        if i not in changed:
            assert serialize_block_xml(paragraph) == serialize_block_xml(after.section[i])
    verify_rewrite_output(source, output, context, [], layout)
    assert {name: data for name, data in before.files.items() if name not in ("Contents/header.xml", "Contents/section0.xml")} == {
        name: data for name, data in after.files.items() if name not in ("Contents/header.xml", "Contents/section0.xml")}


@pytest.mark.parametrize("mutation", ["source", "target", "caption", "parent"])
def test_table_layout_rejects_stale_or_tampered_fences(mutation):
    source, context = sample()
    if mutation == "source": context["source_sha256"] = "0" * 64
    else: context["table_targets"][0][{"target": "paragraph", "caption": "caption_paragraph", "parent": "parent_paragraph"}[mutation]] = 0
    with pytest.raises(HTTPException):
        apply_layout(source, context, dict(parent_half=True, caption_gap_mm=2.0))


def test_no_geometry_or_unsupported_context_cannot_claim_a_layout_change():
    with pytest.raises(ValueError): TableLayout(parent_half=False, caption_gap_mm=None)
    with pytest.raises(ValueError): TableLayout(parent_half=True, caption_gap_mm=11)
    with pytest.raises(ValueError): validated_layout({"rewrite_context": {}}, dict(parent_half=True, caption_gap_mm=None))
    source, context = sample()
    assert apply_layout(source, context, None) == source


def test_verification_rejects_unrequested_cell_change():
    source, context = sample()
    layout = dict(parent_half=True, caption_gap_mm=2.0)
    altered = Semantic(apply_layout(source, context, layout))
    table = altered.section[context["table_targets"][0]["paragraph"]].find(f".//{{{HP}}}tbl")
    next(table.iter(f"{{{HP}}}t")).text = "변조한 셀"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in altered.files.items():
            archive.writestr(name, serialize_block_xml(altered.section) if name == "Contents/section0.xml" else data)
    with pytest.raises(HTTPException): verify_rewrite_output(source, stream.getvalue(), context, [], layout)


def test_layout_context_preview_and_verification_are_read_only_and_lease_fenced(assistant):
    source, context = sample()
    client, _, doc, _, lease, original, _, folder = assistant
    request = dict(base_version=doc["current_version"], source_base64=base64.b64encode(source).decode())
    headers = {"X-Edit-Session": lease}
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    inspection = client.post(f"/api/documents/{doc['id']}/assistant-layout-context", json=request, headers=headers)
    assert inspection.status_code == 200, inspection.text
    assert inspection.json()["table_targets"] == context["table_targets"]
    assert client.post(f"/api/documents/{doc['id']}/assistant-layout-context", json=request, headers={"X-Edit-Session": "wrong"}).status_code == 409
    body = {**copy.deepcopy(original), "selection": None, "message": "표를 상위 기호 반만큼 들여쓰고 제목 아래 2mm", "rewrite_context": context}
    assert create(assistant, body).status_code == 200
    layout = dict(parent_half=True, caption_gap_mm=2.0)
    document_assistant.finish_turn(assistant[1].state.engine, body["id"], dict(answer="표 배치 변경안을 비교하세요.", rewrite_instruction=None, document_rewrites=[], table_layout=layout))
    preview = client.post(f"/api/assistant-turns/{body['id']}/layout-preview", json=request, headers=headers)
    assert preview.status_code == 200, preview.text
    output = preview.json()["output_base64"]
    verified = client.post(f"/api/assistant-turns/{body['id']}/rewrite-check", json={**request, "engine_output_base64": output}, headers=headers)
    assert verified.status_code == 200, verified.text
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    share(client, doc, "reader"); as_user(client, "bob")
    assert client.post(f"/api/assistant-turns/{body['id']}/layout-preview", json=request, headers=headers).status_code == 403
