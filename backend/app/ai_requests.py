"""Durable chat admission: concurrent/retried questions cannot invoke the model twice."""
import time
from fastapi import HTTPException
from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, transaction


class AIRequest(Base):
    __tablename__ = "ai_requests"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[float] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)


def reserve_chat(db, key, user):
    # Caller holds the document row lock. A new app/process sees the same reservation.
    existing = db.get(AIRequest, key)
    if existing:
        if existing.status == "running" and time.time() - existing.created_at < 300:
            raise HTTPException(409, "이 질문의 AI 응답을 생성 중입니다. 완료될 때까지 기다려 주세요.")
        raise HTTPException(409, "이 질문의 이전 실행 결과를 확인할 수 없습니다. 대화를 새로고침한 뒤 필요하면 새 질문으로 요청하세요.")
    db.add(AIRequest(id=key, user_id=user, kind="chat", status="running", created_at=time.time()))
    db.flush()


def finish_chat(engine, key, status):
    with transaction(engine) as db:
        row = db.get(AIRequest, key)
        if row and row.status == "running":
            row.status, row.finished_at = status, time.time()
