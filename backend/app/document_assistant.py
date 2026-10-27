from .library_access import document_role
"""Document conversations with frozen live-editor context and explicit edit proposals."""
from .codex_runtime import allowed as codex_allowed, require_ready as require_codex_ready, reserve_usage as reserve_codex_usage
import json
import base64
import os
import secrets
import time
import unicodedata
import uuid
from contextlib import ExitStack
from threading import Thread
from typing import Literal
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
from .document_rewrite import RewriteContext, ParagraphRewrite, validated_rewrites, verify_rewrite_output
from .assistant_table_layout import TableLayout, targets as table_targets, validated_layout, apply_layout

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


class DesignTarget(Input):
    kind: Literal["paragraph", "table"]
    section: Literal[0]
    paragraph: int = Field(ge=0, le=100000, strict=True)
    control: int | None = Field(default=None, ge=0, le=100000, strict=True)

    @field_validator("section", mode="before")
    @classmethod
    def section_number(cls, value):
        if type(value) is not int:
            raise ValueError("본문 구역 번호가 올바르지 않습니다.")
        return value

    @model_validator(mode="after")
    def address(self):
        if (self.kind == "table") != (self.control is not None):
            raise ValueError("디자인을 적용할 문단 또는 표 위치가 올바르지 않습니다.")
        return self


class DesignContext(Input):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target: DesignTarget | None
    target_text: str = Field(default="", max_length=8000)
    target_text_truncated: bool = Field(default=False, strict=True)
    target_is_title_frame: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def target_details(self):
        if self.target is None and (self.target_text or self.target_text_truncated or self.target_is_title_frame):
            raise ValueError("선택 위치가 없는 요청에는 선택 내용을 포함할 수 없습니다.")
        if self.target_is_title_frame and self.target.kind != "table":
            raise ValueError("제목 상자 표 정보가 올바르지 않습니다.")
        return self


class TurnInput(Input):
    id: uuid.UUID
    base_version: str = Field(min_length=1, max_length=36)
    message: str = Field(min_length=1, max_length=12000)
    document_context: DocumentContext
    selection: dict | None = None
    design_context: DesignContext | None = None
    rewrite_context: RewriteContext | None = None

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


class DesignSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["design", "standard"]
    id: str = Field(min_length=1, max_length=120)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=600)

    @field_validator("reason")
    @classmethod
    def visible(cls, value):
        if not value.strip() or any(unicodedata.category(c) in ("Cs", "Cc") and c not in "\n\r\t" for c in value):
            raise ValueError("유효한 제안 설명이 필요합니다.")
        return value


class AssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str = Field(min_length=1, max_length=12000)
    rewrite_instruction: str | None = Field(default=None, min_length=1, max_length=2000)
    design_suggestions: list[DesignSuggestion] = Field(default_factory=list, max_length=3)
    document_rewrites: list[ParagraphRewrite] = Field(default_factory=list, max_length=120)
    table_layout: TableLayout | None = None

    @field_validator("answer", "rewrite_instruction")
    @classmethod
    def visible(cls, value):
        if value is not None and (not value.strip() or any(unicodedata.category(c) in ("Cs", "Cc") and c not in "\n\r\t" for c in value)):
            raise ValueError("유효한 답변 텍스트가 필요합니다.")
        return value


def request_payload(value):
    # Omission preserves accepted v1 request fingerprints and retry semantics.
    result = value.model_dump(mode="json")
    if result["rewrite_context"] is None:
        result.pop("rewrite_context")
    else:
        for paragraph, incoming in zip(result["rewrite_context"]["paragraphs"], value.rewrite_context.paragraphs):
            for optional in ("protected_text", "rendered_lines"):
                if optional not in incoming.model_fields_set:
                    paragraph.pop(optional, None)
    if result["design_context"] is None:
        result.pop("design_context")
    else:
        result["design_context"] = value.design_context.model_dump(mode="json", exclude_unset=True)
        target = result["design_context"]["target"]
        if target is not None and target.get("control") is None:
            target.pop("control", None)
    return result


