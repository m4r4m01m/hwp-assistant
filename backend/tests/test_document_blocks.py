"""Pure manual-block composition/storage fences; no AI or Hancom claims."""
import base64
import copy
import io
import json
import uuid
import zipfile
from xml.etree import ElementTree as E
import pytest
from app.block_contract import PreviewBlock, catalog, selected_block
from app.block_hwpx import HP, HH, compose, plain_text, verify_engine_output
from app.db import Document, Version, transaction
from app.hwpx import digest
from app.report_contract import report_blocks
from app.report_hwpx import scaffold
from app.report_rich import layout_rich_report
from report_rich_fixture import RICH_DRAFT
from test_collaboration import secured, as_user, share
from test_reports import seed, synthetic_output
from test_storage import save

VALUES = {"title-frame": {"title": "새 업무 보고"}, "summary": {"label": "요약", "text": "담당자와 기한을 확인함"},
          "action-table": {"caption": "담당과 조치", "action1": "접근권한 점검", "owner1": "보안팀", "due1": "30일", "action2": "복구 훈련", "owner2": "업무팀", "due2": "60일"}}


def changed_zip(data, changes):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as dest:
        for entry in source.infolist():
            if changes.get(entry.filename, True) is not None:
                dest.writestr(entry, changes.get(entry.filename, source.read(entry)))
        for name, raw in changes.items():
            if name not in source.namelist() and raw is not None: dest.writestr(name, raw)
    return out.getvalue()


def source_document():
    blocks = report_blocks(copy.deepcopy(RICH_DRAFT), rich=True)
    data, plan = scaffold(seed(), blocks)
    for i, p in enumerate(plan): p["command_id"] = str(i)
    applied = base64.b64decode(synthetic_output({"id":"source", "scaffold_base64":base64.b64encode(data).decode(), "plan":plan})["output_base64"])
    rich = layout_rich_report(applied, plan)
    with zipfile.ZipFile(io.BytesIO(rich)) as z:
        section = E.fromstring(z.read("Contents/section0.xml"))
        # Existing mixed formatting and an opaque asset predate the inserted block.
        p = list(section)[1]; run = p.find(f"{{{HP}}}run")
        next_run = copy.deepcopy(run); next_run.find(f"{{{HP}}}t").text = " 기존 사용자 추가 입력"
        p.append(next_run)
        return changed_zip(rich, {"Contents/section0.xml": E.tostring(section), "BinData/user-image.png": b"protected-image-fixture"})


