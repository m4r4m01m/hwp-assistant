"""Experimental explicit department/team membership; no inherited access or data migration."""
import time
import uuid
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Float, ForeignKey, String, delete, func, select, text, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from .accounts import UserAccount
from .account_management import require_admin
from .auth import require_access
from .audit import record_event
from .db import Access, Base, Document, transaction


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(12))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[float] = mapped_column(Float)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    role: Mapped[str] = mapped_column(String(12))


class WorkspaceDocument(Base):
    __tablename__ = "workspace_documents"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    assigned_by: Mapped[str] = mapped_column(String(80))
    assigned_at: Mapped[float] = mapped_column(Float)


class WorkspaceShare(Base):
    """New, explicit document invitations; never activate old legacy ACL rows."""
    __tablename__ = "workspace_document_shares"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    role: Mapped[str] = mapped_column(String(12))
    granted_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[float] = mapped_column(Float)


def _lock_derived_documents(db, source_ids):
    from .document_branches import DocumentBranch
    if not source_ids:
        return []
    return list(db.scalars(select(Document).join(DocumentBranch,
        DocumentBranch.document_id == Document.id).where(
        DocumentBranch.source_document_id.in_(source_ids)).order_by(Document.id)
        .with_for_update(of=Document).execution_options(populate_existing=True)))


def _retire_lost_edit_access(db, documents, user):
    """Caller holds source then branch locks; never retire another user's work."""
    from .coedit import Member
    from .db import LeaseOwner
    from .library_access import document_role
    from .rewrite_jobs import invalidate_running
    db.flush()
    for doc in documents:
        if document_role(db, doc.id, user) in ("owner", "editor"):
            continue
        invalidate_running(db, doc.id, "문서 공유 권한이 변경되어 실행 결과를 게시하지 않습니다.", user=user)
        db.execute(update(Member).where(Member.document_id == doc.id,
            Member.user_id == user).values(expires_at=0))
        owner = db.get(LeaseOwner, doc.id)
        if (doc.lease_token or "").startswith("coedit:"):
            generation = doc.lease_token.removeprefix("coedit:")
            if not db.scalar(select(Member.id).where(Member.document_id == doc.id,
                    Member.generation == generation, Member.expires_at > time.time()).limit(1)):
                doc.lease_token, doc.lease_until = None, 0
        elif owner and owner.user_id == user:
            doc.lease_token, doc.lease_until = None, 0


def change_document_share(db, doc, user, actor, role=None):
    scoped = db.get(WorkspaceDocument, doc.id, populate_existing=True)
    if not scoped:
        return False
    workspace_access(db, scoped.workspace_id, actor, manager=True)
    if role:
        account = db.get(UserAccount, user, populate_existing=True)
        if not account or not account.is_active or account.approval_status != "approved":
            raise HTTPException(422, "활성 승인 계정에게만 공유할 수 있습니다.")
        if db.get(WorkspaceMember, (scoped.workspace_id, user), populate_existing=True):
            raise HTTPException(409, "이미 문서함 구성원입니다. 구성원 권한에서 관리하세요.")
    query = select(WorkspaceShare).where(WorkspaceShare.document_id == doc.id,
        WorkspaceShare.user_id == user).execution_options(populate_existing=True)
    row = db.scalar(query)
    affected = []
    if row and (role is None or row.role == "editor" and role == "reader"):
        # The caller already locks the source. Lock its branches before taking
        # the ACL row: saves hold their document before reading ACLs FOR SHARE.
        affected = [doc, *_lock_derived_documents(db, [doc.id])]
    row = db.scalar(query.with_for_update())
    if role:
        if row:
            row.role, row.workspace_id, row.granted_by = role, scoped.workspace_id, actor
        else:
            db.add(WorkspaceShare(document_id=doc.id, user_id=user, workspace_id=scoped.workspace_id,
                                  role=role, granted_by=actor, created_at=time.time()))
    elif row:
        db.delete(row)
    _retire_lost_edit_access(db, affected, user)
    record_event(db, actor, "workspace.document.share" if role else "workspace.document.unshare",
                 "workspace", scoped.workspace_id, document_id=doc.id)
    return True


class NewWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["department", "team"]
    parent_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("공간 이름을 입력하세요.")
        value.encode("utf-8")
        return value.strip()


class MemberRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["manager", "editor", "reader"]


class AssignWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: uuid.UUID | None
    expected_version: uuid.UUID


def workspace_for_document(db, document_id):
    row = db.get(WorkspaceDocument, document_id, populate_existing=True)
    if not row:
        return None
    space = db.get(Workspace, row.workspace_id)
    return {"id": space.id, "name": space.name, "kind": space.kind} if space else None


