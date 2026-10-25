"""Fenced, style-only previews for an existing editor buffer. Never publishes."""
import base64
import json
import uuid
from typing import Literal

from fastapi import Header, HTTPException, Request
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .auth import require_access, require_lease_owner
from .block_contract import BlockInput, MAX_BASE64, canonical, decoded
from .block_hwpx import Semantic, verify_engine_output
from .db import DesignFragment, VisualStandard, VisualStandardState, transaction
from .document_blocks import bounded_input
from .document_standard import load_standard
from .editor_design_hwpx import transform
from .hwpx import digest
from .reference_standard import VisualSettingsV3, defaults
from .standard_library import fragment_settings
from .visual_standards import builtin_presets, snapshot_value, verified_file


class DesignTarget(BlockInput):
    kind: Literal["document", "paragraph", "table"]
    section: Literal[0]
    paragraph: int | None = Field(default=None, strict=True, ge=0, le=100000)
    control: int | None = Field(default=None, strict=True, ge=0, le=100000)

    @model_validator(mode="after")
    def coordinates(self):
        if self.kind == "document" and (self.paragraph is not None or self.control is not None):
            raise ValueError("문서 전체 적용에는 부분 위치를 지정할 수 없습니다.")
        if self.kind != "document" and self.paragraph is None:
            raise ValueError("부분 서식을 적용할 문단 위치가 필요합니다.")
        if self.kind == "paragraph" and self.control is not None:
            raise ValueError("문단 서식에는 표 위치를 지정할 수 없습니다.")
        if self.kind == "table" and self.control is None:
            raise ValueError("서식을 적용할 표 위치가 필요합니다.")
        return self


class EditorChoice(BlockInput):
    kind: Literal["design", "standard"]
    id: str = Field(min_length=1, max_length=80)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PreviewDesign(BlockInput):
    request_id: uuid.UUID
    base_version: str = Field(min_length=1, max_length=36)
    source_base64: str = Field(min_length=1, max_length=MAX_BASE64)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target: DesignTarget
    choice: EditorChoice


class VerifyDesign(PreviewDesign):
    engine_output_base64: str = Field(min_length=1, max_length=MAX_BASE64)


class CheckDesign(BlockInput):
    base_version: str = Field(min_length=1, max_length=36)


def input_hash(value):
    return digest(canonical(value.model_dump(mode="json", exclude={"source_base64", "engine_output_base64"})).encode())


def catalog_item(identifier, name, kind, settings, *, builtin=False):
    profile, _ = load_standard()
    frozen = dict(id=identifier, kind=kind, settings=settings,
                  reference_sha256=profile["reference_sha256"], profile_sha256=profile["profile_sha256"],
                  policy="editor-style-only-v1")
    return dict(id=identifier, name=name, kind=kind, sha256=digest(canonical(frozen).encode()),
                settings=settings, builtin=builtin)


def editor_design_catalog(db, user):
    base = defaults()
    designs = [catalog_item("reference-" + kind, name, kind, fragment_settings(base, kind), builtin=True)
               for kind, name in (("table", "기준서 표 · 회색 머리글"), ("title", "기준서 제목 · 파란 상하선"))]
    for row in db.scalars(select(DesignFragment).where(DesignFragment.owner_id == user,
                          DesignFragment.hidden == False).order_by(DesignFragment.created_at.desc())):
        if row.kind not in ("table", "title"):
            continue
        saved = json.loads(row.settings)
        # Older fragments stay immutable. Validate the supported projection rather
        # than accepting extra fields as an editor transformation instruction.
        safe = fragment_settings(saved, row.kind)
        if safe != saved:
            continue
        VisualSettingsV3.model_validate({**base, **safe})
        designs.append(catalog_item(row.id, row.name, row.kind, safe))
    standards = [catalog_item(item["id"], item["name"], "standard", item["settings"], builtin=True)
                 for item in builtin_presets()]
    for row in db.scalars(select(VisualStandard).where(VisualStandard.owner_id == user)
                          .order_by(VisualStandard.created_at.desc())):
        state = db.get(VisualStandardState, row.id)
        if state and (state.hidden or state.replacement_id):
            continue
        snapshot = snapshot_value(row)
        if snapshot["revision"] != 3:
            continue
        settings = VisualSettingsV3.model_validate(snapshot["settings"]).model_dump(mode="json")
        profile, _ = load_standard()
        if snapshot["base_standard"]["reference_sha256"] != profile["reference_sha256"] or snapshot["base_standard"]["profile_sha256"] != profile["profile_sha256"]:
            continue
        standards.append(catalog_item(row.id, snapshot["name"], "standard", settings))
    return dict(designs=designs, standards=standards)


