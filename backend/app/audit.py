"""Append-only action receipts; no document text, credentials, or request bodies."""
import json
import time
from contextvars import ContextVar

from fastapi import Query, Request
from sqlalchemy import Float, Integer, String, Text, event, inspect, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base

actor_context = ContextVar("hwp_audit_actor", default=None)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[float] = mapped_column(Float, index=True)
    actor_id: Mapped[str] = mapped_column(String(80), index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    document_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    details: Mapped[str] = mapped_column(Text, default="{}")


def record_event(db, actor_id, action, target_type, target_id=None, document_id=None,
                 version_id=None, details=None, document_name=None):
    # Callers pass fixed operation labels, never arbitrary payloads or exception text.
    safe = {key: value for key, value in (details or {}).items()
            if key in {"cause", "outcome", "mode"} and isinstance(value, str) and len(value) <= 40}
    row = AuditEvent(created_at=time.time(), actor_id=actor_id, action=action,
                     target_type=target_type, target_id=target_id, document_id=document_id,
                     document_name=document_name, version_id=version_id,
                     details=json.dumps(safe, ensure_ascii=False))
    db.add(row)
    return row


@event.listens_for(Session, "before_flush")
def document_receipts(db, _context, _instances):
    """Write file change receipts in the same transaction as the actual mutation.

    Common version publication covers uploads, copies, report saves and restores.
    Request identity is propagated by Starlette into its worker threads. Autonomous
    publishers can use the immutable VersionAuthor that they already supply.
    """
    from .db import (Document, Version, VersionAuthor, VisualStandard, VisualStandardState,
                     DesignFragment, ReportTask, RewriteTask, ConversationTurn, Application)
    for row in db.dirty | db.deleted:
        if isinstance(row, AuditEvent):
            raise ValueError("Audit receipts are append-only")
    actor = actor_context.get()
    authors = {row.version_id: row.user_id for row in db.new if isinstance(row, VersionAuthor)}
    task_events = {ReportTask: ("ai.report.request", "owner_id", "report"),
                   RewriteTask: ("ai.rewrite.request", "requested_by", "rewrite"),
                   ConversationTurn: ("ai.conversation.request", "requested_by", "conversation"),
                   Application: ("ai.rewrite.approve", "approved_by", "application")}
    for row in list(db.new):
        if isinstance(row, Version):
            author = authors.get(row.id) or actor
            if not author:
                continue
            doc = next((item for item in db.new if isinstance(item, Document) and item.id == row.document_id), None)
            if doc is None:
                doc = db.get(Document, row.document_id)
            action = {"upload": "document.upload", "restore": "document.restore"}.get(row.cause, "document.modify")
            record_event(db, author, action, "document", row.document_id, row.document_id,
                         row.id, {"cause": row.cause}, document_name=doc.name if doc else None)
        elif type(row) in task_events:
            name, author_field, target = task_events[type(row)]
            record_event(db, getattr(row, author_field), name, target, row.id,
                         document_id=getattr(row, "document_id", None))
    if not actor:
        return
    for row in list(db.dirty):
        if isinstance(row, Document) and inspect(row).attrs.name.history.has_changes():
            record_event(db, actor, "document.rename", "document", row.id,
                         document_id=row.id, document_name=row.name)
        elif isinstance(row, (DesignFragment, VisualStandardState)) and inspect(row).attrs.hidden.history.has_changes() and row.hidden:
            kind = "design_fragment" if isinstance(row, DesignFragment) else "visual_standard"
            record_event(db, actor, kind + ".delete", kind, getattr(row, "id", None) or row.standard_id,
                         document_name=getattr(row, "name", None))
    for row in list(db.new):
        if isinstance(row, VisualStandardState) and row.hidden:
            record_event(db, actor, "visual_standard.delete", "visual_standard", row.standard_id)
    for row in list(db.deleted):
        if isinstance(row, (Document, VisualStandard, DesignFragment)):
            kind = {Document: "document", VisualStandard: "visual_standard", DesignFragment: "design_fragment"}[type(row)]
            record_event(db, actor, kind + ".delete", kind, row.id,
                         document_id=row.id if isinstance(row, Document) else None,
                         document_name=getattr(row, "name", None))


def install_audit(app):
    from .account_management import require_admin

    @app.get("/api/admin/audit")
    def list_audit(request: Request, actor: str = Query("", max_length=80),
                   action: str = Query("", max_length=60), before: int | None = Query(None, ge=1),
                   limit: int = Query(50, ge=1, le=100)):
        with Session(app.state.engine) as db:
            require_admin(db, request.state.user)
            statement = select(AuditEvent)
            if actor:
                statement = statement.where(AuditEvent.actor_id == actor)
            if action:
                statement = statement.where(AuditEvent.action == action)
            if before:
                statement = statement.where(AuditEvent.id < before)
            rows = list(db.scalars(statement.order_by(AuditEvent.id.desc()).limit(limit + 1)))
            items = [{key: getattr(row, key) for key in ("id", "created_at", "actor_id", "action", "target_type",
                     "target_id", "document_id", "document_name", "version_id")} for row in rows[:limit]]
            return {"items": items, "next_before": rows[limit - 1].id if len(rows) > limit else None}
