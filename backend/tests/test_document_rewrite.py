"""Synthetic whole-document plans and HWPX validation; no live model/engine."""
import base64
import copy
import uuid
from xml.etree import ElementTree as E

import pytest

from app import document_assistant
from app.block_hwpx import HP, Semantic
from app.document_rewrite import paragraph_prefix
from app.hwpx import digest
from test_document_assistant import assistant, create
from test_document_blocks import source_document, changed_zip
from test_collaboration import as_user, share, secured


def plan(fixture):
    raw = source_document()
    semantic = Semantic(raw)
    index, node = next((i, p) for i, p in enumerate(semantic.section) if paragraph_prefix(semantic, p))
    text = "".join(t.text or "" for t in node.iter(f"{{{HP}}}t"))
    prefix = paragraph_prefix(semantic, node)
    context = dict(source_sha256=digest(raw), paragraphs=[dict(section=0, paragraph=index, text=text,
        text_sha256=digest(text.encode()), length=len(text), fixed_prefix=prefix)], preserved={"제목·표·주석": 4}, unavailable="")
    body = {**copy.deepcopy(fixture[5]), "selection": None, "message": "전체 본문 문장을 간결하게 다듬어줘", "rewrite_context": context}
    change = dict(section=0, paragraph=index, before_sha256=digest(text.encode()), replacement=prefix + " 실행 항목과 책임을 관리할 필요")
    return raw, body, change


def finish(fixture, body, changes):
    document_assistant.finish_turn(fixture[1].state.engine, body["id"],
        dict(answer="제목과 표를 보존하면서 지원 가능한 본문 문장을 간결하게 다듬었습니다. 비교 후 적용하세요.",
             rewrite_instruction=None, document_rewrites=changes))
    return fixture[0].get(f"/api/assistant-turns/{body['id']}").json()


def changed_output(raw, change):
    semantic = Semantic(raw)
    paragraph = semantic.section[change["paragraph"]]
    prefix = paragraph_prefix(semantic, paragraph)
    offset = 0
    for node in paragraph.iter(f"{{{HP}}}t"):
        if offset < len(prefix):
            offset += len(node.text or "")
        else:
            node.text = change["replacement"][len(prefix):] if offset == len(prefix) else ""
            offset = len(prefix) + 1
    return changed_zip(raw, {"Contents/section0.xml": E.tostring(semantic.section)})


def test_document_scope_without_selection_is_frozen_and_never_writes_original(assistant):
    raw, body, change = plan(assistant)
    before = {p.name: p.read_bytes() for p in assistant[7].iterdir()}
    assert create(assistant, body).status_code == 200
    result = finish(assistant, body, [change])
    assert result["status"] == "ready", result
    assert result["selection"] is None and result["document_rewrites"] == [change]
    assert create(assistant, body).json() == result
    assert len(assistant[6]) == 1
    modified = copy.deepcopy(body); modified["rewrite_context"]["source_sha256"] = "0" * 64
    assert create(assistant, modified).status_code == 409
    output = changed_output(raw, change)
    verified = assistant[0].post(f"/api/assistant-turns/{body['id']}/rewrite-check", json=dict(
        base_version=body["base_version"], source_base64=base64.b64encode(raw).decode(), engine_output_base64=base64.b64encode(output).decode()),
        headers={"X-Edit-Session": assistant[4]})
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"] and verified.json()["output_sha256"] == digest(output)
    assert {p.name: p.read_bytes() for p in assistant[7].iterdir()} == before
    assert len(assistant[0].get(f"/api/documents/{assistant[2]['id']}/versions").json()) == 1


@pytest.mark.parametrize("mutation", ["target", "hash", "prefix", "number", "newline", "duplicate"])
def test_whole_plan_rejects_unfrozen_or_semantically_unsafe_replacements(assistant, mutation):
    _, body, change = plan(assistant)
    assert create(assistant, body).status_code == 200
    if mutation == "target": change["paragraph"] += 1
    if mutation == "hash": change["before_sha256"] = "f" * 64
    if mutation == "prefix": change["replacement"] = change["replacement"].replace("(조치)", "(결론)")
    if mutation == "number": change["replacement"] += " 30일"
    if mutation == "newline": change["replacement"] += "\n새 문단"
    result = finish(assistant, body, [change, change] if mutation == "duplicate" else [change])
    assert result["status"] == "failed", result


@pytest.mark.parametrize("mutation", ["title", "table", "resource", "prefix-style", "source", "lease", "reader"])
def test_verify_rejects_other_content_changes_and_lost_authority(assistant, mutation):
    raw, body, change = plan(assistant)
    assert create(assistant, body).status_code == 200
    assert finish(assistant, body, [change])["status"] == "ready"
    output = changed_output(raw, change)
    semantic = Semantic(output)
    if mutation == "title": semantic.section[1].find(f"{{{HP}}}run/{{{HP}}}t").text = "바뀐 제목"
    if mutation == "table": next(semantic.section.iter(f"{{{HP}}}tbl")).find(f".//{{{HP}}}t").text = "바뀐 표"
    if mutation == "prefix-style":
        runs = semantic.section[change["paragraph"]].findall(f"{{{HP}}}run")
        runs[1].set("charPrIDRef", runs[-1].get("charPrIDRef"))
    output = changed_zip(output, {"Contents/section0.xml": E.tostring(semantic.section)})
    if mutation == "resource": output = changed_zip(output, {"BinData/user-image.png": None})
    if mutation == "source": raw = output
    token = "wrong" if mutation == "lease" else assistant[4]
    if mutation == "reader":
        share(assistant[0], assistant[2], "reader"); as_user(assistant[0], "bob")
    response = assistant[0].post(f"/api/assistant-turns/{body['id']}/rewrite-check", json=dict(base_version=body["base_version"],
        source_base64=base64.b64encode(raw).decode(), engine_output_base64=base64.b64encode(output).decode()), headers={"X-Edit-Session": token})
    assert response.status_code in (403, 409, 422), response.text


def test_context_limits_and_hashes_prevent_partial_silent_rewrite(assistant):
    _, body, _ = plan(assistant)
    body["rewrite_context"]["paragraphs"][0]["text_sha256"] = "0" * 64
    assert create(assistant, body).status_code == 422
    _, body, _ = plan(assistant)
    body["rewrite_context"]["paragraphs"] *= 121
    assert create(assistant, body).status_code == 422
    assert not assistant[6]
