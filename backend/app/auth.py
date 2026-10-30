"""Opaque login credentials and revocable browser sessions, with no client-supplied identity."""
import hashlib
import json
import os
import re
import secrets
import time
from contextvars import ContextVar
from collections import OrderedDict, deque
from dataclasses import dataclass
from math import ceil
from threading import Lock
from time import monotonic
from fastapi import HTTPException
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.orm import Session
from .db import Access, AuthSession, Document, LeaseOwner, LeaseLogin, transaction
from .host_validation import allowed_hosts, host_name

ROLES = {"reader": 1, "editor": 2, "owner": 3}
COOKIE = "hwp_session"
SESSION_PREFIX = "hwp-session-"
SESSION_PATTERN = re.compile(r"hwp-session-[A-Za-z0-9_-]{43}\Z")
LOGIN_REQUIRED = "로그인이 필요하거나 인증 토큰이 만료되었습니다."
lease_login_context = ContextVar("lease_login", default=None)


def login_scope(user, credential):
    return hashlib.sha256(f"editor-login-v1\0{user}\0{credential}".encode()).hexdigest()


@dataclass(frozen=True)
class SessionPolicy:
    absolute_seconds: int = 28800
    idle_seconds: int = 1800
    max_per_user: int = 20

    @classmethod
    def from_env(cls):
        absolute = int(os.getenv("HWP_AUTH_SESSION_TTL_SECONDS", "28800"))
        idle = int(os.getenv("HWP_AUTH_SESSION_IDLE_SECONDS", str(min(1800, absolute))))
        maximum = int(os.getenv("HWP_AUTH_SESSION_MAX_PER_USER", "20"))
        if not 60 <= absolute <= 604800 or not 60 <= idle <= absolute or not 1 <= maximum <= 100:
            raise ValueError("Invalid browser session limits (TTL 60..604800, idle 60..TTL, sessions 1..100)")
        return cls(absolute, idle, maximum)

    @property
    def touch_seconds(self):
        return min(60, self.idle_seconds / 4)


class LoginRateLimiter:
    """Short, bounded client failure buckets; never retain a credential or account lockout.

    Uses the ASGI client address only. The trusted ingress must replace untrusted
    forwarding headers before Uvicorn resolves that address. This is process-local,
    matching the production single worker; it is not a distributed abuse quota.
    """
    def __init__(self, *, max_failures=20, window_seconds=60, max_clients=1024, clock=monotonic):
        if max_failures < 1 or window_seconds <= 0 or max_clients < 1:
            raise ValueError("Login failure limits must be positive")
        self.max_failures, self.window_seconds, self.max_clients = max_failures, window_seconds, max_clients
        self.clock, self.lock, self.failures = clock, Lock(), OrderedDict()

    def authenticate(self, client, token, users):
        # Hash to a fixed-size key, without persisting an address or a submitted secret.
        key = hashlib.sha256(client.encode()).digest()
        with self.lock:
            now = self.clock()
            cutoff = now - self.window_seconds
            # Buckets are ordered by their most recent failed attempt, not blocked retries.
            while self.failures:
                oldest = next(iter(self.failures.values()))
                if oldest[-1] > cutoff:
                    break
                self.failures.popitem(last=False)
            failures = self.failures.get(key)
            if failures is not None:
                while failures and failures[0] <= cutoff:
                    failures.popleft()
                if len(failures) >= self.max_failures:
                    retry = max(1, ceil(failures[0] + self.window_seconds - now))
                    raise HTTPException(429, "로그인 실패가 반복되었습니다. 잠시 뒤 다시 시도해 주세요.",
                                        headers={"Retry-After": str(retry)})
            try:
                # Compare while holding the short lock so concurrent guesses cannot pass
                # the check together. No database or network work runs under this lock.
                user = authenticate(token, users)
            except HTTPException as exc:
                if exc.status_code == 401:
                    failures = self.failures.setdefault(key, deque())
                    failures.append(now)
                    self.failures.move_to_end(key)
                    if len(self.failures) > self.max_clients:
                        self.failures.popitem(last=False)
                raise
            self.failures.pop(key, None)
            return user


def request_origin(scope, hosts):
    values = [value for key, value in scope["headers"] if key.lower() == b"host"]
    try:
        if len(values) != 1:
            raise ValueError("Exactly one Host header is required")
        name, port = host_name(values[0].decode("ascii"))
        if hosts and not any(name == item or (item.startswith("*.") and name.endswith(item[1:])) for item in hosts):
            raise ValueError("Untrusted Host header")
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(400, "허용되지 않거나 올바르지 않은 Host 헤더입니다.") from exc
    scheme = scope.get("scheme", "http")
    authority = name if port is None or port == {"http": 80, "https": 443}.get(scheme) else f"{name}:{port}"
    return f"{scheme}://{authority}"