def uploaded(c, source=None):
    raw = source or source_document()
    response = c.post("/api/documents", params={"document_id": str(uuid.uuid4()), "name": "사용자 문서.hwpx"},
                      content=raw, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    doc = response.json()
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    return doc, token, raw


def payload(c, doc, raw, block_id="summary"):
    spec = next(item for item in c.get("/api/document-blocks").json()["blocks"] if item["id"] == block_id)
    with zipfile.ZipFile(io.BytesIO(raw)) as z: section = E.fromstring(z.read("Contents/section0.xml"))
    return {"request_id": str(uuid.uuid4()), "base_version": doc["current_version"], "block_id": spec["id"],
            "block_revision": spec["revision"], "block_sha256": spec["sha256"], "source_base64": base64.b64encode(raw).decode(),
            "source_sha256": digest(raw), "target": {"section":0, "paragraph":1, "expected_text_sha256":digest(plain_text(list(section)[1]).encode())},
            "variables": VALUES[block_id]}


def call(c, doc, token, suffix, value):
    return c.post(f"/api/documents/{doc['id']}/block-" + suffix, json=value, headers={"X-Edit-Session":token})


def counts(app):
    with transaction(app.state.engine) as db:
        return len(list(db.query(Document))), len(list(db.query(Version)))


@pytest.mark.parametrize("block_id", list(VALUES))
def test_preview_and_verified_roundtrip_preserve_existing_content_and_no_server_save(secured, block_id):
    c, app, _, folder = secured
    doc, token, raw = uploaded(c)
    body = payload(c, doc, raw, block_id)
    before = {f.name: digest(f.read_bytes()) for f in folder.iterdir()}
    response = call(c, doc, token, "preview", body)
    assert response.status_code == 200, response.text
    preview = response.json(); output = base64.b64decode(preview["output_base64"])
    assert preview["source_sha256"] == digest(raw) and preview["output_sha256"] == digest(output)
    assert preview["request_id"] == body["request_id"]
    assert call(c, doc, token, "preview", body).json() == preview
    verified = call(c, doc, token, "verify", body | {"engine_output_base64":preview["output_base64"]})
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"] and verified.json()["input_sha256"] == preview["input_sha256"]
    assert call(c, doc, token, "check", {"base_version":doc["current_version"]}).json()["ok"]
    assert counts(app) == (1,1) and before == {f.name: digest(f.read_bytes()) for f in folder.iterdir()}
    with zipfile.ZipFile(io.BytesIO(raw)) as old, zipfile.ZipFile(io.BytesIO(output)) as new:
        for name in old.namelist():
            if name not in ("Contents/header.xml", "Contents/section0.xml"): assert old.read(name) == new.read(name)
        old_section, section = E.fromstring(old.read("Contents/section0.xml")), E.fromstring(new.read("Contents/section0.xml"))
        inserted = preview["plan"]["top_level_count"]
        original = list(section)[:2] + list(section)[2 + inserted:]
        assert [E.tostring(p) for p in old_section] == [E.tostring(p) for p in original]
        if block_id == "action-table":
            table = list(section)[3].find(f".//{{{HP}}}tbl")
            assert table.get("rowCnt") == "3" and table.get("colCnt") == "3"
            assert int(table.find(f"{{{HP}}}sz").get("width")) <= preview["plan"]["usable_width"]


def test_stale_source_target_or_catalog_is_rejected_without_version(secured):
    c, app, _, _ = secured
    doc, token, raw = uploaded(c)
    body = payload(c, doc, raw)
    for damaged in (body | {"source_sha256":"0"*64}, body | {"block_sha256":"0"*64},
                    body | {"block_revision":2}, body | {"target":body["target"] | {"expected_text_sha256":"0"*64}}):
        assert call(c, doc, token, "preview", damaged).status_code in (409,422)
    assert counts(app) == (1,1)


def test_current_acl_lease_owner_expiry_and_base_are_checked_on_every_path(secured):
    c, app, _, _ = secured
    doc, token, raw = uploaded(c)
    body = payload(c, doc, raw)
    output = call(c, doc, token, "preview", body).json()["output_base64"]
    share(c, doc, "reader")
    as_user(c,"bob")
    for suffix, value in (("preview",body), ("verify",body | {"engine_output_base64":output}), ("check",{"base_version":doc["current_version"]})):
        assert call(c, doc, token, suffix, value).status_code == 403
    as_user(c,"alice"); share(c,doc,"editor"); as_user(c,"bob")
    assert call(c,doc,token,"check",{"base_version":doc["current_version"]}).status_code == 409
    as_user(c,"alice")
    c.delete(f"/api/documents/{doc['id']}/access/bob")
    as_user(c,"bob")
    assert call(c,doc,token,"preview",body).status_code == 404
    as_user(c,"alice")
    assert save(c,doc,token,raw).status_code == 200
    assert call(c,doc,token,"preview",body).status_code == 409
    with transaction(app.state.engine) as db: db.get(Document,doc["id"]).lease_until = 0
    assert call(c,doc,token,"check",{"base_version":doc["current_version"]}).status_code == 409


@pytest.mark.parametrize("damage",["body", "existing-table", "resource", "old-style", "new-text", "new-style", "page"])
def test_verify_rejects_semantic_or_resource_losses(secured, damage):
    c, app, _, _ = secured
    doc,token,raw=uploaded(c)
    body=payload(c,doc,raw)
    preview=call(c,doc,token,"preview",body).json()
    output=base64.b64decode(preview["output_base64"])
    with zipfile.ZipFile(io.BytesIO(output)) as z:
        header=E.fromstring(z.read("Contents/header.xml")); section=E.fromstring(z.read("Contents/section0.xml"))
        changes={}
        if damage == "body": list(section)[1].find(f"{{{HP}}}run/{{{HP}}}t").text="lost user text"
        elif damage == "existing-table": next(section.iter(f"{{{HP}}}cellSz")).set("width","1")
        elif damage == "resource": changes["BinData/user-image.png"]=b"corrupt"
        elif damage == "old-style": next(header.iter(f"{{{HH}}}charPr")).set("height","9999")
        elif damage == "new-text": list(section)[2].find(f"{{{HP}}}run/{{{HP}}}t").text="unapproved"
        elif damage == "new-style": list(section)[2].set("paraPrIDRef",list(section)[1].get("paraPrIDRef"))
        else: next(section.iter(f"{{{HP}}}pagePr")).set("width","88888")
        changes.update({"Contents/header.xml":E.tostring(header),"Contents/section0.xml":E.tostring(section)})
    changed=changed_zip(output,changes)
    response=call(c,doc,token,"verify",body | {"engine_output_base64":base64.b64encode(changed).decode()})
    assert response.status_code == 422, response.text
    assert counts(app)==(1,1)


def test_verify_accepts_renumbered_style_object_ids_and_line_cache(secured):
    c,_,_,_=secured
    doc,token,raw=uploaded(c)
    body=payload(c,doc,raw,"title-frame")
    preview=call(c,doc,token,"preview",body).json()
    output=base64.b64decode(preview["output_base64"])
    with zipfile.ZipFile(io.BytesIO(output)) as z:
        header=E.fromstring(z.read("Contents/header.xml")); section=E.fromstring(z.read("Contents/section0.xml"))
        definitions={"borderFillIDRef":"borderFill","charPrIDRef":"charPr","paraPrIDRef":"paraPr","styleIDRef":"style","nextStyleIDRef":"style","tabPrIDRef":"tabPr"}
        maps={tag:{e.get("id"):str(int(e.get("id"))+5000) for e in header.iter(f"{{{HH}}}"+tag)} for tag in set(definitions.values())}
        for root in (header,section):
            for node in root.iter():
                if node.tag.split("}")[-1] in maps: node.set("id",maps[node.tag.split("}")[-1]][node.get("id")])
                for attr,tag in definitions.items():
                    if attr in node.attrib and node.get(attr) in maps[tag]: node.set(attr,maps[tag][node.get(attr)])
        for p in section.iter(f"{{{HP}}}p"):
            p.set("id","1"); E.SubElement(p,f"{{{HP}}}linesegarray")
    altered=changed_zip(output,{"Contents/header.xml":E.tostring(header),"Contents/section0.xml":E.tostring(section)})
    response=call(c,doc,token,"verify",body | {"engine_output_base64":base64.b64encode(altered).decode()})
    assert response.status_code == 200,response.text


@pytest.mark.parametrize("variables", [{"text":""},{"text":"가"*241},{"text":"한 줄\n두 줄"},{"text":"내용","undeclared":"값"}])
def test_variables_are_bounded_declared_literal_manual_values(secured,variables):
    c,app,_,_=secured
    doc,token,raw=uploaded(c)
    body=payload(c,doc,raw)
    assert call(c,doc,token,"preview",body | {"variables":variables}).status_code == 422
    assert counts(app)==(1,1)


@pytest.mark.parametrize("structure",["multi-section","columns","object-target"])
def test_unsupported_insertion_structure_fails_closed(secured,structure):
    c,app,_,_=secured
    doc,token,raw=uploaded(c)
    body=payload(c,doc,raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        section=E.fromstring(z.read("Contents/section0.xml")); changes={}
        if structure == "multi-section": changes["Contents/section1.xml"]=E.tostring(section)
        elif structure == "columns": next(section.iter(f"{{{HP}}}colPr")).set("colCount","2")
        else: E.SubElement(list(section)[1].find(f"{{{HP}}}run"),f"{{{HP}}}tbl")
        changes["Contents/section0.xml"]=E.tostring(section)
    altered=changed_zip(raw,changes)
    body.update(source_base64=base64.b64encode(altered).decode(),source_sha256=digest(altered))
    assert call(c,doc,token,"preview",body).status_code == 422
    assert counts(app)==(1,1)


def test_streaming_total_json_limit_and_invalid_base64(secured,monkeypatch):
    from app import document_blocks
    c,app,_,_=secured
    doc,token,raw=uploaded(c)
    body=payload(c,doc,raw)
    assert call(c,doc,token,"preview",body | {"source_base64":"not-valid!"}).status_code == 422
    monkeypatch.setattr(document_blocks,"MAX_JSON",100)
    assert call(c,doc,token,"preview",body).status_code == 413
    assert counts(app)==(1,1)


@pytest.mark.parametrize("damage", ["head-attribute", "beginNum", "extra-reference", "field-link"])
def test_header_settings_and_unhandled_field_links_fail_closed(secured, damage):
    c, app, _, _ = secured
    doc, token, raw = uploaded(c)
    body = payload(c, doc, raw)
    if damage == "field-link":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            section = E.fromstring(z.read("Contents/section0.xml"))
        run = list(section)[3].find(f"{{{HP}}}run")
        E.SubElement(run, f"{{{HP}}}fieldBegin", {"id":"10"})
        E.SubElement(run, f"{{{HP}}}fieldEnd", {"beginIDRef":"10"})
        source = changed_zip(raw, {"Contents/section0.xml":E.tostring(section)})
        body.update(source_base64=base64.b64encode(source).decode(),source_sha256=digest(source))
        assert call(c,doc,token,"preview",body).status_code == 422
    else:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            header = E.fromstring(z.read("Contents/header.xml"))
        if damage == "extra-reference":
            E.SubElement(header.find(f"{{{HH}}}refList"), f"{{{HH}}}customReference", {"id":"kept"})
            raw = changed_zip(raw, {"Contents/header.xml":E.tostring(header)})
            body.update(source_base64=base64.b64encode(raw).decode(), source_sha256=digest(raw))
        preview = call(c, doc, token, "preview", body).json()
        output = base64.b64decode(preview["output_base64"])
        with zipfile.ZipFile(io.BytesIO(output)) as z: header = E.fromstring(z.read("Contents/header.xml"))
        if damage == "head-attribute": header.set("secCnt","9")
        elif damage == "beginNum":
            node=header.find(f"{{{HH}}}beginNum")
            assert node is not None
            node.set("page","999")
        else:
            references=header.find(f"{{{HH}}}refList")
            references.remove(references.find(f"{{{HH}}}customReference"))
        altered=changed_zip(output,{"Contents/header.xml":E.tostring(header)})
        assert call(c,doc,token,"verify",body | {"engine_output_base64":base64.b64encode(altered).decode()}).status_code == 422
    assert counts(app)==(1,1)


@pytest.mark.parametrize("damage", ["mixed-tail", "package-metadata", "package-spine"])
def test_mixed_content_tails_package_metadata_and_spine_preserved(secured, damage):
    c,app,_,_=secured
    source=source_document()
    with zipfile.ZipFile(io.BytesIO(source)) as z:
        section=E.fromstring(z.read("Contents/section0.xml"))
        package=E.fromstring(z.read("Contents/content.hpf"))
    if damage == "mixed-tail":
        t=list(section)[3].find(f"{{{HP}}}run/{{{HP}}}t")
        E.SubElement(t,f"{{{HP}}}tab",{"width":"100"}).tail=" 보존할 탭 뒤 내용"
    else:
        opf="http://www.idpf.org/2007/opf/"
        if damage == "package-metadata":
            E.SubElement(package,"{test}creator").text="original-author"
        else:
            manifest=E.SubElement(package,"{"+opf+"}manifest")
            E.SubElement(manifest,"{"+opf+"}item",{"id":"section0","href":"Contents/section0.xml","media-type":"text/xml"})
            spine=E.SubElement(package,"{"+opf+"}spine")
            E.SubElement(spine,"{"+opf+"}itemref",{"idref":"section0"})
    source=changed_zip(source,{"Contents/section0.xml":E.tostring(section),"Contents/content.hpf":E.tostring(package)})
    doc,token,_=uploaded(c,source)
    body=payload(c,doc,source)
    preview=call(c,doc,token,"preview",body).json()
    output=base64.b64decode(preview["output_base64"])
    with zipfile.ZipFile(io.BytesIO(output)) as z:
        section=E.fromstring(z.read("Contents/section0.xml")); package=E.fromstring(z.read("Contents/content.hpf"))
    if damage == "mixed-tail":
        next(section.iter(f"{{{HP}}}tab")).tail="modified-tail"
    elif damage == "package-metadata": package.find("{test}creator").text="another-author"
    else:
        next(e for e in package.iter() if e.tag.endswith("}itemref")).set("idref","missing")
    altered=changed_zip(output,{"Contents/section0.xml":E.tostring(section),"Contents/content.hpf":E.tostring(package)})
    assert call(c,doc,token,"verify",body | {"engine_output_base64":base64.b64encode(altered).decode()}).status_code == 422
    assert counts(app)==(1,1)


def test_text_node_coalescing_preserves_different_attributes():
    source=source_document()
    with zipfile.ZipFile(io.BytesIO(source)) as z: section=E.fromstring(z.read("Contents/section0.xml"))
    second=list(section)[1].findall(f"{{{HP}}}run")[1].find(f"{{{HP}}}t")
    second.set("{http://www.w3.org/XML/1998/namespace}space","preserve")
    source=changed_zip(source,{"Contents/section0.xml":E.tostring(section)})
    second.attrib.clear()
    damaged=changed_zip(source,{"Contents/section0.xml":E.tostring(section)})
    with pytest.raises(Exception) as error: verify_engine_output(source,source,damaged)
    assert error.value.status_code == 422


def test_cold_block_serialization_is_independent_of_global_namespace_registration():
    from app.block_hwpx import serialize_block_xml
    source=source_document()
    with zipfile.ZipFile(io.BytesIO(source)) as z:
        header=E.fromstring(z.read("Contents/header.xml")); section=E.fromstring(z.read("Contents/section0.xml"))
    before=dict(E._namespace_map)
    try:
        E._namespace_map.clear()
        encoded_header=serialize_block_xml(header); encoded_section=serialize_block_xml(section)
        assert E._namespace_map == {}
        assert b"<hh:paraHead" in encoded_header or b"<hh:bullet" in encoded_header
        assert b"xmlns:hh=" in encoded_header and b"xmlns:hp=" in encoded_section
        assert b"ns0:" not in encoded_header and b"ns0:" not in encoded_section
        assert E.tostring(E.fromstring(encoded_header)) == E.tostring(header)
        assert E.tostring(E.fromstring(encoded_section)) == E.tostring(section)
    finally:
        E._namespace_map.clear(); E._namespace_map.update(before)


@pytest.mark.parametrize("block_id", list(VALUES))
def test_new_standard_char_effects_are_explicit_without_rewriting_old_styles(secured, block_id):
    c,app,_,_=secured
    doc,token,raw=uploaded(c)
    body=payload(c,doc,raw,block_id)
    preview=call(c,doc,token,"preview",body).json()
    output=base64.b64decode(preview["output_base64"])
    with zipfile.ZipFile(io.BytesIO(raw)) as z: old=E.fromstring(z.read("Contents/header.xml"))
    with zipfile.ZipFile(io.BytesIO(output)) as z: header=E.fromstring(z.read("Contents/header.xml"))
    originals={n.get("id"):E.tostring(n) for n in old.iter(f"{{{HH}}}charPr")}
    added=[]
    for n in header.iter(f"{{{HH}}}charPr"):
        if n.get("id") in originals:
            assert E.tostring(n)==originals[n.get("id")]
        else:
            added.append(n)
            assert n.find(f"{{{HH}}}underline") is not None
            assert n.find(f"{{{HH}}}strikeout").get("shape")=="NONE"
            assert n.find(f"{{{HH}}}outline").get("type")=="NONE"
            assert n.find(f"{{{HH}}}shadow").attrib==dict(type="NONE",color="#000000",offsetX="0",offsetY="0")
            tags=[child.tag for child in n]
            if f"{{{HH}}}bold" in tags: assert tags.index(f"{{{HH}}}bold") < tags.index(f"{{{HH}}}underline")
    assert added
    # Defaults remain exact: a new active effect is never excused as normalization.
    added[-1].find(f"{{{HH}}}outline").set("type","SOLID")
    altered=changed_zip(output,{"Contents/header.xml":E.tostring(header)})
    assert call(c,doc,token,"verify",body | {"engine_output_base64":base64.b64encode(altered).decode()}).status_code==422
    assert counts(app)==(1,1)


@pytest.mark.parametrize("damage", ["heading-reference", "section-outline", "numbering-definition"])
def test_outline_zero_fallback_preserves_section_and_numbering_contract(secured, damage):
    c,app,_,_=secured
    raw=source_document()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        header=E.fromstring(z.read("Contents/header.xml")); section=E.fromstring(z.read("Contents/section0.xml"))
    refs=header.find(f"{{{HH}}}refList")
    numberings=E.SubElement(refs,f"{{{HH}}}numberings",{"itemCnt":"1"})
    numbering=E.SubElement(numberings,f"{{{HH}}}numbering",{"id":"1","start":"0"})
    E.SubElement(numbering,f"{{{HH}}}paraHead",{"level":"1","start":"1","charPrIDRef":"4294967295"}).text="^1."
    para=copy.deepcopy(next(header.iter(f"{{{HH}}}paraPr")))
    paras=refs.find(f"{{{HH}}}paraProperties")
    para.set("id",str(max(int(n.get("id")) for n in paras)+1))
    para.find(f"{{{HH}}}heading").attrib.update(type="OUTLINE",idRef="0")
    paras.append(para);paras.set("itemCnt",str(len(paras)))
    next(section.iter(f"{{{HP}}}secPr")).set("outlineShapeIDRef","1")
    raw=changed_zip(raw,{"Contents/header.xml":E.tostring(header),"Contents/section0.xml":E.tostring(section)})
    doc,token,_=uploaded(c,raw)
    body=payload(c,doc,raw,"title-frame")
    preview=call(c,doc,token,"preview",body).json()
    output=base64.b64decode(preview["output_base64"])
    assert call(c,doc,token,"verify",body | {"engine_output_base64":preview["output_base64"]}).status_code==200
    with zipfile.ZipFile(io.BytesIO(output)) as z:
        header=E.fromstring(z.read("Contents/header.xml"));section=E.fromstring(z.read("Contents/section0.xml"))
    if damage=="heading-reference":
        next(n for n in header.iter(f"{{{HH}}}heading") if n.get("type")=="OUTLINE").set("idRef","9999")
    elif damage=="section-outline":next(section.iter(f"{{{HP}}}secPr")).set("outlineShapeIDRef","0")
    else:next(header.iter(f"{{{HH}}}paraHead")).text="changed-numbering-format"
    altered=changed_zip(output,{"Contents/header.xml":E.tostring(header),"Contents/section0.xml":E.tostring(section)})
    assert call(c,doc,token,"verify",body | {"engine_output_base64":base64.b64encode(altered).decode()}).status_code==422
    assert counts(app)==(1,1)



def test_new_transparent_fill_uses_native_sentinel_and_keeps_original_fills(secured):
    c,app,_,_=secured
    doc,token,raw=uploaded(c)
    body=payload(c,doc,raw,"summary")
    preview=call(c,doc,token,"preview",body).json()
    output=base64.b64decode(preview["output_base64"])
    with zipfile.ZipFile(io.BytesIO(raw)) as z: old=E.fromstring(z.read("Contents/header.xml"))
    with zipfile.ZipFile(io.BytesIO(output)) as z: header=E.fromstring(z.read("Contents/header.xml"))
    originals={n.get("id"):E.tostring(n) for n in old.iter(f"{{{HH}}}borderFill")}
    added=[]
    hc="http://www.hancom.co.kr/hwpml/2011/core"
    for n in header.iter(f"{{{HH}}}borderFill"):
        if n.get("id") in originals: assert E.tostring(n)==originals[n.get("id")]
        else:
            for brush in n.iter(f"{{{hc}}}winBrush"):
                assert brush.get("faceColor") != "none"
                if brush.get("faceColor")=="#FFFFFFFF":added.append(brush)
    assert added
    assert call(c,doc,token,"verify",body | {"engine_output_base64":preview["output_base64"]}).status_code==200
    # Opaque white is not transparent: the verifier must retain the distinction.
    added[-1].set("faceColor","#FFFFFF")
    altered=changed_zip(output,{"Contents/header.xml":E.tostring(header)})
    assert call(c,doc,token,"verify",body | {"engine_output_base64":base64.b64encode(altered).decode()}).status_code==422
    assert counts(app)==(1,1)



def test_block_xml_encoding_preserves_cr_text_tails_and_attributes():
    from app.block_hwpx import serialize_block_xml
    root=E.Element(f"{{{HP}}}t",{"custom":"attribute\r\n\t&<>"})
    root.text="before\rCR\r\nCRLF\nLF\tTAB&<>"
    child=E.SubElement(root,f"{{{HP}}}tab",{"width":"100"})
    child.tail="after\rCR\r\nCRLF\nLF\tTAB&<>"
    encoded=serialize_block_xml(root)
    restored=E.fromstring(encoded)
    assert b"&#13;" in encoded
    assert restored.attrib==root.attrib
    assert restored.text==root.text and restored[0].tail==child.tail


def test_composition_preserves_non_target_cr_text_and_mixed_tail(secured):
    c,app,_,_=secured
    raw=source_document()
    with zipfile.ZipFile(io.BytesIO(raw)) as z: section=E.fromstring(z.read("Contents/section0.xml"))
    t=list(section)[3].find(f"{{{HP}}}run/{{{HP}}}t")
    t.text += " before\rCR\r\nCRLF"
    E.SubElement(t,f"{{{HP}}}tab",{"width":"100"}).tail=" after\rCR\r\nCRLF"
    # Preserve CR in the input fixture as numeric references, like canonical XML.
    section_xml=E.tostring(section).replace(b"\r",b"&#13;")
    raw=changed_zip(raw,{"Contents/section0.xml":section_xml})
    doc,token,_=uploaded(c,raw)
    body=payload(c,doc,raw,"summary")
    preview=call(c,doc,token,"preview",body).json()
    output=base64.b64decode(preview["output_base64"])
    with zipfile.ZipFile(io.BytesIO(output)) as z: rendered=E.fromstring(z.read("Contents/section0.xml"))
    retained=list(rendered)[4].find(f"{{{HP}}}run/{{{HP}}}t")
    assert retained.text==t.text and retained[-1].tail==t[-1].tail
    assert call(c,doc,token,"verify",body | {"engine_output_base64":preview["output_base64"]}).status_code==200
    assert counts(app)==(1,1)
