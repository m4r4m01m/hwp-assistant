"""Primary organizational affiliation, independent of document/workspace ACLs.

The experimental organization uses department/team workspace identifiers as its
catalog. Assignment never adds or removes WorkspaceMember or document grants.
"""
import uuid
import hashlib
import json
import time
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ForeignKey, String, select, delete, or_
from sqlalchemy.orm import Mapped, Session, mapped_column

from .accounts import UserAccount
from .account_management import require_admin, management_lock
from .audit import record_event
from .db import Base, transaction
from .workspaces import Workspace, WorkspaceMember, WorkspaceDocument, WorkspaceShare, NewWorkspace, membership_lock


class AccountAffiliation(Base):
    __tablename__ = "account_affiliations"
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.user_id"), primary_key=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True)
    revision: Mapped[str] = mapped_column(String(36))


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department_id: uuid.UUID | None
    team_id: uuid.UUID | None
    expected_revision: uuid.UUID | None


def affiliation_view(db, user):
    row = db.get(AccountAffiliation, user, populate_existing=True)
    def unit(identifier):
        space = db.get(Workspace, identifier) if identifier else None
        return {"id": space.id, "name": space.name,
                "can_access": db.get(WorkspaceMember, (space.id, user)) is not None} if space else None
    return {"user_id": user, "department": unit(row.department_id) if row else None,
            "team": unit(row.team_id) if row else None, "revision": row.revision if row else None}


def affiliation_views(db, users):
    """Read one bounded account page without per-account organization queries."""
    users = list(users)
    if not users:
        return {}
    rows = {row.user_id: row for row in db.scalars(
        select(AccountAffiliation).where(AccountAffiliation.user_id.in_(users)))}
    unit_ids = {identifier for row in rows.values()
                for identifier in (row.department_id, row.team_id) if identifier}
    spaces = {space.id: space for space in db.scalars(
        select(Workspace).where(Workspace.id.in_(unit_ids)))} if unit_ids else {}
    members = set(db.execute(select(WorkspaceMember.workspace_id, WorkspaceMember.user_id).where(
        WorkspaceMember.workspace_id.in_(unit_ids), WorkspaceMember.user_id.in_(users)))) if unit_ids else set()

    def view(user):
        row = rows.get(user)
        def unit(identifier):
            space = spaces.get(identifier)
            return {"id": space.id, "name": space.name,
                    "can_access": (space.id, user) in members} if space else None
        return {"user_id": user, "department": unit(row.department_id) if row else None,
                "team": unit(row.team_id) if row else None, "revision": row.revision if row else None}
    return {user: view(user) for user in users}



def unit_view(row):
    data = {"id": row.id, "name": row.name, "kind": row.kind, "parent_id": row.parent_id}
    data["revision"] = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return data


class UpdateUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_id: uuid.UUID | None = None


class NewUnit(NewWorkspace):
    kind: Literal["division", "department", "team"]


def validate_parent(db, kind, parent):
    expected = {"department": "division", "team": "department"}.get(kind)
    if kind == "division" and parent:
        raise HTTPException(422, "본부는 최상위에 생성합니다.")
    if kind == "team" and not parent:
        raise HTTPException(422, "팀의 상위 부서를 선택하세요.")
    if parent:
        row = db.get(Workspace, parent)
        if not row or row.kind != expected:
            raise HTTPException(422, "본부 → 부 → 팀 순서로 상위 조직을 선택하세요.")


class DeleteUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def install_organization(app):
    engine = app.state.engine

    @app.get("/api/organization/me")
    def mine(request: Request):
        with Session(engine) as db:
            return affiliation_view(db, request.state.user)

    @app.get("/api/organization/units")
    def units(request: Request):
        with Session(engine) as db:
            require_admin(db, request.state.user)
            return [unit_view(s)
                    for s in db.scalars(select(Workspace).order_by(Workspace.name, Workspace.id))]

    @app.get("/api/admin/document-usage")
    def usage(request: Request):
        from .document_usage import document_usage
        with Session(engine) as db:
            require_admin(db, request.state.user)
            return document_usage(db)

    @app.get("/api/organization/accounts/{user_id}")
    def account(user_id: str, request: Request):
        with Session(engine) as db:
            require_admin(db, request.state.user)
            if not db.get(UserAccount, user_id):
                raise HTTPException(404, "계정을 찾을 수 없습니다.")
            return affiliation_view(db, user_id)

    @app.put("/api/organization/accounts/{user_id}")
    def assign(user_id: str, value: Assignment, request: Request):
        with transaction(engine) as db:
            management_lock(db)
            membership_lock(db)
            require_admin(db, request.state.user, lock=True)
            target = db.get(UserAccount, user_id, populate_existing=True)
            if not target or not target.is_active or target.approval_status != "approved":
                raise HTTPException(422, "활성 승인 계정의 소속만 지정할 수 있습니다.")
            dep = str(value.department_id) if value.department_id else None
            team = str(value.team_id) if value.team_id else None
            department = db.get(Workspace, dep) if dep else None
            team_unit = db.get(Workspace, team) if team else None
            if dep and (not department or department.kind != "department"):
                raise HTTPException(422, "소속 부서를 선택하세요.")
            if team and (not dep or not team_unit or team_unit.kind != "team" or team_unit.parent_id != dep):
                raise HTTPException(422, "선택한 부서 아래의 팀을 선택하세요.")
            row = db.get(AccountAffiliation, user_id, populate_existing=True)
            current = (row.department_id, row.team_id) if row else (None, None)
            if current == (dep, team):
                return affiliation_view(db, user_id)
            expected = str(value.expected_revision) if value.expected_revision else None
            if (row.revision if row else None) != expected:
                raise HTTPException(409, "소속이 다른 관리자에 의해 변경됐습니다. 다시 조회하세요.")
            if row:
                row.department_id, row.team_id, row.revision = dep, team, str(uuid.uuid4())
            else:
                db.add(AccountAffiliation(user_id=user_id, department_id=dep, team_id=team, revision=str(uuid.uuid4())))
            db.flush()
            record_event(db, request.state.user, "account.affiliation", "account", user_id)
            return affiliation_view(db, user_id)

    @app.post("/api/organization/units")
    def create_unit(value: NewUnit, request: Request):
        with transaction(engine) as db:
            management_lock(db)
            membership_lock(db)
            require_admin(db, request.state.user, lock=True)
            parent = str(value.parent_id) if value.parent_id else None
            validate_parent(db, value.kind, parent)
            row = db.get(Workspace, str(value.id))
            if row:
                if (row.name, row.kind, row.parent_id, row.created_by) != (value.name, value.kind, parent, request.state.user):
                    raise HTTPException(409, "동일 조직 ID의 생성 내용이 다릅니다.")
                return unit_view(row)
            if db.scalar(select(Workspace.id).where(Workspace.name == value.name, Workspace.parent_id == parent, Workspace.kind == value.kind)):
                raise HTTPException(409, "같은 위치에 같은 이름의 조직이 있습니다.")
            row = Workspace(id=str(value.id), name=value.name, kind=value.kind, parent_id=parent, created_by=request.state.user, created_at=time.time())
            db.add(row); db.flush()
            if row.kind != "division":
                db.add(WorkspaceMember(workspace_id=row.id, user_id=request.state.user, role="manager"))
            record_event(db, request.state.user, "organization.create", "workspace", row.id)
            return unit_view(row)

    @app.patch("/api/organization/units/{unit_id}")
    def rename_unit(unit_id: str, value: UpdateUnit, request: Request):
        try:
            name = NewWorkspace.valid_name(value.name)
        except ValueError:
            raise HTTPException(422, "조직 이름을 입력하세요.")
        with transaction(engine) as db:
            management_lock(db)
            membership_lock(db)
            require_admin(db, request.state.user, lock=True)
            row = db.get(Workspace, unit_id)
            if not row:
                raise HTTPException(404, "조직을 찾을 수 없습니다.")
            parent = (str(value.parent_id) if value.parent_id else None) if "parent_id" in value.model_fields_set else row.parent_id
            if "parent_id" in value.model_fields_set:
                validate_parent(db, row.kind, parent)
            if row.kind == "team" and parent != row.parent_id:
                raise HTTPException(422, "소속 사용자가 있는 팀의 상위 부서는 이 화면에서 변경할 수 없습니다.")
            if row.name == name and row.parent_id == parent:
                return unit_view(row)
            if unit_view(row)["revision"] != value.expected_revision:
                raise HTTPException(409, "조직 정보가 변경됐습니다. 다시 조회하세요.")
            if db.scalar(select(Workspace.id).where(Workspace.id != unit_id, Workspace.name == name, Workspace.parent_id == parent, Workspace.kind == row.kind)):
                raise HTTPException(409, "같은 위치에 같은 이름의 조직이 있습니다.")
            row.name = name
            row.parent_id = parent
            record_event(db, request.state.user, "organization.rename", "workspace", row.id)
            return unit_view(row)

    @app.delete("/api/organization/units/{unit_id}")
    def delete_unit(unit_id: str, value: DeleteUnit, request: Request):
        with transaction(engine) as db:
            management_lock(db)
            membership_lock(db)
            require_admin(db, request.state.user, lock=True)
            row = db.get(Workspace, unit_id)
            if not row:
                return {"ok": True}
            if unit_view(row)["revision"] != value.expected_revision:
                raise HTTPException(409, "조직 정보가 변경됐습니다. 다시 조회하세요.")
            from .workspace_folders import WorkspaceFolder
            occupied = (db.scalar(select(WorkspaceFolder.id).where(WorkspaceFolder.workspace_id == unit_id).limit(1))
                or db.scalar(select(Workspace.id).where(Workspace.parent_id == unit_id).limit(1))
                or db.scalar(select(AccountAffiliation.user_id).where(or_(AccountAffiliation.department_id == unit_id, AccountAffiliation.team_id == unit_id)).limit(1))
                or db.scalar(select(WorkspaceDocument.document_id).where(WorkspaceDocument.workspace_id == unit_id).limit(1))
                or db.scalar(select(WorkspaceShare.document_id).where(WorkspaceShare.workspace_id == unit_id).limit(1))
                or db.scalar(select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == unit_id, WorkspaceMember.user_id != row.created_by).limit(1)))
            if occupied:
                raise HTTPException(409, "하위 조직·소속 사용자·폴더·문서 또는 공유 구성원이 있습니다. 먼저 이동하거나 해제하세요.")
            # Keep affiliation revision tombstones, only remove this unused catalog entry.
            db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == unit_id))
            db.delete(row)
            record_event(db, request.state.user, "organization.delete", "workspace", unit_id)
            return {"ok": True}
