"""Durable concurrent editing sessions with optimistic, non-overlapping HWPX merges."""
import secrets
import time
import uuid
import threading
from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import ForeignKey, String, Float, select, delete
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, Document, Version, transaction
from .auth import require_access, lease_login_context, bind_lease
from .hwpx import digest, validate, loss_warnings
from .coedit_merge import merge_hwpx
from .rewrite_jobs import invalidate_running

TTL = 120
MAX_BYTES = 10 * 1024 * 1024


class Room(Base):
    __tablename__ = "coedit_rooms"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    generation: Mapped[str] = mapped_column(String(36))
    started_at: Mapped[float] = mapped_column(Float)


class Member(Base):
    __tablename__ = "coedit_members"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    generation: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str] = mapped_column(String(80))
    login_hash: Mapped[str] = mapped_column(String(64))
    token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float)


class Receipt(Base):
    __tablename__ = "coedit_receipts"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64))
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"))


class Join(BaseModel):
    request_id: uuid.UUID


class LivePage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_version: uuid.UUID
    sequence: int = Field(ge=0, le=2**53-1)
    typing: bool = False
    draft: bool = False
    page: int = Field(default=1, ge=1, le=10000)
    svg: str | None = Field(default=None, max_length=600_000)
    paragraph: str | None = Field(default=None, max_length=2000)
    baseline_text: str | None = Field(default=None, max_length=20000)
    watch: uuid.UUID | None = None
    watch_page: int | None = Field(default=None, ge=1, le=10000)
    rendered_page: int | None = Field(default=None, ge=1, le=10000)
    page_count: int | None = Field(default=None, ge=1, le=10000)
    revision: int = Field(default=0, ge=0, le=2**53-1)


