"""Role-authorized account review. No document privilege escalation or hard deletion."""
import hashlib
import re
import secrets
import time
from typing import Literal

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text
from starlette.concurrency import run_in_threadpool

from .accounts import UserAccount, read_input, valid_password
from .db import transaction


def management_lock(db):
    # Shared with signup and the local operator. Serializes last-admin checks and
    # review changes; SQLite's transaction helper already uses BEGIN IMMEDIATE.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(706251025)"))


def require_admin(db, user, *, lock=False):
    query = select(UserAccount).where(UserAccount.user_id == user).execution_options(populate_existing=True)
    row = db.scalar(query.with_for_update() if lock else query)
    if not row or not row.is_active or row.approval_status != "approved" or row.role != "admin":
        raise HTTPException(403, "계정 관리 권한이 필요합니다.")
    return row


def management_revision(row):
    # Separate, one-way public view fence. Never expose the password-session
    # binding, raw password hash, or raw session revision to browser management.
    return hashlib.sha256(("account-management-v1\0" + row.session_revision).encode()).hexdigest()


def account_view(row):
    status = row.approval_status if row.approval_status != "approved" else "approved" if row.is_active else "disabled"
    return {"user_id": row.user_id, "name": row.name, "kind": row.kind,
            "role": row.role, "is_admin": row.role == "admin", "is_active": row.is_active,
            "approval_status": row.approval_status, "status": status,
            "created_at": row.created_at, "last_login_at": row.last_login_at,
            "reviewed_by": row.reviewed_by, "reviewed_at": row.reviewed_at,
            "revision": management_revision(row)}


def protect_last_admin(db, row):
    if row.role == "admin" and row.is_active and row.approval_status == "approved":
        active = db.scalar(select(func.count()).select_from(UserAccount).where(
            UserAccount.role == "admin", UserAccount.is_active.is_(True), UserAccount.approval_status == "approved"))
        if active <= 1:
            raise HTTPException(409, "마지막 활성 관리자 계정은 비활성화할 수 없습니다.")


class AccountAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class RoleChange(AccountAction):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: Literal["admin", "member"]


class PasswordReset(AccountAction):
    password: str = Field(min_length=8, max_length=128)
    _password = field_validator("password")(valid_password)


def install_account_management(app):
    from .audit import record_event
    engine = app.state.engine

    def check_enabled():
        if not app.state.account_policy.enabled:
            raise HTTPException(404, "계정 관리가 활성화되지 않았습니다.")

    @app.get("/api/admin/accounts")
    def accounts(request: Request, status: str | None = Query(default=None, pattern=r"^(pending|approved|rejected|disabled)$"),
                 limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0, le=10000)):
        from sqlalchemy.orm import Session
        check_enabled()
        with Session(engine) as db:
            require_admin(db, request.state.user)
            query = select(UserAccount)
            if status == "disabled":
                query = query.where(UserAccount.approval_status == "approved", UserAccount.is_active.is_(False))
            elif status:
                query = query.where(UserAccount.approval_status == status)
                if status == "approved":
                    query = query.where(UserAccount.is_active.is_(True))
            total = db.scalar(select(func.count()).select_from(query.subquery()))
            rows = list(db.scalars(query.order_by(UserAccount.created_at.desc(), UserAccount.user_id).limit(limit).offset(offset)))
            from .organization import affiliation_views
            affiliations = affiliation_views(db, [row.user_id for row in rows])
            return {"items": [{**account_view(row), "affiliation": affiliations[row.user_id]}
                              for row in rows], "total": total}

    async def change(request, user_id, action, model=AccountAction):
        from .accounts import hash_password
        check_enabled()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.@-]{0,79}", user_id):
            raise HTTPException(404, "계정을 찾을 수 없습니다.")
        from sqlalchemy.orm import Session
        with Session(engine) as db:
            require_admin(db, request.state.user)
        value = await read_input(request, model)
        # Hashing is outside the write transaction and bounded by the same
        # two-operation admission gate as login/signup. Authorization is checked
        # again under the write lock before applying any account change.
        def operation():
            encoded = hash_password(value.password) if action == "password" else None
            with transaction(engine) as db:
                management_lock(db)
                require_admin(db, request.state.user, lock=True)
                row = db.scalar(select(UserAccount).where(UserAccount.user_id == user_id).with_for_update())
                if not row:
                    raise HTTPException(404, "계정을 찾을 수 없습니다.")
                if value.expected_revision is not None and not secrets.compare_digest(value.expected_revision, management_revision(row)):
                    raise HTTPException(409, "계정 상태가 바뀌었습니다. 목록을 새로고침한 뒤 다시 확인하세요.")
                if action in ("approve", "reject"):
                    if row.approval_status != "pending":
                        raise HTTPException(409, "이미 처리된 가입 요청입니다. 목록을 새로고침하세요.")
                    row.approval_status = "approved" if action == "approve" else "rejected"
                    row.is_active = action == "approve"
                    row.reviewed_by, row.reviewed_at = request.state.user, time.time()
                elif action == "disable":
                    if row.approval_status != "approved" or not row.is_active:
                        raise HTTPException(409, "활성 승인 계정만 비활성화할 수 있습니다.")
                    protect_last_admin(db, row)
                    row.is_active = False
                elif action == "enable":
                    if row.approval_status != "approved" or row.is_active:
                        raise HTTPException(409, "비활성 승인 계정만 다시 활성화할 수 있습니다.")
                    row.is_active = True
                elif action == "role":
                    if row.kind != "member" or row.user_id in ("guest", "local") or not row.is_active or row.approval_status != "approved":
                        raise HTTPException(422, "활성 승인된 개인 계정의 권한만 변경할 수 있습니다.")
                    if row.role == value.role:
                        return account_view(row)
                    if row.role == "admin":
                        protect_last_admin(db, row)
                    row.role = value.role
                elif action == "password":
                    row.password_hash = encoded
                row.session_revision = secrets.token_hex(32)
                record_event(db, request.state.user, "account." + action, "account", row.user_id)
                db.flush()
                return account_view(row)
        if action == "password":
            # Auth gate before admission/hashing, rechecked inside write lock.
            limiter = app.state.account_limiter
            limiter.charge(request.client.host if request.client else "unknown", request.state.user)
            return await run_in_threadpool(limiter.run, operation)
        return await run_in_threadpool(operation)

    @app.post("/api/admin/accounts/{user_id}/approve")
    async def approve(user_id: str, request: Request):
        return await change(request, user_id, "approve")

    @app.post("/api/admin/accounts/{user_id}/reject")
    async def reject(user_id: str, request: Request):
        return await change(request, user_id, "reject")

    @app.post("/api/admin/accounts/{user_id}/disable")
    async def disable(user_id: str, request: Request):
        return await change(request, user_id, "disable")

    @app.post("/api/admin/accounts/{user_id}/enable")
    async def enable(user_id: str, request: Request):
        return await change(request, user_id, "enable")

    @app.post("/api/admin/accounts/{user_id}/password")
    async def password(user_id: str, request: Request):
        return await change(request, user_id, "password", PasswordReset)

    @app.post("/api/admin/accounts/{user_id}/role")
    async def role(user_id: str, request: Request):
        return await change(request, user_id, "role", RoleChange)
