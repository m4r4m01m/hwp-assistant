"""Managed TOC safety contracts. Synthetic page evidence is not rendered-page proof."""
import base64
import copy
import io
import uuid
import zipfile
from xml.etree import ElementTree as E
import pytest
from fastapi import HTTPException
from app.block_hwpx import HP, HH, serialize_block_xml
from app.db import Document, transaction
from app.hwpx import digest
from app.toc_contract import TocPreview, TocVerify, input_hash
from app.toc_hwpx import analyze, compose_toc, verify_toc, native_text, PREFIX
from test_document_blocks import source_document, uploaded, changed_zip, counts
from test_collaboration import secured, as_user, share
from test_storage import save


@pytest.fixture(scope="module")
def source(): return source_document()


def body(raw, base="base"):
    candidates = analyze(raw,digest(raw))["candidates"]
    chosen = [c for c in candidates if c["paragraph"] in (2,4)]
    assert len(chosen) == 2
    return dict(request_id=str(uuid.uuid4()), base_version=base, source_base64=base64.b64encode(raw).decode(), source_sha256=digest(raw),
                toc_id=str(uuid.uuid4()), target=dict(section=0, paragraph=1, expected_text_sha256=candidates[0]["text_sha256"]),
                headings=[dict(heading_id=str(uuid.uuid4()), paragraph=h["paragraph"], level=i+1, expected_text_sha256=h["text_sha256"]) for i,h in enumerate(chosen)])


def preview(raw, value): return compose_toc(raw,TocPreview.model_validate(value))


def verified_body(value, out, plan, pages=None):
    pages = pages or plan["pages"]
    evidence = dict(export_sha256=digest(out),page_count=max(pages.values()),revision=3,epoch=1,
                    headings=[{k:h[k] for k in ("id","section","paragraph","length","text_sha256")} | {"page":pages[h["id"]]} for h in plan["headings"]])
    return value | dict(engine_output_base64=base64.b64encode(out).decode(),layout_evidence=evidence)


def edit(data, which, fn):
    with zipfile.ZipFile(io.BytesIO(data)) as z: root=E.fromstring(z.read(which));fn(root)
    return changed_zip(data,{which:serialize_block_xml(root)})


def call(c,doc,token,suffix,value):
    return c.post(f"/api/documents/{doc['id']}/toc-"+suffix,json=value,headers={"X-Edit-Session":token})


def refreshed(out, value):
    toc=analyze(out,digest(out))["toc"]
    return value | dict(source_base64=base64.b64encode(out).decode(),source_sha256=digest(out),target=toc["anchor"],
                        headings=[dict(heading_id=h["id"],paragraph=h["paragraph"],level=h["level"],expected_text_sha256=h["text_sha256"]) for h in toc["headings"]],
                        pages=toc["pages"])


