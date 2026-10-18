"""Owner-fenced native TOC for new report previews; legacy previews stay frozen."""
import base64
import copy
import json
import time
import uuid
from xml.etree import ElementTree as E
from fastapi import HTTPException, Request
from pydantic import Field, StrictInt
from .block_contract import BlockInput, MAX_BASE64, canonical, decoded
from .block_hwpx import HH, HP, HC, parsed, local, Semantic, verify_engine_output
from .document_blocks import bounded_input
from .hwpx import digest
from .toc_contract import TocPreview, TocVerify, TocLayoutEvidence
from .toc_hwpx import analyze, compose_toc, packed, native_text, descriptor, verify_toc
from .db import transaction
from .document_standard import metadata

POLICY = "report-native-toc-v1"


class TocOwner(BlockInput):
    request_id: uuid.UUID
    preview_id: uuid.UUID
    layout_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PrepareToc(TocOwner):
    source_base64: str = Field(min_length=1, max_length=MAX_BASE64)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PreviewToc(TocOwner):
    pass_index: int = Field(ge=1, le=3)
    pages: dict[uuid.UUID, StrictInt] = Field(default_factory=dict, max_length=30)


class VerifyToc(TocOwner):
    pass_index: int = Field(ge=1, le=3)
    engine_output_base64: str = Field(min_length=1, max_length=MAX_BASE64)
    layout_evidence: TocLayoutEvidence


def prepare_hash(value):
    return digest(canonical(value.model_dump(mode="json", exclude={"source_base64"})).encode())


def normalize_generated_layout(data):
    """Explicit known native defaults, restricted to this new generated artifact.

    These exact conversions were demonstrated by block SDK R5/R6 evidence and
    native header serializer: disabled char effects, transparent COLORREF and
    absent NONE diagonal. No existing/legacy/user document uses this function.
    """
    header, section, _, _ = parsed(data)
    defaults = {"underline":dict(type="NONE",shape="SOLID",color="#000000"),
                "strikeout":dict(shape="NONE",color="#000000"), "outline":dict(type="NONE"),
                "shadow":dict(type="NONE",color="#000000",offsetX="0",offsetY="0")}
    for char in header.iter(f"{{{HH}}}charPr"):
        for tag, attrs in defaults.items():
            if char.find(f"{{{HH}}}{tag}") is None: E.SubElement(char,f"{{{HH}}}{tag}",attrs)
        bold=char.find(f"{{{HH}}}bold")
        if bold is not None:
            char.remove(bold)
            char.insert(next((i for i,c in enumerate(char) if local(c)=="underline"),len(char)),bold)
    for fill in header.iter(f"{{{HH}}}borderFill"):
        for child in list(fill):
            if local(child)=="diagonal" and child.get("type")=="NONE":fill.remove(child)
        for brush in fill.iter(f"{{{HC}}}winBrush"):
            if brush.get("faceColor","").casefold()=="none":brush.set("faceColor","#FFFFFFFF")
    return packed(data,header,section)


def initial_config(preview, source, request_id):
    _,section,_,_=parsed(source)
    selected=[]; index=1; position=0
    while position<len(preview["plan"]):
        item=preview["plan"][position]
        if item["kind"]=="heading":
            if index>=len(section):raise HTTPException(422,"보고서 제목 위치를 확인할 수 없습니다.")
            d=descriptor(section[index],index)
            if d["text"]!=item["text"]:raise HTTPException(422,"고정한 보고서 제목과 문서 내용이 다릅니다.")
            selected.append(dict(heading_id=str(uuid.uuid5(uuid.UUID(preview["id"]),"heading:"+item["command_id"])),
                                 paragraph=index,level=1,expected_text_sha256=d["text_sha256"]))
        if item.get("visual_id"):
            ident=item["visual_id"]
            while position<len(preview["plan"]) and preview["plan"][position].get("visual_id")==ident:position+=1
        else:position+=1
        index+=1
    if not 1<=len(selected)<=30:raise HTTPException(422,"자동 목차는 제목 1~30개를 지원합니다.")
    return dict(toc_id=str(uuid.uuid5(uuid.UUID(preview["id"]),"automatic-report-toc")),
                target=dict(section=0,paragraph=1,expected_text_sha256=digest(native_text(section[1],anchor=True).encode())),headings=selected)