def document_workspace_info(db, document_id, user=None):
    space = workspace_for_document(db, document_id)
    share = db.get(WorkspaceShare, (document_id, user)) if space and user else None
    invited = bool(space and user and not db.get(WorkspaceMember, (space["id"], user))
                   and share and share.workspace_id == space["id"])
    return {"workspace_id": space["id"], "workspace_name": space["name"], "shared_with_me": invited} if space else {"workspace_id": None, "workspace_name": None, "shared_with_me": False}


def require_copyable_source(db, document_id, user=None):
    from .document_branches import DocumentBranch
    if db.get(DocumentBranch, document_id):
        raise HTTPException(409, "개인 작업본은 사본이나 양식으로 공개하지 않습니다. 통합 요청을 사용하세요.")
    scoped = db.get(WorkspaceDocument, document_id)
    if scoped and user and not db.get(WorkspaceMember, (scoped.workspace_id, user)):
        raise HTTPException(403, "개별 공유받은 문서는 문서함 안에 복제하거나 양식으로 등록할 수 없습니다.")


def inherit_copy_scope(db, source_id, destination_id, user):
    """Built-in copies never silently escape a source's explicit space."""
    source = db.get(WorkspaceDocument, source_id, populate_existing=True)
    if source:
        db.add(WorkspaceDocument(document_id=destination_id, workspace_id=source.workspace_id,
                                 assigned_by=user, assigned_at=time.time()))
        db.flush()


def require_copy_retry(db, document_id, user):
    doc = require_access(db, document_id, user, lock=True, fresh=True)
    grant = db.get(Access, (document_id, user), populate_existing=True)
    if not grant or grant.role != "owner":
        raise HTTPException(404, "내 사본을 찾을 수 없습니다.")
    return doc


def workspace_access(db, workspace_id, user, *, manager=False, lock=False):
    query = select(Workspace).where(Workspace.id == workspace_id).execution_options(populate_existing=True)
    space = db.scalar(query.with_for_update() if lock else query)
    member = db.get(WorkspaceMember, (workspace_id, user), populate_existing=True)
    if not space or not member:
        raise HTTPException(404, "공간을 찾을 수 없습니다.")
    if manager and member.role != "manager":
        raise HTTPException(403, "공간 관리자 권한이 필요합니다.")
    return space, member


def membership_lock(db):
    # Stable order for membership/assignment mutations. Document saves only lock
    # their member row; they never wait for this coordination lock.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(706281001)"))


