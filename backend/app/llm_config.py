"""Immutable, server-owned model routing. Loading settings never calls a provider."""
import hashlib
import json
import math
import os
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import HTTPException


PRESETS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "openai": "https://api.openai.com/v1",
}


def invalid(message):
    # Never include environment values: a mistaken URL or model can contain a key.
    raise HTTPException(503, message)


def boolean(name, default=False):
    value = os.getenv(name, "true" if default else "false").strip().lower()
    if value not in ("true", "false"):
        invalid(f"{name}은 true 또는 false로 설정하세요.")
    return value == "true"


@dataclass(frozen=True, repr=False)
class LlmSettings:
    provider_name: str
    base: str
    model: str
    key: str
    timeout: float
    external: bool
    max_output_tokens: int
    response_format: str
    temperature: float | None
    token_limit_parameter: str
    openrouter_providers: tuple[str, ...]
    openrouter_zdr: bool

    def metadata(self):
        return {"provider": self.provider_name, "model": self.model, "external": self.external,
                "endpoint_scope": "external" if self.external else "operator-configured"}

    def configuration_fingerprint(self):
        """Bind a revision to its route and generation settings without storing secrets."""
        value = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "key"}
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()


def capture_llm_settings():
    provider = os.getenv("LLM_PROVIDER", "").strip()
    if provider not in (*PRESETS, "openai-compatible"):
        invalid("AI 미설정: LLM_PROVIDER를 openai-compatible, openrouter, nvidia 또는 openai로 설정하세요.")
    raw_base = os.getenv("LLM_BASE_URL", "")
    if any(ord(c) < 32 or ord(c) == 127 for c in raw_base):
        invalid("LLM_BASE_URL 주소 형식이 올바르지 않습니다.")
    base = raw_base.strip().rstrip("/") or PRESETS.get(provider, "")
    model = os.getenv("LLM_MODEL", "").strip()
    key = os.getenv("LLM_API_KEY", "").strip()
    external = boolean("LLM_ALLOW_EXTERNAL")
    if not base or not model:
        invalid("AI 미설정: LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL을 설정하세요.")
    if len(model) > 256 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in model):
        invalid("LLM_MODEL에는 사용할 모델 ID 하나를 설정하세요.")
    if len(key) > 8192 or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in key):
        invalid("LLM_API_KEY의 형식을 확인하세요.")
    try:
        parsed = urlparse(base)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        invalid("LLM_BASE_URL 주소 형식이 올바르지 않습니다.")
    if (parsed.scheme not in ("http", "https") or not host
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment):
        invalid("인증정보·쿼리 없는 HTTP(S) LLM_BASE_URL 주소를 설정하세요.")
    if provider in PRESETS:
        expected = urlparse(PRESETS[provider])
        if (parsed.scheme != "https" or host != expected.hostname or port not in (None, 443)
            or parsed.path.rstrip("/") != expected.path):
            invalid("선택한 LLM_PROVIDER의 공식 HTTPS 기본 주소를 사용하세요. 다른 주소는 openai-compatible로 명시하세요.")
        if not external:
            invalid("외부 AI 사용은 LLM_ALLOW_EXTERNAL=true로 명시적으로 허용해야 합니다.")
        if not key:
            invalid("외부 AI 사용을 위한 서버의 LLM_API_KEY가 필요합니다.")
        if provider == "openrouter" and (model.lower().startswith("openrouter/") or "@" in model):
            invalid("자동 모델 라우터 대신 LLM_MODEL에 사용할 모델 ID 하나를 고정하세요.")
        if provider == "openrouter" and "online" in model.lower().split(":")[1:]:
            invalid("현재 HTTP AI 연결은 웹 검색을 지원하지 않습니다. :online 없는 모델 ID를 사용하세요.")
        base = PRESETS[provider]
    else:
        if (host == "openai.com" or host.endswith(".openai.com")
            or host == "openrouter.ai" or host.endswith(".openrouter.ai")
            or host == "integrate.api.nvidia.com"):
            invalid("공식 외부 AI 주소는 해당 LLM_PROVIDER와 LLM_ALLOW_EXTERNAL=true로 명시하세요.")
        # Custom private endpoints remain operator-declared; no DNS/locality probe is made.
        if external and parsed.scheme != "https":
            invalid("외부 OpenAI 호환 API는 HTTPS 주소를 사용해야 합니다.")
        if external and not key:
            invalid("외부 AI 사용을 위한 서버의 LLM_API_KEY가 필요합니다.")
    try:
        timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        if not math.isfinite(timeout) or not 0 < timeout <= 240:
            raise ValueError()
    except ValueError:
        invalid("LLM_TIMEOUT_SECONDS는 0보다 크고 240 이하인 초 단위 값이어야 합니다.")
    try:
        max_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8000"))
        if not 256 <= max_tokens <= 32768:
            raise ValueError()
    except ValueError:
        invalid("LLM_MAX_OUTPUT_TOKENS는 256 이상 32768 이하의 정수여야 합니다.")
    response_format = os.getenv("LLM_RESPONSE_FORMAT", "text").strip()
    if response_format not in ("text", "json_object", "json_schema"):
        invalid("LLM_RESPONSE_FORMAT은 text, json_object 또는 json_schema로 설정하세요.")
    raw_temperature = os.getenv("LLM_TEMPERATURE", "0.2").strip()
    try:
        temperature = None if raw_temperature == "omit" else float(raw_temperature)
        if temperature is not None and (not math.isfinite(temperature) or not 0 <= temperature <= 2):
            raise ValueError()
    except ValueError:
        invalid("LLM_TEMPERATURE는 0 이상 2 이하의 수 또는 omit이어야 합니다.")
    token_parameter = os.getenv("LLM_TOKEN_LIMIT_PARAMETER", "").strip() or (
        "max_completion_tokens" if provider == "openai" else "max_tokens")
    if token_parameter not in ("max_tokens", "max_completion_tokens"):
        invalid("LLM_TOKEN_LIMIT_PARAMETER는 max_tokens 또는 max_completion_tokens로 설정하세요.")
    upstreams, zdr = (), False
    if provider == "openrouter":
        raw_upstreams = os.getenv("LLM_OPENROUTER_PROVIDERS", "").strip()
        if raw_upstreams:
            upstreams = tuple(dict.fromkeys(part.strip() for part in raw_upstreams.split(",")))
            if (len(upstreams) > 16 or any(not item or len(item) > 160 or any(c.isspace() for c in item)
                                         for item in upstreams)):
                invalid("LLM_OPENROUTER_PROVIDERS에는 공급자 ID를 쉼표로 구분해 설정하세요.")
        zdr = boolean("LLM_OPENROUTER_ZDR")
    return LlmSettings(provider, base, model, key, timeout, external, max_tokens, response_format,
                       temperature, token_parameter, upstreams, zdr)
