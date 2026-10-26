"""Owner-only, reversible document removal; never delete versions or stored bytes."""
import time
import uuid

from fastapi import Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_access
from .db import Access, Document, DocumentTrash, document_is_trashed, transaction


def trash_owner(db, document_id, user):
    # Shared-library editing grants are deliberately not deletion ownership.
    doc = require_access(db, document_id, user, lock=True, fresh=True, include_trashed=True)
    grant = db.get(Access, (document_id, user), populate_existing=True)
    if not grant or grant.role != "owner":
        raise HTTPException(403, "문서 소유자만 휴지통으로 이동하거나 복구할 수 있습니다.")
    return doc


def install_document_trash(app, doc_dict):
    engine = app.state.engine

    @app.get("/api/document-trash")
    def trash_list(request: Request):
        with Session(engine) as db:
            rows = db.execute(select(Document, DocumentTrash).join(DocumentTrash, DocumentTrash.document_id == Document.id)
                              .join(Access, Access.document_id == Document.id)
                              .where(Access.user_id == request.state.user, Access.role == "owner", DocumentTrash.restored_at.is_(None))
                              .order_by(DocumentTrash.deleted_at.desc()))
            return [{**doc_dict(doc), "role": "owner", "can_trash": True, "trash_id": row.id,
                     "deleted_at": row.deleted_at} for doc, row in rows]

    @app.delete("/api/documents/{document_id}")
    def move_to_trash(document_id: str, request: Request, idempotency_key: uuid.UUID = Header()):
        receipt_id = str(idempotency_key)
        with transaction(engine) as db:
            doc = trash_owner(db, document_id, request.state.user)
            receipt = db.get(DocumentTrash, receipt_id)
            if receipt:
                if receipt.document_id != document_id or receipt.deleted_by != request.state.user:
                    raise HTTPException(409, "다른 휴지통 이동 요청에 사용한 ID입니다.")
                if receipt.restored_at is not None:
                    raise HTTPException(409, "이미 복구한 문서입니다. 문서 목록을 새로고침하세요.")
                return {"id": doc.id, "trash_id": receipt.id, "deleted_at": receipt.deleted_at}
            if db.scalar(select(document_is_trashed(document_id))):
                raise HTTPException(409, "이미 휴지통에 있는 문서입니다. 휴지통 목록을 확인하세요.")
            if doc.lease_token and doc.lease_until > time.time():
                raise HTTPException(409, "편집 중인 문서입니다. 편집 화면을 닫은 뒤 휴지통으로 이동하세요.")
            deleted_at = time.time()
            db.add(DocumentTrash(id=receipt_id, document_id=doc.id, deleted_by=request.state.user,
                                 deleted_at=deleted_at))
            record_event(db, request.state.user, "document.trash", "document", target_id=doc.id,
                         document_id=doc.id, version_id=doc.current_version, document_name=doc.name)
            result = {"id": doc.id, "trash_id": receipt_id, "deleted_at": deleted_at}
        return result

    @app.post("/api/document-trash/{trash_id}/restore")
    def restore_document(trash_id: str, request: Request):
        with transaction(engine) as db:
            receipt = db.get(DocumentTrash, trash_id)
            if not receipt:
                raise HTTPException(404, "휴지통 항목을 찾을 수 없습니다.")
            doc = trash_owner(db, receipt.document_id, request.state.user)
            # Refresh after the document lock, including a concurrent restore.
            db.refresh(receipt)
            if receipt.restored_at is not None:
                if db.scalar(select(document_is_trashed(doc.id))):
                    raise HTTPException(409, "다시 휴지통으로 이동한 문서입니다. 최신 목록에서 복구하세요.")
            else:
                receipt.restored_at = time.time()
                record_event(db, request.state.user, "document.restore_from_trash", "document", target_id=doc.id,
                             document_id=doc.id, version_id=doc.current_version, document_name=doc.name)
            result = {**doc_dict(doc), "role": "owner", "can_trash": True}
        return result
