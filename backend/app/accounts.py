"""Password accounts for a bounded PoC. Static automation tokens remain separate.

No public member directory or plaintext password storage. New signups await review.
The shared guest identity deliberately retains its existing document ownership.
"""
import hashlib
import json
import os
import re
import secrets
import time
import unicodedata
from collections import OrderedDict, deque
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Boolean, Float, String, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column
from starlette.concurrency import run_in_threadpool

from .db import Base, transaction


class UserAccount(Base):
    __tablename__ = "user_accounts"
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(80))
    employee_number: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(12), default="member")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String(12), default="member", server_default="member")
    approval_status: Mapped[str] = mapped_column(String(12), default="approved", server_default="approved")
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    session_revision: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float)
    last_login_at: Mapped[float | None] = mapped_column(Float, nullable=True)


def flag(name, default="0"):
    value = os.getenv(name, default)
    if value not in ("0", "1"):
        raise ValueError(f"{name} must be 0 or 1")
    return value == "1"


@dataclass(frozen=True)
class AccountPolicy:
    enabled: bool
    registration: bool
    maximum: int
    # Explicit public synthetic labels only, never a directory of account rows.
    sample_accounts: tuple[tuple[str, str], ...] = ()
    require_approval: bool = True

    @classmethod
    def from_env(cls):
        enabled = flag("HWP_ACCOUNTS_ENABLED")
        registration = flag("HWP_REGISTRATION_ENABLED")
        maximum = int(os.getenv("HWP_REGISTRATION_MAX_USERS", "100"))
        if not 1 <= maximum <= 10000 or registration and not enabled:
            raise ValueError("Enable accounts before registration; maximum must be 1..10000")
        return cls(enabled, registration, maximum, sample_account_settings(enabled),
                   flag("HWP_REGISTRATION_REQUIRE_APPROVAL", "1"))


def sample_account_settings(accounts_enabled):
    """Validate a small public allowlist without logging configuration values.

    Operators create accounts separately with account_admin. Listing an ID never
    creates a principal, supplies a password or changes any document/AI grants.
    """
    error = "HWP_SAMPLE_ACCOUNTS must be a JSON array of at most 8 unique canonical member IDs and public labels; enable accounts first"
    raw = os.getenv("HWP_SAMPLE_ACCOUNTS", "[]")
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate field")
            value[key] = item
        return value
    try:
        if len(raw) > 4096:
            raise ValueError("size")
        values = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(values, list) or len(values) > 8 or values and not accounts_enabled:
            raise ValueError("list")
        result, seen = [], set()
        for item in values:
            if not isinstance(item, dict) or set(item) != {"user_id", "label"}:
                raise ValueError("fields")
            user, label = item["user_id"], item["label"]
            if (not isinstance(user, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.@-]{2,39}", user)
                    or user in {"local", "guest"} or user in seen):
                raise ValueError("identity")
            if (not isinstance(label, str) or not 1 <= len(label) <= 40 or label != label.strip()
                    or any(unicodedata.category(c).startswith("C") for c in label)):
                raise ValueError("label")
            seen.add(user)
            result.append((user, label))
        return tuple(result)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError(error) from None


def public_sample_accounts(db, policy):
    """Filter configured cards to live password identities, preserving order.

    Missing/retired/disabled IDs stay hidden. Database names, employee numbers
    and hashes never enter this public response. Status does not issue a session.
    """
    if not policy.enabled or not policy.sample_accounts:
        return []
    ids = [user for user, _ in policy.sample_accounts]
    active = set(db.scalars(select(UserAccount.user_id).where(
        UserAccount.user_id.in_(ids), UserAccount.is_active.is_(True), UserAccount.approval_status == "approved", UserAccount.kind == "member")))
    return [{"user_id": user, "label": label} for user, label in policy.sample_accounts if user in active]


def valid_password(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 128:
        raise ValueError("password length")
    if any(unicodedata.category(c) in ("Cs", "Cc") for c in value) or len(value.encode("utf-8")) > 512:
        raise ValueError("password encoding")
    return value


def hash_password(password):
    valid_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=32768, r=8, p=3,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt-v1${salt.hex()}${digest.hex()}"


# The same expensive operation for absent/disabled accounts, without making a
# random hash at import time. Fixed dummy salt is NOT used for real passwords.
DUMMY_HASH = "scrypt-v1$" + "00" * 16 + "$" + "00" * 32


def verify_password(password, encoded):
    try:
        version, salt, expected = encoded.split("$")
        if version != "scrypt-v1" or len(salt) != 32 or len(expected) != 64:
            raise ValueError("invalid hash")
        valid_password(password)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=32768, r=8, p=3,
                                maxmem=64 * 1024 * 1024, dklen=32)
        return secrets.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError, AttributeError):
        return False