def test_compose_pure_preserves_old_runs_styles_tables_resources_and_native_toc(source):
    value=body(source);out,plan=preview(source,value)
    assert preview(source,value)==(out,plan)
    assert plan["plan"]["toc_paragraph"]==2
    info=analyze(out,digest(out)); assert info["toc"]["status"]=="current"
    with zipfile.ZipFile(io.BytesIO(source)) as old,zipfile.ZipFile(io.BytesIO(out)) as new:
        for name in old.namelist():
            if name not in ("Contents/header.xml","Contents/section0.xml"): assert old.read(name)==new.read(name)
        oh=E.fromstring(old.read("Contents/header.xml"));nh=E.fromstring(new.read("Contents/header.xml"))
        originals={n.get("id"):E.tostring(n) for n in oh.iter(f"{{{HH}}}style")}
        assert originals.items() <= {n.get("id"):E.tostring(n) for n in nh.iter(f"{{{HH}}}style")}.items()
        os=E.fromstring(old.read("Contents/section0.xml"));ns=E.fromstring(new.read("Contents/section0.xml"));remaining=list(ns);remaining.pop(2)
        for i,(a,b) in enumerate(zip(os,remaining)):
            b=copy.deepcopy(b)
            if i in (2,4): b.set("styleIDRef",a.get("styleIDRef"))
            assert E.tostring(a)==E.tostring(b)
        table=ns[2].find(f".//{{{HP}}}tbl");assert table.get("rowCnt")=="3" and table.get("colCnt")=="1"
        assert list(table.iter(f"{{{HP}}}tab")) and all(t.attrib==dict(width="0",leader="0",type="1") for t in table.iter(f"{{{HP}}}tab"))
        tabs=[t for t in nh.iter(f"{{{HH}}}tabItem") if t.get("type")=="RIGHT" and t.get("leader")=="DASH"]
        assert tabs
        new_fills=[f for f in nh.iter(f"{{{HH}}}borderFill") if int(f.get("id")) > max(int(f.get("id")) for f in oh.iter(f"{{{HH}}}borderFill"))]
        assert all(f.find(f"{{{HH}}}diagonal") is None for f in new_fills if any(x.get("color")=="#A0A0A0" for x in f))
    result=verify_toc(source,out,out,TocVerify.model_validate(verified_body(value,out,plan)),plan)
    assert result["verified"] and result["converged"] and result["can_adopt"]


def test_api_preview_verify_retry_has_no_publication(secured,source):
    c,app,_,folder=secured;doc,token,_=uploaded(c,source);value=body(source,doc["current_version"])
    before={p.name:digest(p.read_bytes()) for p in folder.iterdir()}
    analysis=call(c,doc,token,"analyze",{k:v for k,v in value.items() if k in ("request_id","base_version","source_base64","source_sha256")})
    assert analysis.status_code==200,analysis.text
    response=call(c,doc,token,"preview",value);assert response.status_code==200,response.text
    p=response.json();out=base64.b64decode(p["output_base64"])
    assert call(c,doc,token,"preview",value).json()==p
    vb=verified_body(value,out,p)
    r=call(c,doc,token,"verify",vb);assert r.status_code==200,r.text
    assert r.json()["input_sha256"]==p["input_sha256"] and r.json()["can_adopt"]
    assert call(c,doc,token,"check",{"base_version":doc["current_version"]}).json()["ok"]
    assert counts(app)==(1,1) and before=={p.name:digest(p.read_bytes()) for p in folder.iterdir()}


def test_three_pass_page_convergence_and_exhaustion(source):
    value=body(source);out,p=preview(source,value);ids=[h["id"] for h in p["headings"]]
    measured={ids[0]:2,ids[1]:3}
    v=TocVerify.model_validate(verified_body(value,out,p,measured));r=verify_toc(source,out,out,v,p)
    assert r["verified"] and not r["can_adopt"] and r["measured_pages"]==measured
    value2=value|dict(pass_index=2,pages=measured);out2,p2=preview(source,value2)
    assert verify_toc(source,out2,out2,TocVerify.model_validate(verified_body(value2,out2,p2)),p2)["can_adopt"]
    value3=value|dict(pass_index=3,pages=measured);out3,p3=preview(source,value3)
    r=verify_toc(source,out3,out3,TocVerify.model_validate(verified_body(value3,out3,p3,{ids[0]:3,ids[1]:4})),p3)
    assert r["exhausted"] and not r["can_adopt"]
    with pytest.raises(Exception): TocPreview.model_validate(value|dict(pass_index=4))


