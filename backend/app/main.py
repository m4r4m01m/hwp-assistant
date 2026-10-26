import json
import os
import secrets
import time
import uuid
import asyncio
from contextlib import suppress
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Header, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from .db import Access, ChangeSet, Document, Version, VersionAuthor, document_is_trashed, make_engine, transaction
from .version_compare import author_dict, restore_source_dict, install_version_compare
from .hwpx import validate, digest, loss_warnings, MAX_FILE
from .llm import OpenAICompatible, pooled_client
from .ai_budget import UserAIBudget
from .capabilities import install_capabilities, require_llm_access
from .request_telemetry import RequestTelemetry
from .rewrite_capacity import ExecutionSlot
from .auth import (
    COOKIE, LOGIN_REQUIRED, LoginRateLimiter, SessionPolicy, allowed_hosts, authenticate, authenticate_session,
    credentials, issue_session, request_origin, require_access, bind_lease, require_lease_owner,
    revoke_session, require_password_session,
)
from .schema import migrate
from .accounts import AccountPolicy, UserAccount, initialize_accounts, install_accounts, require_active_if_registered, public_sample_accounts
from .changes import apply_delta, describe_changes
from .rewrite_jobs import invalidate_running
from .audit import actor_context, record_event
from .account_settings import engine_context
from .library_access import library_mode, shared_library, document_role


class Name(BaseModel):
    name: str = Field(min_length=1, max_length=235)


class Review(BaseModel):
    document_id: str
    base_version: str
    revision: int = Field(ge=0)
    original: str = Field(min_length=1, max_length=12000)
    target: dict


