"""Allowlisted report failures; never persist model text or credential-bearing errors."""
import subprocess
from pydantic import ValidationError


MESSAGES = {
    "research_unavailable": "공개자료 조회 완료를 확인하지 못해 보고서를 완료하지 않았습니다. 서버 Codex의 웹 검색 사용 가능 여부를 확인한 뒤 다시 요청하세요. 자료 조회를 끄면 최신 동향 검증 없이 별도 초안을 요청할 수 있습니다.",
    "timeout": "보고서 생성 제한 시간을 초과했습니다. 미완료 내용은 저장하지 않았습니다. 요청 범위를 줄이거나 잠시 후 새 요청을 시작하세요.",
    "login_required": "서버 Codex의 ChatGPT 로그인을 확인할 수 없습니다. 서버 로그인 상태를 확인한 뒤 다시 요청하세요.",
    "login_timeout": "서버 Codex 로그인 상태 확인이 지연되어 중단했습니다. 서버 연결 상태를 확인한 뒤 다시 요청하세요.",
    "cli_unavailable": "서버에서 Codex CLI를 찾을 수 없습니다. 실행 환경을 확인하세요.",
    "invalid_report": "생성 결과가 보고서 형식 검증을 통과하지 못했습니다. 미완료 내용은 저장하지 않았습니다. 요청 범위를 줄여 새 요청을 시작하세요.",
    "model_failed": "Codex가 보고서 생성을 완료하지 못했습니다. 서버의 모델 사용 한도와 연결 상태를 확인한 뒤 다시 요청하세요.",
    "incomplete_output": "Codex의 최종 보고서 응답을 확인하지 못했습니다. 표시된 내용은 미완료이며 저장하지 않았습니다. 새 요청을 시작하세요.",
    "connection_lost": "Codex 연결이 최종 응답 전에 끊겼습니다. 미완료 내용은 저장하지 않았습니다. 연결 상태를 확인한 뒤 다시 요청하세요.",
    "protocol_error": "Codex와 보고서 응답 계약이 일치하지 않아 중단했습니다. 서버 CLI 버전과 연결 설정을 확인하세요.",
    "configuration_error": "서버 Codex의 보고서 실행 설정을 확인할 수 없어 중단했습니다. 모델·제공자·도구 설정을 확인하세요.",
    "scope_violation": "보고서 작성에 허용되지 않은 도구 요청을 감지해 중단했습니다. 문서는 변경하지 않았습니다.",
    "output_limit": "보고서 출력 크기 제한을 초과했습니다. 미완료 내용은 저장하지 않았습니다. 요청 범위를 줄여 다시 요청하세요.",
    "internal_error": "보고서 처리 중 오류가 발생했습니다. 미완료 내용은 저장하지 않았습니다. 작업 ID로 서버 실행 환경을 확인하세요.",
}

UPSTREAM_VARIANTS = {"contextWindowExceeded", "sessionBudgetExceeded", "usageLimitExceeded", "rateLimitExceeded", "serverOverloaded",
                     "cyberPolicy", "misalignmentPolicyViolation", "httpConnectionFailed", "responseStreamConnectionFailed",
                     "responseStreamDisconnected", "responseTooManyFailedAttempts", "badRequest", "unauthorized",
                     "sandboxError", "internalServerError", "other"}


class ReportGenerationError(RuntimeError):
    def __init__(self, code, **details):
        # Callers use fixed codes and already filtered metadata, never raw errors.
        self.code = code if code in MESSAGES else "internal_error"
        self.details = {}
        if details.get("protocol_error") in UPSTREAM_VARIANTS:
            self.details["protocol_error"] = details["protocol_error"]
        status = details.get("http_status")
        if type(status) is int and 100 <= status <= 599:
            self.details["http_status"] = status
        super().__init__(self.code)


def validation_paths(exc):
    """JSON/schema locations only: exclude Pydantic input, messages and context."""
    paths = []
    allowed = {"title", "summary", "sections", "heading", "paragraphs", "tables", "caption", "columns", "rows",
               "diagrams", "steps", "sources", "id", "url", "published", "limitations", "emphasis", "text", "style"}
    for issue in exc.errors(include_input=False, include_context=False, include_url=False):
        location = issue.get("loc", ())
        if not all(type(part) is int and 0 <= part <= 100000 or isinstance(part, str) and part in allowed for part in location):
            continue
        path = ".".join(map(str, location)) or "$"
        if path not in paths:
            paths.append(path)
        if len(paths) == 8:
            break
    return paths


def failure_details(exc):
    if isinstance(exc, ReportGenerationError):
        return {"code": exc.code, **exc.details}
    if isinstance(exc, ValidationError):
        return {"code": "invalid_report", "validation_paths": validation_paths(exc)}
    if isinstance(exc, subprocess.TimeoutExpired):
        return {"code": "timeout"}
    if isinstance(exc, TimeoutError):
        return {"code": "timeout"}
    if isinstance(exc, (BrokenPipeError, ConnectionError, OSError)):
        return {"code": "connection_lost"}
    return {"code": "internal_error"}


def upstream_details(error):
    """Keep only documented error variants/status; upstream prose may be private."""
    if not isinstance(error, dict):
        return {}
    info = error.get("codexErrorInfo")
    if isinstance(info, str):
        return {"protocol_error": info} if info in UPSTREAM_VARIANTS else {}
    if isinstance(info, dict):
        for key, detail in info.items():
            if key in UPSTREAM_VARIANTS:
                result = {"protocol_error": key}
                status = detail.get("httpStatusCode") if isinstance(detail, dict) else None
                if type(status) is int and 100 <= status <= 599:
                    result["http_status"] = status
                return result
    return {}