@pytest.mark.parametrize("damage",["hash","missing","duplicate","unknown","paragraph","text","length","range","order"])
def test_page_evidence_exact_binding_rejects_bad_input(source,damage):
    value=body(source);out,p=preview(source,value);v=verified_body(value,out,p);ev=v["layout_evidence"]
    if damage=="hash":ev["export_sha256"]="0"*64
    elif damage=="missing":ev["headings"].pop()
    elif damage=="duplicate":ev["headings"][1]=dict(ev["headings"][0])
    elif damage=="unknown":ev["headings"][0]["id"]=str(uuid.uuid4())
    elif damage=="paragraph":ev["headings"][0]["paragraph"]+=1
    elif damage=="text":ev["headings"][0]["text_sha256"]="0"*64
    elif damage=="length":ev["headings"][0]["length"]+=1
    elif damage=="range":ev["headings"][0]["page"]=2
    else:ev["page_count"]=3;ev["headings"][0]["page"]=3;ev["headings"][1]["page"]=2
    with pytest.raises(HTTPException) as e:verify_toc(source,out,out,TocVerify.model_validate(v),p)
    assert e.value.status_code==422


@pytest.mark.parametrize("damage",["text","run-format","toc-text","image","header","package","old-table"])
def test_verify_preserves_whole_document_semantics(source,damage):
    value=body(source);out,p=preview(source,value)
    if damage=="image":changed=changed_zip(out,{"BinData/user-image.png":b"changed"})
    elif damage=="header":changed=edit(out,"Contents/header.xml",lambda h:h.set("version","changed"))
    elif damage=="package":changed=edit(out,"Contents/content.hpf",lambda h:h.set("version","changed"))
    else:
        def fn(s):
            if damage=="text":s[1].find(f"{{{HP}}}run/{{{HP}}}t").text="changed body"
            elif damage=="run-format":s[1].find(f"{{{HP}}}run").set("charPrIDRef",s[0].find(f"{{{HP}}}run").get("charPrIDRef"))
            elif damage=="toc-text":s[2].find(f".//{{{HP}}}t").text="changed TOC"
            else:list(s.iter(f"{{{HP}}}tbl"))[1].find(f".//{{{HP}}}cellSz").set("width","1")
        changed=edit(out,"Contents/section0.xml",fn)
    with pytest.raises(HTTPException):verify_toc(source,out,changed,TocVerify.model_validate(verified_body(value,changed,p)),p)


def test_body_edit_stale_explicit_refresh_and_manual_toc_damage_refused(source):
    value=body(source);out,p=preview(source,value)
    changed=edit(out,"Contents/section0.xml",lambda s:setattr(s[-1].find(f"{{{HP}}}run/{{{HP}}}t"),"text","manual body edit"))
    assert analyze(changed,digest(changed))["toc"]["status"]=="stale"
    refreshed_out,_=preview(changed,refreshed(changed,value))
    assert analyze(refreshed_out,digest(refreshed_out))["toc"]["status"]=="current"
    damaged=edit(out,"Contents/section0.xml",lambda s:setattr(s[2].find(f".//{{{HP}}}t"),"text","manual TOC"))
    assert analyze(damaged,digest(damaged))["toc"]["status"]=="damaged"
    dv = value | dict(source_base64=base64.b64encode(damaged).decode(),source_sha256=digest(damaged))
    with pytest.raises(HTTPException) as e:preview(damaged,dv)
    assert e.value.status_code==409


@pytest.mark.parametrize("damage",["deleted","unsupported","duplicate"])
def test_missing_unsupported_duplicate_heading_never_silently_drops(source,damage):
    value=body(source);out,p=preview(source,value)
    def fn(s):
        if damage=="deleted":s.remove(s[p["headings"][1]["paragraph"]])
        elif damage=="duplicate":s.append(copy.deepcopy(s[p["headings"][0]["paragraph"]]))
        else:
            run=s[p["headings"][1]["paragraph"]].find(f"{{{HP}}}run");E.SubElement(run,f"{{{HP}}}tbl")
    changed=edit(out,"Contents/section0.xml",fn)
    if damage=="duplicate":
        with pytest.raises(HTTPException):analyze(changed,digest(changed))
    else:
        info=analyze(changed,digest(changed))["toc"]
        assert info["status"]=="stale" and len(info["heading_ids"])==2
        # UI might submit only supported leftovers; server still refuses.
        candidate=value|dict(source_base64=base64.b64encode(changed).decode(),source_sha256=digest(changed),target=info["anchor"],pages={})
        candidate["headings"]=[dict(heading_id=h["id"],paragraph=h["paragraph"],level=h["level"],expected_text_sha256=h.get("text_sha256","0"*64)) for h in info["headings"]]
        with pytest.raises(HTTPException) as e:preview(changed,candidate)
        assert e.value.status_code==409


