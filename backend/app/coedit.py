"""Durable concurrent editing sessions with optimistic, non-overlapping HWPX merges."""
import secrets
import time
import uuid
from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, Field
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


def install_coedit(app, publish, doc_dict):
    engine, folder = app.state.engine, app.state.folder

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