def install_coedit(app, publish, doc_dict):
    engine, folder = app.state.engine, app.state.folder
    # Ephemeral page previews, never document versions or an AI input. A bounded
    # cache fits the supported single API worker and disappears on restart.
    live_pages = {}
    live_lock = threading.Lock()

    @app.post("/api/documents/{doc_id}/coedit/live")
    def live(doc_id: str, value: LivePage, request: Request, x_coedit_session: str = Header()):
        with transaction(engine) as db:
            doc, room, member = current(db, doc_id, request, x_coedit_session)
            base = db.get(Version, str(value.base_version))
            if not base or base.document_id != doc.id:
                raise HTTPException(409, "작성 화면의 기준 문서가 바뀌었습니다.")
            now = time.time()
            active = {entry.id: entry for entry in members(db, doc, room)}
            with live_lock:
                for key, item in list(live_pages.items()):
                    if item["updated_at"] < now - 6:
                        del live_pages[key]
                key = (doc.id, room.generation, member.id)
                old = live_pages.get(key)
                if not old or value.sequence > old["sequence"]:
                    if len(live_pages) >= 100 and key not in live_pages:
                        raise HTTPException(429, "작성 화면 공유가 혼잡합니다. 잠시 후 다시 연결합니다.")
                    showing = value.typing or value.draft or value.svg is not None
                    count = max(value.page, value.page_count or value.page)
                    rendered = value.rendered_page or value.page
                    if rendered > count:
                        raise HTTPException(422, "미리보기 쪽 번호가 전체 쪽 수를 넘었습니다.")
                    frames = dict(old.get("frames", {})) if old and old["base_version"] == str(value.base_version) and old.get("revision") == value.revision else {}
                    if showing and value.svg:
                        frames.pop(rendered, None)
                        frames[rendered] = {"svg": value.svg, "paragraph": value.paragraph, "baseline_text": value.baseline_text}
                    elif not showing:
                        frames = {}
                    frames = {page: frame for page, frame in frames.items() if page <= count}
                    # Keep the original per-participant SVG budget across pages.
                    while len(frames) > 4 or sum(len(frame["svg"]) for frame in frames.values()) > 600_000:
                        frames.pop(next(iter(frames)))
                    live_pages[key] = {"sequence": value.sequence, "typing": value.typing, "draft": value.draft,
                        "page": value.page, "svg": value.svg if showing else None,
                        "paragraph": value.paragraph if showing else None, "baseline_text": value.baseline_text if showing else None,
                        "base_version": str(value.base_version), "updated_at": now, "frames": frames,
                        "revision": value.revision, "page_count": count, "watch": str(value.watch) if value.watch else None,
                        "watch_page": value.watch_page}
                peers = []
                for identity, entry in active.items():
                    item = live_pages.get((doc.id, room.generation, identity))
                    if identity != member.id and item:
                        peers.append({"id": identity, "user_id": entry.user_id,
                            "typing": item["typing"], "draft": item["draft"], "page": item["page"],
                            "page_count": item["page_count"], "available": bool(item["frames"])})
                watched = next((peer for peer in peers if peer["id"] == str(value.watch)), None)
                preview = live_pages.get((doc.id, room.generation, str(value.watch))) if watched else None
                selected = (value.watch_page or preview["page"]) if preview else None
                frame = preview["frames"].get(selected) if preview else None
                requested = []
                for identity in active:
                    watcher = live_pages.get((doc.id, room.generation, identity))
                    if identity != member.id and watcher and watcher["watch"] == member.id:
                        page = watcher["watch_page"] or value.page
                        if page <= max(value.page, value.page_count or value.page) and page not in requested:
                            requested.append(page)
                return {"peers": peers, "requested_pages": requested,
                        "preview": {**watched, **frame, "page": selected, "writing_page": preview["page"],
                                    "sequence": preview["sequence"], "base_version": preview["base_version"],
                                    "revision": preview["revision"]} if frame else None}

    def members(db, doc, room):
        return list(db.scalars(select(Member).where(Member.document_id == doc.id, Member.generation == room.generation, Member.expires_at > time.time()).order_by(Member.id)))

    def current(db, doc_id, request, token):
        doc = require_access(db, doc_id, request.state.user, "editor", lock=True)
        room = db.get(Room, doc.id)
        member = db.scalar(select(Member).where(Member.document_id == doc.id, Member.token == token)) if token else None
        if (not room or not member or member.expires_at <= 0 or doc.lease_token != "coedit:" + room.generation
                or member.generation != room.generation or member.user_id != request.state.user
                or not secrets.compare_digest(member.login_hash, lease_login_context.get() or "")):
            raise HTTPException(409, "공동편집 세션이 바뀌었습니다. 미저장 내용을 다운로드한 뒤 다시 참여하세요.")
        # Reconnect is safe while nobody replaced this room's exact generation.
        member.expires_at = time.time() + TTL
        doc.lease_until = member.expires_at
        return doc, room, member

    @app.post("/api/documents/{doc_id}/coedit")
    def join(doc_id: str, value: Join, request: Request):
        with transaction(engine) as db:
            doc = require_access(db, doc_id, request.state.user, "editor", lock=True)
            now = time.time(); room = db.get(Room, doc.id)
            active = doc.lease_token and doc.lease_until > now
            if active and (not room or doc.lease_token != "coedit:" + room.generation):
                raise HTTPException(409, "단독 편집 중입니다. 편집자가 저장 후 문서를 닫고 공동편집으로 참여해야 합니다.")
            identity = str(value.request_id); old = db.get(Member, identity)
            db.execute(delete(Member).where(Member.document_id == doc.id, Member.expires_at < now - 86400, Member.id != identity))
            if old and (old.document_id != doc.id or old.user_id != request.state.user or old.login_hash != lease_login_context.get()):
                raise HTTPException(409, "다른 참여 요청의 식별자입니다.")
            if not active:
                if not room: room = Room(document_id=doc.id, generation=str(uuid.uuid4()), started_at=now); db.add(room)
                else: room.generation = str(uuid.uuid4()); room.started_at = now
                doc.lease_token = "coedit:" + room.generation
                bind_lease(db, doc, request.state.user)
                invalidate_running(db, doc.id, "공동편집을 시작하여 이전 편집 후보가 만료됐습니다.")
            if (not old or old.expires_at <= now) and len(members(db, doc, room)) >= 20: raise HTTPException(429, "문서당 공동편집 참여자는 최대 20명입니다.")
            # One durable participant per browser mount; retries return exactly its token.
            member = old or Member(id=identity, document_id=doc.id, generation=room.generation,
                user_id=request.state.user, login_hash=lease_login_context.get(), token=secrets.token_urlsafe(32), expires_at=now + TTL)
            if old and old.generation != room.generation:
                raise HTTPException(409, "이전 참여 요청이 만료됐습니다. 다시 열어 참여하세요.")
            db.add(member); member.expires_at = now + TTL; doc.lease_until = member.expires_at
            return {"token": member.token, "document": {**doc_dict(doc), "role": "editor"}, "expires_at": member.expires_at}

    @app.put("/api/documents/{doc_id}/coedit")
    def sync(doc_id: str, request: Request, x_coedit_session: str = Header()):
        with transaction(engine) as db:
            doc, room, member = current(db, doc_id, request, x_coedit_session)
            return {"current_version": doc.current_version, "expires_at": member.expires_at,
                    "participants": [{"id": entry.id, "user_id": entry.user_id} for entry in members(db, doc, room)]}

    @app.delete("/api/documents/{doc_id}/coedit")
    def leave(doc_id: str, request: Request, x_coedit_session: str = Header()):
        with transaction(engine) as db:
            doc, room, member = current(db, doc_id, request, x_coedit_session)
            member.expires_at = 0; db.flush()
            with live_lock:
                live_pages.pop((doc.id, room.generation, member.id), None)
            if not members(db, doc, room): doc.lease_token = None; doc.lease_until = 0
            return {"ok": True}

    @app.post("/api/documents/{doc_id}/coedit/changes")
    async def changes(doc_id: str, request: Request, base_version: str, request_id: uuid.UUID, x_coedit_session: str = Header()):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BYTES: raise HTTPException(413, "공동편집은 10MB 이하 문서를 지원합니다.")
        incoming = bytes(data)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(commit, doc_id, request, base_version, str(request_id), x_coedit_session, incoming)

    @app.post("/api/documents/{doc_id}/coedit/revision")
    async def revision(doc_id: str, request: Request, base_version: str, request_id: uuid.UUID, x_coedit_session: str = Header()):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BYTES: raise HTTPException(413, "수정안은 10MB 이하 문서를 지원합니다.")
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(preserve_revision, doc_id, request, base_version, str(request_id), x_coedit_session, bytes(data))

    def preserve_revision(doc_id, request, base_version, key, token, incoming):
        from .document_branches import DocumentBranch
        from .db import Access
        validate(incoming)
        with transaction(engine) as db:
            source, room, member = current(db, doc_id, request, token)
            if db.get(DocumentBranch, source.id):
                raise HTTPException(409, "이미 별도 수정안입니다. 현재 내용을 파일로 보관해 주세요.")
            base = db.get(Version, base_version)
            if not base or base.document_id != source.id:
                raise HTTPException(409, "수정안의 기준 버전을 찾을 수 없습니다.")
            identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f"coedit-revision:{source.id}:{member.id}:{key}"))
            old = db.get(DocumentBranch, identity)
            if old:
                initial = db.scalar(select(Version).where(Version.document_id == identity, Version.base_version.is_(None)))
                if old.base_version_id != base.id or not initial or initial.sha256 != digest(incoming):
                    raise HTTPException(409, "같은 수정안 요청을 다른 내용으로 사용할 수 없습니다.")
                copy = require_access(db, identity, request.state.user, "owner", fresh=True)
                return {"document": {**doc_dict(copy), "role": "owner"}, "replayed": True}
            original = (folder / base.storage_key).read_bytes()
            if len(original) > MAX_BYTES or loss_warnings(original, incoming):
                raise HTTPException(422, "기준 문서의 개체를 보존할 수 없어 수정안 저장을 중단했습니다.")
            now = time.time(); name = f"{request.state.user} 수정안 · {source.name}"[:240]
            copy = Document(id=identity, name=name, created_at=now, lease_until=0)
            db.add(copy); db.flush()
            db.add(Access(document_id=identity, user_id=request.state.user, role="owner"))
            db.add(DocumentBranch(document_id=identity, source_document_id=source.id, base_version_id=base.id,
                owner_id=request.state.user, name=name, created_at=now, request_id=key))
            publish(db, copy, incoming, None, None, "coedit-revision:" + key, "upload", author_id=request.state.user)
            return {"document": {**doc_dict(copy), "role": "owner"}, "replayed": False}

    def commit(doc_id, request, base_version, key, token, incoming):
        validate(incoming)
        fingerprint = digest(base_version.encode() + b"\0" + incoming)
        with transaction(engine) as db:
            doc, room, member = current(db, doc_id, request, token)
            receipt = db.get(Receipt, (doc.id, key))
            if receipt:
                if receipt.member_id != member.id or receipt.fingerprint != fingerprint: raise HTTPException(409, "같은 변경 요청 ID를 다른 내용에 사용할 수 없습니다.")
                return {"version_id": receipt.version_id, "current_version": doc.current_version, "replayed": True}
            base = db.get(Version, base_version); live = db.get(Version, doc.current_version)
            if not base or base.document_id != doc.id: raise HTTPException(409, "공동편집 기준 버전을 찾을 수 없습니다.")
            original = (folder / base.storage_key).read_bytes(); current_bytes = (folder / live.storage_key).read_bytes()
            if max(len(original), len(current_bytes)) > MAX_BYTES: raise HTTPException(413, "공동편집은 10MB 이하 문서를 지원합니다.")
            if loss_warnings(original, incoming): raise HTTPException(422, "개체 손실이 감지되어 공동편집 게시를 중단했습니다.")
            merged = merge_hwpx(original, current_bytes, incoming)
            result = live if merged == current_bytes else publish(db, doc, merged, live.id, doc.lease_token, "coedit:" + key, "web-edit", author_id=request.state.user)
            db.add(Receipt(document_id=doc.id, request_id=key, member_id=member.id, fingerprint=fingerprint, version_id=result.id))
            return {"version_id": result.id, "current_version": result.id, "replayed": False}