def session_hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def credential_hash(user, token):
    # Rotation/removal of a login credential also invalidates its existing sessions.
    return hashlib.sha256(f"browser-session-v1\0{user}\0{token}".encode()).hexdigest()


def issue_session(engine, user, users, policy, previous="", *, account_binding=None):
    now = time.time()
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    with transaction(engine) as db:
        if engine.dialect.name == "postgresql":
            # Serialize issuance for this user across processes; SQLite's transaction does this already.
            key = int.from_bytes(hashlib.sha256(f"browser-sessions:{user}".encode()).digest()[:8], "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        if account_binding is not None:
            from .accounts import UserAccount, session_binding
            account = db.scalar(select(UserAccount).where(UserAccount.user_id == user).with_for_update())
            if (not account or not account.is_active or account.approval_status != "approved"
                    or not secrets.compare_digest(account_binding, session_binding(account))):
                raise HTTPException(401, LOGIN_REQUIRED)
            account.last_login_at = now
        # A revoked hash has no audit payload to retain. Prune it on issuance so
        # repeated valid logins cannot accumulate unbounded rows within the TTL.
        db.execute(delete(AuthSession).where(or_(AuthSession.expires_at <= now,
                                                AuthSession.revoked_at.is_not(None))))
        if SESSION_PATTERN.fullmatch(previous):
            db.execute(update(AuthSession).where(AuthSession.token_hash == session_hash(previous),
                       AuthSession.revoked_at.is_(None)).values(revoked_at=now))
        active = list(db.scalars(select(AuthSession).where(AuthSession.user_id == user,
                      AuthSession.revoked_at.is_(None)).order_by(AuthSession.created_at.desc())))
        for old in active[policy.max_per_user - 1:]:
            old.revoked_at = now
        db.add(AuthSession(token_hash=session_hash(token), user_id=user,
                           credential_hash=account_binding if account_binding is not None else credential_hash(user, users[user]), created_at=now,
                           expires_at=now + policy.absolute_seconds, last_seen_at=now, revoked_at=None))
        from .audit import record_event
        record_event(db, user, "account.login", "account", user,
                     details={"mode": "password" if account_binding is not None else "token"})
    return token


def authenticate_session(engine, token, users, policy, accounts_enabled=False):
    if not SESSION_PATTERN.fullmatch(token):
        raise HTTPException(401, LOGIN_REQUIRED)
    token_hash = session_hash(token)
    now = time.time()
    with Session(engine) as db:
        row = db.get(AuthSession, token_hash)
        valid = bool(row and row.user_id in users and secrets.compare_digest(
            row.credential_hash, credential_hash(row.user_id, users[row.user_id])))
        if row and accounts_enabled:
            from .accounts import UserAccount, session_binding
            account = db.get(UserAccount, row.user_id)
            if account and (not account.is_active or account.approval_status != "approved"):
                valid = False
            elif account and secrets.compare_digest(row.credential_hash, session_binding(account)):
                valid = True
        if (row is None or row.revoked_at is not None or row.expires_at <= now
                or row.last_seen_at <= now - policy.idle_seconds or not valid):
            raise HTTPException(401, LOGIN_REQUIRED)
        user, touched_at = row.user_id, row.last_seen_at
    # Read-only for most requests. Do not hold a read transaction while acquiring a write lock.
    if touched_at <= now - policy.touch_seconds:
        with transaction(engine) as db:
            db.execute(update(AuthSession).where(AuthSession.token_hash == token_hash,
                       AuthSession.revoked_at.is_(None), AuthSession.expires_at > now,
                       AuthSession.last_seen_at > now - policy.idle_seconds,
                       AuthSession.last_seen_at <= now - policy.touch_seconds).values(last_seen_at=now))
    return user


def require_password_session(engine, token):
    """When legacy access is retired, old token-derived cookies are retired too."""
    from .accounts import UserAccount, session_binding
    if not SESSION_PATTERN.fullmatch(token):
        raise HTTPException(401, LOGIN_REQUIRED)
    with Session(engine) as db:
        row = db.get(AuthSession, session_hash(token))
        account = db.get(UserAccount, row.user_id) if row else None
        if (not account or not account.is_active or account.approval_status != "approved"
                or not secrets.compare_digest(row.credential_hash, session_binding(account))):
            raise HTTPException(401, LOGIN_REQUIRED)


def revoke_session(engine, token):
    if SESSION_PATTERN.fullmatch(token):
        with transaction(engine) as db:
            row = db.get(AuthSession, session_hash(token))
            if row:
                principal = login_scope(row.user_id, token)
                from .coedit import Member, Room
                from .rewrite_jobs import invalidate_running
                ids = set(db.scalars(select(LeaseLogin.document_id).where(LeaseLogin.login_hash == principal)))
                ids.update(db.scalars(select(Member.document_id).where(Member.login_hash == principal)))
                for doc_id in sorted(ids):
                    doc = db.scalar(select(Document).where(Document.id == doc_id).with_for_update())
                    if not doc: continue
                    db.execute(update(Member).where(Member.document_id == doc_id, Member.login_hash == principal).values(expires_at=0))
                    binding = db.get(LeaseLogin, doc_id)
                    if (doc.lease_token or "").startswith("coedit:"):
                        room = db.get(Room, doc_id)
                        active = db.scalar(select(Member.id).where(Member.document_id == doc_id, Member.generation == room.generation, Member.expires_at > time.time()).limit(1)) if room else None
                        release = not active
                    else:
                        release = binding and binding.login_hash == principal
                    if release:
                        doc.lease_token = None; doc.lease_until = 0
                        invalidate_running(db, doc_id, "로그아웃하여 편집 세션을 종료했습니다.")
            db.execute(update(AuthSession).where(AuthSession.token_hash == session_hash(token),
                       AuthSession.revoked_at.is_(None)).values(revoked_at=time.time()))


def credentials():
    raw = os.getenv("HWP_AUTH_TOKENS", "")
    if not raw:
        return {}
    values = json.loads(raw)
    if not isinstance(values, dict) or not values:
        raise ValueError("HWP_AUTH_TOKENS must be a nonempty user-to-token object")
    if any(not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", u) or
           not isinstance(t, str) or len(t) < 24 for u, t in values.items()):
        raise ValueError("Invalid HWP_AUTH_TOKENS user or token (minimum 24 characters)")
    try:
        for token in values.values():
            token.encode("utf-8")
    except UnicodeError:
        raise ValueError("HWP_AUTH_TOKENS credentials must be valid UTF-8") from None
    if len(set(values.values())) != len(values):
        raise ValueError("Each user needs a unique token")
    return values


def authenticate(token, users):
    try:
        candidate = token.encode("utf-8")
    except UnicodeError:
        # JSON can contain escaped lone surrogates. Reject without echoing the
        # credential, raising a 500, or bypassing failed-login accounting.
        raise HTTPException(401, LOGIN_REQUIRED) from None
    for user, expected in users.items():
        if secrets.compare_digest(candidate, expected.encode("utf-8")):
            return user
    raise HTTPException(401, LOGIN_REQUIRED)


def require_access(db, doc_id, user, role="reader", *, lock=False, fresh=False, include_trashed=False):
    if os.getenv("HWP_ACCOUNTS_ENABLED", "0") == "1":
        from .accounts import UserAccount
        account = db.get(UserAccount, user, populate_existing=fresh)
        if account and (not account.is_active or account.approval_status != "approved"):
            raise HTTPException(401, LOGIN_REQUIRED)
    query = select(Document).where(Document.id == doc_id)
    if fresh:
        query = query.execution_options(populate_existing=True)
    doc = db.scalar(query.with_for_update() if lock else query)
    from .db import document_is_trashed
    if not include_trashed and db.scalar(select(document_is_trashed(doc_id))):
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    from .library_access import document_role
    access_role = document_role(db, doc_id, user, fresh=fresh or lock)
    if not doc or access_role is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    if ROLES[access_role] < ROLES[role]:
        raise HTTPException(403, "이 작업을 수행할 문서 권한이 없습니다.")
    return doc


def bind_lease(db, doc, user):
    owner = db.get(LeaseOwner, doc.id)
    if owner:
        owner.user_id = user
    else:
        db.add(LeaseOwner(document_id=doc.id, user_id=user))
    binding = db.get(LeaseLogin, doc.id)
    principal = lease_login_context.get()
    if principal is not None:
        if binding:
            binding.login_hash = principal
        else:
            db.add(LeaseLogin(document_id=doc.id, login_hash=principal))
    elif binding:
        db.delete(binding)


def require_lease_owner(db, doc, user, *, fresh=False):
    owner = db.get(LeaseOwner, doc.id, populate_existing=fresh)
    if not owner or owner.user_id != user:
        raise HTTPException(409, "다른 사용자의 편집 세션입니다.")
    binding = db.get(LeaseLogin, doc.id, populate_existing=fresh)
    principal = lease_login_context.get()
    if binding and principal is not None and not secrets.compare_digest(binding.login_hash, principal):
        raise HTTPException(409, "로그인 세션이 변경되었습니다. 미저장 내용을 다운로드한 뒤 문서를 다시 여세요.")