def resolve_editor_choice(db, user, choice, *, folder=None):
    catalog = editor_design_catalog(db, user)
    items = catalog["standards" if choice.kind == "standard" else "designs"]
    item = next((item for item in items if item["id"] == choice.id), None)
    if item is None:
        raise HTTPException(404, "현재 계정에서 사용할 수 있는 서식이 아닙니다. 목록을 다시 확인하세요.")
    if item["sha256"] != choice.sha256:
        raise HTTPException(409, "선택한 서식이 변경되었습니다. 목록에서 다시 선택하세요.")
    if choice.kind == "standard" and not item["builtin"] and folder is not None:
        verified_file(folder, db.get(VisualStandard, choice.id))
    return item


def install_editor_designs(app, check_lease):
    engine, folder = app.state.engine, app.state.folder

    def checked(doc_id, base_version, user, token, choice=None):
        with transaction(engine) as db:
            doc = require_access(db, doc_id, user, "editor", lock=True, fresh=True)
            require_lease_owner(db, doc, user, fresh=True)
            check_lease(doc, token)
            if doc.current_version != base_version:
                raise HTTPException(409, "문서의 저장 기준이 변경되었습니다. 현재 문서를 다시 확인하세요.")
            return resolve_editor_choice(db, user, choice, folder=folder) if choice else None

    @app.get("/api/editor-designs")
    def catalog(request: Request):
        with Session(engine) as db:
            return editor_design_catalog(db, request.state.user)

    def prepare(doc_id, value, user, token):
        item = checked(doc_id, value.base_version, user, token, value.choice)
        source = decoded(value.source_base64)
        if digest(source) != value.source_sha256:
            raise HTTPException(422, "현재 편집 문서의 해시가 일치하지 않습니다.")
        data, summary, warnings = transform(source, value.target.model_dump(), item)
        checked(doc_id, value.base_version, user, token, value.choice)
        return source, data, summary, warnings

    @app.post("/api/documents/{doc_id}/design-preview")
    async def preview(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, PreviewDesign)
        _, output, summary, warnings = await run_in_threadpool(prepare, doc_id, value, request.state.user, x_edit_session)
        return dict(request_id=str(value.request_id), input_sha256=input_hash(value), source_sha256=value.source_sha256,
                    output_base64=base64.b64encode(output).decode(), output_sha256=digest(output), summary=summary, warnings=warnings)

    @app.post("/api/documents/{doc_id}/design-verify")
    async def verify(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, VerifyDesign)
        source, expected, _, _ = await run_in_threadpool(prepare, doc_id, value, request.state.user, x_edit_session)
        actual = decoded(value.engine_output_base64)
        await run_in_threadpool(verify_engine_output, source, expected, actual)
        expected_files, actual_files = Semantic(expected).files, Semantic(actual).files
        if {name for name in expected_files if not name.startswith("Preview/")} != {name for name in actual_files if not name.startswith("Preview/")}:
            raise HTTPException(422, "서식 적용 미리보기에 예상하지 않은 문서 자원이 추가되거나 누락되었습니다.")
        checked(doc_id, value.base_version, request.state.user, x_edit_session, value.choice)
        return dict(request_id=str(value.request_id), input_sha256=input_hash(value), source_sha256=value.source_sha256,
                    output_sha256=digest(actual), verified=True)

    @app.post("/api/documents/{doc_id}/design-check")
    async def check(doc_id: str, request: Request, x_edit_session: str = Header()):
        value = await bounded_input(request, CheckDesign)
        checked(doc_id, value.base_version, request.state.user, x_edit_session)
        return dict(ok=True, base_version=value.base_version)
