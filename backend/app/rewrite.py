"""One frozen selection -> Codex/MCP -> two candidates -> explicit approval -> version."""
import io
import json
import os
import re
import secrets
import time
import uuid
import zipfile
from threading import Thread
from fastapi import HTTPException, Request, Header
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from defusedxml import ElementTree as ET
from .auth import require_access, require_lease_owner
from .collaboration import NewContext
from .db import Application, Conversation, RewriteCandidate, RewriteTask, Version, transaction
from .hwpx import digest


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskInput(NewContext):
    id: uuid.UUID
    instruction: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def exact_body_paragraph(self):
        try:
            s = self.selection
            target, state, evidence = s["selection"]["target"], s["state"], s["evidence"]
            valid = (target["kind"] == "body_paragraph" and target["charOffset"] == 0
                     and all(type(target[k]) is int and target[k] >= 0 for k in ("section", "paragraph", "length"))
                     and type(state["changeSeq"]) is int and state["changeSeq"] >= 0
                     and bool(evidence["text"])
                     and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in (
                         state["documentSha256"], evidence["textSha256"], evidence["formatSha256"], evidence["adjacentContextSha256"])))
            if not valid:
                raise ValueError("unsupported selection")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("단일 본문 문단의 대상·상태·확인값이 필요합니다.") from exc
        return self


class CandidateInput(Input):
    replacement: str = Field(min_length=1, max_length=12000)
    reason: str = Field(min_length=1, max_length=2000)


class CandidatesInput(Input):
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidates: list[CandidateInput] = Field(min_length=2, max_length=2)


class Approval(Input):
    id: uuid.UUID
    candidate_id: str


class Receipt(Input):
    engine_receipt: dict
    export_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def task_access(db, task_id, user, role="reader", lock=False):
    task = db.get(RewriteTask, task_id)
    if not task:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    doc = require_access(db, task.document_id, user, role, lock=lock)
    return task, doc


def run_identity(engine, token, path, method):
    """Per-run credential exposes only this task's input and candidate submission."""
    with Session(engine) as db:
        task = db.scalar(select(RewriteTask).where(RewriteTask.token_hash == digest(token.encode())))
        if not task or task.status != "running" or time.time() - task.created_at > 600:
            raise HTTPException(401, "문서 실행 토큰이 만료되었습니다.")
        allowed = {("GET", f"/api/rewrite-tasks/{task.id}/input"), ("POST", f"/api/rewrite-tasks/{task.id}/candidates")}
        if (method, path) not in allowed:
            raise HTTPException(403, "문서 실행기는 이 작업의 입력과 후보만 사용할 수 있습니다.")
        require_access(db, task.document_id, task.requested_by, "editor")
        return task.requested_by, task.id


def application_value(db, row):
    task = db.get(RewriteTask, row.task_id)
    candidate = db.get(RewriteCandidate, row.candidate_id)
    return {"id": row.id, "task_id": task.id, "candidate_id": candidate.id,
            "document_id": task.document_id, "base_version": task.base_version,
            "requested_by": task.requested_by, "approved_by": row.approved_by,
            "status": row.status, "saved_version": row.saved_version,
            "original": json.loads(task.snapshot)["evidence"]["text"],
            "replacement": candidate.replacement, "created_at": row.created_at}


def validate_application_save(db, doc, application_id, user, key, base, data):
    row = db.get(Application, application_id)
    if not row:
        raise HTTPException(404, "승인된 적용 요청이 없습니다.")
    task, _ = task_access(db, row.task_id, user, "editor")
    if task.document_id != doc.id or row.approved_by != user or key != row.id or base != task.base_version:
        raise HTTPException(409, "적용 요청·문서·기준 버전 연결이 다릅니다.")
    if not row.receipt or row.export_sha256 != digest(data):
        raise HTTPException(409, "승인 적용 직후의 출력과 저장 파일이 다릅니다.")
    target = json.loads(task.snapshot)["selection"]["target"]
    candidate = db.get(RewriteCandidate, row.candidate_id)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            section = ET.fromstring(z.read(f"Contents/section{target['section']}.xml"), forbid_dtd=True)
        paragraphs = [p for p in section if p.tag.split("}")[-1] == "p"]
        paragraph = paragraphs[target["paragraph"]]
        actual = "".join((t.text or "") for t in paragraph.iter() if t.tag.split("}")[-1] == "t")
        if actual != candidate.replacement:
            raise ValueError("candidate mismatch")
    except (KeyError, IndexError, ValueError, ET.ParseError) as exc:
        raise HTTPException(409, "저장 HWPX의 대상 문단이 승인 후보와 다릅니다.") from exc
    if row.saved_version:
        version = db.get(Version, row.saved_version)
        if version.request_id != key or version.sha256 != digest(data):
            raise HTTPException(409, "다른 적용 저장 요청입니다.")
    return row


