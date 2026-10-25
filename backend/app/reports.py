"""Owner-scoped report drafts and explicit approval into a new, immutable document."""
from .codex_runtime import allowed as codex_allowed, require_ready as require_codex_ready, reserve_usage as reserve_codex_usage
import base64
import json
import os
import time
import uuid
from threading import Thread
from fastapi import HTTPException, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import select, text
from .auth import require_access, require_lease_owner
from .codex_runner import capture_settings
from .db import Access, Document, ReportTask, Version, transaction
from .hwpx import digest, validate
from .report_contract import ReportValue, ReportDraft, canonical, report_hash, report_blocks, paragraph
from .report_hwpx import scaffold, paragraphs
from .report_runner import generate_report, CONTRACT
from .rewrite_capacity import ExecutionSlot
from .document_standard import load_standard, metadata as standard_metadata, format_signature, reference_view
from .report_templates import ReportTemplate, catalog as template_catalog, validate_selection
from .visual_standards import VisualSelection, validate_visual_selection, install_visual_standards, matches_base


class ReportCover(ReportValue):
    date: str = Field(default="", max_length=60)
    department: str = Field(default="", max_length=100)

    @field_validator("date", "department")
    @classmethod
    def plain_cover_line(cls, value):
        return paragraph(value) if value else value


class CreateReport(ReportValue):
    id: uuid.UUID
    prompt: str = Field(min_length=5, max_length=6000)
    research: bool = True
    template: ReportTemplate | None = None
    visual_standard: VisualSelection | None = None
    cover: ReportCover | None = None


class ApproveReport(ReportValue):
    id: uuid.UUID
    draft_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    seed_base64: str = Field(min_length=1, max_length=2000000)


class ReviseReport(ReportValue):
    id: uuid.UUID
    draft_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    feedback: str = Field(min_length=1, max_length=6000)

    @field_validator("feedback")
    @classmethod
    def useful_feedback(cls, value):
        if not value.strip() or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("보완할 내용을 입력해 주세요.")
        return value


class RevisionSourceError(ValueError):
    pass


class SaveReport(ReportValue):
    approval_id: uuid.UUID
    token: str = Field(min_length=1, max_length=100)
    output_base64: str = Field(min_length=1, max_length=3000000)
    receipts: list[dict] = Field(min_length=1, max_length=180)
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def decode(value):
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise HTTPException(422, "잘못된 파일 인코딩입니다.") from exc


def lock_report(db, task_id, user):
    row = db.scalar(select(ReportTask).where(ReportTask.id == task_id).with_for_update().execution_options(populate_existing=True))
    if not row or row.owner_id != user:
        raise HTTPException(404, "보고서 작업을 찾을 수 없습니다.")
    return row


def task_dict(row, *, detail=True):
    approval = json.loads(row.approval) if row.approval else None
    execution = json.loads(row.execution)
    preview = execution.pop("preview", None)
    if not detail: execution.pop("progress", None)
    return {"id": row.id, "input": json.loads(row.input), "status": row.status,
            "draft": json.loads(row.draft) if row.draft else None, "draft_sha256": row.draft_sha256,
            "execution": execution, "preview_id": preview["id"] if preview else None, "error": row.error, "created_at": row.created_at,
            "approval": {k: approval[k] for k in ("id", "document_id", "saved_version") if k in approval} if approval else None}


