"""Document conversations with frozen live-editor context and explicit edit proposals."""
import json
import os
import secrets
import time
import unicodedata
import uuid
from contextlib import ExitStack
from threading import Thread
from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from .auth import require_access, require_lease_owner
from .capabilities import llm_allowed, require_llm_access
from .db import Access, Conversation, ConversationTurn, Document, Message, transaction
from .hwpx import digest
from .rewrite import TaskInput
from .rewrite_capacity import ExecutionSlot

MAX_SECONDS = 600
CONTRACT = "live-document-conversation-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentContext(Input):
    text: str = Field(max_length=60000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truncated: bool = Field(strict=True)
    revision: int = Field(ge=0, le=2**53-1, strict=True)
    epoch: int = Field(ge=1, le=2**53-1, strict=True)

    @model_validator(mode="after")
    def integrity(self):
        if digest(self.text.encode("utf-8")) != self.sha256:
            raise ValueError("현재 문서 텍스트 확인값이 다릅니다.")
        return self


class TurnInput(Input):
    id: uuid.UUID
    base_version: str = Field(min_length=1, max_length=36)
    message: str = Field(min_length=1, max_length=12000)
    document_context: DocumentContext
    selection: dict | None = None

    @model_validator(mode="before")
    @classmethod
    def encoding(cls, value):
        try:
            if len(canonical(value).encode("utf-8")) > 500000:
                raise ValueError("too large")
        except (ValueError, UnicodeError, TypeError) as exc:
            raise HTTPException(422, "질문은 500KB 이내의 유효한 UTF-8 JSON이어야 합니다.") from exc
        return value

    @model_validator(mode="after")
    def snapshot(self):
        if not self.message.strip():
            raise ValueError("질문을 입력하세요.")
        if self.selection is not None:
            TaskInput.model_validate({"id": self.id, "base_version": self.base_version,
                "expected_revision": 0, "selection": self.selection, "instruction": "선택 문단 확인"})
            if (self.selection["revision"] != self.document_context.revision or
                    self.selection["state"]["documentEpoch"] != self.document_context.epoch):
                raise ValueError("문서와 선택 문단의 편집 시점이 다릅니다.")
        return self


class AssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str = Field(min_length=1, max_length=12000)
    rewrite_instruction: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("answer", "rewrite_instruction")
    @classmethod
    def visible(cls, value):
        if value is not None and (not value.strip() or any(unicodedata.category(c) in ("Cs", "Cc") and c not in "\n\r\t" for c in value)):
            raise ValueError("유효한 답변 텍스트가 필요합니다.")
        return value


def parse_response(raw):
    if not isinstance(raw, str) or len(raw) > 100000:
        raise ValueError("invalid answer size")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result
    def finite(_):
        raise ValueError("invalid number")
    return AssistantResponse.model_validate(json.loads(raw, object_pairs_hook=unique, parse_constant=finite))


def configured_provider():
    provider = os.getenv("HWP_DOCUMENT_ASSISTANT_PROVIDER", os.getenv("HWP_REWRITE_PROVIDER", "codex"))
    if provider == "codex":
        from .codex_runner import capture_settings
        return provider, capture_settings()
    if provider == "llm":
        from .llm import OpenAICompatible
        return provider, OpenAICompatible()
    raise HTTPException(503, "HWP_DOCUMENT_ASSISTANT_PROVIDER는 codex 또는 llm이어야 합니다.")


def status(row):
    return "expired" if row.status == "running" and time.time() - row.created_at >= MAX_SECONDS else row.status


def lock_turn(db, turn_id):
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("Lock before modifying a turn")
    row = db.get(ConversationTurn, turn_id)
    if row is None:
        raise HTTPException(404, "질문을 찾을 수 없습니다.")
    doc = db.scalar(select(Document).where(Document.id == row.document_id).with_for_update().execution_options(populate_existing=True))
    db.refresh(row)
    return row, doc


def require_current(db, row, doc):
    require_access(db, doc.id, row.requested_by, "editor", fresh=True)
    require_lease_owner(db, doc, row.requested_by, fresh=True)
    execution = json.loads(row.execution)
    if status(row) != "running" or doc.current_version != row.base_version:
        raise HTTPException(409, "질문이 종료됐거나 문서의 저장 버전이 바뀌었습니다.")
    if doc.lease_until <= time.time() or not secrets.compare_digest(execution["_lease_sha256"], digest((doc.lease_token or "").encode())):
        raise HTTPException(409, "질문 중 편집 세션이 만료되거나 변경되었습니다.")


def finish_turn(engine, turn_id, response=None, evidence=None, error=""):
    with transaction(engine) as db:
        row, doc = lock_turn(db, turn_id)
        if row.status != "running":
            return
        try:
            require_current(db, row, doc)
            if error:
                raise ValueError(error)
            value = AssistantResponse.model_validate(response)
            payload = json.loads(row.input)
            if payload["selection"] is None and value.rewrite_instruction is not None:
                raise ValueError("선택 문단이 없는 답변에 수정 요청이 포함됐습니다.")
            row.response, row.status = canonical(value.model_dump()), "ready"
            # A completed turn is also visible to the existing shared conversation.
            for role, content in (("user", payload["message"]), ("assistant", value.answer)):
                db.add(Message(id=str(uuid.uuid5(uuid.UUID(row.id), role)), conversation_id=row.conversation_id,
                    author_id=row.requested_by, role=role, content=content, source="document_ai",
                    request_id=f"assistant:{row.id}:{role}", created_at=time.time()))
        except (HTTPException, ValueError) as exc:
            row.status, row.error = "failed", str(exc.detail if isinstance(exc, HTTPException) else exc)[:1000]
        row.execution = canonical({**json.loads(row.execution), **(evidence or {}),
            "completed": row.status == "ready", "outcome": row.status, "finished_at": time.time()})


def run_turn(engine, turn_id, provider, settings):
    def active():
        try:
            with Session(engine) as db:
                row = db.get(ConversationTurn, turn_id)
                require_current(db, row, db.get(Document, row.document_id))
            return True
        except HTTPException:
            return False
    try:
        with Session(engine) as db:
            row = db.get(ConversationTurn, turn_id)
            require_current(db, row, db.get(Document, row.document_id))
            value = json.loads(row.input)
        from .assistant_runner import generate_answer
        response, evidence = generate_answer(value, provider, settings, active)
        finish_turn(engine, turn_id, response.model_dump(), evidence)
    except Exception as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        # Avoid exposing model endpoint credentials in provider exception strings.
        finish_turn(engine, turn_id, error=message if provider == "codex" else "AI 답변을 완료하지 못했습니다. 연결 상태와 모델 응답을 확인하세요.")


def public_turn(row, doc, user, role):
    payload, result = json.loads(row.input), json.loads(row.response) if row.response else {}
    context = payload["document_context"]
    return {"id": row.id, "conversation_id": row.conversation_id, "document_id": row.document_id,
        "base_version": row.base_version, "requested_by": row.requested_by, "message": payload["message"],
        "status": status(row), "answer": result.get("answer"), "rewrite_instruction": result.get("rewrite_instruction"),
        "selection": payload["selection"], "document_context": {k: context[k] for k in ("sha256", "truncated", "revision", "epoch")} | {"characters": len(context["text"])},
        "error": row.error or ("응답 제한 시간이 지났습니다. 같은 질문을 자동으로 다시 실행하지 않습니다." if status(row) == "expired" else ""),
        "execution": {k: v for k, v in json.loads(row.execution).items() if not k.startswith("_")},
        "created_at": row.created_at, "stale": doc.current_version != row.base_version,
        "can_cancel": status(row) == "running" and role != "reader" and (row.requested_by == user or role == "owner")}


def install_document_assistant(app, check_lease):
    engine = app.state.engine

    @app.get("/api/document-assistant-provider")
    def provider_info(request: Request):
        try:
            provider, settings = configured_provider()
            allowed = llm_allowed(request.state.user) if provider == "llm" else request.state.user == os.getenv("HWP_CODEX_USER", "local")
            configured = provider != "codex" or bool(settings.executable)
            reason = "이 사용자는 서버의 AI 연결을 실행할 권한이 없습니다." if provider == "llm" else "이 서버에 연결된 Codex 계정의 사용자만 질문할 수 있습니다."
            return {"provider": provider, "model": settings.model, "configured": configured, "allowed": allowed and configured,
                "reason": "설치된 Codex CLI가 필요합니다." if not configured else "" if allowed else reason}
        except HTTPException as exc:
            return {"provider": os.getenv("HWP_DOCUMENT_ASSISTANT_PROVIDER", os.getenv("HWP_REWRITE_PROVIDER", "codex")),
                "model": None, "configured": False, "allowed": False, "reason": str(exc.detail)}

    @app.post("/api/threads/{thread_id}/assistant-turns")
    def create_turn(thread_id: str, value: TurnInput, request: Request, x_edit_session: str = Header()):
        with ExecutionSlot(app.state.rewrite_capacity) as slot, ExitStack() as pending_budget:
            with transaction(engine) as db:
                thread = db.get(Conversation, thread_id)
                if not thread:
                    raise HTTPException(404, "대화를 찾을 수 없습니다.")
                doc = require_access(db, thread.document_id, request.state.user, "editor", lock=True)
                fingerprint = digest(canonical(value.model_dump(mode="json")).encode())
                old = db.get(ConversationTurn, str(value.id))
                role = db.get(Access, (doc.id, request.state.user)).role
                if old:
                    if old.conversation_id != thread_id or old.requested_by != request.state.user or old.request_sha256 != fingerprint:
                        raise HTTPException(409, "같은 질문 ID의 내용 또는 사용자가 다릅니다.")
                    return public_turn(old, doc, request.state.user, role)
                require_lease_owner(db, doc, request.state.user)
                check_lease(doc, x_edit_session)
                if doc.current_version != value.base_version:
                    raise HTTPException(409, "문서의 저장 버전이 바뀌었습니다. 현재 문서로 다시 질문하세요.")
                provider, settings = configured_provider()
                if provider == "llm":
                    require_llm_access(request.state.user)
                if provider == "codex" and request.state.user != os.getenv("HWP_CODEX_USER", "local"):
                    raise HTTPException(403, "이 서버에 연결된 Codex 계정의 사용자만 질문할 수 있습니다.")
                if provider == "codex" and not settings.executable:
                    raise HTTPException(503, "설치된 Codex CLI가 필요합니다.")
                history = list(db.scalars(select(ConversationTurn).where(ConversationTurn.conversation_id == thread_id,
                    ConversationTurn.status == "ready").order_by(ConversationTurn.created_at.desc()).limit(10)))
                payload = {**value.model_dump(mode="json"), "_assistant_contract": CONTRACT, "document_name": doc.name, "history": [
                    {"message": json.loads(turn.input)["message"], "answer": json.loads(turn.response)["answer"],
                     "base_version": turn.base_version} for turn in reversed(history)]}
                encoded = canonical(payload)
                row = ConversationTurn(id=str(value.id), conversation_id=thread_id, document_id=doc.id,
                    requested_by=request.state.user, base_version=value.base_version, input=encoded,
                    input_sha256=digest(encoded.encode()), request_sha256=fingerprint, status="running", response=None,
                    execution=canonical({"provider": provider, "model": settings.model, "contract": CONTRACT,
                        "completed": False, "outcome": "running", "input_sha256": digest(encoded.encode()),
                        "history_turns": len(history), "_lease_sha256": digest(x_edit_session.encode())}), error="", created_at=time.time())
                slot.reserve()
                # Identical retries above only read the accepted request. A
                # global-capacity rejection must not consume a user's quota.
                pending_budget.enter_context(app.state.ai_budget.reserve(request.state.user))
                db.add(row)
                result = public_turn(row, doc, request.state.user, role)
            # The worker owns admission until actual inference/cleanup ends.
            # Returning HTTP 200 or cancelling a logical turn cannot free it.
            worker_budget = pending_budget.pop_all()
            def execute(*args):
                try:
                    run_turn(*args)
                finally:
                    worker_budget.close()
            try:
                slot.start(Thread, execute, (engine, row.id, provider, settings))
            except Exception:
                finish_turn(engine, row.id, error="질문 실행기를 시작하지 못했습니다.")
                result.update(status="failed", error="질문 실행기를 시작하지 못했습니다.")
            finally:
                if not slot.started:
                    worker_budget.close()
            return result

    @app.get("/api/threads/{thread_id}/assistant-turns")
    def turns(thread_id: str, request: Request):
        with Session(engine) as db:
            thread = db.get(Conversation, thread_id)
            if not thread:
                raise HTTPException(404, "대화를 찾을 수 없습니다.")
            doc = require_access(db, thread.document_id, request.state.user)
            role = db.get(Access, (doc.id, request.state.user)).role
            rows = db.scalars(select(ConversationTurn).where(ConversationTurn.conversation_id == thread_id)
                .order_by(ConversationTurn.created_at.desc()).limit(100))
            return [public_turn(row, doc, request.state.user, role) for row in rows]

    @app.get("/api/assistant-turns/{turn_id}")
    def get_turn(turn_id: str, request: Request):
        with Session(engine) as db:
            row = db.get(ConversationTurn, turn_id)
            if not row:
                raise HTTPException(404, "질문을 찾을 수 없습니다.")
            doc = require_access(db, row.document_id, request.state.user)
            return public_turn(row, doc, request.state.user, db.get(Access, (doc.id, request.state.user)).role)

    @app.post("/api/assistant-turns/{turn_id}/cancel")
    def cancel(turn_id: str, request: Request):
        with transaction(engine) as db:
            row, doc = lock_turn(db, turn_id)
            require_access(db, doc.id, request.state.user, "editor")
            if row.requested_by != request.state.user:
                require_access(db, doc.id, request.state.user, "owner")
            if row.status != "cancelled":
                if status(row) != "running":
                    raise HTTPException(409, "이미 종료된 질문입니다.")
                row.status, row.error = "cancelled", "답변 수신을 중단했습니다. 문서는 변경되지 않았습니다."
                row.execution = canonical({**json.loads(row.execution), "completed": False, "outcome": "cancelled", "finished_at": time.time()})
            return public_turn(row, doc, request.state.user, db.get(Access, (doc.id, request.state.user)).role)
