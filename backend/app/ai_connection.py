"""Local CLI onboarding, without credentials, model calls or provider reconfiguration."""
import json
import os
import shutil
import tempfile
import threading
import time
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from . import codex_runner
from .capabilities import codex_executable_available, feature_statuses
from .db import AIConnectionSetup, transaction
from .report_rpc import ReportRpc
from .report_stream import DISABLED

CHECK_TIMEOUT = 10
CHECK_CACHE_SECONDS = 5
CHECK_FRESH_SECONDS = 300
MESSAGES = {
    "unchecked": "Codex 로그인 상태를 아직 확인하지 않았습니다.",
    "not_installed": "이 PC에서 Codex CLI를 찾을 수 없습니다. 설치 후 다시 확인하세요.",
    "chatgpt": "저장된 ChatGPT 로그인을 확인했습니다. 실제 모델 이용 가능 여부는 실행 시 확인합니다.",
    "signed_out": "Codex CLI에서 ChatGPT로 로그인한 뒤 다시 확인하세요.",
    "unsupported": "이 연결은 Codex의 ChatGPT 로그인을 사용합니다. 현재 로그인 방식은 지원하지 않습니다.",
    "timeout": "로그인 상태 확인 시간이 초과됐습니다. 잠시 뒤 다시 확인하세요.",
    "error": "Codex 로그인 상태를 확인하지 못했습니다. CLI 설치와 설정을 확인한 뒤 다시 시도하세요.",
}
FEATURE_LABELS = {
    "reports": "보고서 초안 작성", "rewrite": "선택 문단 수정",
    "document_assistant": "문서와 대화", "chat": "대화 답변", "review": "문서 검토",
}


class CheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetupInput(CheckInput):
    choice: Literal["codex", "later"]


def local_setup(app, user):
    # A localhost URL is not proof of a native launcher: Docker also uses localhost.
    return (os.getenv("HWP_LOCAL_AI_SETUP") == "1" and not app.state.users
            and not app.state.account_policy.enabled
            and user == os.getenv("HWP_CODEX_USER", "local"))


def executable():
    return os.getenv("HWP_CODEX_EXECUTABLE") or shutil.which("codex")


def probe_command(settings):
    args = [settings.executable, "app-server", "--listen", "stdio://"]
    for key, value in {"approval_policy": "never", "sandbox_mode": "read-only",
                       "model_provider": "openai", "notify": [], "project_doc_max_bytes": 0,
                       "developer_instructions": "", "web_search": "disabled"}.items():
        args += ["-c", key + "=" + json.dumps(value)]
    for feature in DISABLED:
        args += ["--disable", feature]
    return args + ["--enable", "skip_host_skill_discovery"]


def probe_account(settings):
    """Initialize and account/read only. No thread, turn, login, refresh or auth-file read."""
    with tempfile.TemporaryDirectory(prefix="hwp-ai-check-") as directory:
        with ReportRpc(probe_command(settings), environment=dict(settings.environment),
                       cwd=directory, timeout=CHECK_TIMEOUT) as rpc:
            serial = 0

            def call(method, params):
                nonlocal serial
                serial += 1
                rpc.send({"id": serial, "method": method, "params": params})
                for _ in range(64):
                    event = rpc.receive()
                    if not isinstance(event, dict) or ("method" in event and "id" in event):
                        raise RuntimeError("Unexpected account protocol request")
                    if event.get("id") == serial:
                        if "error" in event or not isinstance(event.get("result"), dict):
                            raise RuntimeError("Account protocol error")
                        return event["result"]
                raise RuntimeError("Account protocol event limit")

            call("initialize", {"clientInfo": {"name": "hwp_ai_connection", "title": "HWP Assistant", "version": "0.1.0"}})
            rpc.send({"method": "initialized"})
            result = call("account/read", {"refreshToken": False})
            if "account" not in result:
                raise RuntimeError("Missing account result")
            account = result.get("account")
            if account is None:
                return "signed_out"
            if not isinstance(account, dict):
                raise RuntimeError("Invalid account result")
            # Account details are discarded here, never serialized, logged or persisted.
            return "chatgpt" if account.get("type") == "chatgpt" else "unsupported"


