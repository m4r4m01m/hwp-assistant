"""Bounded manual TOC inputs and stateless document APIs; no model or publication."""
import base64
import uuid
from typing import Literal
from fastapi import Header, HTTPException, Request
from pydantic import Field, StrictInt, model_validator
from .block_contract import BlockInput, BlockTarget, MAX_BASE64, canonical, decoded
from .document_standard import load_standard
from .hwpx import digest

POLICY = "managed-native-toc-v1"
MAX_HEADINGS = 30
MAX_PASSES = 3


def toc_catalog():
    profile, _ = load_standard()
    return {"policy": POLICY, "max_headings": MAX_HEADINGS, "max_passes": MAX_PASSES,
            "standard_reference_sha256": profile["reference_sha256"], "standard_profile_sha256": profile["profile_sha256"]}


class TocSource(BlockInput):
    request_id: uuid.UUID
    base_version: str = Field(min_length=1, max_length=36)
    source_base64: str = Field(min_length=1, max_length=MAX_BASE64)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TocHeading(BlockInput):
    heading_id: uuid.UUID
    paragraph: int = Field(ge=0, le=100000)
    level: Literal[1, 2]
    expected_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TocPreview(TocSource):
    toc_id: uuid.UUID
    target: BlockTarget
    headings: list[TocHeading] = Field(min_length=1, max_length=MAX_HEADINGS)
    pass_index: int = Field(default=1, ge=1, le=MAX_PASSES)
    pages: dict[uuid.UUID, StrictInt] = Field(default_factory=dict, max_length=MAX_HEADINGS)

    @model_validator(mode="after")
    def distinct(self):
        ids = [heading.heading_id for heading in self.headings]
        paragraphs = [heading.paragraph for heading in self.headings]
        if len(ids) != len(set(ids)) or len(paragraphs) != len(set(paragraphs)):
            raise HTTPException(422, "목차 제목 ID 또는 문단을 중복 선택할 수 없습니다.")
        if paragraphs != sorted(paragraphs) or self.headings[0].level != 1:
            raise HTTPException(422, "제목은 본문 순서로 선택하고 첫 제목은 1계층이어야 합니다.")
        if self.pages and set(self.pages) != set(ids):
            raise HTTPException(422, "목차 쪽수는 선택한 모든 제목 ID와 일치해야 합니다.")
        if self.pass_index > 1 and not self.pages:
            raise HTTPException(422, "후속 배치에는 직전 실제 페이지 측정값이 필요합니다.")
        values = [self.pages[identifier] for identifier in ids] if self.pages else []
        if any(page < 1 or page > 100000 for page in values) or values != sorted(values):
            raise HTTPException(422, "물리 쪽수 범위와 본문 순서를 확인하세요.")
        return self


class TocPage(BlockInput):
    id: uuid.UUID
    section: Literal[0]
    paragraph: int = Field(ge=0, le=100000)
    length: int = Field(ge=1, le=300)
    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page: StrictInt = Field(ge=1, le=100000)


class TocLayoutEvidence(BlockInput):
    export_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_count: int = Field(ge=1, le=100000)
    revision: int = Field(ge=0)
    epoch: int = Field(ge=0)
    headings: list[TocPage] = Field(min_length=1, max_length=MAX_HEADINGS)


class TocVerify(TocPreview):
    engine_output_base64: str = Field(min_length=1, max_length=MAX_BASE64)
    layout_evidence: TocLayoutEvidence


def input_hash(value):
    frozen = value.model_dump(mode="json", exclude={"source_base64", "engine_output_base64", "layout_evidence"})
    return digest(canonical(frozen).encode())


def install_toc(app, check_lease):
    from .auth import require_access, require_lease_owner
    from .db import transaction
    from .document_blocks import bounded_input
    from .block_contract import CheckBlock
    from .toc_hwpx import analyze, compose_toc, verify_toc
    engine = app.state.engine

    def access(doc_id, base, user, token):
        with transaction(engine) as db:
            doc = require_access(db, doc_id, user, "editor", lock=True, fresh=True)
            require_lease_owner(db, doc, user, fresh=True)
            check_lease(doc, token)
            if doc.current_version != base:
                raise HTTPException(409, "문서의 저장 기준이 변경되었습니다. 현재 문서를 다시 확인하세요.")

    @app.get("/api/document-toc")
    def catalog():
        return toc_catalog()

    @app.post("/api/documents/{doc_id}/toc-analyze")
    async def toc_analyze(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, TocSource)
        access(doc_id, value.base_version, request.state.user, x_edit_session)
        result = analyze(decoded(value.source_base64), value.source_sha256)
        access(doc_id, value.base_version, request.state.user, x_edit_session)
        return {"request_id": str(value.request_id), "input_sha256": input_hash(value), "source_sha256": value.source_sha256,
                **toc_catalog(), **result}

    @app.post("/api/documents/{doc_id}/toc-preview")
    async def toc_preview(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, TocPreview)
        access(doc_id, value.base_version, request.state.user, x_edit_session)
        output, plan = compose_toc(decoded(value.source_base64), value)
        access(doc_id, value.base_version, request.state.user, x_edit_session)
        return {"request_id": str(value.request_id), "input_sha256": input_hash(value), "source_sha256": value.source_sha256,
                "output_base64": base64.b64encode(output).decode(), "output_sha256": digest(output),
                **toc_catalog(), **plan}

    @app.post("/api/documents/{doc_id}/toc-verify")
    async def toc_verify(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, TocVerify)
        access(doc_id, value.base_version, request.state.user, x_edit_session)
        source = decoded(value.source_base64)
        expected, plan = compose_toc(source, value)
        actual = decoded(value.engine_output_base64)
        result = verify_toc(source, expected, actual, value, plan)
        access(doc_id, value.base_version, request.state.user, x_edit_session)
        return {"request_id": str(value.request_id), "input_sha256": input_hash(value), "source_sha256": value.source_sha256,
                "output_sha256": digest(actual), **result}

    @app.post("/api/documents/{doc_id}/toc-check")
    async def toc_check(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, CheckBlock)
        access(doc_id, value.base_version, request.state.user, x_edit_session)
        return {"ok": True, "base_version": value.base_version}