def create_app(database_url=None, storage_dir=None):
    users = credentials()
    account_policy = AccountPolicy.from_env()
    library_mode()
    legacy_tokens_enabled = os.getenv("HWP_LEGACY_TOKEN_ACCESS_ENABLED", "1")
    if legacy_tokens_enabled not in {"0", "1"} or (legacy_tokens_enabled == "0" and not account_policy.enabled):
        raise ValueError("Disabling legacy token access requires password accounts")
    authentication_required = bool(users) or account_policy.enabled
    session_policy = SessionPolicy.from_env()
    hosts = allowed_hosts()
    folder = Path(storage_dir or os.getenv("STORAGE_DIR", "./data/files")).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    engine = make_engine(
        database_url or os.getenv("DATABASE_URL", "sqlite:///./data/metadata.db")
    )

    # Explicit initial migration is also exposed as python -m app.migrate.
    @asynccontextmanager
    async def lifespan(app):
        migrate(engine, users)
        initialize_accounts(engine, account_policy, users)
        from .account_notifications import notification_loop
        notifications = asyncio.create_task(notification_loop(engine)) if account_policy.enabled else None
        try:
            async with pooled_client() as client:
                app.state.llm_client = client
                yield
        finally:
            if notifications is not None:
                notifications.cancel()
                with suppress(asyncio.CancelledError):
                    await notifications
            engine.dispose()

    app = FastAPI(title="HWPX 공용 문서함", lifespan=lifespan)
    app.state.engine = engine
    app.state.folder = folder
    app.state.users = users
    app.state.account_policy = account_policy
    app.state.login_limiter = LoginRateLimiter()
    app.state.ai_budget = UserAIBudget()
    lease_seconds = int(os.getenv("SESSION_TTL_SECONDS", "120"))

    @app.middleware("http")
    async def same_origin(request, call_next):
        # Routing uses scope.path. Host-derived request.url must never decide authentication.
        path = request.scope["path"]
        api_path = path == "/api" or path.startswith("/api/")
        cookie_auth = False
        try:
            expected_origin = request_origin(request.scope, hosts)
            origin = request.headers.get("origin")
            # No CORS. SameSite cookies and this check also reject simple cross-origin POSTs.
            if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin != expected_origin:
                raise HTTPException(403, "다른 출처의 쓰기 요청은 허용하지 않습니다.")
            if path.startswith("/api/admin/") and not account_policy.enabled:
                raise HTTPException(404, "계정 관리가 활성화되지 않았습니다.")
            public = (request.method, path) in {
                ("GET", "/api/health"), ("GET", "/api/auth/status"),
                ("POST", "/api/auth/login"), ("POST", "/api/auth/logout"),
                ("POST", "/api/auth/password-login"), ("POST", "/api/auth/register"),
            }
            if api_path and not public:
                authorization = request.headers.get("authorization", "")
                scheme, _, token = authorization.partition(" ")
                token = token.strip()
                if authorization and (scheme.lower() != "bearer" or not token):
                    raise HTTPException(401, LOGIN_REQUIRED)
                if token.startswith("hwp-run-"):
                    from .rewrite import run_identity
                    request.state.user, request.state.run_task = await run_in_threadpool(
                        run_identity, engine, token, path, request.method
                    )
                elif not authentication_required:
                    request.state.user = "local"
                elif authorization:
                    if legacy_tokens_enabled == "0":
                        raise HTTPException(401, LOGIN_REQUIRED)
                    # Static credentials remain supported only for explicit automation Bearer auth.
                    request.state.user = authenticate(token, users)
                else:
                    cookie_auth = True
                    request.state.user = await run_in_threadpool(
                        authenticate_session, engine, request.cookies.get(COOKIE, ""), users, session_policy, account_policy.enabled
                    )
                    if legacy_tokens_enabled == "0":
                        await run_in_threadpool(require_password_session, engine, request.cookies.get(COOKIE, ""))
                if account_policy.enabled:
                    await run_in_threadpool(require_active_if_registered, engine, request.state.user)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            if api_path:
                response.headers["Cache-Control"] = "no-store"
            if cookie_auth and request.cookies.get(COOKIE):
                response.delete_cookie(COOKIE, path="/api", httponly=True, samesite="strict",
                                       secure=request.scope.get("scheme") == "https")
            return response
        actor_token = actor_context.set(getattr(request.state, "user", None))
        engine_token = engine_context.set(engine if account_policy.enabled else None)
        try:
            response = await call_next(request)
        finally:
            actor_context.reset(actor_token)
            engine_context.reset(engine_token)
        if api_path:
            response.headers["Cache-Control"] = "no-store"
        return response

    def locked(db, doc_id, user, role="editor"):
        return require_access(db, doc_id, user, role, lock=True)

    def check_lease(doc, token):
        if (
            not token
            or not secrets.compare_digest(doc.lease_token or "", token)
            or doc.lease_until <= time.time()
        ):
            raise HTTPException(
                409,
                "편집 세션이 만료되었거나 다른 탭이 사용 중입니다. 복구용 다운로드 후 다시 여세요.",
            )

    def write_file(key, data):
        # Exclusive immutable key. fsync before metadata commit; crash orphans are not versions.
        with (folder / key).open("xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt":
            fd = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def version_dict(v):
        return {**{
            k: getattr(v, k)
            for k in (
                "id",
                "document_id",
                "base_version",
                "size",
                "sha256",
                "created_at",
                "cause",
            )
        }, "author": author_dict(v), "restore_source": restore_source_dict(v)}

    def doc_dict(d):
        return {
            k: getattr(d, k) for k in ("id", "name", "current_version", "created_at")
        }

    async def body(request):
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > MAX_FILE:
                raise HTTPException(413, "30MB 한도를 초과했습니다.")
        data = bytes(chunks)
        await run_in_threadpool(validate, data)
        return data

    def publish(db, doc, data, base, token, request_id, cause, *, author_id):
        if not request_id or len(request_id) > 64:
            raise HTTPException(422, "Idempotency-Key가 필요합니다.")
        fingerprint = digest(json.dumps([base, cause, digest(data)]).encode())
        existing = db.scalar(
            select(Version).where(
                Version.document_id == doc.id, Version.request_id == request_id
            )
        )
        if existing:
            if existing.fingerprint != fingerprint or existing.session_hash != digest(
                (token or "").encode()
            ):
                raise HTTPException(
                    409, "같은 요청 ID를 다른 저장에 사용할 수 없습니다."
                )
            if cause != "upload":
                check_lease(doc, token)
            if doc.current_version != existing.id:
                raise HTTPException(
                    409, "이 저장 이후 새 버전이 생겼습니다. 최신 문서를 다시 여세요."
                )
            return existing  # Already committed receipt; does not renew or modify anything.
        if cause != "upload":
            check_lease(doc, token)
            if doc.current_version != base:
                raise HTTPException(
                    409,
                    "기준 버전 충돌. 변경사항을 복구용 다운로드하고 최신 문서를 다시 여세요.",
                )
        if cause == "web-edit" and base:
            old = db.get(Version, base)
            lost = loss_warnings((folder / old.storage_key).read_bytes(), data)
            if lost:
                recovery = str(uuid.uuid4()) + ".hwpx"
                write_file(recovery, data)
                raise HTTPException(
                    422,
                    {
                        "message": "리소스/개체 누락 감지로 게시를 차단했습니다.",
                        "missing": lost,
                        "recovery_key": recovery,
                    },
                )
        key = str(uuid.uuid4()) + ".hwpx"
        write_file(key, data)
        v = Version(
            id=str(uuid.uuid4()),
            document_id=doc.id,
            base_version=base,
            storage_key=key,
            size=len(data),
            sha256=digest(data),
            created_at=time.time(),
            cause=cause,
            request_id=request_id,
            fingerprint=fingerprint,
            session_hash=digest((token or "").encode()),
        )
        db.add(v)
        db.flush()
        db.add(VersionAuthor(version_id=v.id, user_id=author_id,
                             identity_kind="authenticated" if authentication_required else "local"))
        db.flush()
        if base:
            old = db.get(Version, base)
            before = (folder / old.storage_key).read_bytes()
            db.add(ChangeSet(version_id=v.id, base_sha256=old.sha256,
                             entries=json.dumps(describe_changes(before, data))))
        doc.current_version = v.id
        return v

    @app.get("/api/auth/status")
    def auth_status():
        with Session(engine) as db:
            guest = db.get(UserAccount, "guest") if account_policy.enabled else None
            guest_enabled = bool(guest and guest.is_active)
            sample_accounts = public_sample_accounts(db, account_policy)
        return {"authentication_required": authentication_required,
                "accounts_enabled": account_policy.enabled,
                "registration_enabled": account_policy.enabled and account_policy.registration,
                "registration_requires_approval": account_policy.require_approval,
                "guest_enabled": guest_enabled,
                "sample_accounts": sample_accounts,
                "library_access_mode": library_mode(),
                "token_login_enabled": legacy_tokens_enabled == "1"}

    class Login(BaseModel):
        token: str = Field(min_length=24, max_length=500)

        @model_validator(mode="before")
        @classmethod
        def valid_input(cls, value):
            # A JSON lone surrogate must not enter Pydantic's error `input` and
            # fail again while rendering that error as UTF-8. Never echo a token.
            detail = "로그인 토큰은 24~500자의 올바른 UTF-8 문자열이어야 합니다."
            if not isinstance(value, dict) or not isinstance(value.get("token"), str):
                raise HTTPException(422, detail)
            if not 24 <= len(value["token"]) <= 500:
                raise HTTPException(422, detail)
            try:
                value["token"].encode("utf-8")
            except UnicodeError:
                raise HTTPException(422, detail) from None
            return value

    @app.post("/api/auth/login")
    def login(value: Login, request: Request):
        if legacy_tokens_enabled == "0":
            raise HTTPException(403, "아이디와 비밀번호로 로그인하세요.")
        if not users:
            raise HTTPException(400, "단일 사용자 개발 모드입니다.")
        user = app.state.login_limiter.authenticate(
            request.client.host if request.client else "unknown", value.token, users
        )
        if account_policy.enabled:
            require_active_if_registered(engine, user)
        session = issue_session(engine, user, users, session_policy, request.cookies.get(COOKIE, ""))
        response = JSONResponse({"user_id": user})
        response.set_cookie(COOKIE, session, httponly=True, samesite="strict",
                            secure=request.scope.get("scheme") == "https",
                            max_age=session_policy.absolute_seconds, path="/api")
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request):
        # Idempotent even for an expired session; never revoke an automation credential.
        try:
            actor = authenticate_session(engine, request.cookies.get(COOKIE, ""), users,
                                         session_policy, account_policy.enabled)
        except HTTPException:
            actor = None
        revoke_session(engine, request.cookies.get(COOKIE, ""))
        if actor:
            with transaction(engine) as db:
                record_event(db, actor, "account.logout", "account", actor)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/api", httponly=True, samesite="strict",
                               secure=request.scope.get("scheme") == "https")
        return response

    @app.get("/api/auth/me")
    def me(request: Request):
        from .accounts import password_change_account
        with Session(engine) as db:
            row = db.get(UserAccount, request.state.user) if account_policy.enabled else None
            return {"user_id": request.state.user, "authentication_required": authentication_required,
                    "name": row.name if row else request.state.user,
                    "kind": row.kind if row else "legacy",
                    "can_change_password": bool(account_policy.enabled and not request.headers.get("authorization")
                        and password_change_account(db, request.state.user, request.cookies.get(COOKIE, ""), session_policy)),
                    "library_access_mode": library_mode(),
                    "is_admin": bool(row and row.role == "admin" and row.is_active
                                     and row.approval_status == "approved")}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/documents")
    def documents(request: Request, q: str = Query("", max_length=200)):
        with Session(engine) as db:
            if shared_library(db, request.state.user):
                owned = select(Access.document_id).where(Access.user_id == request.state.user, Access.role == "owner")
                return [{**doc_dict(doc), "role": "owner", "can_trash": can_trash} for doc, can_trash in db.execute(
                    select(Document, Document.id.in_(owned)).where(~document_is_trashed(Document.id))
                    .where(Document.name.contains(q, autoescape=True)).order_by(Document.created_at.desc()))]
            return [
                {**doc_dict(d), "role": role, "can_trash": role == "owner"}
                for d, role in db.execute(
                    select(Document, Access.role)
                    .join(Access, Access.document_id == Document.id)
                    .where(Access.user_id == request.state.user)
                    .where(~document_is_trashed(Document.id))
                    .where(Document.name.contains(q, autoescape=True))
                    .order_by(Document.created_at.desc())
                )
            ]

    @app.post("/api/documents")
    async def upload(
        request: Request,
        document_id: str,
        name: str = Query(min_length=1, max_length=235),
        idempotency_key: str = Header(),
    ):
        try:
            uuid.UUID(document_id)
        except ValueError:
            raise HTTPException(422, "문서 ID는 UUID여야 합니다.")
        if not name.lower().endswith(".hwpx"):
            raise HTTPException(422, "HWPX 파일만 업로드하세요.")
        data = await body(request)
        def persist_upload():
            with transaction(engine) as db:
                # Serialize concurrent creation/retries by stable client document UUID on PostgreSQL.
                if engine.dialect.name == "postgresql":
                    from sqlalchemy import text

                    db.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"),
                        {"key": int(digest(document_id.encode())[:15], 16)},
                    )
                doc = db.get(Document, document_id)
                if not doc:
                    doc = Document(
                        id=document_id, name=name, created_at=time.time(), lease_until=0
                    )
                    db.add(doc)
                    db.flush()
                    db.add(Access(document_id=doc.id, user_id=request.state.user, role="owner"))
                elif not db.scalar(
                    select(Version).where(
                        Version.document_id == doc.id, Version.request_id == idempotency_key
                    )
                ):
                    raise HTTPException(409, "이미 존재하는 문서 ID입니다.")
                else:
                    require_access(db, doc.id, request.state.user, "owner", lock=True)
                publish(db, doc, data, None, None, idempotency_key, "upload", author_id=request.state.user)
                result = {**doc_dict(doc), "role": "owner"}
            return result

        return await run_in_threadpool(persist_upload)

    @app.patch("/api/documents/{doc_id}")
    def rename(doc_id: str, value: Name, request: Request):
        with transaction(engine) as db:
            doc = locked(db, doc_id, request.state.user)
            doc.name = (
                value.name
                if value.name.lower().endswith(".hwpx")
                else value.name + ".hwpx"
            )
            return doc_dict(doc)

    @app.post("/api/documents/{doc_id}/session")
    def acquire(doc_id: str, request: Request):
        with transaction(engine) as db:
            doc = locked(db, doc_id, request.state.user)
            if doc.lease_token and doc.lease_until > time.time():
                raise HTTPException(
                    409,
                    "다른 편집 세션이 사용 중입니다. 탭을 닫거나 최대 120초 후 다시 시도하세요.",
                )
            invalidate_running(db, doc.id, "기존 편집 임대가 끝났습니다. 새 스냅샷으로 요청하세요.")
            doc.lease_token = secrets.token_urlsafe(32)
            doc.lease_until = time.time() + lease_seconds
            bind_lease(db, doc, request.state.user)
            return {
                "token": doc.lease_token,
                "expires_at": doc.lease_until,
                "document": {**doc_dict(doc), "role": document_role(db, doc.id, request.state.user)},
            }

    @app.put("/api/documents/{doc_id}/session")
    def renew(doc_id: str, request: Request, x_edit_session: str = Header()):
        with transaction(engine) as db:
            doc = locked(db, doc_id, request.state.user)
            require_lease_owner(db, doc, request.state.user)
            check_lease(doc, x_edit_session)
            doc.lease_until = time.time() + lease_seconds
            return {"expires_at": doc.lease_until}

    @app.delete("/api/documents/{doc_id}/session")
    def release(doc_id: str, request: Request, x_edit_session: str = Header()):
        with transaction(engine) as db:
            doc = locked(db, doc_id, request.state.user)
            require_lease_owner(db, doc, request.state.user)
            if secrets.compare_digest(doc.lease_token or "", x_edit_session):
                invalidate_running(db, doc.id, "편집 임대가 반납되어 실행 결과를 게시하지 않습니다.")
                doc.lease_token = None
                doc.lease_until = 0
            return {"ok": True}

    @app.get("/api/documents/{doc_id}/versions")
    def versions(doc_id: str, request: Request):
        with Session(engine) as db:
            require_access(db, doc_id, request.state.user)
            return [
                version_dict(v)
                for v in db.scalars(
                    select(Version)
                    .where(Version.document_id == doc_id)
                    .order_by(Version.created_at.desc())
                )
            ]

    @app.get("/api/documents/{doc_id}/versions/{version_id}/file")
    def download(doc_id: str, version_id: str, request: Request):
        with transaction(engine) as db:
            doc = require_access(db, doc_id, request.state.user)
            v = db.get(Version, version_id)
            if not doc or not v or v.document_id != doc_id:
                raise HTTPException(404, "버전을 찾을 수 없습니다.")
            record_event(db, request.state.user, "document.download", "document", doc.id,
                         document_id=doc.id, version_id=v.id, document_name=doc.name)
            return FileResponse(
                folder / v.storage_key,
                filename=doc.name,
                media_type="application/hwp+zip",
                headers={"ETag": f'"{v.sha256}"', "Cache-Control": "no-store"},
            )

    @app.post("/api/documents/{doc_id}/versions")
    async def save(
        doc_id: str,
        request: Request,
        base_version: str,
        cause: str,
        x_edit_session: str = Header(),
        idempotency_key: str = Header(),
        x_application_id: str | None = Header(default=None),
    ):
        if cause not in ("web-edit", "reupload"):
            raise HTTPException(422, "올바르지 않은 생성 원인")
        delta = request.headers.get("content-type", "").split(";")[0] == "application/vnd.hwp.delta+json"
        if delta:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > MAX_FILE * 1.4 + 10000:
                    raise HTTPException(413, "변경분 크기 한도 초과")
        else:
            data = await body(request)
        def persist_save():
            with transaction(engine) as db:
                doc = locked(db, doc_id, request.state.user)
                require_lease_owner(db, doc, request.state.user)
                if delta:
                    old = db.get(Version, base_version)
                    if not old or old.document_id != doc.id:
                        raise HTTPException(409, "기준 버전을 찾을 수 없습니다.")
                    resolved = apply_delta((folder / old.storage_key).read_bytes(), bytes(raw))
                else:
                    resolved = data
                application = None
                if x_application_id:
                    from .rewrite import validate_application_save
                    application = validate_application_save(db, doc, x_application_id, request.state.user,
                                                            idempotency_key, base_version, resolved)
                v = publish(
                    db, doc, resolved, base_version, x_edit_session, idempotency_key, cause, author_id=request.state.user
                )
                result = version_dict(v)
                if application:
                    application.saved_version, application.status = v.id, "saved"
            return result

        return await run_in_threadpool(persist_save)

    @app.post("/api/documents/{doc_id}/versions/{version_id}/restore")
    def restore(
        doc_id: str,
        version_id: str,
        base_version: str,
        request: Request,
        x_edit_session: str = Header(),
        idempotency_key: str = Header(),
    ):
        with transaction(engine) as db:
            doc = locked(db, doc_id, request.state.user)
            require_lease_owner(db, doc, request.state.user)
            old = db.get(Version, version_id)
            if not old or old.document_id != doc_id:
                raise HTTPException(404, "버전을 찾을 수 없습니다.")
            from .db import VersionRestore
            existing = db.scalar(select(Version).where(
                Version.document_id == doc.id, Version.request_id == idempotency_key))
            if existing:
                receipt = db.get(VersionRestore, existing.id)
                if not receipt or receipt.source_version_id != old.id:
                    raise HTTPException(409, "같은 요청 ID로 다른 버전을 복원할 수 없습니다.")
            v = publish(
                db,
                doc,
                (folder / old.storage_key).read_bytes(),
                base_version,
                x_edit_session,
                idempotency_key,
                "restore",
                author_id=request.state.user,
            )
            if not existing:
                db.add(VersionRestore(version_id=v.id, source_version_id=old.id))
                db.flush()
            result = version_dict(v)
        return result

    @app.get("/api/ai/models")
    async def models(request: Request):
        require_llm_access(request.state.user)
        with app.state.ai_budget.reserve(request.state.user), ExecutionSlot(app.state.rewrite_capacity) as slot:
            slot.reserve()
            provider = OpenAICompatible()
            ids = await provider.models()
        return {
            "models": ids,
            "configured_model": provider.model,
            "model_available": provider.model in ids,
        }

    @app.post("/api/ai/review")
    async def review(value: Review, request: Request, x_edit_session: str = Header()):
        from .codex_chat_review import capture_provider, provider_name, require_provider_access, request_active
        from .codex_runtime import reserve_usage
        selected = provider_name("review")
        require_provider_access(selected, request.state.user)
        with transaction(engine) as db:
            doc = locked(db, value.document_id, request.state.user)
            require_lease_owner(db, doc, request.state.user)
            check_lease(doc, x_edit_session)
            if doc.current_version != value.base_version:
                raise HTTPException(409, "기준 버전이 변경되었습니다.")
        with app.state.ai_budget.reserve(request.state.user), ExecutionSlot(app.state.rewrite_capacity) as slot:
            slot.reserve()
            provider = await run_in_threadpool(capture_provider, selected, OpenAICompatible)
            if selected == "codex":
                # Each accepted review is a new call; this endpoint has no request ID.
                # Lock/recheck immediately before committing the shared daily quota.
                with transaction(engine) as db:
                    doc = locked(db, value.document_id, request.state.user)
                    require_lease_owner(db, doc, request.state.user)
                    check_lease(doc, x_edit_session)
                    if doc.current_version != value.base_version:
                        raise HTTPException(409, "기준 버전이 변경되었습니다.")
                    reserve_usage(db, "review:" + str(uuid.uuid4()), request.state.user)
                def current_review():
                    from sqlalchemy.orm import Session
                    with Session(engine) as db:
                        require_provider_access(selected, request.state.user)
                        doc = require_access(db, value.document_id, request.state.user, "editor")
                        require_lease_owner(db, doc, request.state.user)
                        check_lease(doc, x_edit_session)
                        return doc.current_version == value.base_version

                async with request_active(request, current_review) as active:
                    proposal = await provider.review(value.original, active=active)
                    if not active():
                        raise HTTPException(409, "요청 연결 또는 문서 상태가 바뀌어 검토 결과를 게시하지 않았습니다.")
            else:
                proposal = await provider.review(value.original)
        # Access/base can change while the provider is running.
        with transaction(engine) as db:
            require_provider_access(selected, request.state.user)
            doc = locked(db, value.document_id, request.state.user)
            require_lease_owner(db, doc, request.state.user)
            check_lease(doc, x_edit_session)
            if doc.current_version != value.base_version:
                raise HTTPException(409, "기준 버전이 변경되었습니다.")
        return {
            **value.model_dump(),
            **proposal.model_dump(),
            "original_sha256": digest(value.original.encode()),
        }

    from .collaboration import install_collaboration
    install_collaboration(app, locked, check_lease, publish, doc_dict, version_dict)
    install_version_compare(app, version_dict)
    from .rewrite import install_rewrite
    install_rewrite(app, check_lease)
    from .document_assistant import install_document_assistant
    install_document_assistant(app, check_lease)
    from .document_copy import install_document_copy
    install_document_copy(app, locked, publish, doc_dict)
    from .document_trash import install_document_trash
    install_document_trash(app, doc_dict)
    from .document_blocks import install_document_blocks
    install_document_blocks(app, check_lease)
    from .editor_designs import install_editor_designs
    install_editor_designs(app, check_lease)
    from .toc_contract import install_toc
    install_toc(app, check_lease)
    from .reports import install_reports
    install_reports(app, publish, check_lease, doc_dict)
    install_capabilities(app)
    install_accounts(app, account_policy, session_policy)
    from .account_management import install_account_management
    install_account_management(app)
    from .audit import install_audit
    install_audit(app)
    from .account_settings import install_account_settings
    install_account_settings(app)
    from .ai_connection import install_ai_connection
    install_ai_connection(app)
    app.add_middleware(RequestTelemetry, runtime=app)
    return app


app = create_app()