def session_binding(row):
    return hashlib.sha256(f"password-session-v1\0{row.user_id}\0{row.password_hash}\0{row.session_revision}".encode()).hexdigest()


def account_exists(engine, user):
    with Session(engine) as db:
        row = db.get(UserAccount, user)
        return bool(row and row.is_active and row.approval_status == "approved")


def require_active_if_registered(engine, user):
    with Session(engine) as db:
        row = db.get(UserAccount, user)
        if row is not None and (not row.is_active or row.approval_status != "approved"):
            raise HTTPException(401, "이 계정은 비활성화되어 있습니다.")


def historical_identity_in_use(db, user):
    """A removed token does not free a principal for public re-registration.

    An operator may deliberately attach a password to an existing identity via
    account_admin. Public signup must never inherit its documents or audit trail.
    Match legacy case variants too, since new usernames are normalized lowercase.
    """
    from .db import (Access, Application, AuthSession, Conversation, ConversationTurn,
                     LeaseOwner, Message, Proposal, ReportTask, RewriteTask,
                     TemplateFavorite, VersionAuthor, VisualStandard)
    from .audit import AuditEvent
    from .account_notifications import RegistrationNotification
    if db.scalar(select(AuditEvent.id).where(AuditEvent.target_type == "account",
                 func.lower(AuditEvent.target_id) == user).limit(1)) is not None:
        return True
    columns = (AuditEvent.actor_id, RegistrationNotification.user_id,
               Access.user_id, Application.approved_by, AuthSession.user_id,
               Conversation.owner_id, ConversationTurn.requested_by,
               LeaseOwner.user_id, Message.author_id, Proposal.author_id,
               ReportTask.owner_id, RewriteTask.requested_by,
               TemplateFavorite.user_id, VersionAuthor.user_id, VisualStandard.owner_id)
    return any(db.scalar(select(column).where(func.lower(column) == user).limit(1)) is not None
               for column in columns)


def public_account(row):
    # Employee number is intentionally not sent to the browser after signup.
    return {"user_id": row.user_id, "name": row.name, "kind": row.kind}


def initialize_accounts(engine, policy, users):
    """First-use guest only. Never rotate a password or reassign documents on startup."""
    if not policy.enabled:
        return
    password = os.getenv("HWP_GUEST_PASSWORD", "")
    if not password:
        return
    valid_password(password)
    with transaction(engine) as db:
        if engine.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(706251025)"))
        if db.get(UserAccount, "guest") is None:
            db.add(UserAccount(user_id="guest", name="공용 체험", employee_number=None,
                password_hash=hash_password(password), kind="guest", is_active=True,
                session_revision=secrets.token_hex(32), created_at=time.time()))


class AttemptLimiter:
    """Bound all attempts BEFORE password hashing, including successful signups.

    Process-local: production intentionally has one worker. Bounded cardinality,
    per-client + per-account buckets, plus two concurrent password operations.
    """
    def __init__(self):
        self.lock = Lock()
        self.buckets = OrderedDict()
        self.capacity = BoundedSemaphore(2)

    def charge(self, client, username, *, registration=False):
        now = time.monotonic()
        entries = [("client:" + client, 20, 60), ("account:" + username, 10, 60)]
        if registration:
            entries.append(("signup:" + client, 6, 3600))
        with self.lock:
            for raw, maximum, period in entries:
                key = hashlib.sha256(raw.encode()).digest()
                bucket = self.buckets.setdefault(key, deque())
                while bucket and bucket[0] <= now - period:
                    bucket.popleft()
                self.buckets.move_to_end(key)
                if len(bucket) >= maximum:
                    raise HTTPException(429, "요청이 많습니다. 잠시 뒤 다시 시도하세요.", headers={"Retry-After": str(max(1, int(bucket[0] + period - now) + 1))})
                bucket.append(now)
            while len(self.buckets) > 2048:
                self.buckets.popitem(last=False)

    def run(self, operation):
        if not self.capacity.acquire(blocking=False):
            raise HTTPException(429, "로그인 요청이 많습니다. 잠시 뒤 다시 시도하세요.", headers={"Retry-After": "2"})
        try:
            return operation()
        finally:
            self.capacity.release()


class PasswordLogin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def username_valid(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.@-]{0,79}", value):
            raise ValueError("invalid username")
        return value

    _password = field_validator("password")(valid_password)