def run_report(engine, task_id, user, value, settings):
    from .report_rpc import ReportStopped
    def active():
        with transaction(engine) as db:
            row = lock_report(db, task_id, user)
            return row.status == "running" and time.time() - row.created_at <= settings.timeout + 30
    def progress(value):
        with transaction(engine) as db:
            row = lock_report(db, task_id, user)
            if row.status != "running" or time.time() - row.created_at > settings.timeout + 30:
                raise ReportStopped("Report reception stopped")
            execution = json.loads(row.execution)
            execution["progress"] = {**value, "revision": execution.get("progress", {}).get("revision", 0) + 1}
            row.execution = canonical(execution)
    try:
        draft, evidence = generate_report(value, settings, progress, active)
        draft = ReportDraft.model_validate(draft).model_dump()
        if revision := value.get("revision"):
            # A review can add research, but it must not silently rebind existing
            # citation IDs or discard their evidence. Prose still needs review.
            sources = {source["id"]: source for source in draft["sources"]}
            if any(sources.get(source["id"]) != source for source in revision["draft"]["sources"]):
                raise RevisionSourceError("보완 초안의 기존 출처가 변경되었습니다.")
        with transaction(engine) as db:
            row = lock_report(db, task_id, user)
            if row.status != "running":
                return
            if time.time() - row.created_at > settings.timeout + 30:
                row.status, row.error = "failed", "보고서 생성 시간이 만료되었습니다."
                return
            row.draft, row.draft_sha256 = canonical(draft), report_hash(draft)
            execution = {**json.loads(row.execution), **evidence, "completed_at": time.time()}
            if "progress" in execution: execution["progress"]["stage"] = "complete"
            row.execution = canonical(execution)
            row.status = "ready"
    except Exception as exc:
        with transaction(engine) as db:
            row = lock_report(db, task_id, user)
            if row.status == "running":
                row.status = "failed"
                row.error = ("보완본에서 기존 출처가 달라져 적용하지 않았습니다. 원래 초안은 보존되어 있습니다. 의견을 확인한 뒤 다시 요청하세요."
                    if isinstance(exc, RevisionSourceError) else
                    "Codex 보고서 생성을 완료하지 못했습니다. 로그인·연결·출력 형식을 확인한 뒤 새 요청을 시작하세요.")
                execution = json.loads(row.execution)
                execution.update(completed_at=time.time(), completed=False)
                row.execution = canonical(execution)


