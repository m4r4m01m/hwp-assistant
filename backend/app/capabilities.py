"""Cheap feature availability; never contact a model or infer connectivity from config."""
import os
import re
from fastapi import HTTPException, Request
from . import codex_runner
from .llm import OpenAICompatible


def llm_allowed(user):
    raw = os.getenv("HWP_LLM_ALLOWED_USERS", "").strip()
    if not raw:
        return True  # Preserve the existing authenticated-user policy unless restricted.
    users = [part.strip() for part in raw.split(",")]
    if any(not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", part) for part in users):
        raise HTTPException(503, "AI 실행 사용자 설정을 확인하세요.")
    return user in users


def require_llm_access(user):
    if not llm_allowed(user):
        raise HTTPException(403, "이 사용자는 서버의 AI 연결을 실행할 권한이 없습니다.")


def feature(configured, allowed, reason=None, message="", *, stream=False):
    return {"configured": configured, "reachable": None, "allowed": allowed,
            "reason_code": reason, "message": message, "supports_stream": stream}


def http_feature(user):
    try:
        allowed = llm_allowed(user)
    except HTTPException as exc:
        return feature(False, False, "invalid_configuration", str(exc.detail))
    try:
        OpenAICompatible()  # Validates local config only; never calls /models.
    except HTTPException:
        return feature(False, allowed, "llm_not_configured",
                       "서버의 AI 연결이 아직 준비되지 않았습니다. 문서 편집과 저장은 사용할 수 있습니다.")
    if not allowed:
        return feature(True, False, "user_not_allowed", "이 사용자는 AI 실행 권한이 없습니다.")
    return feature(True, True, message="AI 연결이 설정되어 있습니다. 실제 연결 상태는 실행 시 확인합니다.")


def codex_feature(user, *, stream=False):
    from .codex_runtime import allowed as permitted, readiness
    try:
        allowed = permitted(user)
        settings = codex_runner.capture_settings()
    except HTTPException:
        return feature(False, False, "invalid_configuration",
                       "서버의 Codex 연결 설정을 확인해야 합니다. 관리자에게 문의하세요.", stream=stream)
    state = readiness(settings)
    if not state["configured"]:
        return feature(False, allowed, state["reason_code"], state["message"], stream=stream)
    if not allowed:
        return feature(True, False, "user_not_allowed", "이 사용자는 서버의 Codex 실행 권한이 없습니다.", stream=stream)
    return feature(True, True, message=state["message"], stream=stream)


def install_capabilities(app):
    @app.get("/api/capabilities")
    def capabilities(request: Request):
        user = request.state.user
        http = http_feature(user)
        provider = os.getenv("HWP_REWRITE_PROVIDER", "codex")
        rewrite = (http if provider == "llm" else codex_feature(user) if provider == "codex" else
                   feature(False, False, "invalid_configuration", "문체 개선 실행기 설정을 확인하세요."))
        return {"features": {"rewrite": rewrite, "chat": http, "review": http,
                             "reports": codex_feature(user, stream=True)}}
