"""Cheap feature availability; never contact a model or infer connectivity from config."""
import os
import re
import shutil
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
                       "서버 관리자가 HTTP AI 연결(LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL)을 설정해야 합니다. Codex 사용은 기능별 서버 설정에서 선택합니다. 문서 편집과 저장은 사용할 수 있습니다.")
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
    if not codex_executable_available(settings.executable):
        return feature(False, allowed, "codex_not_installed",
                       "서버에 Codex 실행기가 준비되지 않았습니다. 문서 편집과 저장은 사용할 수 있습니다.", stream=stream)
    state = readiness(settings)
    if not state["configured"]:
        return feature(False, allowed, state["reason_code"], state["message"], stream=stream)
    if not allowed:
        return feature(True, False, "user_not_allowed", "이 사용자는 서버의 Codex 실행 권한이 없습니다.", stream=stream)
    return feature(True, True, message=state["message"], stream=stream)


def codex_executable_available(executable):
    """Check the configured executable only; never start it or inspect credentials."""
    return bool(executable and shutil.which(executable))


def provider_feature(user, provider):
    if provider == "llm":
        return http_feature(user)
    if provider == "codex":
        return codex_feature(user)
    return feature(False, False, "invalid_configuration", "AI 실행기 설정을 확인하세요.")


def feature_statuses(user):
    """Same cheap configuration facts for the capability and connection screens."""
    from .codex_chat_review import provider_name

    def selected(kind):
        try:
            provider = provider_name(kind)
        except HTTPException:
            return {**feature(False, False, "invalid_configuration", "AI 실행기 설정을 확인하세요."), "provider": "unknown"}
        return {**provider_feature(user, provider), "provider": provider}

    return {
        "rewrite": provider_feature(user, os.getenv("HWP_REWRITE_PROVIDER", "codex")),
        "document_assistant": provider_feature(user, os.getenv("HWP_DOCUMENT_ASSISTANT_PROVIDER",
                                                               os.getenv("HWP_REWRITE_PROVIDER", "codex"))),
        "chat": selected("chat"), "review": selected("review"), "reports": codex_feature(user, stream=True),
    }


def install_capabilities(app):
    @app.get("/api/capabilities")
    def capabilities(request: Request):
        return {"features": feature_statuses(request.state.user)}