def design_catalog(db, user, context):
    from .editor_designs import editor_design_catalog
    catalog = editor_design_catalog(db, user)
    target = context.get("target")
    expected = "table" if target and target["kind"] == "table" else "title" if target else None
    allowed_kinds = {expected}
    if context.get("target_is_title_frame"):
        allowed_kinds = {"title"}
    choices, size = [], 0
    for category, items in (("design", catalog["designs"]), ("standard", catalog["standards"])):
        for item in items[:40]:
            if category == "design" and item["kind"] not in allowed_kinds:
                continue
            # Only presentation facts help select a design. Do not send the
            # complete standard (or arbitrary persisted fields) to the model.
            prefixes = ("table_",) if item.get("kind") == "table" else ("title_",)
            if category == "standard":
                prefixes = ("title_", "body_", "square_", "table_", "page_")
            summary = {key: value for key, value in item["settings"].items() if
                key.startswith(prefixes) or category == "standard" and key in ("preset_id", "cover_mode", "toc_mode")}
            choice = {"kind": category, "id": item["id"], "sha256": item["sha256"],
                "name": item["name"], "design_kind": item.get("kind") if category == "design" else None,
                "settings": summary}
            length = len(canonical(choice).encode("utf-8"))
            if size + length > 64000:
                continue
            choices.append(choice)
            size += length
    return choices


def validated_design_proposals(db, user, payload, suggestions):
    if not suggestions:
        return []
    context = payload.get("design_context")
    if not context:
        raise ValueError("문서 디자인 정보가 없는 요청에는 디자인을 제안할 수 없습니다.")
    frozen = {(item["kind"], item["id"], item["sha256"]): item for item in payload.get("design_catalog", [])}
    current = {(item["kind"], item["id"], item["sha256"]) for item in design_catalog(db, user, context)}
    result, seen = [], set()
    for suggestion in suggestions:
        key = (suggestion.kind, suggestion.id, suggestion.sha256)
        item = frozen.get(key)
        if item is None or key not in current:
            raise ValueError("제안한 디자인이 요청 당시 목록과 다르거나 더 이상 사용할 수 없습니다.")
        if key in seen:
            raise ValueError("같은 디자인을 중복 제안할 수 없습니다.")
        seen.add(key)
        target = {"kind": "document", "section": 0} if suggestion.kind == "standard" else context["target"]
        allowed_kinds = {"table" if target and target["kind"] == "table" else "title"}
        if context.get("target_is_title_frame"):
            allowed_kinds = {"title"}
        if not target or (suggestion.kind == "design" and item["design_kind"] not in allowed_kinds):
            raise ValueError("디자인 종류와 현재 선택 위치가 다릅니다.")
        target = {key: value for key, value in target.items() if value is not None}
        result.append({**suggestion.model_dump(), "name": item["name"], "design_kind": item["design_kind"], "target": target})
    return result


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
    if execution.get("provider") == "codex":
        from .account_settings import registered_codex_allowed
        if not registered_codex_allowed(row.requested_by, db.get_bind()):
            raise HTTPException(403, "계정 또는 AI 이용 권한이 변경되어 답변 생성을 중단했습니다.")
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
            if payload.get("rewrite_context") and value.rewrite_instruction is not None:
                raise ValueError("문서 전체 수정은 고정된 본문 수정안으로 반환해야 합니다.")
            proposals = validated_design_proposals(db, row.requested_by, payload, value.design_suggestions)
            rewrites = validated_rewrites(payload, value.document_rewrites)
            layout = validated_layout(payload, value.table_layout.model_dump() if value.table_layout else None)
            row.response, row.status = canonical({**value.model_dump(), "design_proposals": proposals, "document_rewrites": rewrites, "table_layout": layout}), "ready"
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
        "design_proposals": result.get("design_proposals", []), "design_context": payload.get("design_context"),
        "rewrite_context": payload.get("rewrite_context"), "document_rewrites": result.get("document_rewrites", []),
        "table_layout": result.get("table_layout"),
        "selection": payload["selection"], "document_context": {k: context[k] for k in ("sha256", "truncated", "revision", "epoch")} | {"characters": len(context["text"])},
        "error": row.error or ("응답 제한 시간이 지났습니다. 같은 질문을 자동으로 다시 실행하지 않습니다." if status(row) == "expired" else ""),
        "execution": {k: v for k, v in json.loads(row.execution).items() if not k.startswith("_")},
        "created_at": row.created_at, "stale": doc.current_version != row.base_version,
        "can_cancel": status(row) == "running" and role != "reader" and (row.requested_by == user or role == "owner")}


