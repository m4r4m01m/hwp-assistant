"""Document-scoped conversations, app-owned context, proposals and HWPX templates."""
import json
import time
import uuid
from typing import Literal
from fastapi import HTTPException, Request, Header, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .auth import require_access, require_lease_owner
from .db import Access, ChangeSet, Context, Conversation, Document, LeaseOwner, Message, Proposal, Template, TemplateCatalog, Version, transaction
from .hwpx import digest
from .llm import OpenAICompatible
from .ai_requests import AIRequest, reserve_chat, finish_chat
from .capabilities import require_llm_access
from .rewrite_capacity import ExecutionSlot
from .rewrite_context import ConfirmedFactInput, confirmed_content
from .template_market import Category, install_template_market, registration_hash, source_version, verified_bytes


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Grant(Input):
    user_id: str = Field(pattern=r"^[A-Za-z0-9_.@-]{1,80}$")
    role: Literal["reader", "editor"]


class NewThread(Input):
    id: uuid.UUID
    title: str = Field(min_length=1, max_length=120)
    visibility: Literal["document", "private"] = "document"


class NewMessage(Input):
    request_id: str = Field(min_length=1, max_length=60)
    content: str = Field(min_length=1, max_length=12000)


class NewContext(Input):
    expected_revision: int = Field(ge=0)
    base_version: str
    purpose: str = Field(max_length=4000, default="")
    facts: str = Field(max_length=12000, default="")
    selection: dict | None = None

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.selection is not None:
            if len(json.dumps(self.selection)) > 100000:
                raise ValueError("선택 컨텍스트 크기 초과")
            try:
                s = self.selection
                valid = (isinstance(s["revision"], int) and s["revision"] >= 0
                         and s["selection"]["editable"] and s["selection"]["target"]
                         and isinstance(s["state"]["documentEpoch"], int)
                         and s["evidence"]["textSha256"] == digest(s["evidence"]["text"].encode())
                         and len(s["evidence"]["text"]) <= 12000
                         and s["evidence"]["formatSha256"] and s["evidence"]["adjacentContextSha256"])
                if not valid:
                    raise ValueError("invalid selection")
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise ValueError("선택 원문/편집기 증거가 올바르지 않습니다.") from exc
        return self


class NewProposal(Input):
    context_revision: int = Field(ge=1)
    replacement: str = Field(min_length=1, max_length=12000)
    reason: str = Field(min_length=1, max_length=2000)


class ContextUpdate(NewContext):
    request_id: uuid.UUID | None = None
    writing_criteria: str = Field(default="", max_length=4000)
    confirmed_facts: list[ConfirmedFactInput] = Field(default_factory=list, max_length=20)
    confirm_for_rewrite: bool = False

    @model_validator(mode="before")
    @classmethod
    def valid_encoding(cls, value):
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > 200000:
                raise ValueError("too large")
        except (UnicodeError, ValueError, TypeError) as exc:
            raise HTTPException(422, "작성 정보는 200KB 이내의 유효한 UTF-8 JSON이어야 합니다.") from exc
        return value


class NewTemplate(Input):
    id: uuid.UUID | None = None
    category: Category = "기타"
    document_id: str
    version_id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def valid_encoding(cls, value):
        try:
            json.dumps(value, ensure_ascii=False).encode("utf-8")
        except (UnicodeError, TypeError) as exc:
            raise HTTPException(422, "템플릿 정보에 유효한 UTF-8 문자를 사용하세요.") from exc
        return value

    @model_validator(mode="after")
    def tag_limits(self):
        if any(not t.strip() or len(t) > 40 for t in self.tags):
            raise ValueError("태그는 1~40자여야 합니다.")
        return self


class ApplyTemplate(Input):
    document_id: uuid.UUID
    name: str = Field(min_length=1, max_length=235)
    request_id: str = Field(min_length=1, max_length=40)


def fields(row, *keys):
    return {key: getattr(row, key) for key in keys}


