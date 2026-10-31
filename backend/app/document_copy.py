"""Copy one immutable saved version into a new document owned by the caller."""
import json
import time
import uuid
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, text
from .auth import require_access
from .db import Access, Document, Version, transaction
from .hwpx import digest


class CopyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: uuid.UUID
    source_version: uuid.UUID
    request_id: uuid.UUID
    name: str = Field(min_length=1, max_length=235)
    workspace_id: uuid.UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def valid_encoding(cls, value):
        try:
            json.dumps(value, ensure_ascii=False).encode("utf-8")
        except (UnicodeError, TypeError) as exc:
            raise HTTPException(422, "문서 복제 요청에 유효한 UTF-8 문자를 사용하세요.") from exc
        return value

    @field_validator("name")
    @classmethod
    def visible_name(cls, value):
        if not value.strip() or any(ord(c) < 32 or c in '/\\' for c in value):
            raise ValueError("파일 이름에는 경로나 제어 문자를 사용할 수 없습니다.")
        return value if value.lower().endswith(".hwpx") else value + ".hwpx"


def install_document_copy(app, locked, publish, doc_dict):
    engine, folder = app.state.engine, app.state.folder

    @app.post("/api/documents/{source_id}/copies")
    def copy_document(source_id: str, value: CopyDocument, request: Request):
        destination = str(value.document_id)
        if destination == source_id:
            raise HTTPException(409, "사본은 새 문서 ID로 만들어야 합니다.")
        with transaction(engine) as db:
            from .workspaces import membership_lock, send_target_access, WorkspaceDocument
            membership_lock(db)
            locked(db, source_id, request.state.user, "reader")
            from .workspaces import require_copyable_source, inherit_copy_scope, require_copy_retry, document_workspace_info
            from .library_access import document_role
            require_copyable_source(db, source_id, request.state.user)
            target = None
            if value.workspace_id is not None:
                grant = db.get(Access, (source_id, request.state.user), populate_existing=True)
                if not grant or grant.role != "owner":
                    raise HTTPException(403, "문서의 원 소유자만 다른 문서함으로 사본을 보낼 수 있습니다.")
                target, _ = send_target_access(db, str(value.workspace_id), request.state.user)
            version = db.get(Version, str(value.source_version))
            if not version or version.document_id != source_id:
                raise HTTPException(404, "원본 문서의 저장 버전을 찾을 수 없습니다.")
            try:
                data = (folder / version.storage_key).read_bytes()
            except OSError as exc:
                raise HTTPException(503, "원본 저장 파일을 읽지 못했습니다. 사본은 생성되지 않았습니다.") from exc
            if len(data) != version.size or digest(data) != version.sha256:
                raise HTTPException(422, "원본 저장본의 크기 또는 해시가 일치하지 않습니다.")
            if engine.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(digest(destination.encode())[:15], 16)})
            # Keep historical no-target retry fingerprints unchanged.
            payload = value.model_dump(mode="json", exclude={"workspace_id"} if target is None else set())
            key = digest(json.dumps(["document-copy-v1", source_id, payload], sort_keys=True).encode())
            doc = db.get(Document, destination)
            if doc:
                doc = require_copy_retry(db, destination, request.state.user)
                if not db.scalar(select(Version).where(Version.document_id == destination, Version.request_id == key)):
                    raise HTTPException(409, "같은 사본 ID에 다른 복제 요청을 사용할 수 없습니다.")
                if target:
                    scope = db.get(WorkspaceDocument, destination)
                    if not scope or scope.workspace_id != target.id:
                        raise HTTPException(409, "사본의 문서함이 변경됐습니다. 현재 위치를 확인하세요.")
            else:
                doc = Document(id=destination, name=value.name, created_at=time.time(), lease_until=0)
                db.add(doc); db.flush()
                db.add(Access(document_id=destination, user_id=request.state.user, role="owner"))
                if target:
                    db.add(WorkspaceDocument(document_id=destination, workspace_id=target.id,
                                             assigned_by=request.state.user, assigned_at=time.time()))
                    db.flush()
                else:
                    inherit_copy_scope(db, source_id, destination, request.state.user)
            # A byte-preserving first upload reuses the existing fsync + commit boundary.
            publish(db, doc, data, None, None, key, "upload", author_id=request.state.user)
            result = {**doc_dict(doc), "role": document_role(db, doc.id, request.state.user),
                      **document_workspace_info(db, doc.id), "source_document": source_id, "source_version": version.id}
        return result
