from .standard_policy import cover_enabled, toc_mode
"""Fixed synthetic reports through production formatting, without AI or documents.

An owner-bound, short-lived signed stage pins the settings and rendered source.
Only a verified engine output can become an immutable visual-standard example.
The older source-excerpt preview and registration contracts remain independent.
"""
import base64
import copy
import hashlib
import hmac
import io
import json
import secrets
import threading
import time
import uuid
import zipfile
from xml.etree import ElementTree as ET

from fastapi import HTTPException, Request
from pydantic import Field, StrictInt, field_validator
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from .block_contract import BlockInput, decoded
from .block_hwpx import HH, Semantic, parsed, verify_engine_output
from .db import VisualStandard, transaction
from .hwpx import digest, validate
from .report_contract import ReportDraft, canonical, paragraph, report_blocks, report_hash, role
from .report_hwpx import HP, paragraphs, scaffold
from .report_rich import layout_rich_report
from .report_toc import initial_config, normalize_generated_layout, stage_response, toc_value
from .reference_standard import MAX_PAGES
from .toc_contract import TocLayoutEvidence, TocVerify
from .toc_hwpx import packed, verify_toc
from .visual_standards import (
    PreviewVisual, VisualSelection, base_standard, builtin_presets, decoded_seed,
    matches_base, settings_revision, snapshot_value, verified_file, write_immutable,
)

SAMPLE_ID = "standard-business-report-v6"
TTL_SECONDS = 600
MAX_JSON = 5_000_000
MAX_CONTEXT = 3_000_000
MAX_OUTPUT = 2_000_000
MAX_RECEIPTS = 1024
MAX_RECEIPT_BYTES = 64_000_000

