"""Style application contracts on synthetic files; real rhwp is tested separately."""
import base64
import copy
import io
import uuid
import zipfile
from xml.etree import ElementTree as E

import pytest

from app.block_hwpx import HH, HP, Semantic, plain_text
from app.db import Document, Version, transaction
from app.editor_design_hwpx import paragraph_controls
from app.hwpx import digest
from app.reference_standard import defaults
from app.reference_source import mm_to_hwp
from test_collaboration import as_user, secured, share
from test_document_blocks import changed_zip, source_document, uploaded
from test_standard_samples import finish, prepare, registration
from test_storage import save


def roots(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return E.fromstring(archive.read("Contents/header.xml")), E.fromstring(archive.read("Contents/section0.xml"))


def choice(client, kind="title"):
    result = client.get("/api/editor-designs")
    assert result.status_code == 200, result.text
    key = "standards" if kind == "standard" else "designs"
    item = next(item for item in result.json()[key] if kind == "standard" or item["kind"] == kind)
    return dict(kind="standard" if kind == "standard" else "design", id=item["id"], sha256=item["sha256"])


def target_table(raw):
    _, section = roots(raw)
    for index, paragraph in enumerate(section):
        for control, node in enumerate(paragraph_controls(paragraph)):
            if node.tag == f"{{{HP}}}tbl" and node.get("colCnt") != "1":
                return dict(kind="table", section=0, paragraph=index, control=control)
    raise AssertionError("Synthetic document needs a table")


def payload(client, doc, raw, kind="title"):
    target = dict(kind="document", section=0) if kind == "standard" else target_table(raw) if kind == "table" else dict(kind="paragraph", section=0, paragraph=1)
    return dict(request_id=str(uuid.uuid4()), base_version=doc["current_version"], source_base64=base64.b64encode(raw).decode(),
                source_sha256=digest(raw), target=target, choice=choice(client, kind))


def post(client, doc, token, action, body):
    return client.post(f"/api/documents/{doc['id']}/design-{action}", json=body, headers={"X-Edit-Session": token})


@pytest.mark.parametrize("kind", ["title", "table", "standard"])
def test_preview_preserves_text_resources_source_and_versions(secured, kind):
    client, app, _, folder = secured
    doc, token, raw = uploaded(client)
    body = payload(client, doc, raw, kind)
    original = {path.name: path.read_bytes() for path in folder.iterdir()}
    response = post(client, doc, token, "preview", body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert post(client, doc, token, "preview", body).json() == result
    output = base64.b64decode(result["output_base64"])
    assert result["source_sha256"] == digest(raw) and result["output_sha256"] == digest(output)
    assert result["summary"]["content_preserved"] and result["summary"]["changed_paragraphs"] > 0
    before, after = roots(raw)[1], roots(output)[1]
    assert [t.text for t in before.iter(f"{{{HP}}}t")] == [t.text for t in after.iter(f"{{{HP}}}t")]
    assert [node.tag for node in before.iter() if not node.tag.endswith(("lineseg", "linesegarray"))] == [node.tag for node in after.iter()]
    with zipfile.ZipFile(io.BytesIO(raw)) as first, zipfile.ZipFile(io.BytesIO(output)) as second:
        assert first.namelist() == second.namelist()
        for name in first.namelist():
            if name not in ("Contents/header.xml", "Contents/section0.xml"):
                assert first.read(name) == second.read(name)
    verified = post(client, doc, token, "verify", {**body, "engine_output_base64": result["output_base64"]})
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"] and verified.json()["input_sha256"] == result["input_sha256"]
    assert post(client, doc, token, "check", {"base_version": doc["current_version"]}).status_code == 200
    assert original == {path.name: path.read_bytes() for path in folder.iterdir()}
    with transaction(app.state.engine) as db:
        assert db.query(Document).count() == db.query(Version).count() == 1


@pytest.mark.parametrize("kind", ["title", "table"])
def test_partial_design_keeps_every_other_paragraph_and_page(secured, kind):
    client, _, _, _ = secured
    doc, token, raw = uploaded(client)
    body = payload(client, doc, raw, kind)
    response = post(client, doc, token, "preview", body)
    assert response.status_code == 200, response.text
    original, changed = Semantic(raw), Semantic(base64.b64decode(response.json()["output_base64"]))
    index = body["target"]["paragraph"]
    for i in range(len(original.section)):
        if i != index:
            assert original.node(original.section[i]) == changed.node(changed.section[i])
    assert E.tostring(original.section.find(f".//{{{HP}}}secPr")) == E.tostring(changed.section.find(f".//{{{HP}}}secPr"))
    assert original.node(original.section[index]) != changed.node(changed.section[index])


def test_title_paragraph_reserves_frame_extent_in_addition_to_configured_spacing(secured):
    client, _, _, _ = secured
    doc, token, raw = uploaded(client)
    configured = {**defaults(), "title_before_pt": 3, "title_after_pt": 7}
    saved = client.post("/api/design-fragments", json=dict(id=str(uuid.uuid4()), name="합성 제목 간격", kind="title", settings=configured))
    assert saved.status_code == 200, saved.text
    item = next(item for item in client.get("/api/editor-designs").json()["designs"] if item["id"] == saved.json()["id"])
    body = payload(client, doc, raw)
    body["choice"].update(id=item["id"], sha256=item["sha256"])
    observed = []
    for _ in range(2):
        response = post(client, doc, token, "preview", body)
        assert response.status_code == 200, response.text
        result = response.json()
        header, section = roots(base64.b64decode(result["output_base64"]))
        para = next(p for p in header.iter(f"{{{HH}}}paraPr") if p.get("id") == section[1].get("paraPrIDRef"))
        border = para.find(f"{{{HH}}}border")
        stroke = mm_to_hwp(configured["title_rule_width_mm"])
        reserve = int(border.get("offsetBottom")) + (stroke + 1) // 2 + stroke
        switch = para.find(f"{{{HP}}}switch")
        branches = list(switch) if switch is not None else [para]
        values = []
        for branch in branches:
            factor = 2 if branch.tag.endswith("}default") else 1
            margin = branch.find(f"{{{HH}}}margin")
            assert margin is not None
            previous = next(node for node in margin if node.tag.endswith("}prev"))
            following = next(node for node in margin if node.tag.endswith("}next"))
            assert int(previous.get("value")) == (300 + reserve) * factor
            assert int(following.get("value")) == (700 + reserve) * factor
            values.append((previous.get("value"), following.get("value")))
        observed.append(values)
        body.update(request_id=str(uuid.uuid4()), source_base64=result["output_base64"], source_sha256=result["output_sha256"])
    assert observed[0] == observed[1]


def test_control_number_is_native_all_controls_and_not_table_ordinal(secured):
    client, _, _, _ = secured
    raw = source_document()
    _, section = roots(raw)
    target = target_table(raw)
    run = section[target["paragraph"]].find(f"{{{HP}}}run")
    ctrl = E.Element(f"{{{HP}}}ctrl")
    E.SubElement(ctrl, f"{{{HP}}}pageNum", pos="NONE", formatType="DIGIT", sideChar="-")
    run.insert(0, ctrl)
    raw = changed_zip(raw, {"Contents/section0.xml": E.tostring(section)})
    doc, token, _ = uploaded(client, raw)
    body = payload(client, doc, raw, "table")
    assert body["target"]["control"] == 1
    assert post(client, doc, token, "preview", body).status_code == 200
    body["target"]["control"] = 0
    assert post(client, doc, token, "preview", body).status_code == 409


def title_table_source(*, blue=True, sibling=False):
    raw = source_document()
    header, section = roots(raw)
    target = target_table(raw)
    paragraph = section[target["paragraph"]]
    table = paragraph_controls(paragraph)[target["control"]]
    rows = table.findall(f"{{{HP}}}tr")
    for row in rows[1:]:
        table.remove(row)
    for cell in list(rows[0])[1:]:
        rows[0].remove(cell)
    table.attrib.update(rowCnt="1", colCnt="1")
    cell = rows[0][0]
    if blue:
        cell.set("borderFillIDRef", "7")
    for text in cell.iter(f"{{{HP}}}t"):
        text.text = "합성 제목 상자"
    if sibling:
        run = E.SubElement(paragraph, f"{{{HP}}}run", charPrIDRef="0")
        E.SubElement(run, f"{{{HP}}}t").text = "원래 본문"
    return changed_zip(raw, {"Contents/section0.xml": E.tostring(section)}), target


def test_title_design_applies_to_recognized_frame_and_can_be_reapplied(secured):
    client, _, _, _ = secured
    raw, target = title_table_source()
    doc, token, _ = uploaded(client, raw)
    body = {**payload(client, doc, raw), "target": target}
    fragment = client.post("/api/design-fragments", json=dict(id=str(uuid.uuid4()), name="합성 빨간 제목", kind="title",
                           settings={**defaults(), "title_rule_color": "#FF0000"})).json()
    selected = next(item for item in client.get("/api/editor-designs").json()["designs"] if item["id"] == fragment["id"])
    body["choice"].update(id=selected["id"], sha256=selected["sha256"])
    result = post(client, doc, token, "preview", body)
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["summary"]["changed_tables"] == 1
    assert any("높이" in text for text in result["warnings"])
    output = base64.b64decode(result["output_base64"])
    assert [t.text for t in roots(raw)[1].iter(f"{{{HP}}}t")] == [t.text for t in roots(output)[1].iter(f"{{{HP}}}t")]
    assert post(client, doc, token, "verify", {**body, "engine_output_base64": result["output_base64"]}).status_code == 200
    retry = {**body, "request_id": str(uuid.uuid4()), "source_base64": result["output_base64"], "source_sha256": result["output_sha256"]}
    assert post(client, doc, token, "preview", retry).status_code == 200


@pytest.mark.parametrize("blue,sibling", [(False, False), (True, True)])
def test_title_design_does_not_reinterpret_ordinary_table_or_sibling_body(secured, blue, sibling):
    client, _, _, _ = secured
    raw, target = title_table_source(blue=blue, sibling=sibling)
    doc, token, _ = uploaded(client, raw)
    response = post(client, doc, token, "preview", {**payload(client, doc, raw), "target": target})
    assert response.status_code == 422, response.text


def test_merged_table_rejected_as_partial_and_reported_in_whole_document(secured):
    client, _, _, _ = secured
    raw = source_document()
    _, section = roots(raw)
    section.find(f".//{{{HP}}}tbl/{{{HP}}}tr/{{{HP}}}tc/{{{HP}}}cellSpan").set("colSpan", "2")
    raw = changed_zip(raw, {"Contents/section0.xml": E.tostring(section)})
    doc, token, _ = uploaded(client, raw)
    response = post(client, doc, token, "preview", payload(client, doc, raw, "table"))
    assert response.status_code == 422, response.text
    response = post(client, doc, token, "preview", payload(client, doc, raw, "standard"))
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["skipped_tables"] >= 1
    assert any("병합" in text for text in response.json()["warnings"])


def test_whole_standard_preserves_unknown_prose_and_announces_structure_scope(secured):
    client, _, _, _ = secured
    raw = source_document()
    header, section = roots(raw)
    # An unlabelled prose paragraph and weekday must not become inferred headings.
    for text in ("지정하지 않은 일반 문장", "(금) 합성 점검을 실시함"):
        paragraph = copy.deepcopy(section[2])
        for run in paragraph.findall(f"{{{HP}}}run"):
            for t in run.findall(f"{{{HP}}}t"):
                t.text = text
        section.append(paragraph)
    raw = changed_zip(raw, {"Contents/section0.xml": E.tostring(section)})
    doc, token, _ = uploaded(client, raw)
    response = post(client, doc, token, "preview", payload(client, doc, raw, "standard"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["summary"]["skipped_paragraphs"] >= 2
    assert any("표지·목차" in text for text in result["warnings"])
    first, second = Semantic(raw), Semantic(base64.b64decode(result["output_base64"]))
    assert first.node(first.section[-1]) == second.node(second.section[-1])
    assert first.node(first.section[-2]) == second.node(second.section[-2])


@pytest.mark.parametrize("damage", ["source", "choice", "target", "text", "asset", "extra-asset", "old-style"])
def test_stale_or_tampered_inputs_and_engine_changes_are_rejected(secured, damage):
    client, _, _, _ = secured
    doc, token, raw = uploaded(client)
    body = payload(client, doc, raw)
    if damage == "source":
        body["source_sha256"] = "0" * 64
    elif damage == "choice":
        body["choice"]["sha256"] = "0" * 64
    elif damage == "target":
        body["target"]["paragraph"] = 10000
    else:
        preview = post(client, doc, token, "preview", body).json()
        output = base64.b64decode(preview["output_base64"])
        if damage == "text":
            _, section = roots(output)
            section.find(f".//{{{HP}}}t").text = "추가된 잘못된 문구"
            output = changed_zip(output, {"Contents/section0.xml": E.tostring(section)})
        elif damage == "asset":
            output = changed_zip(output, {"BinData/user-image.png": b"modified"})
        elif damage == "extra-asset":
            output = changed_zip(output, {"BinData/unexpected.png": b"unexpected"})
        else:
            header, _ = roots(output)
            header.find(f".//{{{HH}}}charPr").set("height", "1999")
            output = changed_zip(output, {"Contents/header.xml": E.tostring(header)})
        body["engine_output_base64"] = base64.b64encode(output).decode()
    response = post(client, doc, token, "verify" if damage in ("text", "asset", "extra-asset", "old-style") else "preview", body)
    assert response.status_code in (409, 422), response.text


def test_every_endpoint_checks_acl_lease_and_version(secured):
    client, app, _, _ = secured
    doc, token, raw = uploaded(client)
    body = payload(client, doc, raw)
    output = post(client, doc, token, "preview", body).json()["output_base64"]
    share(client, doc, "reader")
    as_user(client, "bob")
    for action, request in (("preview", body), ("verify", {**body, "engine_output_base64": output}), ("check", {"base_version": doc["current_version"]})):
        assert post(client, doc, token, action, request).status_code == 403
    as_user(client, "alice")
    share(client, doc, "editor")
    as_user(client, "bob")
    assert post(client, doc, token, "preview", body).status_code == 409
    as_user(client, "alice")
    assert save(client, doc, token, raw).status_code == 200
    assert post(client, doc, token, "preview", body).status_code == 409
    with transaction(app.state.engine) as db:
        db.get(Document, doc["id"]).lease_until = 0
    assert post(client, doc, token, "check", {"base_version": doc["current_version"]}).status_code == 409


def test_owned_fragments_and_standards_are_listed_and_removed_choices_rejected(secured):
    client, _, _, _ = secured
    fragment = client.post("/api/design-fragments", json=dict(id=str(uuid.uuid4()), name="합성 사용자 제목", kind="title", settings={**defaults(), "title_color": "#0000FF"})).json()
    _, stage = prepare(client)
    saved = client.post("/api/visual-standards/sample-register", json=registration(finish(client, stage)))
    assert saved.status_code == 200, saved.text
    standard = saved.json()
    catalog = client.get("/api/editor-designs").json()
    assert any(item["id"] == fragment["id"] for item in catalog["designs"])
    assert any(item["id"] == standard["id"] for item in catalog["standards"])
    doc, token, raw = uploaded(client)
    body = payload(client, doc, raw)
    item = next(item for item in catalog["designs"] if item["id"] == fragment["id"])
    body["choice"].update(id=item["id"], sha256=item["sha256"])
    assert post(client, doc, token, "preview", body).status_code == 200
    as_user(client, "bob")
    theirs = client.get("/api/editor-designs").json()
    assert not any(item["id"] == fragment["id"] for item in theirs["designs"])
    assert not any(item["id"] == standard["id"] for item in theirs["standards"])
    as_user(client, "alice")
    assert client.delete("/api/design-fragments/" + fragment["id"]).status_code == 200
    assert post(client, doc, token, "preview", body).status_code == 404
    assert client.delete("/api/visual-standards/" + standard["id"]).status_code == 200
    assert not any(item["id"] == standard["id"] for item in client.get("/api/editor-designs").json()["standards"])
