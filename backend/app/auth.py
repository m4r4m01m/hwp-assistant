"""Opaque login credentials and revocable browser sessions, with no client-supplied identity."""
import hashlib
import json
import os
import re
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from math import ceil
from threading import Lock
from time import monotonic
from fastapi import HTTPException
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.orm import Session
from .db import Access, AuthSession, Document, LeaseOwner, transaction
from .host_validation import allowed_hosts, host_name

ROLES = {"reader": 1, "editor": 2, "owner": 3}
COOKIE = "hwp_session"
SESSION_PREFIX = "hwp-session-"
SESSION_PATTERN = re.compile(r"hwp-session-[A-Za-z0-9_-]{43}\Z")
LOGIN_REQUIRED = "로그인이 필요하거나 인증 토큰이 만료되었습니다."


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


def issue_session(engine, user, users, policy, previous=""):
    now = time.time()
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    with transaction(engine) as db:
        if engine.dialect.name == "postgresql":
            # Serialize issuance for this user across processes; SQLite's transaction does this already.
            key = int.from_bytes(hashlib.sha256(f"browser-sessions:{user}".encode()).digest()[:8], "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
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
                           credential_hash=credential_hash(user, users[user]), created_at=now,
                           expires_at=now + policy.absolute_seconds, last_seen_at=now, revoked_at=None))
    return token


def authenticate_session(engine, token, users, policy):
    if not SESSION_PATTERN.fullmatch(token):
        raise HTTPException(401, LOGIN_REQUIRED)
    token_hash = session_hash(token)
    now = time.time()
    with Session(engine) as db:
        row = db.get(AuthSession, token_hash)
        if (row is None or row.revoked_at is not None or row.expires_at <= now
                or row.last_seen_at <= now - policy.idle_seconds or row.user_id not in users
                or not secrets.compare_digest(row.credential_hash, credential_hash(row.user_id, users[row.user_id]))):
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


def revoke_session(engine, token):
    if SESSION_PATTERN.fullmatch(token):
        with transaction(engine) as db:
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


def require_access(db, doc_id, user, role="reader", *, lock=False, fresh=False):
    query = select(Document).where(Document.id == doc_id)
    if fresh:
        query = query.execution_options(populate_existing=True)
    doc = db.scalar(query.with_for_update() if lock else query)
    access = db.get(Access, (doc_id, user), populate_existing=fresh)
    if not doc or not access:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    if ROLES[access.role] < ROLES[role]:
        raise HTTPException(403, "이 작업을 수행할 문서 권한이 없습니다.")
    return doc


def bind_lease(db, doc, user):
    owner = db.get(LeaseOwner, doc.id)
    if owner:
        owner.user_id = user
    else:
        db.add(LeaseOwner(document_id=doc.id, user_id=user))


def require_lease_owner(db, doc, user, *, fresh=False):
    owner = db.get(LeaseOwner, doc.id, populate_existing=fresh)
    if not owner or owner.user_id != user:
        raise HTTPException(409, "다른 사용자의 편집 세션입니다.")