# The complete, validated draft is shared by every preset/settings revision.
# No source-document example facts or user document contents are imported.
SAMPLE_V2 = ReportDraft.model_validate({
    "title": "문서 관리 개선 계획",
    "summary": "□ (요약) 문서 수집과 검토 절차를 정비하여 자료의 누락을 줄이고 담당자의 확인 기준을 통일함.",
    "sections": [
        {"heading": "정비 필요성", "paragraphs": [
            "가. 적용 범위",
            "(1) 검토 대상",
            "(가) 자료 분류",
            "① 확인 순서",
            "㉮ 세부 기준",
            "□ (현황) 합성 점검 대상 20건 중 5건의 필수 항목을 확인함. 나머지 15건은 동일한 기준으로 점검할 예정임.",
            "ㅇ (조치) 작성 담당자가 문서명과 담당 부서를 확인하고 검토 담당자가 확인 결과를 기록함.",
            "- (결론) 필수 항목이 모두 확인된 자료만 정비 완료로 분류함.",
            "․ (핵심) 확인 일자와 담당자를 함께 기록함.",
        ]},
        {"heading": "정비 방안", "paragraphs": [
            "(목적) 이 문서는 서식 비교를 위한 고정 합성 업무 자료이며 실제 업무 실적을 나타내지 않음.",
            "□ (방안) 문서명과 담당 부서를 필수 항목으로 지정하고 검토 결과를 같은 목록에 기록함. [S1] [S2]",
            "□ (조치) 보완한 자료는 담당자가 다시 확인한 뒤 완료 여부(필수 항목 충족 시)를 기록함. [S1]",
        ], "tables": [{
            "caption": "담당별 정비 항목",
            "columns": ["정비 항목", "담당", "완료 기준"],
            "rows": [["필수 항목 확인", "작성 담당", "문서명·부서 확인 [S1]"],
                     ["검토 결과 기록", "검토 담당", "확인 결과 기록 [S2]"]],
        }]},
        {"heading": "추진 일정", "paragraphs": [
            "□ (일정) 예시 운영팀은 2026년 10월 30일까지 합성 점검 대상 20건의 확인을 완료할 계획임.",
            "ㅇ (현황) 2026년 10월 23일 기준 완료 5건과 미완료 15건을 구분하여 진행 상황을 확인함.",
        ]},
    ],
    "sources": [
        {"id": "S1", "title": "합성 문서 점검 기준", "url": "https://example.invalid/checklist", "published": "2026. 10. 23."},
        {"id": "S2", "title": "합성 검토 기록 기준", "url": "https://example.invalid/review", "published": "2026. 10. 23."},
    ],
    "limitations": [], "emphasis": [],
}).model_dump(mode="json")
# Keep the previous synthetic draft available; new previews use symbol body paragraphs.
SAMPLE = copy.deepcopy(SAMPLE_V2)
SAMPLE["sections"][1]["paragraphs"][0] = "□ " + SAMPLE["sections"][1]["paragraphs"][0]
SAMPLE_V3 = copy.deepcopy(SAMPLE)
# Show each deep heading with its actual content, rather than an empty chain of headings.
first = SAMPLE["sections"][0]["paragraphs"]
SAMPLE["sections"][0]["paragraphs"] = [*first[:4], first[5], first[4], *first[6:]]
SAMPLE_V4 = copy.deepcopy(SAMPLE)
# One uninterrupted paragraph deliberately crosses several native line boundaries.
# No manual linefeeds or pre-wrapped chunks are stored in the synthetic draft.
SAMPLE["sections"][2]["paragraphs"].extend([
    "□ (방안) 문서관리체계와 업무인수인계절차를 함께 점검하여 자료의 누락 여부를 확인하고 담당자별 검토 결과를 기록함. 한글어절단위줄바꿈과 글자단위줄바꿈의 차이를 비교할 수 있도록 이 문장은 강제 개행 없이 이어지며 문서작성표준화와 부서간협업절차에 관한 설명을 같은 문단에 담음.",
    "ㅇ (조치) 필수 항목 확인과 검토 결과 기록을 구분하여 아래 기준으로 관리함.",
    "- (현황) 필수 항목은 문서명과 담당 부서이며 작성 담당자가 확인함. [S1]",
    "- (조치) 검토 담당자는 확인 일자와 담당자를 검토 결과에 함께 기록함. [S2]",
])
SAMPLE_V5 = copy.deepcopy(SAMPLE)
# Demonstrate word-level citations without changing claims or source identities.
SAMPLE["sections"][1]["paragraphs"][1] = "□ (방안) 문서명과 담당 부서[S1]를 필수 항목으로 지정하고 검토 결과[S2]를 같은 목록에 기록함."
SAMPLE["sections"][1]["tables"][0]["rows"][0][2] = "문서명·부서[S1] 확인"
SAMPLE["sections"][1]["tables"][0]["rows"][1][2] = "확인 결과[S2] 기록"
SAMPLE = ReportDraft.model_validate(SAMPLE).model_dump(mode="json")
SAMPLE_SHA256 = report_hash(SAMPLE)
SAMPLE_COVER = {"date": "2026. 10. 23.", "department": "예시 운영팀"}


class PrepareSample(PreviewVisual):
    id: uuid.UUID


class VerifySample(BlockInput):
    context: str = Field(min_length=1, max_length=MAX_CONTEXT)
    engine_output_base64: str = Field(min_length=1, max_length=MAX_OUTPUT)
    page_count: StrictInt = Field(ge=1, le=MAX_PAGES)
    layout_evidence: TocLayoutEvidence | None = None


class RegisterSample(BlockInput):
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    context: str = Field(min_length=1, max_length=MAX_CONTEXT)
    preview_base64: str = Field(min_length=1, max_length=MAX_OUTPUT)
    replaces: uuid.UUID | None = None

    _name = field_validator("name")(paragraph)