def install_reports(app, publish, check_lease, doc_dict):
    engine, folder = app.state.engine, app.state.folder
    install_visual_standards(app)
    from .report_preview import install_preview
    install_preview(app, publish, doc_dict, lock_report, decode)

    @app.get("/api/document-standard")
    def get_document_standard():
        return standard_metadata()

    @app.get("/api/document-standard/reference")
    def get_document_standard_reference():
        _, data = load_standard()
        return Response(data, media_type="application/hwp+zip", headers={"Content-Disposition": 'attachment; filename="document-writing-standard.hwpx"'})

    @app.get("/api/document-standard/reference-view")
    def get_document_standard_reference_view():
        profile, data = reference_view()
        return Response(data, media_type="application/hwp+zip", headers={
            "Content-Disposition": 'attachment; filename="document-writing-standard-view.hwpx"',
            "ETag": '"' + digest(data) + '"', "X-HWP-Original-SHA256": profile["reference_sha256"],
        })

    @app.get("/api/report-templates")
    def get_report_templates():
        return template_catalog()

    @app.get("/api/report-tasks")
    def list_reports(request: Request):
        with transaction(engine) as db:
            return [task_dict(r, detail=False) for r in db.scalars(select(ReportTask).where(ReportTask.owner_id == request.state.user).order_by(ReportTask.created_at.desc()).limit(30))]

    @app.post("/api/report-tasks")
    def create_report(value: CreateReport, request: Request):
        user, task_id = request.state.user, str(value.id)
        if not codex_allowed(user):
            raise HTTPException(403, "이 서버의 Codex 실행 사용자에게 보고서 작성을 요청하세요.")
        # Omit an absent selection so legacy IDs retain their exact input hash.
        body = value.model_dump(mode="json", exclude_none=True)
        with ExecutionSlot(app.state.rewrite_capacity) as slot:
            with transaction(engine) as db:
                if engine.dialect.name == "postgresql":
                    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(digest(task_id.encode())[:15], 16)})
                if db.get(ReportTask, task_id):
                    row = lock_report(db, task_id, user)
                    if row.input_sha256 != report_hash(body):
                        raise HTTPException(409, "같은 요청 ID에 다른 보고서 지시를 사용할 수 없습니다.")
                    return task_dict(row)
                settings = capture_settings()
                require_codex_ready(settings)
                if value.template is not None:
                    validate_selection(value.template)
                if value.visual_standard is not None:
                    validate_visual_selection(db, user, value.visual_standard, folder=folder)
                slot.reserve()
                reserve_codex_usage(db, "report:" + task_id, user)
                row = ReportTask(id=task_id, owner_id=user, input=canonical(body), input_sha256=report_hash(body),
                    status="running", created_at=time.time(), execution=canonical({"executor": "codex-app-server", "model": settings.model,
                    "contract": CONTRACT, "research": body["research"], "input_sha256": report_hash(body), "context_used": False}))
                db.add(row); db.flush(); result = task_dict(row)
            slot.start(Thread, run_report, (engine, task_id, user, body, settings))
        return result

    @app.post("/api/report-tasks/{source_task_id}/revise")
    def revise_report(source_task_id: str, value: ReviseReport, request: Request):
        user, task_id = request.state.user, str(value.id)
        if not codex_allowed(user):
            raise HTTPException(403, "이 서버의 Codex 실행 사용자에게 보고서 작성을 요청하세요.")
        requested = {"source_task_id": source_task_id, "draft_sha256": value.draft_sha256, "feedback": value.feedback}
        with ExecutionSlot(app.state.rewrite_capacity) as slot:
            with transaction(engine) as db:
                if engine.dialect.name == "postgresql":
                    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(digest(task_id.encode())[:15], 16)})
                # Reconfirm a lost response before checking current configuration
                # or capacity. The already frozen request must never run again.
                if db.get(ReportTask, task_id):
                    row = lock_report(db, task_id, user)
                    frozen = json.loads(row.input).get("revision", {})
                    if any(frozen.get(key) != item for key, item in requested.items()):
                        raise HTTPException(409, "같은 요청 ID에 다른 초안이나 보완 의견을 사용할 수 없습니다.")
                    return task_dict(row)
                source = lock_report(db, source_task_id, user)
                if source.status != "ready" or not source.draft or source.draft_sha256 != value.draft_sha256:
                    raise HTTPException(409, "검토한 초안이 완료되지 않았거나 현재 초안과 다릅니다.")
                source_draft = json.loads(source.draft)
                if report_hash(source_draft) != value.draft_sha256:
                    raise HTTPException(409, "기존 초안의 무결성을 확인할 수 없습니다.")
                # Keep the preceding task, any frozen preview, approval and saved
                # document untouched. Repeated reviews snapshot only the latest
                # validated draft, avoiding an unbounded chain in model input.
                body = {key: item for key, item in json.loads(source.input).items() if key not in ("id", "revision")}
                body.update(id=task_id, revision={**requested, "draft": source_draft})
                settings = capture_settings()
                require_codex_ready(settings)
                slot.reserve()
                reserve_codex_usage(db, "report:" + task_id, user)
                row = ReportTask(id=task_id, owner_id=user, input=canonical(body), input_sha256=report_hash(body),
                    status="running", created_at=time.time(), execution=canonical({"executor": "codex-app-server", "model": settings.model,
                    "contract": CONTRACT, "research": body["research"], "input_sha256": report_hash(body), "context_used": False,
                    "revision_of": source_task_id, "reviewed_draft_sha256": value.draft_sha256}))
                db.add(row); db.flush(); result = task_dict(row)
            slot.start(Thread, run_report, (engine, task_id, user, body, settings))
        return result

    @app.get("/api/report-tasks/{task_id}")
    def get_report(task_id: str, request: Request):
        with transaction(engine) as db:
            row = lock_report(db, task_id, request.state.user)
            if row.status == "running" and time.time() - row.created_at > 300:
                row.status, row.error = "failed", "보고서 생성 결과 수신이 만료되었습니다."
            return task_dict(row)

    @app.post("/api/report-tasks/{task_id}/cancel")
    def cancel_report(task_id: str, request: Request):
        with transaction(engine) as db:
            row = lock_report(db, task_id, request.state.user)
            if row.status == "running":
                row.status = "cancelled"
            return task_dict(row)

    @app.post("/api/report-tasks/{task_id}/approve")
    def approve_report(task_id: str, value: ApproveReport, request: Request):
        seed = decode(value.seed_base64)
        with transaction(engine) as db:
            row = lock_report(db, task_id, request.state.user)
            if row.status != "ready" or row.draft_sha256 != value.draft_sha256:
                raise HTTPException(409, "검토한 보고서 초안과 현재 결과가 다릅니다.")
            if row.approval:
                approval = json.loads(row.approval)
                if approval["id"] != str(value.id) or approval["seed_sha256"] != digest(seed):
                    raise HTTPException(409, "기존 승인과 동일한 입력으로 재확인하세요.")
                doc = require_access(db, approval["document_id"], request.state.user, "owner", lock=True, fresh=True)
            else:
                visual = json.loads(row.input).get("visual_standard")
                if visual:
                    matches_base(visual["base_standard"])
                data, plan = (scaffold(seed, report_blocks(json.loads(row.draft), visual_settings=visual["settings"], cover=json.loads(row.input).get("cover")), visual_settings=visual["settings"])
                              if visual else scaffold(seed, report_blocks(json.loads(row.draft))))
                if visual:
                    matches_base(visual["base_standard"])
                doc = Document(id=str(uuid.uuid4()), name="작성 중 - " + json.loads(row.draft)["title"].replace("/", " ").replace("\\", " ") + ".hwpx", created_at=time.time(), lease_until=0)
                db.add(doc); db.flush(); db.add(Access(document_id=doc.id, user_id=request.state.user, role="owner"))
                base = publish(db, doc, data, None, None, str(value.id), "upload", author_id=request.state.user)
                approval = {"id": str(value.id), "draft_sha256": row.draft_sha256, "document_id": doc.id,
                    "base_version": base.id, "seed_sha256": digest(seed), "scaffold_sha256": digest(data),
                    "document_standard": standard_metadata(),
                    "plan": [{**p, "command_id": str(uuid.uuid5(value.id, str(i)))} for i, p in enumerate(plan)]}
                if visual:
                    approval["visual_standard"] = visual
                row.approval = canonical(approval)
            base = db.get(Version, approval["base_version"])
            return {**approval, "document": {**doc_dict(doc), "role": "owner"}, "scaffold_base64": base64.b64encode((folder / base.storage_key).read_bytes()).decode()}

    @app.get("/api/report-tasks/{task_id}/approved")
    def approved_report(task_id: str, request: Request):
        with transaction(engine) as db:
            row = lock_report(db, task_id, request.state.user)
            if not row.approval:
                raise HTTPException(404, "승인된 보고서가 없습니다.")
            approval = json.loads(row.approval)
            doc = require_access(db, approval["document_id"], request.state.user, "owner", lock=True, fresh=True)
            base = db.get(Version, approval["base_version"])
            return {**{k: v for k, v in approval.items() if k != "receipts"}, "document": {**doc_dict(doc), "role": "owner"},
                    "scaffold_base64": base64.b64encode((folder / base.storage_key).read_bytes()).decode()}

    @app.post("/api/report-tasks/{task_id}/save")
    def save_report(task_id: str, value: SaveReport, request: Request):
        data = decode(value.output_base64); validate(data)
        with transaction(engine) as db:
            row = lock_report(db, task_id, request.state.user)
            approval = json.loads(row.approval) if row.approval else None
            if not approval or approval["id"] != str(value.approval_id):
                raise HTTPException(409, "보고서 초안 승인이 필요합니다.")
            if approval.get("mode") == "formatted-preview":
                raise HTTPException(409, "확정한 한글 초안은 편집기의 일반 저장을 사용하세요.")
            doc = require_access(db, approval["document_id"], request.state.user, "owner", lock=True, fresh=True)
            require_lease_owner(db, doc, request.state.user, fresh=True); check_lease(doc, value.token)
            from .report_output import verify_output
            base = db.get(Version, approval["base_version"])
            verify_output(data, value, approval, (folder / base.storage_key).read_bytes())
            binding = report_hash({"receipts": value.receipts, "output_sha256": digest(data), "document_sha256": value.document_sha256})
            if approval.get("save_binding") and approval["save_binding"] != binding:
                raise HTTPException(409, "기존 저장 출력과 다른 내용으로 재시도할 수 없습니다.")
            version = publish(db, doc, data, approval["base_version"], value.token, str(uuid.uuid5(value.approval_id, "save")), "report", author_id=request.state.user)
            doc.name = json.loads(row.draft)["title"].replace("/", " ").replace("\\", " ") + ".hwpx"
            approval.update(save_binding=binding, saved_version=version.id, output_sha256=digest(data), receipts=value.receipts)
            row.approval = canonical(approval)
            result = {**doc_dict(doc), "role": "owner"}
        return result