class Register(PasswordLogin):
    username: str = Field(min_length=3, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    employee_number: str = Field(min_length=1, max_length=32)

    @field_validator("username")
    @classmethod
    def registration_username_length(cls, value):
        # Field length constraints run before the inherited strip/lower
        # validator. Check the canonical ID too, without rejecting short
        # legacy IDs on the separate password-login path.
        if not 3 <= len(value) <= 40:
            raise ValueError("invalid registration username length")
        return value

    @field_validator("name")
    @classmethod
    def name_valid(cls, value):
        value = value.strip()
        if not value or any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("invalid name")
        return value

    @field_validator("employee_number")
    @classmethod
    def employee_valid(cls, value):
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9-]{1,32}", value):
            raise ValueError("invalid employee number")
        return value


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    _passwords = field_validator("current_password", "new_password")(valid_password)


def password_change_account(db, user, token, session_policy, *, lock=False):
    """Only a live personal password session may change its own credential.

    Recheck the session and account under the write transaction after hashing:
    an intervening logout, admin reset or competing change must win the fence.
    """
    from .auth import SESSION_PATTERN, session_hash
    from .db import AuthSession
    if not SESSION_PATTERN.fullmatch(token):
        return None
    account_query = select(UserAccount).where(UserAccount.user_id == user)
    row = db.scalar(account_query.with_for_update() if lock else account_query)
    session_query = select(AuthSession).where(AuthSession.token_hash == session_hash(token))
    session = db.scalar(session_query.with_for_update() if lock else session_query)
    now = time.time()
    if (not row or row.kind != "member" or not row.is_active or row.approval_status != "approved"
            or not session or session.user_id != user or session.revoked_at is not None
            or session.expires_at <= now or session.last_seen_at <= now - session_policy.idle_seconds
            or not secrets.compare_digest(session.credential_hash, session_binding(row))):
        return None
    return row