def test_refresh_only_consumes_one_new_style_preserves_old_definitions(source):
    value=body(source);current,p=preview(source,value)
    def style_count(raw):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:return len(list(E.fromstring(z.read("Contents/header.xml")).iter(f"{{{HH}}}style")))
    start=style_count(current)
    for i in range(5):
        old=current;current,p=preview(current,refreshed(current,value));assert style_count(current)==start+i+1
        assert analyze(current,digest(current))["toc"]["status"]=="current"
        # Existing unused metadata definitions remain immutable too.
        from app.block_hwpx import verify_engine_output
        verify_engine_output(old,current,current)


def test_style_u8_capacity_and_source_target_fences(source):
    value=body(source)
    for broken in (value|dict(source_sha256="0"*64),value|dict(target=value["target"]|dict(expected_text_sha256="0"*64)),value|dict(headings=[value["headings"][0]|dict(expected_text_sha256="0"*64),value["headings"][1]])):
        with pytest.raises(HTTPException):preview(source,broken)
    def fill(h):
        ss=h.find(f".//{{{HH}}}styles");prototype=ss[0]
        for i in range(max(int(s.get("id")) for s in ss)+1,256):
            s=copy.deepcopy(prototype);s.set("id",str(i));ss.append(s)
        ss.set("itemCnt",str(len(ss)))
    full=edit(source,"Contents/header.xml",fill)
    with pytest.raises(HTTPException) as e:preview(full,body(full))
    assert e.value.status_code==422


def test_softbreak_unicode_scalars_and_pinned_style_suggestions(source):
    def fn(s):
        t=s[2].find(f"{{{HP}}}run/{{{HP}}}t");t.text="title😀";E.SubElement(t,f"{{{HP}}}lineBreak").tail="part"
    changed=edit(source,"Contents/section0.xml",fn);info=analyze(changed,digest(changed))
    candidate=next(c for c in info["candidates"] if c["paragraph"]==2)
    assert candidate["native_text"]=="title😀\npart" and candidate["length"]==11 and candidate["text"]=="title😀part"
    assert candidate["text_sha256"]==digest(candidate["native_text"].encode()) and candidate["suggested_level"]==1
    out,p=preview(changed,body(changed));assert p["headings"][0]["length"]==11


@pytest.mark.parametrize("case",["reader","other-editor-lease","revoked","stale-base","expired-lease"])
def test_all_api_paths_current_acl_lease_base(secured,source,case):
    c,app,_,_=secured;doc,token,_=uploaded(c,source);value=body(source,doc["current_version"]);out,p=preview(source,value)
    if case in ("reader","other-editor-lease","revoked"):
        share(c,doc,"reader" if case=="reader" else "editor")
        if case=="revoked":c.delete(f"/api/documents/{doc['id']}/access/bob")
        as_user(c,"bob")
    elif case=="stale-base":assert save(c,doc,token,source).status_code==200
    else:
        with transaction(app.state.engine) as db:db.get(Document,doc["id"]).lease_until=0
    expected={"reader":403,"other-editor-lease":409,"revoked":404,"stale-base":409,"expired-lease":409}[case]
    sourcebody={k:v for k,v in value.items() if k in ("request_id","base_version","source_base64","source_sha256")}
    for suffix,v in (("analyze",sourcebody),("preview",value),("verify",verified_body(value,out,p)),("check",dict(base_version=doc["current_version"]))):
        r=call(c,doc,token,suffix,v);assert r.status_code==expected,(suffix,r.text)