async def sample_input(request, model):
    if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > MAX_JSON:
        raise HTTPException(413, "표준 예시 요청은 5MB 이하여야 합니다.")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_JSON:
            raise HTTPException(413, "표준 예시 요청은 5MB 이하여야 합니다.")
    from pydantic import ValidationError
    try:
        return model.model_validate_json(bytes(content))
    except ValidationError as exc:
        raise HTTPException(422, [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]) from exc


def encoded(data):
    return base64.b64encode(data).decode()


def native_synthetic_layout(data):
    """Match the initial native serialization skipped by fixed-text insertion.

    Actual report staging already exports through the editor before rich layout.
    The native writer omits the empty control-host text, emits an empty colPr ID
    and orders all char properties (including unused source definitions). Keep
    the strict engine verifier unchanged and normalize only this new artifact.
    """
    from .reference_report import native_character
    data = normalize_generated_layout(data)
    header, section, _, _ = parsed(data)
    control_run = section[0].find(f"{{{HP}}}run")
    # PAGE and PAGE/TOTAL_PAGE footers contain the same control-only runs.
    # Preserve every nonempty text and every native auto-number control.
    for run in section[0].iter(f"{{{HP}}}run"):
        if run.find(f"{{{HP}}}ctrl") is None and run.find(f"{{{HP}}}secPr") is None:
            continue
        for empty_text in run.findall(f"{{{HP}}}t"):
            if not empty_text.attrib and not empty_text.text and not empty_text.tail and not len(empty_text):
                run.remove(empty_text)
    column = control_run.find(f"{{{HP}}}ctrl/{{{HP}}}colPr")
    if column is not None and column.get("id") == "0":
        column.set("id", "")
    for char in header.iter(f"{{{HH}}}charPr"):
        native_character(char)
    return packed(data, header, section)


def materialize(seed, settings, identifier):
    """Only these fixed synthetic strings bypass the AI exact-command stage."""
    blocks = report_blocks(SAMPLE, concise=True, rich=True, visual_settings=settings, cover=SAMPLE_COVER)
    data, plan = scaffold(seed, blocks, visual_settings=settings)
    section, _ = paragraphs(data)
    for index, item in enumerate(plan):
        item["command_id"] = str(uuid.uuid5(uuid.UUID(identifier), str(index)))
        section[item["paragraph"]].find(f"{{{HP}}}run/{{{HP}}}t").text = item["text"]
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
        for entry in source.infolist():
            target.writestr(copy.copy(entry), ET.tostring(section, encoding="utf-8", xml_declaration=True)
                            if entry.filename == "Contents/section0.xml" else source.read(entry))
    try:
        result = layout_rich_report(output.getvalue(), plan, visual_settings=settings)
        result = native_synthetic_layout(result)
    except ValueError as exc:
        raise HTTPException(422, "이 설정으로 업무 예시를 배치할 수 없습니다. 수치를 조정하세요.") from exc
    validate(result)
    return result, plan


def sample_regions(plan, settings, needs_toc):
    groups, note_regions, occurrences = {}, [], {}
    for index, item in enumerate(plan):
        kind = ("table" if item.get("visual_id") else "caption" if item.get("visual_caption")
                else item.get("style_kind", role(item["text"], item["kind"])))
        if kind == "title" and cover_enabled(settings):
            kind = "cover_title"
        if item.get("note_source_id"):
            normalized = "".join(item["text"].split())
            occurrence = occurrences.get(normalized, 0)
            occurrences[normalized] = occurrence + 1
            note_regions.append({"role": kind, "texts": [item["text"]], "occurrence": occurrence,
                                 "id": f"{item['note_scope']}:{item['note_source_id']}"})
        else:
            groups.setdefault(kind, []).append(item["text"])
        if item.get("parenthetical_role"):
            groups.setdefault("parenthetical_" + item["parenthetical_role"], []).append(item["text"])
        if item["text"] == "핵심 요약" and item["kind"] == "heading":
            groups["summary"] = [item["text"], plan[index + 1]["text"]]
    if needs_toc:
        groups["toc_title"] = ["목차"]
    labels = {"title": "대제목", "cover_title": "표지 제목", "heading": "절 제목", "subheading": "하위 제목",
              "minor_heading": "세부 제목", "body": "일반 설명", "square": "□ 항목", "circle": "ㅇ 항목",
              "dash": "- 항목", "dot": "․ 항목", "table": "표", "caption": "표 제목", "note": "본문 주석",
              "table_source": "표 주석", "toc_title": "목차 제목", "cover_date": "표지 일자", "cover_department": "표지 부서", "summary": "핵심 요약"}
    return [{"role": key, "label": labels.get(key, key), "texts": list(dict.fromkeys(values))} for key, values in groups.items()] + note_regions


