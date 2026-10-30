"""Primary organizational affiliation, independent of document/workspace ACLs.

The experimental organization uses department/team workspace identifiers as its
catalog. Assignment never adds or removes WorkspaceMember or document grants.
"""
import uuid

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import ForeignKey, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .accounts import UserAccount
from .account_management import require_admin
from .audit import record_event
from .db import Base, transaction
from .workspaces import Workspace, WorkspaceMember, membership_lock


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
            return [{"id": s.id, "name": s.name, "kind": s.kind, "parent_id": s.parent_id}
                    for s in db.scalars(select(Workspace).order_by(Workspace.name, Workspace.id))]

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
            membership_lock(db)
            require_admin(db, request.state.user)
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
