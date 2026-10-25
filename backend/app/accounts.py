"""Password accounts for a bounded PoC. Static automation tokens remain separate.

No public member directory, plaintext password storage, or automatic Codex grants.
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
from sqlalchemy import Boolean, Float, String, func, select, text
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

    @classmethod
    def from_env(cls):
        enabled = flag("HWP_ACCOUNTS_ENABLED")
        registration = flag("HWP_REGISTRATION_ENABLED")
        maximum = int(os.getenv("HWP_REGISTRATION_MAX_USERS", "100"))
        if not 1 <= maximum <= 10000 or registration and not enabled:
            raise ValueError("Enable accounts before registration; maximum must be 1..10000")
        return cls(enabled, registration, maximum)


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
        return bool(row and row.is_active)


def require_active_if_registered(engine, user):
    with Session(engine) as db:
        row = db.get(UserAccount, user)
        if row is not None and not row.is_active:
            raise HTTPException(401, "이 계정은 비활성화되어 있습니다.")


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

    @app.post("/api/auth/password-login")
    async def login(request: Request):
        check_enabled()
        value = await read_input(request, PasswordLogin)
        limiter.charge(request.client.host if request.client else "unknown", value.username)
        def operation():
            with Session(engine) as db:
                row = db.get(UserAccount, value.username)
                encoded = row.password_hash if row and row.is_active else DUMMY_HASH
                valid = verify_password(value.password, encoded)
                if not row or not row.is_active or not valid:
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
        reserved = {"guest", "local", os.getenv("HWP_CODEX_USER", "local").lower(), *(user.lower() for user in tokens)}
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
                    row = UserAccount(user_id=value.username, password_hash=encoded, name=value.name,
                        employee_number=value.employee_number, kind="member", is_active=True,
                        session_revision=secrets.token_hex(32), created_at=time.time())
                    db.add(row)
                    db.flush()
                    user, binding = public_account(row), session_binding(row)
            except IntegrityError:
                raise HTTPException(409, "이 아이디 또는 사번으로 계정을 만들 수 없습니다.") from None
            # Commit precedes session issuance. A lost reply is recovered by
            # signing in, never overwriting the existing account's credentials.
            return response_for(user, binding, request)
        return await run_in_threadpool(limiter.run, operation)