def install_rewrite(app, check_lease):
    engine = app.state.engine

    @app.post("/api/threads/{thread_id}/rewrite-tasks")
    def create_task(thread_id: str, value: TaskInput, request: Request, x_edit_session: str = Header()):
        if request.state.user != os.getenv("HWP_CODEX_USER", "local"):
            raise HTTPException(403, "이 서버의 본인 Codex 계정에 연결된 사용자만 실행할 수 있습니다.")
        if not value.selection:
            raise HTTPException(422, "요청 시점의 선택 문단이 필요합니다.")
        from .codex_runner import run_codex
        with transaction(engine) as db:
            thread = db.get(Conversation, thread_id)
            if not thread:
                raise HTTPException(404, "대화를 찾을 수 없습니다.")
            doc = require_access(db, thread.document_id, request.state.user, "editor", lock=True)
            require_lease_owner(db, doc, request.state.user)
            check_lease(doc, x_edit_session)
            existing = db.get(RewriteTask, str(value.id))
            snapshot = json.dumps(value.selection, ensure_ascii=False, sort_keys=True)
            if existing:
                if (existing.conversation_id, existing.requested_by, existing.base_version, existing.snapshot, existing.instruction) != (
                    thread_id, request.state.user, value.base_version, snapshot, value.instruction):
                    raise HTTPException(409, "같은 작업 ID의 요청 내용이 다릅니다.")
                return {"id": existing.id, "status": existing.status}
            if doc.current_version != value.base_version:
                raise HTTPException(409, "기준 버전이 변경되었습니다.")
            token = "hwp-run-" + secrets.token_urlsafe(32)
            row = RewriteTask(id=str(value.id), conversation_id=thread_id, document_id=doc.id,
                              requested_by=request.state.user, base_version=value.base_version,
                              snapshot=snapshot, instruction=value.instruction, status="running",
                              token_hash=digest(token.encode()), created_at=time.time())
            db.add(row)
            result = {"id": row.id, "status": row.status}
        # Only the local server owner's logged-in Codex may execute here; no auth files copied.
        Thread(target=run_codex, args=(engine, str(value.id), token), daemon=True).start()
        return result

    @app.get("/api/rewrite-tasks/{task_id}/input")
    def task_input(task_id: str, request: Request):
        with Session(engine) as db:
            task, doc = task_access(db, task_id, request.state.user)
            return {"task_id": task.id, "document_id": doc.id, "conversation_id": task.conversation_id,
                    "requested_by": task.requested_by, "base_version": task.base_version,
                    "snapshot": json.loads(task.snapshot), "instruction": task.instruction,
                    "stale": doc.current_version != task.base_version,
                    "note": "This is the unsaved selection at request time. Generate two candidates; never apply or save."}

    @app.post("/api/rewrite-tasks/{task_id}/candidates")
    def candidates(task_id: str, value: CandidatesInput, request: Request):
        if getattr(request.state, "run_task", None) != task_id:
            raise HTTPException(403, "이 작업의 문서 실행기만 후보를 제출할 수 있습니다.")
        with transaction(engine) as db:
            task, _ = task_access(db, task_id, request.state.user, "editor", lock=True)
            if value.original_sha256 != json.loads(task.snapshot)["evidence"]["textSha256"]:
                raise HTTPException(409, "후보의 AS-IS 확인값이 요청 원문과 다릅니다.")
            if value.candidates[0].replacement == value.candidates[1].replacement:
                raise HTTPException(422, "서로 다른 후보 두 개가 필요합니다.")
            ids = []
            for i, item in enumerate(value.candidates, 1):
                candidate_id = str(uuid.uuid5(uuid.UUID(task.id), f"candidate-{i}"))
                old = db.get(RewriteCandidate, candidate_id)
                if old and (old.replacement, old.reason) != (item.replacement, item.reason):
                    raise HTTPException(409, "이미 보관된 후보를 덮어쓸 수 없습니다.")
                if not old:
                    db.add(RewriteCandidate(id=candidate_id, task_id=task.id, ordinal=i, **item.model_dump()))
                ids.append(candidate_id)
        return {"task_id": task_id, "candidate_ids": ids, "document_changed": False}

    @app.get("/api/threads/{thread_id}/rewrite-tasks")
    def tasks(thread_id: str, request: Request):
        with Session(engine) as db:
            thread = db.get(Conversation, thread_id)
            if not thread:
                raise HTTPException(404, "대화를 찾을 수 없습니다.")
            doc = require_access(db, thread.document_id, request.state.user)
            rows = db.scalars(select(RewriteTask).where(RewriteTask.conversation_id == thread_id).order_by(RewriteTask.created_at.desc()))
            result = []
            for row in rows:
                choices = list(db.scalars(select(RewriteCandidate).where(RewriteCandidate.task_id == row.id).order_by(RewriteCandidate.ordinal)))
                application = db.scalar(select(Application).where(Application.task_id == row.id))
                result.append({"id": row.id, "document_id": doc.id, "base_version": row.base_version,
                    "requested_by": row.requested_by, "snapshot": json.loads(row.snapshot), "instruction": row.instruction,
                    "status": row.status, "error": row.error, "execution": json.loads(row.execution),
                    "stale": doc.current_version != row.base_version,
                    "candidates": [{"id": c.id, "ordinal": c.ordinal, "replacement": c.replacement, "reason": c.reason} for c in choices],
                    "application": application_value(db, application) if application else None})
            return result

    @app.post("/api/rewrite-tasks/{task_id}/approve")
    def approve(task_id: str, value: Approval, request: Request, x_edit_session: str = Header()):
        with transaction(engine) as db:
            task, doc = task_access(db, task_id, request.state.user, "editor", lock=True)
            require_lease_owner(db, doc, request.state.user)
            check_lease(doc, x_edit_session)
            old = db.scalar(select(Application).where(Application.task_id == task.id))
            if old:
                if (old.id, old.candidate_id, old.approved_by) != (str(value.id), value.candidate_id, request.state.user):
                    raise HTTPException(409, "이 작업에서 다른 후보 또는 적용 요청이 이미 승인되었습니다.")
                if doc.current_version != (old.saved_version or task.base_version):
                    raise HTTPException(409, "승인 이후 기준 버전이 변경되었습니다.")
                return {**application_value(db, old), "snapshot": json.loads(task.snapshot)}
            candidate = db.get(RewriteCandidate, value.candidate_id)
            if not candidate or candidate.task_id != task.id:
                raise HTTPException(404, "이 작업의 후보가 아닙니다.")
            if task.status != "ready" or doc.current_version != task.base_version:
                raise HTTPException(409, "후보 실행이 완료되지 않았거나 기준 버전이 오래되었습니다.")
            if db.get(Application, str(value.id)):
                raise HTTPException(409, "이미 사용한 적용 ID입니다.")
            row = Application(id=str(value.id), task_id=task.id, candidate_id=candidate.id,
                              approved_by=request.state.user, status="approved", created_at=time.time())
            db.add(row)
            result = {**application_value(db, row), "snapshot": json.loads(task.snapshot)}
        return result

    @app.post("/api/applications/{application_id}/receipt")
    def receipt(application_id: str, value: Receipt, request: Request, x_edit_session: str = Header()):
        with transaction(engine) as db:
            row = db.get(Application, application_id)
            if not row:
                raise HTTPException(404, "적용 요청이 없습니다.")
            task, doc = task_access(db, row.task_id, request.state.user, "editor", lock=True)
            require_lease_owner(db, doc, request.state.user)
            check_lease(doc, x_edit_session)
            if row.approved_by != request.state.user or doc.current_version != (row.saved_version or task.base_version):
                raise HTTPException(409, "적용 사용자 또는 기준 버전이 다릅니다.")
            snapshot = json.loads(task.snapshot)
            candidate = db.get(RewriteCandidate, row.candidate_id)
            r = value.engine_receipt
            if (r.get("commandId") != row.id or r.get("operation") != "apply"
                or r.get("beforeTextSha256") != snapshot["evidence"]["textSha256"]
                or r.get("beforeDocumentSha256") != snapshot["state"]["documentSha256"]
                or r.get("documentEpoch") != snapshot["state"]["documentEpoch"]
                or r.get("beforeChangeSeq") != snapshot["state"]["changeSeq"]
                or r.get("formatSha256") != snapshot["evidence"]["formatSha256"]
                or r.get("adjacentContextSha256") != snapshot["evidence"]["adjacentContextSha256"]
                or r.get("target") != snapshot["selection"]["target"]
                or r.get("afterTextSha256") != digest(candidate.replacement.encode())):
                raise HTTPException(409, "에디터 적용 영수증이 승인 후보와 다릅니다.")
            encoded = json.dumps(r, sort_keys=True)
            if row.receipt and (row.receipt != encoded or row.export_sha256 != value.export_sha256):
                raise HTTPException(409, "같은 적용 ID에 다른 영수증을 보낼 수 없습니다.")
            row.receipt, row.export_sha256 = encoded, value.export_sha256
            if not row.saved_version:
                row.status = "applied"
        return {"id": application_id, "status": row.status}

    @app.get("/api/documents/{doc_id}/applications")
    def history(doc_id: str, request: Request):
        with Session(engine) as db:
            require_access(db, doc_id, request.state.user)
            rows = db.scalars(select(Application).join(RewriteTask, RewriteTask.id == Application.task_id)
                             .where(RewriteTask.document_id == doc_id).order_by(Application.created_at.desc()))
            return [application_value(db, row) for row in rows]
