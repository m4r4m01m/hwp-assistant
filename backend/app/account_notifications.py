"""Durable bounded Mattermost signup notifications. Credentials never enter DB/logs."""
import asyncio
import logging
import os
import time
from contextvars import ContextVar
from urllib.parse import urlsplit

import httpx
from sqlalchemy import Float, Integer, String, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, transaction

_sending_webhook = ContextVar("hwp_sending_webhook", default=False)


class _WebhookLogFilter(logging.Filter):
    def filter(self, record):
        # HTTPX logs full request URLs at INFO. A webhook path is a credential;
        # suppress transport logging only in this delivery's async context.
        return not _sending_webhook.get()


_webhook_log_filter = _WebhookLogFilter()
for _logger in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy", "httpcore.socks"):
    logging.getLogger(_logger).addFilter(_webhook_log_filter)


class RegistrationNotification(Base):
    __tablename__ = "registration_notifications"
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float)
    next_attempt_at: Mapped[float] = mapped_column(Float)
    delivered_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), default="queued")


def webhook_url():
    value = os.getenv("HWP_MATTERMOST_WEBHOOK_URL", "")
    if not value:
        return None
    try:
        url = urlsplit(value)
        allowed = url.scheme == "https" or (url.scheme == "http" and os.getenv("HWP_MATTERMOST_ALLOW_HTTP") == "1")
        if not allowed or not url.hostname or url.username or url.password or url.query or url.fragment or not url.path.startswith("/hooks/"):
            return None
        _ = url.port
    except ValueError:
        return None
    return value


def enqueue_registration_notification(db, user_id):
    # One durable receipt per reserved ID. Duplicate signup never requeues it.
    if db.get(RegistrationNotification, user_id) is None:
        db.add(RegistrationNotification(user_id=user_id, status="pending", attempts=0,
               created_at=time.time(), next_attempt_at=time.time(), outcome="queued"))


async def post_notification(url, user_id):
    label = user_id.replace("@", "＠")
    payload = {"text": f"HWP Assistant 회원가입 승인 요청: `{label}`\nadmin 계정으로 로그인하여 계정 관리에서 확인하세요."}
    token = _sending_webhook.set(True)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4), follow_redirects=False, trust_env=False) as client:
            # Do not retain a response body, URL, exception, name, or employee number.
            async with client.stream("POST", url, json=payload) as response:
                return 200 <= response.status_code < 300
    finally:
        _sending_webhook.reset(token)


async def deliver_registration_notification(engine, user_id):
    url = webhook_url()
    if not url:
        return
    now = time.time()
    with transaction(engine) as db:
        row = db.scalar(select(RegistrationNotification).where(
            RegistrationNotification.user_id == user_id).with_for_update())
        if row is None or row.status == "delivered" or row.attempts >= 5 or row.next_attempt_at > now:
            return
        from .accounts import UserAccount
        account = db.get(UserAccount, user_id)
        if account is None or account.approval_status != "pending":
            row.status, row.outcome = "superseded", "already_reviewed"
            return
        row.attempts += 1
        attempt = row.attempts
        # A 30-second lease also recovers a process interrupted during delivery.
        row.next_attempt_at, row.status = now + 30, "pending"
    try:
        success = await post_notification(url, user_id)
    except Exception:
        success = False
    with transaction(engine) as db:
        row = db.scalar(select(RegistrationNotification).where(
            RegistrationNotification.user_id == user_id).with_for_update())
        if row is None or row.attempts != attempt or row.status == "delivered":
            return
        row.status = "delivered" if success else "failed" if attempt >= 5 else "pending"
        row.outcome = "delivered" if success else "delivery_failed"
        row.delivered_at = time.time() if success else None
        row.next_attempt_at = time.time() + min(3600, 30 * 2 ** attempt)


async def deliver_safely(engine, user_id):
    # Background delivery must never turn a committed signup into an HTTP error.
    try:
        await deliver_registration_notification(engine, user_id)
    except Exception:
        pass  # Durable lease remains retryable; never log exception/secret URL.


async def notification_loop(engine):
    while True:
        try:
            if not webhook_url():
                await asyncio.sleep(30)
                continue
            with transaction(engine) as db:
                db.execute(update(RegistrationNotification).where(
                    RegistrationNotification.status == "pending", RegistrationNotification.attempts >= 5,
                    RegistrationNotification.next_attempt_at <= time.time()).values(status="failed", outcome="delivery_failed"))
            with Session(engine) as db:
                users = list(db.scalars(select(RegistrationNotification.user_id).where(
                    RegistrationNotification.status == "pending", RegistrationNotification.attempts < 5,
                    RegistrationNotification.next_attempt_at <= time.time()).order_by(
                    RegistrationNotification.created_at).limit(5)))
            for user in users:
                await deliver_safely(engine, user)
        except Exception:
            pass  # A transient DB outage must not permanently stop the dispatcher.
        await asyncio.sleep(30)


def notification_status(db):
    from sqlalchemy import func
    return {"configured": bool(webhook_url()),
            "pending": db.scalar(select(func.count()).select_from(RegistrationNotification).where(RegistrationNotification.status == "pending")),
            "failed": db.scalar(select(func.count()).select_from(RegistrationNotification).where(RegistrationNotification.status == "failed"))}