def install_collaboration(app, locked, check_lease, publish, doc_dict, version_dict):
    from .rewrite_jobs import invalidate_running
    engine, folder = app.state.engine, app.state.folder
    install_template_market(app, locked)

    def thread_access(db, thread_id, user, role="reader", lock=False):
        thread = db.get(Conversation, thread_id)
        if not thread:
            raise HTTPException(404, "대화를 찾을 수 없습니다.")
        doc = require_access(db, thread.document_id, user, role, lock=lock)
        return thread, doc

    def context_value(db, thread, doc):
        ctx = db.get(Context, thread.id)
        return {"revision": ctx.revision if ctx else 0,
                "base_version": ctx.base_version if ctx else doc.current_version,
                "stale": bool(ctx and ctx.base_version != doc.current_version),
                "purpose": "", "facts": "", "selection": None, "writing_criteria": "",
                "confirmed_facts": [], "confirmation": None,
                **({k: v for k, v in json.loads(ctx.content).items() if not k.startswith("_")} if ctx else {})}

    def messages_value(db, thread_id, limit=100):
        rows = list(db.scalars(select(Message).where(Message.conversation_id == thread_id)
                             .order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)))
        return [fields(m, "id", "author_id", "role", "content", "source", "created_at") for m in reversed(rows)]

    @app.get("/api/documents/{doc_id}")
    def get_document(doc_id: str, request: Request):
        with Session(engine) as db:
            doc = require_access(db, doc_id, request.state.user)
            return {**doc_dict(doc), "role": db.get(Access, (doc_id, request.state.user)).role}

    @app.get("/api/documents/{doc_id}/access")
    def access_list(doc_id: str, request: Request):
        with Session(engine) as db:
            require_access(db, doc_id, request.state.user, "owner")
            return [fields(a, "user_id", "role") for a in db.scalars(select(Access).where(Access.document_id == doc_id))]

    @app.put("/api/documents/{doc_id}/access")
    def grant(doc_id: str, value: Grant, request: Request):
        if value.user_id not in app.state.users:
            raise HTTPException(422, "서버에 등록된 사용자에게만 권한을 부여할 수 있습니다.")
        with transaction(engine) as db:
            doc = locked(db, doc_id, request.state.user, "owner")
            row = db.get(Access, (doc_id, value.user_id))
            if row and row.role == "owner":
                raise HTTPException(409, "소유자 권한은 변경할 수 없습니다.")
            lost_edit_access = row is not None and row.role == "editor" and value.role == "reader"
            if lost_edit_access:
                invalidate_running(db, doc.id, "편집 권한이 회수되어 실행 결과를 게시하지 않습니다.", user=value.user_id)
                owner = db.get(LeaseOwner, doc.id)
                if owner and owner.user_id == value.user_id:
                    doc.lease_token, doc.lease_until = None, 0
            if row:
                row.role = value.role
            else:
                db.add(Access(document_id=doc_id, **value.model_dump()))
        return {"ok": True}

    @app.delete("/api/documents/{doc_id}/access/{user_id}")
    def revoke(doc_id: str, user_id: str, request: Request):
        with transaction(engine) as db:
            doc = locked(db, doc_id, request.state.user, "owner")
            row = db.get(Access, (doc_id, user_id))
            if row and row.role == "owner":
                raise HTTPException(409, "소유자 권한은 삭제할 수 없습니다.")
            if row:
                if row.role == "editor":
                    invalidate_running(db, doc.id, "편집 권한이 회수되어 실행 결과를 게시하지 않습니다.", user=user_id)
                owner = db.get(LeaseOwner, doc.id)
                if owner and owner.user_id == user_id:
                    doc.lease_token, doc.lease_until = None, 0
                db.delete(row)
        return {"ok": True}

    @app.get("/api/documents/{doc_id}/versions/{version_id}/changes")
    def changes(doc_id: str, version_id: str, request: Request):
        with Session(engine) as db:
            require_access(db, doc_id, request.state.user)
            v = db.get(Version, version_id)
            if not v or v.document_id != doc_id:
                raise HTTPException(404, "버전을 찾을 수 없습니다.")
            change = db.get(ChangeSet, version_id)
            return {"version": version_dict(v), "base_sha256": change.base_sha256 if change else None,
                    "entries": json.loads(change.entries) if change else [], "recorded": change is not None}

    @app.get("/api/documents/{doc_id}/threads")
    def threads(doc_id: str, request: Request):
        with Session(engine) as db:
            require_access(db, doc_id, request.state.user)
            rows = db.scalars(select(Conversation).where(Conversation.document_id == doc_id)
                  .order_by(Conversation.created_at))
            return [fields(t, "id", "document_id", "owner_id", "visibility", "title", "created_at") for t in rows]

    @app.post("/api/documents/{doc_id}/threads")
    def new_thread(doc_id: str, value: NewThread, request: Request):
        with transaction(engine) as db:
            locked(db, doc_id, request.state.user)
            existing = db.get(Conversation, str(value.id))
            if existing:
                if (existing.document_id, existing.owner_id, existing.title) != (
                        doc_id, request.state.user, value.title):
                    raise HTTPException(409, "이미 사용된 대화 ID입니다.")
                return fields(existing, "id", "title", "visibility")
            row = Conversation(id=str(value.id), document_id=doc_id, owner_id=request.state.user,
                               title=value.title, visibility="document", created_at=time.time())
            db.add(row)
            result = fields(row, "id", "title", "visibility")
        return result

    @app.get("/api/threads/{thread_id}")
    def get_thread(thread_id: str, request: Request, limit: int = Query(100, ge=1, le=200)):
        with Session(engine) as db:
            thread, doc = thread_access(db, thread_id, request.state.user)
            return {**fields(thread, "id", "document_id", "title", "visibility", "owner_id"),
                    "context": context_value(db, thread, doc), "messages": messages_value(db, thread_id, limit)}

    def append_message(db, thread_id, user, value, source, role="user"):
        old = db.scalar(select(Message).where(Message.conversation_id == thread_id, Message.request_id == value.request_id))
        if old:
            if (old.author_id, old.content, old.source) != (user, value.content, source):
                raise HTTPException(409, "같은 요청 ID의 메시지 내용이 다릅니다.")
            return old
        row = Message(id=str(uuid.uuid4()), conversation_id=thread_id, author_id=user, role=role,
                      content=value.content, source=source, request_id=value.request_id, created_at=time.time())
        db.add(row)
        return row

    @app.post("/api/threads/{thread_id}/messages")
    def add_message(thread_id: str, value: NewMessage, request: Request):
        with transaction(engine) as db:
            thread_access(db, thread_id, request.state.user, "editor", lock=True)
            row = append_message(db, thread_id, request.state.user, value, "user")
            result = fields(row, "id", "content", "source")
        return result

    @app.post("/api/threads/{thread_id}/external-messages")
    def external_message(thread_id: str, value: NewMessage, request: Request):
        with transaction(engine) as db:
            thread_access(db, thread_id, request.state.user, "editor", lock=True)
            row = append_message(db, thread_id, request.state.user, value, "codex", "assistant")
            result = fields(row, "id", "content", "source")
        return result

    @app.put("/api/threads/{thread_id}/context")
    def put_context(thread_id: str, value: ContextUpdate, request: Request):
        with transaction(engine) as db:
            thread, doc = thread_access(db, thread_id, request.state.user, "editor", lock=True)
            row = db.get(Context, thread_id)
            write_hash = digest(json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode())
            previous_write = json.loads(row.content).get("_last_write") if row else None
            if value.request_id and previous_write and previous_write["id"] == str(value.request_id):
                if previous_write["by"] != request.state.user or previous_write["sha256"] != write_hash:
                    raise HTTPException(409, "같은 작성 정보 저장 ID의 내용 또는 사용자가 다릅니다.")
                return {"revision": row.revision, "base_version": row.base_version}
            if doc.current_version != value.base_version:
                raise HTTPException(409, "컨텍스트 기준 버전이 변경되었습니다.")
            revision = row.revision if row else 0
            if revision != value.expected_revision:
                raise HTTPException(409, "다른 사용자가 컨텍스트를 변경했습니다. 다시 불러오세요.")
            content_value = confirmed_content(db, doc, request.state.user, value)
            if value.request_id:
                content_value["_last_write"] = {"id": str(value.request_id), "by": request.state.user, "sha256": write_hash}
            content = json.dumps(content_value, ensure_ascii=False)
            if row:
                row.revision, row.base_version, row.content, row.updated_at = revision + 1, value.base_version, content, time.time()
            else:
                db.add(Context(conversation_id=thread_id, revision=1, base_version=value.base_version,
                               content=content, updated_at=time.time()))
            result = {"revision": revision + 1, "base_version": value.base_version}
        return result

    class Chat(Input):
        message_id: str

    def chat_question(db, thread_id, message_id, user, *, lock=False):
        thread, doc = thread_access(db, thread_id, user, "editor", lock=lock)
        message = db.get(Message, message_id)
        if not message or message.conversation_id != thread_id or message.author_id != user or message.role != "user":
            raise HTTPException(404, "질문을 찾을 수 없습니다.")
        existing = db.scalar(select(Message).where(Message.conversation_id == thread_id,
                                                 Message.request_id == "ai:" + message.id))
        return thread, doc, message, existing

    @app.post("/api/threads/{thread_id}/chat")
    async def chat(thread_id: str, value: Chat, request: Request):
        require_llm_access(request.state.user)
        # Reading a completed response consumes no model capacity or execution quota.
        # Repeat every access/author check, including when a previous caller had access.
        with Session(engine) as db:
            _, _, _, existing = chat_question(db, thread_id, value.message_id, request.state.user)
            if existing:
                return fields(existing, "id", "content", "source")
        with ExecutionSlot(app.state.rewrite_capacity) as slot:
            slot.reserve()
            with app.state.ai_budget.reserve(request.state.user):
                with transaction(engine) as db:
                    thread, doc, message, existing = chat_question(db, thread_id, value.message_id, request.state.user, lock=True)
                    if existing:
                        return fields(existing, "id", "content", "source")
                    ctx = context_value(db, thread, doc)
                    if ctx["stale"]:
                        raise HTTPException(409, "컨텍스트가 오래되었습니다. 최신 기준으로 저장하세요.")
                    history = messages_value(db, thread_id, 20)
                    base = doc.current_version
                    provider = OpenAICompatible()
                    key = digest((thread_id + ":" + message.id).encode())
                    reserve_chat(db, key, request.state.user)
                try:
                    answer = await provider.chat(ctx, history)
                    with transaction(engine) as db:
                        thread, doc = thread_access(db, thread_id, request.state.user, "editor", lock=True)
                        current = context_value(db, thread, doc)
                        if doc.current_version != base or current["revision"] != ctx["revision"]:
                            raise HTTPException(409, "응답 중 문서/컨텍스트가 바뀌었습니다. 다시 질문하세요.")
                        row = append_message(db, thread_id, request.state.user,
                              NewMessage(request_id="ai:" + value.message_id, content=answer), "model", "assistant")
                        claim = db.get(AIRequest, key)
                        claim.status, claim.finished_at = "completed", time.time()
                        result = fields(row, "id", "content", "source")
                    return result
                except BaseException:
                    # Do not automatically repeat a model call whose outcome is uncertain.
                    finish_chat(engine, key, "failed")
                    raise

    @app.post("/api/threads/{thread_id}/proposals")
    def propose(thread_id: str, value: NewProposal, request: Request):
        with transaction(engine) as db:
            thread, doc = thread_access(db, thread_id, request.state.user, "editor", lock=True)
            ctx = context_value(db, thread, doc)
            if ctx["revision"] != value.context_revision or ctx["stale"] or not ctx["selection"]:
                raise HTTPException(409, "최신 문단 선택 컨텍스트가 필요합니다.")
            row = Proposal(id=str(uuid.uuid4()), conversation_id=thread_id, context_revision=value.context_revision,
                           base_version=doc.current_version, snapshot=json.dumps(ctx["selection"]),
                           replacement=value.replacement, reason=value.reason,
                           author_id=request.state.user, created_at=time.time())
            db.add(row)
            result = {"id": row.id, "status": "pending_user_approval"}
        return result

    @app.get("/api/threads/{thread_id}/proposals")
    def proposals(thread_id: str, request: Request):
        with Session(engine) as db:
            thread, doc = thread_access(db, thread_id, request.state.user)
            ctx = context_value(db, thread, doc)
            rows = db.scalars(select(Proposal).where(Proposal.conversation_id == thread_id)
                              .order_by(Proposal.created_at.desc()).limit(30))
            return [{**fields(p, "id", "replacement", "reason", "author_id", "base_version", "created_at"),
                     "snapshot": json.loads(p.snapshot),
                     "stale": p.base_version != doc.current_version or p.context_revision != ctx["revision"]} for p in rows]

    @app.post("/api/threads/{thread_id}/proposals/{proposal_id}/check")
    def check_proposal(thread_id: str, proposal_id: str, request: Request, x_edit_session: str = Header()):
        with transaction(engine) as db:
            thread, doc = thread_access(db, thread_id, request.state.user, "editor", lock=True)
            require_lease_owner(db, doc, request.state.user)
            check_lease(doc, x_edit_session)
            row = db.get(Proposal, proposal_id)
            ctx = context_value(db, thread, doc)
            if not row or row.conversation_id != thread_id:
                raise HTTPException(404, "제안을 찾을 수 없습니다.")
            if row.base_version != doc.current_version or row.context_revision != ctx["revision"]:
                raise HTTPException(409, "오래된 제안입니다. 문단을 다시 공유하세요.")
            return {"snapshot": json.loads(row.snapshot), "replacement": row.replacement}

    @app.post("/api/templates")
    def register_template(value: NewTemplate, request: Request):
        template_id = str(value.id or uuid.uuid4())
        with transaction(engine) as db:
            locked(db, value.document_id, request.state.user)
            if engine.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                           {"key": int(digest(("template:" + template_id).encode())[:15], 16)})
            old = db.get(Template, template_id)
            if old and old.document_id != value.document_id:
                raise HTTPException(404, "템플릿 등록 ID를 사용할 수 없습니다.")
            version = db.get(Version, value.version_id)
            if not version or version.document_id != value.document_id:
                raise HTTPException(404, "템플릿 기준 버전을 찾을 수 없습니다.")
            verified_bytes(folder, version)
            frozen_hash = registration_hash(value)
            if old:
                catalog = db.get(TemplateCatalog, old.id)
                if not catalog or catalog.registration_sha256 != frozen_hash:
                    raise HTTPException(409, "같은 템플릿 등록 ID의 입력이 다릅니다.")
                return {"id": old.id, "version_id": old.version_id}
            row = Template(id=template_id, **value.model_dump(exclude={"tags", "id", "category"}),
                           tags=json.dumps(value.tags, ensure_ascii=False), created_at=time.time())
            db.add(row)
            db.flush()
            db.add(TemplateCatalog(template_id=row.id, category=value.category, revision=1,
                                   registration_sha256=frozen_hash))
            result = {"id": row.id, "version_id": row.version_id}
        return result

    @app.get("/api/templates")
    def recommend(request: Request, q: str = Query("", max_length=200)):
        with Session(engine) as db:
            rows = db.scalars(select(Template).join(Access, Access.document_id == Template.document_id)
                             .where(Access.user_id == request.state.user))
            results = []
            for row in rows:
                tags = json.loads(row.tags)
                haystack = (row.name + " " + row.description + " " + " ".join(tags)).casefold()
                matched = [term for term in q.casefold().split() if term in haystack]
                score = len(matched)
                if q.strip() and not score:
                    continue
                results.append({**fields(row, "id", "name", "description", "document_id", "version_id"),
                                "tags": tags, "score": score,
                                "reason": "일치: " + ", ".join(matched) if matched else "사용 가능한 문서 양식"})
            return sorted(results, key=lambda r: (-r["score"], r["name"], r["id"]))[:50]

    @app.post("/api/templates/{template_id}/apply")
    def apply_template(template_id: str, value: ApplyTemplate, request: Request):
        destination = str(value.document_id)
        with transaction(engine) as db:
            template = db.get(Template, template_id)
            if not template:
                raise HTTPException(404, "템플릿을 찾을 수 없습니다.")
            # The template always inherits current source-document read access.
            locked(db, template.document_id, request.state.user, "reader")
            version = source_version(db, template)
            data = verified_bytes(folder, version)
            if engine.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(digest(destination.encode())[:15], 16)})
            doc = db.get(Document, destination)
            # Bind retries to both template and client request, within Version's 64-character key.
            key = digest((template_id + ":" + value.request_id).encode())
            if doc:
                require_access(db, destination, request.state.user, "owner", lock=True)
                if not db.scalar(select(Version).where(Version.document_id == destination, Version.request_id == key)):
                    raise HTTPException(409, "다른 문서 ID로 템플릿을 적용하세요.")
            else:
                doc = Document(id=destination, name=value.name if value.name.endswith(".hwpx") else value.name + ".hwpx",
                               created_at=time.time(), lease_until=0)
                db.add(doc)
                db.flush()
                db.add(Access(document_id=destination, user_id=request.state.user, role="owner"))
            publish(db, doc, data, None, None, key, "upload", author_id=request.state.user)
            result = {**doc_dict(doc), "role": "owner", "template_id": template.id, "source_version": version.id}
        return result