def install_document_assistant(app, check_lease):
    engine = app.state.engine

    from .block_contract import BlockInput, MAX_BASE64, decoded
    from .document_blocks import bounded_input
    from starlette.concurrency import run_in_threadpool

    class RewriteCheck(BlockInput):
        base_version: str = Field(min_length=1, max_length=36)

    class LayoutSource(RewriteCheck):
        source_base64: str = Field(min_length=1, max_length=MAX_BASE64)

    class RewriteVerify(LayoutSource):
        engine_output_base64: str = Field(min_length=1, max_length=MAX_BASE64)

    def checked_rewrite(doc_id, base_version, user, token, turn_id=None):
        with transaction(engine) as db:
            doc = require_access(db, doc_id, user, "editor", lock=True, fresh=True)
            require_lease_owner(db, doc, user, fresh=True)
            check_lease(doc, token)
            if doc.current_version != base_version:
                raise HTTPException(409, "문서의 저장 기준이 변경됐습니다. 현재 문서로 다시 요청하세요.")
            if turn_id is None:
                return None
            row = db.get(ConversationTurn, turn_id)
            if not row or row.document_id != doc_id or row.requested_by != user:
                raise HTTPException(404, "현재 사용자의 수정 요청을 찾을 수 없습니다.")
            if row.status != "ready" or row.base_version != base_version:
                raise HTTPException(409, "완료된 현재 문서 수정안만 적용할 수 있습니다.")
            payload, result = json.loads(row.input), json.loads(row.response)
            changes = validated_rewrites(payload, result.get("document_rewrites", []))
            layout = validated_layout(payload, result.get("table_layout"))
            if not changes and not layout:
                raise HTTPException(409, "적용할 전체 문단 수정안이 없습니다.")
            return payload["rewrite_context"], changes, layout

    @app.post("/api/documents/{doc_id}/assistant-layout-context")
    async def layout_context(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, LayoutSource)
        checked_rewrite(doc_id, value.base_version, request.state.user, x_edit_session)
        source = decoded(value.source_base64)
        result = await run_in_threadpool(table_targets, source)
        checked_rewrite(doc_id, value.base_version, request.state.user, x_edit_session)
        return {"source_sha256": digest(source), "table_targets": result}

    @app.post("/api/assistant-turns/{turn_id}/layout-preview")
    async def layout_preview(turn_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, LayoutSource)
        with Session(engine) as db:
            row = db.get(ConversationTurn, turn_id)
            if not row:
                raise HTTPException(404, "질문을 찾을 수 없습니다.")
            doc_id = row.document_id
        context, _, layout = checked_rewrite(doc_id, value.base_version, request.state.user, x_edit_session, turn_id)
        output = await run_in_threadpool(apply_layout, decoded(value.source_base64), context, layout)
        checked_rewrite(doc_id, value.base_version, request.state.user, x_edit_session, turn_id)
        return {"source_sha256": context["source_sha256"], "output_base64": base64.b64encode(output).decode(), "output_sha256": digest(output)}

    @app.post("/api/assistant-turns/{turn_id}/rewrite-check")
    async def verify_document_rewrite(turn_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, RewriteVerify)
        with Session(engine) as db:
            row = db.get(ConversationTurn, turn_id)
            if not row:
                raise HTTPException(404, "수정 요청을 찾을 수 없습니다.")
            doc_id = row.document_id
        context, changes, layout = checked_rewrite(doc_id, value.base_version, request.state.user, x_edit_session, turn_id)
        source, output = decoded(value.source_base64), decoded(value.engine_output_base64)
        await run_in_threadpool(verify_rewrite_output, source, output, context, changes, layout)
        checked_rewrite(doc_id, value.base_version, request.state.user, x_edit_session, turn_id)
        return {"verified": True, "source_sha256": digest(source), "output_sha256": digest(output), "turn_id": turn_id}

    @app.post("/api/documents/{doc_id}/assistant-rewrite-check")
    async def check_document_rewrite(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, RewriteCheck)
        checked_rewrite(doc_id, value.base_version, request.state.user, x_edit_session)
        return {"ok": True, "base_version": value.base_version}

    @app.get("/api/document-assistant-provider")
    def provider_info(request: Request):
        try:
            provider, settings = configured_provider()
            allowed = llm_allowed(request.state.user) if provider == "llm" else codex_allowed(request.state.user)
            from .capabilities import codex_feature
            state = codex_feature(request.state.user) if provider == "codex" else None
            configured = state["configured"] if state else True
            reason = "이 사용자는 서버의 AI 연결을 실행할 권한이 없습니다." if provider == "llm" else "이 서버에 연결된 Codex 계정의 사용자만 질문할 수 있습니다."
            return {"provider": provider, "model": settings.model, "configured": configured, "allowed": allowed and configured,
                "reason": state["message"] if state and not configured else "" if allowed else reason}
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
                fingerprint = digest(canonical(request_payload(value)).encode())
                old = db.get(ConversationTurn, str(value.id))
                role = document_role(db, doc.id, request.state.user)
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
                if provider == "codex" and not codex_allowed(request.state.user):
                    raise HTTPException(403, "이 서버에 연결된 Codex 계정의 사용자만 질문할 수 있습니다.")
                if provider == "codex":
                    require_codex_ready(settings)
                history = list(db.scalars(select(ConversationTurn).where(ConversationTurn.conversation_id == thread_id,
                    ConversationTurn.status == "ready").order_by(ConversationTurn.created_at.desc()).limit(10)))
                payload = {**request_payload(value), "_assistant_contract": CONTRACT, "document_name": doc.name, "history": [
                    {"message": json.loads(turn.input)["message"], "answer": json.loads(turn.response)["answer"],
                     "base_version": turn.base_version} for turn in reversed(history)]}
                if value.design_context is not None:
                    payload["design_catalog"] = design_catalog(db, request.state.user, payload["design_context"])
                encoded = canonical(payload)
                row = ConversationTurn(id=str(value.id), conversation_id=thread_id, document_id=doc.id,
                    requested_by=request.state.user, base_version=value.base_version, input=encoded,
                    input_sha256=digest(encoded.encode()), request_sha256=fingerprint, status="running", response=None,
                    execution=canonical({"provider": provider, "model": settings.model, "contract": CONTRACT,
                        "completed": False, "outcome": "running", "input_sha256": digest(encoded.encode()),
                        "history_turns": len(history), "_lease_sha256": digest(x_edit_session.encode())}), error="", created_at=time.time())
                slot.reserve()
                if provider == "codex":
                    reserve_codex_usage(db, "assistant:" + str(value.id), request.state.user)
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
            role = document_role(db, doc.id, request.state.user)
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
            return public_turn(row, doc, request.state.user, document_role(db, doc.id, request.state.user))

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
            return public_turn(row, doc, request.state.user, document_role(db, doc.id, request.state.user))
