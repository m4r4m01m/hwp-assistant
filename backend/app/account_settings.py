"""Explicit server-wide Codex admission setting, separate from account approval."""
import os
import time
from contextvars import ContextVar
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, transaction

engine_context = ContextVar("hwp_account_engine", default=None)


class AccountSetting(Base):
    __tablename__ = "account_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(40))
    updated_by: Mapped[str] = mapped_column(String(80))
    updated_at: Mapped[float] = mapped_column(Float)


def codex_policy(db):
    row = db.get(AccountSetting, "codex_account_access")
    value = row.value if row else os.getenv("HWP_CODEX_ACCOUNT_ACCESS", "allowlist")
    if value not in {"allowlist", "approved_accounts"}:
        raise HTTPException(503, "Codex 계정 이용 정책을 확인하세요.")
    return value


def account_codex_allowed(user, engine=None):
    """None means retain the existing allowlist; False is an explicit denial."""
    engine = engine if engine is not None else engine_context.get()
    if engine is None:
        return None
    from .accounts import UserAccount
    with Session(engine) as db:
        account = db.get(UserAccount, user)
        if account and (not account.is_active or account.approval_status != "approved"):
            return False
        if codex_policy(db) == "approved_accounts" and account:
            return True
    return None


def registered_codex_allowed(user, engine):
    """Revocation fence for managed accounts, preserving legacy worker contracts."""
    if os.getenv("HWP_ACCOUNTS_ENABLED", "0") != "1":
        return True
    from .accounts import UserAccount
    with Session(engine) as db:
        if db.get(UserAccount, user) is None:
            return True
    token = engine_context.set(engine)
    try:
        from .codex_runtime import allowed
        return allowed(user)
    except HTTPException:
        return False
    finally:
        engine_context.reset(token)


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    codex_account_access: Literal["allowlist", "approved_accounts"]


def install_account_settings(app):
    from .accounts import read_input
    from .account_management import require_admin
    from .audit import record_event
    engine = app.state.engine

    @app.get("/api/admin/settings")
    def get_settings(request: Request):
        with Session(engine) as db:
            require_admin(db, request.state.user)
            from .account_notifications import notification_status
            return {"codex_account_access": codex_policy(db), "mattermost": notification_status(db)}

    @app.patch("/api/admin/settings")
    async def set_settings(request: Request):
        value = await read_input(request, PolicyInput)
        with transaction(engine) as db:
            from .account_management import management_lock
            management_lock(db)
            require_admin(db, request.state.user, lock=True)
            row = db.get(AccountSetting, "codex_account_access")
            if row is None:
                row = AccountSetting(key="codex_account_access")
                db.add(row)
            row.value, row.updated_by, row.updated_at = value.codex_account_access, request.state.user, time.time()
            record_event(db, request.state.user, "settings.codex_access", "setting", row.key,
                         details={"mode": row.value})
            return {"codex_account_access": row.value}

    @app.post("/api/admin/notifications/retry")
    def retry_notifications(request: Request):
        from .account_notifications import RegistrationNotification, webhook_url
        from .account_management import management_lock
        from sqlalchemy import select
        with transaction(engine) as db:
            management_lock(db)
            require_admin(db, request.state.user, lock=True)
            if not webhook_url():
                raise HTTPException(409, "서버의 Mattermost 알림 설정을 먼저 확인하세요.")
            rows = list(db.scalars(select(RegistrationNotification).where(RegistrationNotification.status == "failed").with_for_update()))
            for row in rows:
                row.status, row.attempts, row.next_attempt_at, row.outcome = "pending", 0, time.time(), "queued"
            record_event(db, request.state.user, "notifications.retry", "notification", details={"outcome": "queued"})
            return {"queued": len(rows)}
