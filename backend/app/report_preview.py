"""An isolated editable preview, separate from user approval and document publication."""
import base64
import json
import time
import uuid
from fastapi import HTTPException, Request
from pydantic import Field
from .auth import require_access
from .db import Access, Document, transaction
from .hwpx import digest, validate
from .document_standard import metadata
from .report_contract import ReportValue, canonical, report_blocks, report_hash
from .report_hwpx import scaffold
from .report_layout import layout_report
from .report_output import verify_output
from .report_rich import POLICY, layout_rich_report
from .report_toc import POLICY as TOC_POLICY, heading_level, install_report_toc, normalize_generated_layout


class PreparePreview(ReportValue):
    id: uuid.UUID
    draft_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    seed_base64: str = Field(min_length=1, max_length=2000000)


class RenderPreview(ReportValue):
    preview_id: uuid.UUID
    output_base64: str = Field(min_length=1, max_length=3000000)
    receipts: list[dict] = Field(min_length=1, max_length=180)
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    client_apply_ms: int | None = Field(default=None, ge=0, le=86400000)


class SavePreview(ReportValue):
    id: uuid.UUID
    preview_id: uuid.UUID
    layout_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_base64: str = Field(min_length=1, max_length=3000000)


def public_preview(preview):
    result = {k: v for k, v in preview.items() if k not in ("render_receipts", "render_binding", "save_binding") and not k.startswith("_toc_")}
    pending=preview.get("_toc_pending")
    if pending:
        index=max(int(k) for k in pending["passes"])
        last=pending["verifications"].get(str(index),{}).get("result")
        result["toc_state"]={"request_id":pending["prepare_input"]["request_id"],"base_layout_sha256":pending["prepare_input"]["layout_sha256"],
            "prepare_input":pending["prepare_input"],"next_pass":index+1 if last and not last["converged"] else index,
            "measured_pages":last["measured_pages"] if last else None}
    return result


def verify_standard_binding(preview):
    standard = metadata()
    selection = preview.get("template")
    if selection and (selection["standard_id"] != standard["id"] or selection["reference_sha256"] != standard["reference_sha256"] or selection["standard_profile_sha256"] != standard["profile_sha256"]):
        raise HTTPException(409, "보고서 요청에서 고정한 기본 서식이 변경되었습니다.")
    visual = preview.get("visual_standard")
    if visual and visual["base_standard"] != {k: standard[k] for k in ("id", "reference_sha256", "profile_sha256")}:
        raise HTTPException(409, "등록 표준의 원본 기준이 달라졌습니다. 기존 초안은 보존되어 있습니다.")