def refresh_config(preview, source):
    info=analyze(source,digest(source));toc=info["toc"];frozen=preview["toc"]
    if not toc or toc["status"]=="damaged" or toc["id"]!=frozen["id"]:
        raise HTTPException(409,"기존 보고서 목차가 없거나 수동으로 변경되었습니다. 덮어쓰지 않았습니다.")
    ids=[h["id"] for h in frozen["headings"]]
    if toc.get("heading_ids")!=ids or toc.get("missing_ids") or toc.get("unsupported_ids") or [h["id"] for h in toc["headings"]]!=ids:
        raise HTTPException(409,"기존 제목이 삭제되거나 지원하지 않는 구조로 변경되었습니다. 목차 항목을 자동으로 줄이지 않았습니다.")
    return dict(toc_id=toc["id"],target=toc["anchor"],headings=[dict(heading_id=h["id"],paragraph=h["paragraph"],
                level=h["level"],expected_text_sha256=h["text_sha256"]) for h in toc["headings"]])


def toc_value(pending, pass_index, pages):
    start=pending["prepare_input"]
    return TocPreview.model_validate(dict(request_id=start["request_id"],base_version=start["preview_id"],
        source_base64=start["source_base64"],source_sha256=start["source_sha256"],
        **pending["config"],pass_index=pass_index,pages=pages))


def standard_matches(expected):
    current=metadata()
    if any(current[key]!=expected[key] for key in ("id","reference_sha256","profile_sha256")):
        raise HTTPException(409,"보고서 요청에서 고정한 기본 서식이 변경되었습니다.")


def stage_response(pending, index, pages):
    standard_matches(pending["standard"])
    value=toc_value(pending,index,pages);source=decoded(pending["prepare_input"]["source_base64"])
    output,plan=compose_toc(source,value)
    standard_matches(pending["standard"])
    start=pending["prepare_input"]
    return dict(request_id=start["request_id"],preview_id=start["preview_id"],base_layout_sha256=start["layout_sha256"],
                source_sha256=start["source_sha256"],start_input_sha256=pending["input_sha256"],
                target=pending["config"]["target"],selected_headings=pending["config"]["headings"],
                output_base64=base64.b64encode(output).decode(),output_sha256=digest(output),**plan)