def install_workspaces(app):
    engine = app.state.engine

    @app.get("/api/workspaces")
    def list_workspaces(request: Request):
        with Session(engine) as db:
            rows = list(db.execute(select(Workspace, WorkspaceMember.role).join(
                WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id).where(
                WorkspaceMember.user_id == request.state.user).order_by(Workspace.created_at, Workspace.id)))
            visible = {space.id for space, _ in rows}
            return [{"id": space.id, "name": space.name, "kind": space.kind,
                     "parent_id": space.parent_id if space.parent_id in visible else None,
                     "role": role} for space, role in rows]

    @app.post("/api/workspaces")
    def create_workspace(value: NewWorkspace, request: Request):
        with transaction(engine) as db:
            require_admin(db, request.state.user)
            # Also serializes concurrent retry creation on PostgreSQL.
            membership_lock(db)
            parent = str(value.parent_id) if value.parent_id else None
            if parent:
                parent_space, _ = workspace_access(db, parent, request.state.user, manager=True, lock=True)
                if value.kind != "team" or parent_space.kind != "department":
                    raise HTTPException(422, "팀은 부서 아래에만 만들 수 있습니다.")
            row = db.get(Workspace, str(value.id))
            if row:
                if (row.name, row.kind, row.parent_id, row.created_by) != (value.name, value.kind, parent, request.state.user):
                    raise HTTPException(409, "같은 공간 ID에 다른 생성 요청을 사용할 수 없습니다.")
                workspace_access(db, row.id, request.state.user, manager=True)
            else:
                row = Workspace(id=str(value.id), name=value.name, kind=value.kind, parent_id=parent,
                                created_by=request.state.user, created_at=time.time())
                db.add(row); db.flush()
                db.add(WorkspaceMember(workspace_id=row.id, user_id=request.state.user, role="manager"))
                record_event(db, request.state.user, "workspace.create", "workspace", row.id)
            return {"id": row.id, "name": row.name, "kind": row.kind, "parent_id": row.parent_id, "role": "manager"}

    @app.get("/api/workspaces/{workspace_id}/members")
    def members(workspace_id: str, request: Request):
        with Session(engine) as db:
            workspace_access(db, workspace_id, request.state.user)
            return [{"user_id": row.user_id, "role": row.role} for row in db.scalars(
                select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id).order_by(WorkspaceMember.user_id))]

    def change_member(db, workspace_id, user_id, actor, role):
        membership_lock(db)
        workspace_access(db, workspace_id, actor, manager=True, lock=True)
        account = db.get(UserAccount, user_id, populate_existing=True)
        if role and (not account or not account.is_active or account.approval_status != "approved"):
            raise HTTPException(422, "활성 승인 계정만 공간에 참여할 수 있습니다.")
        query = select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id,
                        WorkspaceMember.user_id == user_id).execution_options(populate_existing=True)
        row = db.scalar(query)
        affected = []
        if row and (role is None or row.role in ("manager", "editor") and role == "reader"):
            # membership_lock stabilizes assignments. Lock sources, then their
            # branches, before the member row to match document publication.
            sources = list(db.scalars(select(Document).join(WorkspaceDocument,
                WorkspaceDocument.document_id == Document.id).where(
                WorkspaceDocument.workspace_id == workspace_id).order_by(Document.id)
                .with_for_update(of=Document).execution_options(populate_existing=True)))
            affected = [*sources, *_lock_derived_documents(db, [doc.id for doc in sources])]
        row = db.scalar(query.with_for_update())
        if row and row.role == "manager" and role != "manager":
            count = db.scalar(select(func.count()).select_from(WorkspaceMember).join(UserAccount,
                UserAccount.user_id == WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == "manager", UserAccount.is_active.is_(True), UserAccount.approval_status == "approved"))
            if count <= 1:
                raise HTTPException(409, "마지막 활성 공간 관리자는 제거하거나 권한을 낮출 수 없습니다.")
        if role:
            if row: row.role = role
            else: db.add(WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role))
        elif row:
            db.delete(row)
        _retire_lost_edit_access(db, affected, user_id)
        record_event(db, actor, "workspace.member.change", "workspace", workspace_id)

    @app.put("/api/workspaces/{workspace_id}/members/{user_id}")
    def put_member(workspace_id: str, user_id: str, value: MemberRole, request: Request):
        with transaction(engine) as db:
            change_member(db, workspace_id, user_id, request.state.user, value.role)
        return {"ok": True}

    @app.delete("/api/workspaces/{workspace_id}/members/{user_id}")
    def delete_member(workspace_id: str, user_id: str, request: Request):
        with transaction(engine) as db:
            change_member(db, workspace_id, user_id, request.state.user, None)
        return {"ok": True}

    @app.put("/api/documents/{doc_id}/workspace")
    def assign_document(doc_id: str, value: AssignWorkspace, request: Request):
        with transaction(engine) as db:
            membership_lock(db)
            doc = require_access(db, doc_id, request.state.user, "owner", lock=True, fresh=True)
            from .document_branches import DocumentBranch
            if db.get(DocumentBranch, doc_id):
                raise HTTPException(409, "개인 작업본은 공간으로 이동할 수 없습니다. 통합 요청을 사용하세요.")
            grant = db.get(Access, (doc_id, request.state.user), populate_existing=True)
            if not grant or grant.role != "owner":
                raise HTTPException(403, "문서의 원 소유자만 공간을 지정할 수 있습니다.")
            if doc.current_version != str(value.expected_version):
                raise HTTPException(409, "문서가 변경됐습니다. 최신 저장본을 확인하세요.")
            row = db.get(WorkspaceDocument, doc_id, populate_existing=True)
            if value.workspace_id is None:
                from .document_library import PersonalDocument
                if not row and db.get(PersonalDocument, doc_id):
                    return {"ok": True, "workspace_id": None}
                if doc.lease_token and doc.lease_until > time.time():
                    raise HTTPException(409, "편집 중인 문서는 이동할 수 없습니다. 저장 후 편집을 종료하세요.")
                if row: db.delete(row)
                db.execute(delete(WorkspaceShare).where(WorkspaceShare.document_id == doc_id))
                db.execute(delete(Access).where(Access.document_id == doc_id, Access.user_id != request.state.user))
                if not db.get(PersonalDocument, doc_id):
                    db.add(PersonalDocument(document_id=doc_id, owner_id=request.state.user))
                record_event(db, request.state.user, "document.make_private", "document", doc_id, document_id=doc_id)
                return {"ok": True, "workspace_id": None}
            space, _ = workspace_access(db, str(value.workspace_id), request.state.user, manager=True, lock=True)
            if row and row.workspace_id == space.id:
                return {"ok": True, "workspace_id": space.id}
            if doc.lease_token and doc.lease_until > time.time():
                raise HTTPException(409, "편집 중인 문서는 이동할 수 없습니다. 저장 후 편집을 종료하세요.")
            # Moving a document never revives invitations from an earlier space.
            db.execute(delete(WorkspaceShare).where(WorkspaceShare.document_id == doc_id))
            if row: row.workspace_id, row.assigned_by, row.assigned_at = space.id, request.state.user, time.time()
            else: db.add(WorkspaceDocument(document_id=doc_id, workspace_id=space.id,
                                           assigned_by=request.state.user, assigned_at=time.time()))
            record_event(db, request.state.user, "workspace.document.assign", "workspace", space.id, document_id=doc_id)
        return {"ok": True, "workspace_id": space.id}
