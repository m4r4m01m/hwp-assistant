"""Private defaults for new documents and per-user folders, independent of ACLs."""
import time
import uuid
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import ForeignKey, String, delete, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column
from .db import Base, Access, transaction
from .auth import require_access


class PersonalDocument(Base):
    __tablename__ = "personal_documents"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)


class LibraryFolder(Base):
    __tablename__ = "library_folders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(100))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("library_folders.id"), nullable=True)


class FolderDocument(Base):
    __tablename__ = "library_folder_documents"
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    folder_id: Mapped[str] = mapped_column(ForeignKey("library_folders.id"), index=True)


class FolderValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    parent_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def visible(cls, value):
        if not value.strip() or any(ord(c) < 32 or c in '/\\' for c in value):
            raise ValueError("폴더 이름에 경로나 제어 문자를 사용할 수 없습니다.")
        return value.strip()


class Placement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: uuid.UUID | None = None


def library_info(db, document_id, user):
    from .workspaces import WorkspaceDocument
    personal = db.get(PersonalDocument, document_id)
    placement = db.get(FolderDocument, (user, document_id))
    access = db.get(Access, (document_id, user))
    scoped = db.get(WorkspaceDocument, document_id)
    return {"personal_document": bool(personal and not scoped),
            "owned_by_me": bool(access and access.role == "owner"),
            "individually_shared": bool(personal and not scoped and access and access.role != "owner"),
            "folder_id": placement.folder_id if placement else None}


def install_document_library(app):
    engine = app.state.engine

    def lock(db, user):
        if engine.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:user, 706291))"), {"user": user})

    def owned_folder(db, identity, user):
        row = db.get(LibraryFolder, str(identity), populate_existing=True)
        if not row or row.owner_id != user:
            raise HTTPException(404, "내 폴더를 찾을 수 없습니다.")
        return row

    @app.get("/api/folders")
    def folders(request: Request):
        with Session(engine) as db:
            return [{"id": row.id, "name": row.name, "parent_id": row.parent_id} for row in db.scalars(
                select(LibraryFolder).where(LibraryFolder.owner_id == request.state.user).order_by(LibraryFolder.name, LibraryFolder.id))]

    @app.put("/api/folders/{folder_id}")
    def put_folder(folder_id: uuid.UUID, value: FolderValue, request: Request):
        with transaction(engine) as db:
            user = request.state.user; lock(db, user)
            identity = str(folder_id); row = db.get(LibraryFolder, identity)
            if row and row.owner_id != user:
                raise HTTPException(404, "내 폴더를 찾을 수 없습니다.")
            parent = str(value.parent_id) if value.parent_id else None
            cursor, depth = parent, 0
            while cursor:
                if cursor == identity or depth >= 8:
                    raise HTTPException(409, "폴더가 자신을 포함하거나 8단계를 넘을 수 없습니다.")
                cursor = owned_folder(db, cursor, user).parent_id; depth += 1
            # Existing subtrees retain their maximum depth when moved.
            rows = list(db.scalars(select(LibraryFolder).where(LibraryFolder.owner_id == user)))
            def height(node, seen):
                if node in seen: raise HTTPException(409, "폴더 연결을 확인하세요.")
                return max([1] + [1 + height(child.id, seen | {node}) for child in rows if child.parent_id == node])
            if depth + height(identity, set()) > 8:
                raise HTTPException(409, "폴더는 8단계까지 만들 수 있습니다.")
            if any(f.id != identity and f.parent_id == parent and f.name.casefold() == value.name.casefold() for f in rows):
                raise HTTPException(409, "같은 위치에 같은 이름의 폴더가 있습니다.")
            if row: row.name, row.parent_id = value.name, parent
            else: db.add(LibraryFolder(id=identity, owner_id=user, name=value.name, parent_id=parent))
        return {"id": identity, "name": value.name, "parent_id": parent}

    @app.delete("/api/folders/{folder_id}")
    def delete_folder(folder_id: uuid.UUID, request: Request):
        with transaction(engine) as db:
            lock(db, request.state.user); row = owned_folder(db, folder_id, request.state.user)
            if db.scalar(select(LibraryFolder.id).where(LibraryFolder.parent_id == row.id).limit(1)):
                raise HTTPException(409, "하위 폴더를 먼저 옮기거나 삭제하세요.")
            db.execute(delete(FolderDocument).where(FolderDocument.user_id == request.state.user, FolderDocument.folder_id == row.id))
            db.delete(row)
        return {"ok": True}

    @app.put("/api/documents/{document_id}/folder")
    def place(document_id: str, value: Placement, request: Request):
        with transaction(engine) as db:
            lock(db, request.state.user)
            require_access(db, document_id, request.state.user)
            target = owned_folder(db, value.folder_id, request.state.user) if value.folder_id else None
            row = db.get(FolderDocument, (request.state.user, document_id))
            if target:
                if row: row.folder_id = target.id
                else: db.add(FolderDocument(user_id=request.state.user, document_id=document_id, folder_id=target.id))
            elif row: db.delete(row)
        return {"ok": True}