def install_report_toc(app,lock_report,public_preview):
    engine=app.state.engine

    def current(db,task_id,user,value):
        row=lock_report(db,task_id,user);execution=json.loads(row.execution);preview=execution.get("preview")
        if not preview or preview["id"]!=str(value.preview_id) or preview.get("toc_policy")!=POLICY:
            raise HTTPException(409,"자동 목차를 준비한 보고서 기준이 다릅니다.")
        if row.status!="ready" or row.draft_sha256!=preview["draft_sha256"] or row.approval:
            raise HTTPException(409,"보고서 초안 상태가 변경되었거나 이미 승인되었습니다.")
        return row,execution,preview,row.execution

    def persist(db,task_id,user,value,execution,fence):
        fresh,_,_,_=current(db,task_id,user,value)
        if fresh.execution!=fence:
            raise HTTPException(409,"보고서 목차 준비 상태가 변경되었습니다. 늦은 완료를 반영하지 않았습니다.")
        fresh.execution=canonical(execution)

    def pending_for(preview,value):
        for key in ("_toc_pending","_toc_completed"):
            pending=preview.get(key)
            if pending and pending["prepare_input"]["request_id"]==str(value.request_id):
                if pending["prepare_input"]["layout_sha256"]!=value.layout_sha256:raise HTTPException(409,"같은 목차 요청의 기준을 변경할 수 없습니다.")
                return pending,key
        raise HTTPException(409,"현재 목차 요청과 다릅니다. 이전 요청의 늦은 완료를 반영하지 않았습니다.")

    def base_matches(preview,value):
        if preview.get("layout_sha256")!=value.layout_sha256:raise HTTPException(409,"보고서 배치 기준이 변경되었습니다. 현재 미리보기를 다시 확인하세요.")

    @app.post("/api/report-tasks/{task_id}/preview/toc-prepare")
    async def prepare(task_id:str,request:Request):
        value=await bounded_input(request,PrepareToc);source=decoded(value.source_base64)
        if digest(source)!=value.source_sha256:raise HTTPException(422,"미저장 보고서의 해시가 일치하지 않습니다.")
        with transaction(engine) as db:
            row,execution,preview,fence=current(db,task_id,request.state.user,value)
            for key in ("_toc_pending","_toc_completed"):
                old=preview.get(key)
                if old and old["prepare_input"]["request_id"]==str(value.request_id):
                    if old["input_sha256"]!=prepare_hash(value):raise HTTPException(409,"같은 목차 요청 ID에 다른 입력을 사용할 수 없습니다.")
                    return old["passes"]["1"]["response"]
            if preview.get("_toc_pending"):raise HTTPException(409,"이미 준비 중인 목차 요청을 재확인하거나 종료하세요.")
            cancelled=preview.get("_toc_cancelled",{})
            if str(value.request_id) in cancelled:raise HTTPException(409,"중단한 목차 요청에는 새 ID를 사용하세요.")
            if len(cancelled)>=128:raise HTTPException(409,"이 초안의 목차 준비 중단 한도에 도달했습니다. 현재 출력은 보존되어 있습니다.")
            base_matches(preview,value)
            if not preview.get("toc_verified"):
                original=decoded(preview["layout_base64"])
                verify_engine_output(original,original,source)
                if {n for n in Semantic(original).files if not n.startswith("Preview/")}!={n for n in Semantic(source).files if not n.startswith("Preview/")}:
                    raise HTTPException(422,"고정한 보고서 이외의 자원이 추가되거나 누락되었습니다.")
                config=initial_config(preview,source,value.request_id)
            else:config=refresh_config(preview,source)
            pending=dict(prepare_input=value.model_dump(mode="json"),input_sha256=prepare_hash(value),config=config,
                         passes={},verifications={},started_at=time.time(),standard=preview["document_standard"])
            response=stage_response(pending,1,{})
            pending["passes"]["1"]=dict(pages={},response=response)
            preview["_toc_pending"]=pending;persist(db,task_id,request.state.user,value,execution,fence)
            return response

    @app.post("/api/report-tasks/{task_id}/preview/toc-preview")
    async def preview_pass(task_id:str,request:Request):
        value=await bounded_input(request,PreviewToc)
        with transaction(engine) as db:
            row,execution,preview,fence=current(db,task_id,request.state.user,value);pending,key=pending_for(preview,value)
            index=str(value.pass_index);pages={str(k):v for k,v in value.pages.items()};old=pending["passes"].get(index)
            if old:
                if old["pages"]!=pages:raise HTTPException(409,"같은 배치 회차의 쪽수 입력을 변경할 수 없습니다.")
                return old["response"]
            if key!="_toc_pending":raise HTTPException(409,"이미 완료한 목차에 다른 회차를 추가할 수 없습니다.")
            base_matches(preview,value)
            previous=pending["verifications"].get(str(value.pass_index-1))
            if not previous or previous["result"]["converged"] or previous["result"]["measured_pages"]!=pages:
                raise HTTPException(409,"직전 실제 페이지 검증 결과와 같은 쪽수로 다음 배치를 요청하세요.")
            response=stage_response(pending,value.pass_index,pages)
            pending["passes"][index]=dict(pages=pages,response=response);persist(db,task_id,request.state.user,value,execution,fence)
            return response

    @app.post("/api/report-tasks/{task_id}/preview/toc-verify")
    async def verify(task_id:str,request:Request):
        value=await bounded_input(request,VerifyToc);actual=decoded(value.engine_output_base64)
        binding=digest(canonical(dict(request_id=str(value.request_id),preview_id=str(value.preview_id),layout_sha256=value.layout_sha256,
            pass_index=value.pass_index,output_sha256=digest(actual),layout_evidence=value.layout_evidence.model_dump(mode="json"))).encode())
        with transaction(engine) as db:
            row,execution,preview,fence=current(db,task_id,request.state.user,value);pending,key=pending_for(preview,value)
            index=str(value.pass_index);old=pending["verifications"].get(index)
            if old:
                if old["binding"]!=binding:raise HTTPException(409,"검증한 목차 출력·쪽수 증거와 같은 입력으로 재확인하세요.")
                return old["result"]
            if key!="_toc_pending":raise HTTPException(409,"완료한 목차 요청의 새 출력을 반영할 수 없습니다.")
            base_matches(preview,value)
            current_pass=pending["passes"].get(index)
            if not current_pass:raise HTTPException(409,"먼저 해당 회차의 목차 배치를 준비하세요.")
            standard_matches(pending["standard"])
            cfg=toc_value(pending,value.pass_index,current_pass["pages"])
            check=TocVerify.model_validate(cfg.model_dump(mode="json")|dict(engine_output_base64=value.engine_output_base64,layout_evidence=value.layout_evidence.model_dump(mode="json")))
            source=decoded(pending["prepare_input"]["source_base64"]);expected=decoded(current_pass["response"]["output_base64"])
            result=verify_toc(source,expected,actual,check,current_pass["response"])
            standard_matches(pending["standard"])
            result.update(request_id=str(value.request_id),preview_id=str(value.preview_id),base_layout_sha256=value.layout_sha256,
                          source_sha256=pending["prepare_input"]["source_sha256"],output_sha256=digest(actual),verification_sha256=binding)
            if result["converged"]:
                preview.update(layout_base64=base64.b64encode(actual).decode(),layout_sha256=digest(actual),toc_verified=True,
                    toc=dict(id=result["toc_id"],status="current",body_sha256=result["body_sha256"],headings=result["headings"],
                        pages=result["measured_pages"],page_count=value.layout_evidence.page_count,layout_sha256=digest(actual),verification_sha256=binding))
                preview.setdefault("timings",{})["toc_ms"]=round((time.time()-pending["started_at"])*1000)
                preview.pop("_toc_pending",None);preview["_toc_completed"]=pending
                result["preview"]=public_preview(preview)
            pending["verifications"][index]=dict(binding=binding,result=result)
            persist(db,task_id,request.state.user,value,execution,fence)
            return result

    @app.post("/api/report-tasks/{task_id}/preview/toc-cancel")
    async def cancel(task_id:str,request:Request):
        value=await bounded_input(request,TocOwner)
        with transaction(engine) as db:
            row,execution,preview,fence=current(db,task_id,request.state.user,value)
            cancelled=preview.get("_toc_cancelled",{})
            if str(value.request_id) in cancelled:
                if cancelled[str(value.request_id)]!=value.layout_sha256:raise HTTPException(409,"중단한 목차 요청의 기준이 다릅니다.")
                return dict(cancelled=True,completed=False,preview=public_preview(preview))
            pending,key=pending_for(preview,value)
            if key=="_toc_completed":return dict(cancelled=False,completed=True,preview=public_preview(preview))
            base_matches(preview,value);preview.pop("_toc_pending");preview.setdefault("_toc_cancelled",{})[str(value.request_id)]=value.layout_sha256
            persist(db,task_id,request.state.user,value,execution,fence)
            return dict(cancelled=True,completed=False,preview=public_preview(preview))
