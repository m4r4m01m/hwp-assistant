"""One frozen selection -> configured generator -> candidates -> approval -> version."""
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
from .db import Access, Application, Conversation, RewriteCandidate, RewriteTask, Version, transaction
from .hwpx import digest
from .llm import OpenAICompatible
from .capabilities import require_llm_access, llm_allowed, codex_executable_available
from .rewrite_contract import CandidateInput, CandidatePair, PROMPT_CONTRACT
from .rewrite_capacity import ExecutionSlot, configured_capacity
from .rewrite_context import freeze_context, frozen_context, context_stale, generation_input, input_hash, CONTEXT_PROMPT_CONTRACT
from .rewrite_jobs import lock_task, require_current, store_candidates, public_execution, fail_task, effective_status, update_execution, MAX_JOB_SECONDS


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskInput(NewContext):
    id: uuid.UUID
    instruction: str = Field(min_length=1, max_length=2000)
    context_revision: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="before")
    @classmethod
    def encodable_request(cls, value):
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
        except (UnicodeError, ValueError) as exc:
            # Pydantic's default error includes the invalid input, which itself may
            # be impossible to encode in an HTTP error response (lone surrogate).
            raise HTTPException(422, "요청에는 유효한 UTF-8 JSON 문자와 유한한 수만 사용할 수 있습니다.") from exc
        return value

    @model_validator(mode="after")
    def exact_body_paragraph(self):
        if not self.instruction.strip():
            raise ValueError("문체 개선 요청을 입력하세요.")
        try:
            s = self.selection
            target, state, evidence = s["selection"]["target"], s["state"], s["evidence"]
            valid = (s["selection"]["editable"] is True and target["kind"] == "body_paragraph"
                     and type(target["charOffset"]) is int and target["charOffset"] == 0
                     and set(target) == {"kind", "section", "paragraph", "charOffset", "length"}
                     and all(type(target[k]) is int and 0 <= target[k] <= 2**53 - 1 for k in ("section", "paragraph", "length"))
                     and type(state["changeSeq"]) is int and 0 <= state["changeSeq"] <= 2**53 - 1
                     and type(state["documentEpoch"]) is int and 1 <= state["documentEpoch"] <= 2**53 - 1
                     and type(s["revision"]) is int and 0 <= s["revision"] <= 2**53 - 1
                     and target["length"] == len(evidence["text"]) <= 4000
                     and bool(evidence["text"])
                     and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in (
                         state["documentSha256"], evidence["textSha256"], evidence["formatSha256"], evidence["adjacentContextSha256"])))
            if not valid:
                raise ValueError("unsupported selection")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("단일 본문 문단의 대상·상태·확인값이 필요합니다.") from exc
        return self