async def read_input(request, model):
    # Manual bounded parsing prevents framework validation errors from echoing
    # password/employee-number fields, including malformed and surrogate JSON.
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, "JSON 형식으로 요청하세요.")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise HTTPException(413, "인증 요청 크기를 초과했습니다.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result
    try:
        return model.model_validate(json.loads(raw, object_pairs_hook=unique))
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(422, "입력값을 확인하세요. 아이디·이름·사번과 8~128자 비밀번호가 필요합니다.") from None


def install_accounts(app, policy, session_policy):
    from .auth import COOKIE, issue_session
    from .audit import record_event
    engine, tokens = app.state.engine, app.state.users
    limiter = AttemptLimiter()
    app.state.account_limiter = limiter

    def check_enabled():
        if not policy.enabled:
            raise HTTPException(404, "계정 로그인이 활성화되지 않았습니다.")

    def response_for(user, binding, request):
        token = issue_session(engine, user["user_id"], tokens, session_policy,
            request.cookies.get(COOKIE, ""), account_binding=binding)
        response = JSONResponse(user)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
            secure=request.scope.get("scheme") == "https", path="/api", max_age=session_policy.absolute_seconds)
        return response

    @app.post("/api/auth/password-change")
    async def change_password(request: Request):
        from .db import AuthSession
        check_enabled()
        # Automation credentials and shared guest accounts never get this action.
        if request.headers.get("authorization"):
            raise HTTPException(403, "개인 계정의 비밀번호로 로그인한 뒤 변경하세요.")
        user, cookie = request.state.user, request.cookies.get(COOKIE, "")
        value = await read_input(request, PasswordChange)
        limiter.charge(request.client.host if request.client else "unknown", user)

        def operation():
            with Session(engine) as db:
                row = password_change_account(db, user, cookie, session_policy)
                if row is None:
                    raise HTTPException(403, "개인 계정의 비밀번호로 로그인한 뒤 변경하세요.")
                previous_binding, encoded = session_binding(row), row.password_hash
            if not verify_password(value.current_password, encoded):
                raise HTTPException(400, "현재 비밀번호가 일치하지 않습니다.")
            if secrets.compare_digest(value.current_password.encode(), value.new_password.encode()):
                raise HTTPException(422, "현재 비밀번호와 다른 새 비밀번호를 입력하세요.")
            replacement = hash_password(value.new_password)
            with transaction(engine) as db:
                row = password_change_account(db, user, cookie, session_policy, lock=True)
                if row is None or not secrets.compare_digest(previous_binding, session_binding(row)):
                    raise HTTPException(409, "로그인 상태가 바뀌었습니다. 다시 로그인한 뒤 변경하세요.")
                row.password_hash, row.session_revision = replacement, secrets.token_hex(32)
                db.execute(update(AuthSession).where(AuthSession.user_id == user,
                    AuthSession.revoked_at.is_(None)).values(revoked_at=time.time()))
                record_event(db, user, "account.password_change", "account", user)
                account, binding = public_account(row), session_binding(row)
            # Commit first, then issue a fresh cookie. A lost response is safely
            # recoverable by logging in with the new password, never retrying blindly.
            return response_for(account, binding, request)
        return await run_in_threadpool(limiter.run, operation)

    @app.post("/api/auth/password-login")
    async def login(request: Request):
        check_enabled()
        value = await read_input(request, PasswordLogin)
        limiter.charge(request.client.host if request.client else "unknown", value.username)
        def operation():
            with Session(engine) as db:
                row = db.get(UserAccount, value.username)
                # Pending users can see the review notice only after proving the
                # password. Absent/rejected/disabled users retain generic errors.
                encoded = row.password_hash if row and (row.is_active or row.approval_status == "pending") else DUMMY_HASH
                valid = verify_password(value.password, encoded)
                if not row or not valid:
                    raise HTTPException(401, "아이디 또는 비밀번호를 확인하세요.")
                if row.approval_status == "pending":
                    raise HTTPException(403, "관리자 승인을 기다리고 있습니다. 승인 후 로그인할 수 있습니다.")
                if not row.is_active or row.approval_status != "approved":
                    raise HTTPException(401, "아이디 또는 비밀번호를 확인하세요.")
                user, binding = public_account(row), session_binding(row)
            return response_for(user, binding, request)
        return await run_in_threadpool(limiter.run, operation)

    @app.post("/api/auth/register")
    async def register(request: Request):
        check_enabled()
        if not policy.registration:
            raise HTTPException(403, "현재 새 계정 생성을 받지 않습니다. 관리자에게 문의하세요.")
        value = await read_input(request, Register)
        limiter.charge(request.client.host if request.client else "unknown", value.username, registration=True)
        reserved = {"guest", "admin", "local", os.getenv("HWP_CODEX_USER", "local").lower(), *(user.lower() for user in tokens)}
        # A missing configured sample must not be claimed through public signup.
        reserved.update(user for user, _ in policy.sample_accounts)
        # A pre-authorized identity must be created by the operator, not claimed
        # through open signup before its intended owner arrives.
        for variable in ("HWP_CODEX_ALLOWED_USERS", "HWP_LLM_ALLOWED_USERS"):
            reserved.update(user.strip().lower() for user in os.getenv(variable, "").split(",") if user.strip())
        if value.username in reserved:
            raise HTTPException(409, "이 아이디 또는 사번으로 계정을 만들 수 없습니다.")
        def operation():
            encoded = hash_password(value.password)
            try:
                with transaction(engine) as db:
                    if engine.dialect.name == "postgresql":
                        db.execute(text("SELECT pg_advisory_xact_lock(706251025)"))
                    if db.scalar(select(func.count()).select_from(UserAccount)) >= policy.maximum:
                        raise HTTPException(403, "체험 계정 수 한도에 도달했습니다. 관리자에게 문의하세요.")
                    if historical_identity_in_use(db, value.username):
                        raise HTTPException(409, "이 아이디 또는 사번으로 계정을 만들 수 없습니다.")
                    row = UserAccount(user_id=value.username, password_hash=encoded, name=value.name,
                        employee_number=value.employee_number, kind="member", is_active=not policy.require_approval,
                        role="member", approval_status="pending" if policy.require_approval else "approved",
                        session_revision=secrets.token_hex(32), created_at=time.time())
                    db.add(row)
                    db.flush()
                    user, binding = public_account(row), session_binding(row)
                    record_event(db, row.user_id, "account.signup", "account", row.user_id,
                                 details={"outcome": row.approval_status})
                    if policy.require_approval:
                        from .account_notifications import enqueue_registration_notification
                        enqueue_registration_notification(db, row.user_id)
            except IntegrityError:
                raise HTTPException(409, "이 아이디 또는 사번으로 계정을 만들 수 없습니다.") from None
            # Commit precedes both notification and session issuance. A pending
            # signup never receives a cookie, document access, or AI capability.
            if policy.require_approval:
                from starlette.background import BackgroundTask
                from .account_notifications import deliver_safely
                return JSONResponse({"status": "pending", "user_id": user["user_id"],
                    "message": "가입 요청을 접수했습니다. 관리자 승인 후 로그인할 수 있습니다."}, status_code=202,
                    background=BackgroundTask(deliver_safely, engine, user["user_id"]))
            return response_for(user, binding, request)
        return await run_in_threadpool(limiter.run, operation)