def verified_regions(regions, result):
    """Locate body headings using measured pages; TOC precedes their bodies."""
    headings = {heading["text"]: heading for heading in result["headings"]}
    output = []
    for region in regions:
        for text in region["texts"]:
            heading = headings.get(text)
            output.append({**region, "texts": [text], **({"page": result["measured_pages"][heading["id"]]} if heading else {})})
    for heading in result["headings"]:
        major = heading["level"] == 1
        output.append({"role": "toc_major" if major else "toc_minor", "label": "목차 큰 항목" if major else "목차 하위 항목",
                       "texts": [heading["text"]], "occurrence": 0})
    return output


def sign(state, key):
    payload = base64.urlsafe_b64encode(canonical(state).encode()).decode()
    return payload + "." + hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def unseal(context, key, user):
    try:
        payload, signature = context.rsplit(".", 1)
        expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        state = json.loads(base64.b64decode(payload, altchars=b"-_", validate=True))
        if state["owner"] != user:
            raise HTTPException(404, "현재 사용자의 표준 예시가 아닙니다.")
        if state["expires"] <= time.time():
            raise HTTPException(409, "표준 예시 확인 시간이 지났습니다. 현재 설정으로 다시 미리보기 하세요.")
        if state["sample_id"] != SAMPLE_ID or state["sample_sha256"] != SAMPLE_SHA256:
            raise ValueError("sample")
        matches_base(state["base_standard"])
        return state
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise HTTPException(409, "표준 예시의 고정 입력을 확인할 수 없습니다. 다시 미리보기 하세요.") from exc


def pending(state):
    return {"prepare_input": state["toc_input"], "input_sha256": report_hash(state["toc_input"]),
            "config": state["toc_config"], "standard": state["base_standard"]}


def response(state, key, data, toc_stage=None):
    return {"context": sign(state, key), "phase": state["phase"], "preview_base64": encoded(data),
            "preview_sha256": digest(data), "settings_sha256": report_hash(state["settings"]),
            "base_standard": state["base_standard"], "sample_id": SAMPLE_ID, "sample_sha256": SAMPLE_SHA256,
            "sample_regions": state["regions"], "max_pages": MAX_PAGES, "needs_toc": state["needs_toc"], "expires_at": state["expires"],
            **({"page_count": state["page_count"]} if "page_count" in state else {}),
            **({"toc_stage": toc_stage} if toc_stage else {}),
            **({"verification": state["verification"]} if "verification" in state else {})}


def same_engine_document(expected, actual):
    verify_engine_output(expected, expected, actual)
    if {n for n in Semantic(expected).files if not n.startswith("Preview/")} != {n for n in Semantic(actual).files if not n.startswith("Preview/")}:
        raise HTTPException(422, "고정 예시 이외의 자원이 추가되거나 누락되었습니다.")