def install_preview(app, publish, doc_dict, lock_report, decode):
    engine = app.state.engine
    install_report_toc(app, lock_report, public_preview)

    @app.get("/api/report-tasks/{task_id}/preview")
    def get_preview(task_id: str, request: Request):
        with transaction(engine) as db:
            row = lock_report(db, task_id, request.state.user)
            preview = json.loads(row.execution).get("preview")
            if not preview: raise HTTPException(404, "아직 준비한 한글 초안이 없습니다.")
            return public_preview(preview)

    @app.post("/api/report-tasks/{task_id}/preview")
    def prepare_preview(task_id: str, value: PreparePreview, request: Request):
        seed = decode(value.seed_base64)
        with transaction(engine) as db:
            row = lock_report(db, task_id, request.state.user)
            if row.status != "ready" or row.draft_sha256 != value.draft_sha256:
                raise HTTPException(409, "초안이 바뀌었습니다. 현재 초안을 다시 확인하세요.")
            if row.approval: raise HTTPException(409, "이미 승인한 보고서는 저장된 문서에서 편집하세요.")
            execution = json.loads(row.execution)
            preview = execution.get("preview")
            if preview:
                if preview["id"] != str(value.id) or preview["seed_sha256"] != digest(seed):
                    raise HTTPException(409, "기존 한글 초안을 다시 열어 주세요.")
            else:
                selection=json.loads(row.input).get("template")
                visual=json.loads(row.input).get("visual_standard")
                standard=metadata()
                if selection and (selection["standard_id"]!=standard["id"] or selection["reference_sha256"]!=standard["reference_sha256"] or selection["standard_profile_sha256"]!=standard["profile_sha256"]):
                    raise HTTPException(409,"보고서 요청에서 고정한 기본 서식이 변경되었습니다.")
                if visual and visual["base_standard"] != {k:standard[k] for k in ("id", "reference_sha256", "profile_sha256")}:
                    raise HTTPException(409,"등록 표준의 원본 기준이 달라졌습니다. 기존 초안은 보존되어 있습니다.")
                blocks = report_blocks(json.loads(row.draft), concise=True, rich=True,
                                       visual_settings=visual["settings"] if visual else None,
                                       cover=json.loads(row.input).get("cover"))
                if len(blocks) > 180: raise HTTPException(422, "보고서가 너무 큽니다. 본문이나 표를 줄여 다시 요청하세요.")
                from .standard_policy import toc_mode
                needs_toc = (toc_mode(visual["settings"]) != "NONE") if visual and visual.get("revision") == 3 else bool(selection)
                if needs_toc:
                    count = sum(heading_level(item, blocks) is not None for item in blocks)
                    if not 1 <= count <= 30:
                        raise HTTPException(422, f"자동 목차는 큰제목·중제목·요약·출처를 합해 1~30개를 지원합니다. 현재 {count}개입니다. "
                                            "초안은 보존되어 있습니다. 중요한 설명은 본문에 유지하면서 제목 구성을 보완한 뒤 다시 문서로 만들어 주세요.")
                # Each exact command runs in its own staging page. Final pagination
                # is a later layout operation, without lowering the engine fence.
                for block in blocks: block["page_break"] = True
                data, plan = scaffold(seed, blocks, visual_settings=visual["settings"]) if visual else scaffold(seed, blocks)
                if selection or visual:
                    observed=metadata()
                    if selection and (selection["standard_id"]!=observed["id"] or selection["reference_sha256"]!=observed["reference_sha256"] or selection["standard_profile_sha256"]!=observed["profile_sha256"]):
                        raise HTTPException(409,"보고서 틀 준비 중 고정한 기본 서식이 변경되었습니다.")
                    if visual and visual["base_standard"] != {k:observed[k] for k in ("id", "reference_sha256", "profile_sha256")}:
                        raise HTTPException(409,"등록 표준의 원본 기준이 준비 중 변경되었습니다.")
                preview = {"id": str(value.id), "draft_sha256": row.draft_sha256, "seed_sha256": digest(seed),
                           "name": json.loads(row.draft)["title"].replace("/", " ").replace("\\", " ") + ".hwpx",
                           "document_standard": standard, "outline_policy": "short-items-v1", "presentation_policy": POLICY, "scaffold_base64": base64.b64encode(data).decode(),
                           "plan": [{**p, "command_id": str(uuid.uuid5(value.id, str(i)))} for i, p in enumerate(plan)]}
                if needs_toc:
                    preview.update(toc_policy=TOC_POLICY,toc_verified=False,template=selection)
                elif selection:
                    preview["template"] = selection
                if visual:
                    preview["visual_standard"] = visual
                execution["preview"] = preview; row.execution = canonical(execution)
            return public_preview(preview)

    @app.post("/api/report-tasks/{task_id}/preview/render")
    def render_preview(task_id: str, value: RenderPreview, request: Request):
        data = decode(value.output_base64); validate(data)
        with transaction(engine) as db:
            row = lock_report(db, task_id, request.state.user)
            execution = json.loads(row.execution); preview = execution.get("preview")
            if not preview or preview["id"] != str(value.preview_id):
                raise HTTPException(409, "한글 초안 준비 상태가 다릅니다.")
            if row.approval: raise HTTPException(409, "확정한 초안을 다시 배치할 수 없습니다.")
            strict_preview = preview.get("toc_policy")==TOC_POLICY or bool(preview.get("visual_standard"))
            if strict_preview and (row.status!="ready" or row.draft_sha256!=preview["draft_sha256"]):
                raise HTTPException(409,"보고서 초안 상태가 변경되었습니다.")
            binding = report_hash({"output": digest(data), "receipts": value.receipts, "document_sha256": value.document_sha256})
            if preview.get("render_binding"):
                if preview["render_binding"] != binding: raise HTTPException(409, "기존 미리보기와 같은 출력으로 재확인하세요.")
            else:
                frozen_execution=row.execution
                if strict_preview:
                    verify_standard_binding(preview)
                layout_started = time.monotonic()
                verify_output(data, value, preview, decode(preview["scaffold_base64"]))
                layout = layout_rich_report if preview.get("presentation_policy") == POLICY else layout_report
                visual = preview.get("visual_standard")
                laid_out = layout(data, preview["plan"], visual_settings=visual["settings"]) if visual else layout(data, preview["plan"])
                if preview.get("toc_policy")==TOC_POLICY:laid_out=normalize_generated_layout(laid_out)
                preview.update(layout_base64=base64.b64encode(laid_out).decode(), layout_sha256=digest(laid_out),
                               render_binding=binding, render_receipts=value.receipts,
                               timings={"layout_ms": round((time.monotonic() - layout_started) * 1000), "client_apply_ms": value.client_apply_ms})
                if strict_preview:
                    verify_standard_binding(preview)
                    fresh=lock_report(db,task_id,request.state.user)
                    if fresh.status!="ready" or fresh.approval or fresh.draft_sha256!=preview["draft_sha256"] or fresh.execution!=frozen_execution:
                        raise HTTPException(409,"보고서 배치 중 초안 상태가 변경되었습니다.")
                row.execution = canonical(execution)
            return public_preview(preview)

    @app.post("/api/report-tasks/{task_id}/preview/save")
    def save_preview(task_id: str, value: SavePreview, request: Request):
        data = decode(value.output_base64); validate(data)
        with transaction(engine) as db:
            row = lock_report(db, task_id, request.state.user)
            execution = json.loads(row.execution); preview = execution.get("preview")
            if not preview or preview["id"] != str(value.preview_id) or preview.get("layout_sha256") != value.layout_sha256:
                raise HTTPException(409, "확인한 한글 초안의 기준이 다릅니다.")
            if preview.get("toc_policy")==TOC_POLICY and not row.approval and (row.status!="ready" or not preview.get("toc_verified") or preview.get("_toc_pending")):
                raise HTTPException(409,"실제 페이지 목차 검증이 끝난 초안을 확인한 뒤 저장하세요.")
            if preview.get("visual_standard") and not row.approval and (row.status != "ready" or row.draft_sha256 != preview["draft_sha256"]):
                raise HTTPException(409, "보고서 초안 상태가 변경되었습니다. 확인한 출력은 보존되어 있습니다.")
            binding = report_hash({"id": str(value.id), "preview_id": str(value.preview_id), "output": digest(data), "layout": value.layout_sha256})
            if row.approval:
                approval = json.loads(row.approval)
                doc = require_access(db, approval["document_id"], request.state.user, "owner", lock=True, fresh=True)
                if approval.get("save_binding") != binding: raise HTTPException(409, "확정한 파일과 같은 내용으로 재확인하세요.")
                saved_version = approval["saved_version"]
            else:
                # This explicit action approves the visible document, including user
                # edits. A new document is published atomically; no existing buffer,
                # lease, original or historical version is modified.
                doc = Document(id=str(uuid.uuid4()), name=preview["name"], created_at=time.time(), lease_until=0)
                db.add(doc); db.flush(); db.add(Access(document_id=doc.id, user_id=request.state.user, role="owner"))
                version = publish(db, doc, data, None, None, str(value.id), "upload", author_id=request.state.user)
                saved_version = version.id
                approved = {"id": str(value.id), "mode": "formatted-preview", "document_id": doc.id,
                    "base_version": version.id, "saved_version": version.id, "save_binding": binding,
                    "preview_id": preview["id"], "layout_sha256": value.layout_sha256, "output_sha256": digest(data),
                    "output_differs_from_layout": digest(data) != value.layout_sha256, "document_standard": preview["document_standard"]}
                if preview.get("visual_standard"):
                    approved["visual_standard"] = preview["visual_standard"]
                row.approval = canonical(approved)
            # A response-loss retry acknowledges this frozen save even if another
            # editor has since published a later version. It never moves the head.
            result = {**doc_dict(doc), "current_version": saved_version, "role": "owner"}
        return result