def test_access_rechecked_after_computation(secured,source,monkeypatch):
    c,app,_,_=secured;doc,token,_=uploaded(c,source);value=body(source,doc["current_version"])
    import app.toc_hwpx as mod
    original=mod.compose_toc
    def revoke(*args):
        result=original(*args)
        with transaction(app.state.engine) as db:db.get(Document,doc["id"]).lease_until=0
        return result
    monkeypatch.setattr(mod,"compose_toc",revoke)
    # A fresh app install captures the monkeypatched compose callable.
    from app.toc_contract import install_toc
    app.router.routes=[r for r in app.router.routes if not getattr(r,"path","").endswith("/toc-preview")]
    def check(doc,token):
        if doc.lease_until<=0:raise HTTPException(409,"expired")
    install_toc(app,check)
    assert call(c,doc,token,"preview",value).status_code==409
    assert counts(app)==(1,1)


@pytest.mark.parametrize("damage",["orphan","nested-heading","nested-box"])
def test_orphan_or_nested_managed_markers_fail_closed(source,damage):
    value=body(source);out,p=preview(source,value)
    def fn(s):
        if damage=="orphan":s[2].set("styleIDRef",s[0].get("styleIDRef"))
        else:
            cell=s[2].find(f".//{{{HP}}}subList")
            cell.append(copy.deepcopy(s[p["headings"][0]["paragraph"]] if damage=="nested-heading" else s[2]))
    damaged=edit(out,"Contents/section0.xml",fn)
    with pytest.raises(HTTPException) as e:analyze(damaged,digest(damaged))
    assert e.value.status_code==422


@pytest.mark.parametrize("case",["duplicate-id","duplicate-paragraph","wrong-order","first-minor","pages-missing","bool-page","too-many","empty"])
def test_bounded_manual_heading_contract(source,case):
    value=body(source)
    if case=="duplicate-id":value["headings"][1]["heading_id"]=value["headings"][0]["heading_id"]
    elif case=="duplicate-paragraph":value["headings"][1]["paragraph"]=value["headings"][0]["paragraph"]
    elif case=="wrong-order":value["headings"].reverse()
    elif case=="first-minor":value["headings"][0]["level"]=2
    elif case=="pages-missing":value["pass_index"]=2
    elif case=="bool-page":value["pages"]={h["heading_id"]:True for h in value["headings"]}
    elif case=="too-many":value["headings"]*=16
    else:value["headings"]=[]
    with pytest.raises(Exception):TocPreview.model_validate(value)


def test_standard_signature_is_prepared_once_per_analysis(source,monkeypatch):
    import app.toc_hwpx as mod
    original=mod.report_styles;calls=[]
    def counted():calls.append(1);return original()
    monkeypatch.setattr(mod,"report_styles",counted)
    assert len(analyze(source,digest(source))["candidates"])>1
    assert len(calls)==1


def test_thirty_headings_refresh_adds_only_one_style(source):
    with zipfile.ZipFile(io.BytesIO(source)) as z:original_count=len(E.fromstring(z.read("Contents/section0.xml")))
    def fn(s):
        template=s[2]
        for i in range(30):
            p=copy.deepcopy(template);p.set("id",str(10000+i));p.find(f"{{{HP}}}run/{{{HP}}}t").text=f"Heading {i+1}";s.append(p)
    expanded=edit(source,"Contents/section0.xml",fn);value=body(expanded)
    candidates=analyze(expanded,digest(expanded))["candidates"]
    value["headings"]=[dict(heading_id=str(uuid.uuid4()),paragraph=c["paragraph"],level=1 if i%3==0 else 2,expected_text_sha256=c["text_sha256"]) for i,c in enumerate(c for c in candidates if c["paragraph"]>=original_count)]
    out,p=preview(expanded,value);assert len(p["headings"])==30
    next_out,_=preview(out,refreshed(out,value))
    def count(raw):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:return len(list(E.fromstring(z.read("Contents/header.xml")).iter(f"{{{HH}}}style")))
    assert count(next_out)==count(out)+1