def install_standard_samples(app):
    engine, folder = app.state.engine, app.state.folder
    key = secrets.token_bytes(32)
    # Keep only short-lived verification receipts, never user documents/jobs.
    # Reusing one signed stage with different output/evidence is rejected.
    receipts = {}
    receipt_lock = threading.Lock()
    app.state.standard_sample_key = key
    app.state.standard_sample_receipts = receipts

    @app.post("/api/visual-standards/sample-preview")
    async def prepare(request: Request):
        value = await sample_input(request, PrepareSample)
        settings = value.settings.model_dump(mode="json")
        standard = base_standard()
        data, plan = await run_in_threadpool(materialize, decoded_seed(value.seed_base64), settings, str(value.id))
        needs_toc = toc_mode(settings) != "NONE"
        state = dict(owner=request.state.user, expires=time.time() + TTL_SECONDS, id=str(value.id),
                     sample_id=SAMPLE_ID, sample_sha256=SAMPLE_SHA256, settings=settings,
                     revision=settings_revision(value.settings), base_standard=standard, phase="layout",
                     source_base64=encoded(data), plan=plan, needs_toc=needs_toc,
                     regions=sample_regions(plan, settings, needs_toc))
        matches_base(standard)
        return response(state, key, data)

    @app.post("/api/visual-standards/sample-preview/verify")
    async def verify(request: Request):
        value = await sample_input(request, VerifySample)
        return await run_in_threadpool(verify_stage, value, request.state.user)

    def cached_receipt(receipt_key, binding, *, needed=0):
        # Caller holds receipt_lock; checked again after expensive XML work.
        for identifier in [identifier for identifier, item in receipts.items() if item["expires"] <= time.time()]:
            del receipts[identifier]
        old = receipts.get(receipt_key)
        if old:
            if old["binding"] != binding:
                raise HTTPException(409, "같은 예시 단계는 검증한 출력·쪽수 증거와 같은 입력으로 재확인하세요.")
            return old["response"]
        if len(receipts) >= MAX_RECEIPTS or sum(item["size"] for item in receipts.values()) + needed > MAX_RECEIPT_BYTES:
            raise HTTPException(429, "표준 예시 확인 요청이 많습니다. 잠시 후 다시 미리보기 하세요.")
        return None

    def verify_stage(value, user):
        state = unseal(value.context, key, user)
        actual = decoded(value.engine_output_base64)
        validate(actual)
        receipt_key = digest(value.context.encode())
        binding = report_hash({**value.model_dump(mode="json"), "context": receipt_key, "engine_output_base64": digest(actual)})
        with receipt_lock:
            old = cached_receipt(receipt_key, binding)
            if old:
                return old
        if state["phase"] == "layout":
            same_engine_document(decoded(state["source_base64"]), actual)
            if state["needs_toc"]:
                request_id = str(uuid.uuid5(uuid.UUID(state["id"]), "sample-toc"))
                state["toc_config"] = initial_config({"id": state["id"], "plan": state["plan"]}, actual, request_id)
                state["toc_input"] = dict(request_id=request_id, preview_id=state["id"],
                    layout_sha256=digest(decoded(state["source_base64"])), source_base64=encoded(actual), source_sha256=digest(actual))
                state.update(phase="toc", pass_index=1, pages={})
                state.pop("plan")
                state.pop("source_base64")
            else:
                state.update(phase="ready", preview_sha256=digest(actual), page_count=value.page_count)
                state.pop("plan")
                state.pop("source_base64")
        elif state["phase"] == "toc":
            if value.layout_evidence is None or value.layout_evidence.page_count != value.page_count:
                raise HTTPException(422, "목차의 실제 쪽수 측정 증거가 필요합니다.")
            frozen = pending(state)
            stage = stage_response(frozen, state["pass_index"], state["pages"])
            cfg = toc_value(frozen, state["pass_index"], state["pages"])
            check = TocVerify.model_validate(cfg.model_dump(mode="json") | {
                "engine_output_base64": value.engine_output_base64, "layout_evidence": value.layout_evidence.model_dump(mode="json")})
            result = verify_toc(decoded(state["toc_input"]["source_base64"]), decoded(stage["output_base64"]), actual, check, stage)
            if result["converged"]:
                state.update(phase="ready", preview_sha256=digest(actual), page_count=value.page_count, verification=result)
                state["regions"] = verified_regions(state["regions"], result)
                for name in ("toc_input", "toc_config", "pages", "pass_index"):
                    state.pop(name)
            elif result["exhausted"]:
                raise HTTPException(422, "3회 실제 배치에서 목차 쪽수가 수렴하지 않았습니다. 설정을 조정하여 다시 확인하세요.")
            else:
                state.update(pass_index=state["pass_index"] + 1, pages=result["measured_pages"])
        else:
            raise HTTPException(409, "이미 확인한 예시는 새 배치 단계로 사용할 수 없습니다.")
        matches_base(state["base_standard"])
        if state["phase"] == "toc":
            stage = stage_response(pending(state), state["pass_index"], state["pages"])
            result = response(state, key, decoded(stage["output_base64"]), stage)
        else:
            result = response(state, key, actual)
        size = len(canonical(result).encode())
        with receipt_lock:
            old = cached_receipt(receipt_key, binding, needed=size)
            if old:
                return old
            receipts[receipt_key] = {"expires": state["expires"], "binding": binding, "response": result, "size": size}
        return result

    @app.post("/api/visual-standards/sample-register")
    async def register(request: Request):
        value = await sample_input(request, RegisterSample)
        return await run_in_threadpool(register_sample, value, request.state.user)

    def register_sample(value, user):
        data = decoded(value.preview_base64)
        registration_sha256 = report_hash({**value.model_dump(mode="json", exclude_none=True), "preview_base64": digest(data)})
        with transaction(engine) as db:
            if engine.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                           {"key": int(digest(("visual-standard:" + str(value.id)).encode())[:15], 16)})
            old = db.get(VisualStandard, str(value.id))
            if old:
                if old.owner_id != user:
                    raise HTTPException(404, "표준 등록 ID를 사용할 수 없습니다.")
                if old.registration_sha256 != registration_sha256:
                    raise HTTPException(409, "같은 표준 등록 ID에 다른 입력을 사용할 수 없습니다.")
                verified_file(folder, old)
                return snapshot_value(old)
            catalog_state = None
            if value.replaces:
                from .standard_library import active_standard
                catalog_state = active_standard(db, str(value.replaces), user)
            state = unseal(value.context, key, user)
            if state["phase"] != "ready" or state.get("preview_sha256") != digest(data) or not state.get("page_count"):
                raise HTTPException(409, "실제 배치를 확인한 최종 예시만 등록할 수 있습니다.")
            if state["needs_toc"] and not state.get("verification", {}).get("converged"):
                raise HTTPException(409, "목차의 실제 쪽수 검증을 완료하세요.")
            if str(value.id) in {item["id"] for item in [*builtin_presets(), *builtin_presets(legacy=True)]}:
                raise HTTPException(409, "원문 별첨의 고정 ID는 새 표준 등록에 사용할 수 없습니다.")
            validate(data)
            snapshot = VisualSelection.model_validate(dict(id=str(value.id), name=value.name, revision=state["revision"],
                settings=state["settings"], settings_sha256=report_hash(state["settings"]),
                base_standard=state["base_standard"], preview_sha256=digest(data))).model_dump(mode="json")
            storage_key = "visual-" + str(uuid.uuid4()) + ".hwpx"
            write_immutable(folder, storage_key, data)
            row = VisualStandard(id=str(value.id), owner_id=user, snapshot=canonical(snapshot),
                registration_sha256=registration_sha256, storage_key=storage_key, size=len(data), created_at=time.time())
            db.add(row)
            db.flush()
            if value.replaces:
                from .db import VisualStandardState
                if catalog_state is None:
                    catalog_state = VisualStandardState(standard_id=str(value.replaces))
                    db.add(catalog_state)
                catalog_state.replacement_id = str(value.id)
            result = snapshot_value(row)
        return result