class CandidatesInput(CandidatePair):
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


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
        if not task or task.status != "running" or time.time() - task.created_at >= MAX_JOB_SECONDS:
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
    app.state.rewrite_capacity = configured_capacity()

    def configured_generator():
        provider = os.getenv("HWP_REWRITE_PROVIDER", "codex")
        if provider == "codex":
            from .codex_runner import capture_settings
            return provider, capture_settings()
        if provider == "llm":
            return provider, OpenAICompatible()
        raise HTTPException(503, "HWP_REWRITE_PROVIDER는 codex 또는 llm이어야 합니다.")

    @app.get("/api/rewrite-provider")
    def rewrite_provider(request: Request):
        try:
            provider, client = configured_generator()
            allowed = llm_allowed(request.state.user) if provider == "llm" else request.state.user == os.getenv("HWP_CODEX_USER", "local")
            configured = provider != "codex" or codex_executable_available(client.executable)
            return {"provider": provider, "model": client.model if client else None,
                    "configured": configured, "allowed": allowed and configured, "context_used": False, "supports_writing_context": True,
                    "reason": "설치된 Codex CLI가 필요합니다." if not configured else "" if allowed else "이 서버의 본인 Codex 계정에 연결된 사용자만 실행할 수 있습니다."}
        except HTTPException as exc:
            return {"provider": os.getenv("HWP_REWRITE_PROVIDER", "codex"), "model": None,
                    "configured": False, "allowed": False, "context_used": False, "reason": str(exc.detail)}

    @app.post("/api/threads/{thread_id}/rewrite-tasks")
    def create_task(thread_id: str, value: TaskInput, request: Request, x_edit_session: str = Header()):
        if not value.selection:
            raise HTTPException(422, "요청 시점의 선택 문단이 필요합니다.")
        with ExecutionSlot(app.state.rewrite_capacity) as slot:
            with transaction(engine) as db:
                thread = db.get(Conversation, thread_id)
                if not thread:
                    raise HTTPException(404, "대화를 찾을 수 없습니다.")
                doc = require_access(db, thread.document_id, request.state.user, "editor", lock=True)
                existing = db.get(RewriteTask, str(value.id))
                requested_provider = public_execution(existing)["provider"] if existing else os.getenv("HWP_REWRITE_PROVIDER", "codex")
                if requested_provider == "codex" and request.state.user != os.getenv("HWP_CODEX_USER", "local"):
                    raise HTTPException(403, "이 서버의 본인 Codex 계정에 연결된 사용자만 실행할 수 있습니다.")
                require_lease_owner(db, doc, request.state.user)
                check_lease(doc, x_edit_session)
                snapshot = json.dumps(value.selection, ensure_ascii=False, sort_keys=True)
                # Preserve the request fingerprint of pre-context jobs on identical retries.
                fingerprint = digest(json.dumps(value.model_dump(mode="json", exclude={"context_revision"} if value.context_revision is None else set()), ensure_ascii=False, sort_keys=True).encode())
                if existing:
                    if (existing.conversation_id, existing.requested_by, existing.base_version, existing.snapshot, existing.instruction) != (
                        thread_id, request.state.user, value.base_version, snapshot, value.instruction):
                        raise HTTPException(409, "같은 작업 ID의 요청 내용이 다릅니다.")
                    old_fingerprint = json.loads(existing.execution).get("_request_sha256")
                    if old_fingerprint and old_fingerprint != fingerprint:
                        raise HTTPException(409, "같은 작업 ID의 요청 내용이 다릅니다.")
                    return {"id": existing.id, "status": effective_status(existing)}
                if doc.current_version != value.base_version:
                    raise HTTPException(409, "기준 버전이 변경되었습니다.")
                context = freeze_context(db, thread, doc, value.context_revision)
                provider, client = configured_generator()
                if provider == "llm":
                    require_llm_access(request.state.user)
                if provider == "codex" and request.state.user != os.getenv("HWP_CODEX_USER", "local"):
                    raise HTTPException(403, "이 서버의 본인 Codex 계정에 연결된 사용자만 실행할 수 있습니다.")
                token = "hwp-run-" + secrets.token_urlsafe(32) if provider == "codex" else ""
                if context is not None and provider == "codex":
                    from dataclasses import replace
                    client = replace(client, prompt_contract=CONTEXT_PROMPT_CONTRACT)
                payload = generation_input(value.selection["evidence"]["text"], value.instruction, context)
                execution = {"provider": provider, "executor": "codex-cli" if provider == "codex" else "openai-compatible",
                             "model": client.model, "completed": False, "outcome": "running",
                             "prompt_contract": CONTEXT_PROMPT_CONTRACT if context is not None else PROMPT_CONTRACT, "timeout_seconds": client.timeout,
                             "model_source": "configured" if client.model else "CLI default; ID not confirmed",
                             "endpoint_kind": "task-api" if provider == "codex" else "model-api",
                             "_endpoint": client.api_url if provider == "codex" else client.base,
                             "context_used": context is not None, "original_sha256": value.selection["evidence"]["textSha256"],
                             "snapshot_sha256": digest(snapshot.encode()),
                             "input_sha256": input_hash(payload),
                             "_request_sha256": fingerprint, "_lease_sha256": digest(x_edit_session.encode())}
                if context is not None:
                    execution.update(_context_snapshot=context, context_id=context["id"],
                                     context_revision=context["revision"], context_sha256=input_hash(context))
                row = RewriteTask(id=str(value.id), conversation_id=thread_id, document_id=doc.id,
                                  requested_by=request.state.user, base_version=value.base_version,
                                  snapshot=snapshot, instruction=value.instruction, status="running",
                                  token_hash=digest(token.encode()) if token else "",
                                  execution=json.dumps(execution, ensure_ascii=False), created_at=time.time())
                slot.reserve()
                db.add(row)
                result = {"id": row.id, "status": row.status}
            # Dispatch once, only after the task commit. Never run a second provider on failure.
            if provider == "codex":
                from .codex_runner import run_codex
                target, args = run_codex, (engine, str(value.id), token, client)
            else:
                from .local_rewrite import run_local_rewrite
                target, args = run_local_rewrite, (engine, str(value.id), client)
            try:
                slot.start(Thread, target, args)
            except Exception:
                fail_task(engine, str(value.id), "후보 생성 실행기를 시작하지 못했습니다.")
                result["status"] = "failed"
            return result

    @app.get("/api/rewrite-tasks/{task_id}/input")
    def task_input(task_id: str, request: Request):
        with Session(engine) as db:
            task, doc = task_access(db, task_id, request.state.user)
            context = frozen_context(task)
            payload = generation_input(json.loads(task.snapshot)["evidence"]["text"], task.instruction, context)
            return {"task_id": task.id, "document_id": doc.id, "conversation_id": task.conversation_id,
                    "requested_by": task.requested_by, "base_version": task.base_version,
                    "snapshot": json.loads(task.snapshot), "instruction": task.instruction,
                    "generation_input": payload, "input_sha256": input_hash(payload),
                    "context_used": context is not None,
                    "stale": doc.current_version != task.base_version or context_stale(db, task),
                    "note": "This is the unsaved selection at request time. Generate two candidates; never apply or save."}

    @app.post("/api/rewrite-tasks/{task_id}/candidates")
    def candidates(task_id: str, value: CandidatesInput, request: Request):
        if getattr(request.state, "run_task", None) != task_id:
            raise HTTPException(403, "이 작업의 문서 실행기만 후보를 제출할 수 있습니다.")
        with transaction(engine) as db:
            task, doc = lock_task(db, task_id)
            require_current(db, task, doc)
            if value.original_sha256 != json.loads(task.snapshot)["evidence"]["textSha256"]:
                raise HTTPException(409, "후보의 AS-IS 확인값이 요청 원문과 다릅니다.")
            ids = store_candidates(db, task, CandidatePair(candidates=value.candidates))
        return {"task_id": task_id, "candidate_ids": ids, "document_changed": False}

    @app.get("/api/threads/{thread_id}/rewrite-tasks")
    def tasks(thread_id: str, request: Request):
        with Session(engine) as db:
            thread = db.get(Conversation, thread_id)
            if not thread:
                raise HTTPException(404, "대화를 찾을 수 없습니다.")
            doc = require_access(db, thread.document_id, request.state.user)
            grant = db.get(Access, (doc.id, request.state.user))
            if not grant:
                raise HTTPException(404, "문서를 찾을 수 없습니다.")
            rows = db.scalars(select(RewriteTask).where(RewriteTask.conversation_id == thread_id).order_by(RewriteTask.created_at.desc()))
            result = []
            for row in rows:
                choices = list(db.scalars(select(RewriteCandidate).where(RewriteCandidate.task_id == row.id).order_by(RewriteCandidate.ordinal)))
                application = db.scalar(select(Application).where(Application.task_id == row.id))
                changed_context = context_stale(db, row)
                result.append({"id": row.id, "document_id": doc.id, "base_version": row.base_version,
                    "requested_by": row.requested_by, "snapshot": json.loads(row.snapshot), "instruction": row.instruction,
                    "status": effective_status(row),
                    "error": row.error or ("제한 시간 안에 완료 응답을 확인하지 못했습니다. 원문과 후보는 보존되며 자동 재실행하지 않습니다." if effective_status(row) == "expired" else ""),
                    "execution": public_execution(row),
                    "can_cancel": row.status == "running" and grant.role != "reader" and
                        (row.requested_by == request.state.user or grant.role == "owner"),
                    "stale": doc.current_version != row.base_version or (changed_context and not application),
                    "context_stale": changed_context,
                    "candidates": [{"id": c.id, "ordinal": c.ordinal, "replacement": c.replacement, "reason": c.reason} for c in choices],
                    "application": application_value(db, application) if application else None})
            return result

    @app.post("/api/rewrite-tasks/{task_id}/cancel")
    def cancel(task_id: str, request: Request):
        with transaction(engine) as db:
            task, doc = lock_task(db, task_id)
            require_access(db, doc.id, request.state.user, "editor")
            if task.requested_by != request.state.user:
                require_access(db, doc.id, request.state.user, "owner")
            if task.status == "cancelled":
                return {"id": task.id, "status": task.status}
            if task.status != "running":
                raise HTTPException(409, "이미 종료된 작업입니다. 원문과 후보는 그대로 보존됩니다.")
            task.status, task.error = "cancelled", "사용자가 이 작업의 결과 수신을 중단했습니다. 원문과 후보는 보존됩니다."
            update_execution(task, {"cancelled_by": request.state.user, "completed": False}, "cancelled")
        return {"id": task_id, "status": "cancelled"}

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
            if context_stale(db, task):
                raise HTTPException(409, "후보 생성에 사용한 작성 정보가 변경되었습니다. 현재 내용으로 새 후보를 요청하세요.")
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