class ConnectionCheck:
    """One bounded native probe per application, with short retry cache and expiring receipt."""
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.checked_at = None
        self.checked_monotonic = None
        self.executable = None
        self.auth_state = "unchecked"

    def snapshot(self, current_executable):
        with self.lock:
            if (self.running or self.checked_monotonic is None or self.executable != current_executable
                    or time.monotonic() - self.checked_monotonic > CHECK_FRESH_SECONDS):
                return "unchecked", None
            return self.auth_state, self.checked_at

    def check(self):
        current_executable = executable()
        with self.lock:
            if self.running:
                raise HTTPException(409, "Codex 연결을 확인하고 있습니다. 잠시 뒤 다시 확인하세요.")
            if (self.checked_monotonic is not None and self.executable == current_executable
                    and time.monotonic() - self.checked_monotonic < CHECK_CACHE_SECONDS):
                return
            self.running = True
        state = "error"
        try:
            if not codex_executable_available(current_executable):
                state = "not_installed"
            else:
                settings = codex_runner.capture_settings()
                state = probe_account(settings)
        except TimeoutError:
            state = "timeout"
        except Exception:
            # CLI stderr, account details, executable paths and exception strings stay private.
            state = "error"
        finally:
            with self.lock:
                self.auth_state = state
                self.checked_at = time.time()
                self.checked_monotonic = time.monotonic()
                self.executable = current_executable
                self.running = False


def install_ai_connection(app):
    engine = app.state.engine
    app.state.ai_connection_check = ConnectionCheck()

    def require_local(user):
        if not local_setup(app, user):
            raise HTTPException(403, "AI 연결 설정은 이 PC의 로컬 실행 환경에서만 변경할 수 있습니다.")

    def status(user):
        configurable = local_setup(app, user)
        current_executable = executable()
        installed = codex_executable_available(current_executable)
        state, checked_at = app.state.ai_connection_check.snapshot(current_executable) if configurable else ("unchecked", None)
        if not installed:
            state = "not_installed"
        choice = None
        if configurable:
            with Session(engine) as db:
                receipt = db.get(AIConnectionSetup, user)
                choice = receipt.choice if receipt else None
        providers = {"reports": "codex", "rewrite": os.getenv("HWP_REWRITE_PROVIDER", "codex"),
                     "document_assistant": os.getenv("HWP_DOCUMENT_ASSISTANT_PROVIDER", os.getenv("HWP_REWRITE_PROVIDER", "codex")),
                     "chat": "llm", "review": "llm"}
        facts = feature_statuses(user)
        features = [{"id": key, "label": label, "provider": providers[key] if providers[key] in ("codex", "llm") else "unknown",
                     "configured": facts[key]["configured"], "allowed": facts[key]["allowed"],
                     "message": facts[key]["message"]} for key, label in FEATURE_LABELS.items()]
        message = MESSAGES[state]
        if not configurable:
            message = "서버의 AI 설정을 표시합니다. 브라우저에서 사용하는 PC의 Codex 로그인은 확인하지 않습니다."
        return {"deployment_mode": "local" if configurable else "server", "can_configure": configurable,
                "needs_setup": configurable and choice is None, "choice": choice,
                "codex": {"installed": installed, "auth_state": state, "checked_at": checked_at, "message": message},
                "features": features}

    @app.get("/api/ai-connection")
    def connection_status(request: Request):
        return status(request.state.user)

    @app.post("/api/ai-connection/check")
    def connection_check(value: CheckInput, request: Request):
        require_local(request.state.user)
        app.state.ai_connection_check.check()
        return status(request.state.user)

    @app.post("/api/ai-connection/setup")
    def connection_setup(value: SetupInput, request: Request):
        user = request.state.user
        require_local(user)
        if value.choice == "codex":
            state, _ = app.state.ai_connection_check.snapshot(executable())
            if state != "chatgpt" or not codex_executable_available(executable()):
                raise HTTPException(409, "먼저 Codex의 ChatGPT 로그인 상태를 다시 확인하세요.")
        with transaction(engine) as db:
            receipt = db.get(AIConnectionSetup, user, with_for_update=True)
            if receipt is None:
                db.add(AIConnectionSetup(user_id=user, choice=value.choice, updated_at=time.time()))
            elif receipt.choice != value.choice:
                receipt.choice, receipt.updated_at = value.choice, time.time()
        # Only the committed onboarding choice is acknowledged; no provider/env/job is changed.
        return status(user)
