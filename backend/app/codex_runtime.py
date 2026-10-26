"""Local Codex readiness and explicit admission. Never invokes inference or reads auth.json."""
import os
from pathlib import Path
import re
import shutil
import time
from threading import Lock

from fastapi import HTTPException
from sqlalchemy import Float, String, func, select, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .cli_process import run_process


class CodexUsage(Base):
    __tablename__ = "codex_usage"
    request_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[float] = mapped_column(Float, index=True)


def allowed(user):
    from .account_settings import account_codex_allowed
    decision = account_codex_allowed(user)
    if decision is not None:
        return decision
    raw = os.getenv("HWP_CODEX_ALLOWED_USERS", "").strip()
    users = [part.strip() for part in raw.split(",")] if raw else [os.getenv("HWP_CODEX_USER", "local")]
    if any(not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", value) for value in users):
        raise HTTPException(503, "Codex 실행 사용자 설정을 확인하세요.")
    return user in users


def require_access(user):
    if not allowed(user):
        raise HTTPException(403, "이 사용자는 서버의 Codex 실행 권한이 없습니다. 관리자에게 문의하세요.")


def reserve_usage(db, key, user):
    """Persistent, account-wide UTC daily request cap; not a token/cost estimate.

    Call only for a NEW accepted task, inside its transaction and after capacity
    reservation. Failed/uncertain calls consume a slot; readback never does.
    """
    try:
        maximum = int(os.getenv("HWP_CODEX_DAILY_REQUEST_LIMIT", "0"))
        if not 0 <= maximum <= 10000:
            raise ValueError()
    except ValueError:
        raise HTTPException(503, "Codex 일일 요청 한도 설정을 확인하세요.") from None
    if not maximum:
        return
    now = time.time()
    start = int(now // 86400) * 86400
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(706251026)"))
    # Caller owns idempotency fences; repeated reservation is a configuration bug.
    if db.get(CodexUsage, key):
        raise HTTPException(409, "이미 접수한 AI 요청입니다. 같은 작업의 결과를 확인하세요.")
    used = db.scalar(select(func.count()).select_from(CodexUsage).where(CodexUsage.created_at >= start))
    if used >= maximum:
        raise HTTPException(429, "서버의 오늘 AI 요청 한도에 도달했습니다. 문서 편집·저장은 계속 사용할 수 있습니다.",
                            headers={"Retry-After": str(max(1, int(start + 86400 - now)))})
    db.add(CodexUsage(request_key=key, user_id=user, created_at=now))


_lock = Lock()
_cache = (None, 0.0, None)


def readiness(settings):
    """At most one bounded local status process per cache interval. No model calls.

    Native development keeps the historical 'check on execution' behavior unless
    HWP_CODEX_CHECK_AUTH=1. Container deployment opts in explicitly.
    """
    if not settings.executable:
        return {"configured": False, "reason_code": "codex_not_installed",
                "message": "서버에 Codex 실행기가 준비되지 않았습니다. 문서 편집과 저장은 사용할 수 있습니다."}
    if os.getenv("HWP_CODEX_CHECK_AUTH", "0") != "1":
        return {"configured": True, "reason_code": None,
                "message": "Codex 실행기가 설정되어 있습니다. 로그인·사용 한도는 실행 시 확인합니다."}
    environment = dict(settings.environment)
    if not shutil.which(settings.executable, path=environment.get("PATH")):
        return {"configured": False, "reason_code": "codex_not_installed", "message": "Codex 실행 파일을 확인할 수 없습니다."}
    home = Path(environment.get("CODEX_HOME") or Path(environment.get("HOME", str(Path.home()))) / ".codex")
    def stamp(path):
        try:
            value = path.stat()
            return (value.st_mtime_ns, value.st_size, value.st_ino)
        except OSError:
            return None
    key = (settings.executable, str(home), stamp(home / "auth.json"), stamp(home / "config.toml"))
    global _cache
    with _lock:
        if _cache[0] == key and _cache[1] > time.monotonic():
            return dict(_cache[2])
        try:
            status = run_process([settings.executable, "login", "status"], capture_output=True,
                encoding="utf-8", errors="replace", timeout=min(5, settings.login_timeout), env=environment)
            authenticated = status.returncode == 0 and "Logged in using ChatGPT" in status.stdout + status.stderr
            result = ({"configured": True, "reason_code": None,
                       "message": "Codex ChatGPT 로그인을 확인했습니다. 모델 응답·구독 사용 한도는 실행 시 확인합니다."}
                      if authenticated else {"configured": False, "reason_code": "codex_login_required",
                       "message": "서버에서 Codex ChatGPT 로그인이 필요합니다. 관리자에게 문의하세요. 편집·저장은 사용할 수 있습니다."})
        except Exception:
            result = {"configured": False, "reason_code": "codex_status_unavailable",
                      "message": "Codex 로그인 상태를 확인하지 못했습니다. 잠시 뒤 다시 확인하세요."}
        _cache = (key, time.monotonic() + 15, result)
        return dict(result)


def require_ready(settings):
    result = readiness(settings)
    if not result["configured"]:
        raise HTTPException(503, result["message"])
