"""Frozen research policy for new requests; no retries, source rewriting or saves."""
from .report_contract import ReportDraft, ReportResearchNotice
from .report_errors import ReportGenerationError

POLICIES = ("require_completed", "allow_unverified")
WARNING_MESSAGES = {
    "research_incomplete": "요청한 공개자료 조회를 모두 완료했는지 확인하지 못해 최신 정보가 미검증인 초안임. 일반 지식과 분석을 바탕으로 작성된 주장·날짜·수치·인용 및 출처를 사용자가 확인해야 함.",
    "sources_unverified": "출처 목록의 제목·URL·발행일과 본문의 인용은 모델이 제시한 미검증 후보임. 검색 도구의 완료 관측은 개별 URL의 내용·사실·최신성 검증을 뜻하지 않음.",
    "research_not_requested": "공개자료 조회를 요청하지 않은 초안임. 제공 자료와 일반 지식을 바탕으로 작성됐으며 최신 동향과 출처를 별도로 확인해야 함.",
    "inherited_research_unverified": "보완 전 초안의 공개자료 조회 미완료 경고를 유지함. 보완 중 검색 완료가 관측되어도 기존 주장과 출처가 검증됐음을 뜻하지 않음.",
}


def research_policy(value):
    """An absent field means the original strict contract, not a new default."""
    return value.get("research_policy") or "require_completed"


def policy_prompt(value, prompt):
    if value.get("research_policy") != "allow_unverified":
        return prompt
    return prompt + (
        "\nThis NEW request explicitly allows an unverified draft when enabled public research cannot be completed. "
        "Attempt the requested public research. If search is unavailable or fails, continue the same report turn "
        "using supplied material, general knowledge and clearly qualified analysis; do not invent current facts "
        "or claim that research succeeded. State research and recency limitations. Never present remembered URLs "
        "or citations as verified sources. Preserve genuine facts, quotations and source identities from a reviewed "
        "draft while keeping their verification limits. Return a completed, schema-valid ReportDraft; this policy "
        "never permits incomplete JSON, tool scope changes, automatic retries, approving or saving documents. "
        "Do not emit research_notice: the server owns execution provenance and inserts it after validation.\n")


def apply_research_policy(value, draft, evidence):
    """Called before hashing; idempotent and limited to explicitly new policies."""
    if "research_policy" not in value:
        return draft, evidence  # Historical/legacy request shape and policy stay exact.
    policy = research_policy(value)
    if policy not in POLICIES:
        raise ReportGenerationError("configuration_error")
    if evidence.get("completed") is not True:
        raise ReportGenerationError("incomplete_output")
    calls = evidence.get("web_search_calls", 0)
    started = evidence.get("web_search_started", 0)
    incomplete = evidence.get("web_search_incomplete", 0)
    if any(type(count) is not int or not 0 <= count <= 100000 for count in (calls, started, incomplete)) or incomplete > started:
        raise ReportGenerationError("protocol_error")
    requested = value["research"]
    if requested and not calls and policy == "require_completed":
        raise ReportGenerationError("research_unavailable")
    status = "not_requested" if not requested else "completed" if calls and not incomplete else "unverified"
    warnings = []
    def add(code, message=None):
        existing = next((warning for warning in warnings if warning["code"] == code), None)
        if existing is None:
            warnings.append({"code": code, "message": message or WARNING_MESSAGES[code]})
        elif message is not None:
            existing["message"] = message  # Preserve the source's exact frozen warning.
    if requested and (not calls or incomplete):
        add("research_incomplete")
    if not requested:
        add("research_not_requested")
    if draft["sources"]:
        add("sources_unverified")
    source = value.get("revision", {}).get("draft", {})
    previous = source.get("research_notice")
    if previous is not None:
        frozen = ReportResearchNotice.model_validate(previous)
        for warning in frozen.warnings:
            add(warning.code, warning.message)
        if any(warning.code in ("research_incomplete", "inherited_research_unverified") for warning in frozen.warnings):
            add("inherited_research_unverified")
    notice = ReportResearchNotice(version="research-notice-v1", policy=policy, requested=requested,
        status=status, completed_calls=calls, started_calls=started, incomplete_calls=incomplete,
        source_verification="unverified", warnings=warnings).model_dump()
    # Source entries, citations, factual prose and the original limitations are
    # untouched. Validate again before the draft and its notice acquire a hash.
    result = ReportDraft.model_validate({**draft, "research_notice": notice}).model_dump()
    research = {key: item for key, item in notice.items() if key not in ("version", "warnings")}
    return result, {**evidence, "research": research, "warnings": notice["warnings"]}
