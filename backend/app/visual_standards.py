"""Owner-scoped immutable visual standards over the verified report standard.

The preview uses fixed synthetic prose and the same HWPX renderer as a report.
It never calls an AI model, updates the bundled assets or publishes a document.
"""
import base64
import copy
import io
import os
import time
import uuid
import zipfile
from typing import Annotated, Literal
from urllib.parse import quote
from xml.etree import ElementTree as ET

from fastapi import HTTPException, Request, Response
from pydantic import ConfigDict, Field, field_validator, model_serializer, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import VisualStandard, transaction
from .document_standard import metadata
from .hwpx import digest, validate
from .report_contract import PARENTHETICAL_SCOPES, ReportValue, canonical, paragraph, report_blocks, report_hash, validate_parenthetical_scope
from .report_hwpx import HP, paragraphs, scaffold
from .report_rich import layout_rich_report
from . import reference_standard
from .reference_standard import VisualSettingsV3

Size = Annotated[float, Field(strict=True, ge=8, le=36, allow_inf_nan=False)]
Line = Annotated[int, Field(strict=True, ge=100, le=220)]
After = Annotated[float, Field(strict=True, ge=0, le=36, allow_inf_nan=False)]
Indent = Annotated[float, Field(strict=True, ge=0, le=40, allow_inf_nan=False)]
Margin = Annotated[float, Field(strict=True, ge=10, le=35, allow_inf_nan=False)]
Color = Annotated[str, Field(strict=True, pattern=r"^#[0-9A-Fa-f]{6}$")]
Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class VisualSettings(ReportValue):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    title_size_pt: Size
    title_line_percent: Line
    title_after_pt: After
    heading_size_pt: Size
    heading_line_percent: Line
    heading_after_pt: After
    body_size_pt: Size
    body_line_percent: Line
    body_after_pt: After
    source_size_pt: Size
    source_line_percent: Line
    source_after_pt: After
    square_indent_pt: Indent
    circle_indent_pt: Indent
    dash_indent_pt: Indent
    margin_top_mm: Margin
    margin_bottom_mm: Margin
    margin_left_mm: Margin
    margin_right_mm: Margin
    table_size_pt: Annotated[float, Field(strict=True, ge=8, le=20, allow_inf_nan=False)]
    table_line_percent: Line
    table_header_fill: Color
    table_border_color: Color

    @field_validator("table_header_fill", "table_border_color")
    @classmethod
    def normalized_color(cls, value):
        return value.upper()

    @model_validator(mode="after")
    def ordered_indents(self):
        if not self.square_indent_pt <= self.circle_indent_pt <= self.dash_indent_pt:
            raise ValueError("문단 들여쓰기는 □ → ㅇ → - 순서대로 유지하세요.")
        return self


class VisualSettingsV2(VisualSettings):
    """Required added fields keep revision-1 JSON and its hashes byte-stable."""
    table_horizontal_align: Literal["LEFT", "CENTER", "RIGHT"]
    table_vertical_align: Literal["TOP", "CENTER", "BOTTOM"]
    table_border_style: Literal["SOLID", "DASH", "DOT", "NONE"]
    table_border_width_mm: Annotated[float, Field(strict=True, ge=.1, le=1, allow_inf_nan=False)]
    table_border_scope: Literal["ALL", "OUTER", "HORIZONTAL", "NONE"]
    table_cell_padding_mm: Annotated[float, Field(strict=True, ge=0, le=5, allow_inf_nan=False)]
    title_vertical_offset_pt: Annotated[float, Field(strict=True, ge=-12, le=24, allow_inf_nan=False)]
    body_parenthetical_labels: Annotated[bool, Field(strict=True)]
    body_parenthetical_scope: str | None = Field(default=None, max_length=64)
    show_core_summary: Annotated[bool, Field(strict=True)]
    page_number_style: Literal["DASH", "PLAIN", "ROMAN", "NONE"]
    page_number_position: Literal["BOTTOM_CENTER", "BOTTOM_LEFT", "BOTTOM_RIGHT"]
    citation_style: Literal["BRACKET_S", "NOTE", "ASTERISK"]

    _scope = field_validator("body_parenthetical_scope", mode="before")(validate_parenthetical_scope)

    @model_serializer(mode="wrap")
    def preserve_legacy_scope(self, handler):
        value = handler(self)
        if value.get("body_parenthetical_scope") is None:
            value.pop("body_parenthetical_scope", None)
        return value

    @field_validator("table_border_width_mm")
    @classmethod
    def supported_border_width(cls, value):
        if value not in (.1, .12, .15, .2, .25, .3, .4, .5, .6, .7, 1.0):
            raise ValueError("HWPX에서 지원하는 선 두께를 선택하세요.")
        return value


LEGACY_DEFAULTS = VisualSettings.model_validate({
    "title_size_pt": 22, "title_line_percent": 160, "title_after_pt": 20,
    "heading_size_pt": 16, "heading_line_percent": 160, "heading_after_pt": 14,
    "body_size_pt": 15, "body_line_percent": 160, "body_after_pt": 10,
    "source_size_pt": 12, "source_line_percent": 130, "source_after_pt": 7,
    "square_indent_pt": 0, "circle_indent_pt": 10, "dash_indent_pt": 20,
    "margin_top_mm": 20, "margin_bottom_mm": 10, "margin_left_mm": 20, "margin_right_mm": 20,
    "table_size_pt": 12, "table_line_percent": 125,
    "table_header_fill": "#E8EFF8", "table_border_color": "#7595B7",
}).model_dump(mode="json")
DEFAULTS = VisualSettingsV2.model_validate({**LEGACY_DEFAULTS,
    "table_horizontal_align": "LEFT", "table_vertical_align": "CENTER",
    "table_border_style": "SOLID", "table_border_width_mm": .1, "table_border_scope": "ALL",
    "table_cell_padding_mm": 1.8, "title_vertical_offset_pt": 12,
    "body_parenthetical_labels": True, "show_core_summary": True,
    "page_number_style": "DASH", "page_number_position": "BOTTOM_CENTER", "citation_style": "BRACKET_S",
}).model_dump(mode="json")


def field_catalog():
    fields = []
    for prefix, group in (("title", "대제목"), ("heading", "절 제목"), ("body", "본문"), ("source", "출처·주석")):
        for suffix, label, minimum, maximum, step, unit in (
            ("size_pt", "글자 크기", 8, 36, .5, "pt"),
            ("line_percent", "줄 간격", 100, 220, 5, "%"),
            ("after_pt", "문단 뒤 간격", 0, 36, 1, "pt"),
        ):
            fields.append(dict(key=f"{prefix}_{suffix}", label=label, group=group, type="number",
                               min=minimum, max=maximum, step=step, unit=unit))
    for prefix, label in (("square", "□ 항목"), ("circle", "ㅇ 항목"), ("dash", "- 항목")):
        fields.append(dict(key=prefix + "_indent_pt", label=label, group="문단 계층", type="number", min=0, max=40, step=1, unit="pt"))
    for side, label in (("top", "위쪽"), ("bottom", "아래쪽"), ("left", "왼쪽"), ("right", "오른쪽")):
        fields.append(dict(key=f"margin_{side}_mm", label=label, group="쪽 여백", type="number", min=10, max=35, step=1, unit="mm"))
    fields.extend([
        dict(key="table_size_pt", label="글자 크기", group="표", type="number", min=8, max=20, step=.5, unit="pt"),
        dict(key="table_line_percent", label="줄 간격", group="표", type="number", min=100, max=220, step=5, unit="%"),
        dict(key="table_header_fill", label="머리글 채움색", group="표", type="color", min=None, max=None, step=None, unit=""),
        dict(key="table_border_color", label="테두리 색", group="표", type="color", min=None, max=None, step=None, unit=""),
    ])
    def choice(key, label, group, options, **extras):
        return dict(key=key, label=label, group=group, type="select", unit="",
                    options=[dict(value=value, label=name) for value, name in options], **extras)
    fields.extend([
        dict(key="title_vertical_offset_pt", label="제목 세로 위치", group="대제목", type="number", min=-12, max=24, step=1, unit="pt",
             help="양수는 파란 상하선 안의 위쪽 여백을 늘려 제목을 아래로, 음수는 아래쪽 여백을 늘립니다."),
        choice("table_horizontal_align", "가로 정렬", "표", [("LEFT", "왼쪽"), ("CENTER", "가운데"), ("RIGHT", "오른쪽")]),
        choice("table_vertical_align", "세로 정렬", "표", [("TOP", "위쪽"), ("CENTER", "가운데"), ("BOTTOM", "아래쪽")]),
        choice("table_border_scope", "테두리 적용", "표", [("ALL", "전체"), ("OUTER", "바깥선"), ("HORIZONTAL", "가로선"), ("NONE", "없음")]),
        choice("table_border_style", "선 종류", "표", [("SOLID", "실선"), ("DASH", "파선"), ("DOT", "점선"), ("NONE", "없음")],
               disabled_when=dict(key="table_border_scope", value="NONE")),
        choice("table_border_width_mm", "선 두께", "표", [(value, f"{value:g} mm") for value in (.1, .12, .15, .2, .25, .3, .4, .5, .6, .7, 1.0)],
               disabled_when=dict(key="table_border_scope", value="NONE")),
        dict(key="table_cell_padding_mm", label="셀 안쪽 여백", group="표", type="number", min=0, max=5, step=.1, unit="mm"),
        dict(key="body_parenthetical_labels", label="괄호 요약어", group="본문 구성", type="boolean", unit="",
             help="(현황), (조치)처럼 문장 앞에 붙는 짧은 요약어입니다. 표시할 항목을 선택할 수 있으며 제목·표·주석과 본문의 사실·조건은 바꾸지 않습니다."),
        dict(key="show_core_summary", label="핵심 요약", group="본문 구성", type="boolean", unit="",
             help="끄면 별도 핵심 요약 절과 요약 내용을 표시하지 않습니다. 원래 초안의 요약·업무 절·출처는 보존됩니다."),
        choice("page_number_style", "쪽번호 형식", "쪽번호·주석", [("DASH", "- 1 -"), ("PLAIN", "1"), ("ROMAN", "- I - / - II -"), ("NONE", "없음")]),
        choice("page_number_position", "쪽번호 위치", "쪽번호·주석", [("BOTTOM_LEFT", "아래 왼쪽"), ("BOTTOM_CENTER", "아래 가운데"), ("BOTTOM_RIGHT", "아래 오른쪽")],
               disabled_when=dict(key="page_number_style", value="NONE")),
        choice("citation_style", "출처 표식", "쪽번호·주석", [("BRACKET_S", "[S1]"), ("NOTE", "주1)"), ("ASTERISK", "*1")],
               help="본문과 출처 목록을 같은 표식으로 연결합니다. 쪽 아래 각주로 변환하는 기능은 아닙니다."),
    ])
    return fields


class VisualBaseStandard(ReportValue):
    id: str = Field(min_length=1, max_length=100)
    reference_sha256: Sha
    profile_sha256: Sha


class VisualSelection(ReportValue):
    id: uuid.UUID
    revision: Literal[1, 2, 3]
    name: str = Field(min_length=1, max_length=120)
    settings: VisualSettingsV3 | VisualSettingsV2 | VisualSettings
    settings_sha256: Sha
    base_standard: VisualBaseStandard
    preview_sha256: Sha

    _name = field_validator("name")(paragraph)

    @field_validator("revision", mode="before")
    @classmethod
    def integer_revision(cls, value):
        if type(value) is not int:
            raise ValueError("표준 리비전은 정수여야 합니다.")
        return value

    @model_validator(mode="after")
    def bound_settings(self):
        if self.revision != settings_revision(self.settings):
            raise ValueError("표준 리비전과 설정 계약이 일치하지 않습니다.")
        if report_hash(self.settings.model_dump(mode="json")) != self.settings_sha256:
            raise ValueError("서식 설정과 해시가 일치하지 않습니다.")
        return self


class PreviewVisual(ReportValue):
    settings: VisualSettingsV3 | VisualSettingsV2 | VisualSettings
    seed_base64: str = Field(min_length=1, max_length=2000000)


class RegisterVisual(PreviewVisual):
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    settings_sha256: Sha
    preview_sha256: Sha
    base_standard: VisualBaseStandard

    _name = field_validator("name")(paragraph)


def base_standard():
    value = metadata()
    return {key: value[key] for key in ("id", "reference_sha256", "profile_sha256")}


def settings_revision(settings):
    return 3 if isinstance(settings, VisualSettingsV3) else 2 if isinstance(settings, VisualSettingsV2) else 1


def builtin_presets(*, legacy=False):
    """Stable source-based choices use the same immutable selection contract."""
    standard = base_standard()
    defaults = reference_standard.legacy_defaults if legacy else reference_standard.defaults
    return [dict(id=reference_standard.builtin_identity(item["preset_id"], legacy=legacy), name=item["name"], revision=3,
        settings=VisualSettingsV3.model_validate(defaults(item["preset_id"])).model_dump(mode="json"),
        base_standard=standard, **{key: value for key, value in item.items() if key in ("description", "source_range")})
        for item in reference_standard.preset_definitions()]


def _complete_builtin(item):
    settings = item["settings"]
    return {**item, "settings_sha256": report_hash(settings),
            "preview_sha256": digest(reference_standard.render_preset(settings))}


def builtin_selections():
    return [_complete_builtin(item) for item in builtin_presets()]


def _selection_only(item):
    return {key: item[key] for key in VisualSelection.model_fields}


def matches_base(expected):
    if base_standard() != expected:
        raise HTTPException(409, "기본 문서 표준이 변경되었습니다. 예시를 다시 확인하세요.")


def decoded_seed(value):
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise HTTPException(422, "새 빈 문서의 파일 인코딩이 올바르지 않습니다.") from exc


# These are deliberately synthetic labels and facts, rather than AI-written text
# or a user's document. The same fixed content is used for every settings change.
SAMPLE = {
    "title": "문서작성 표준 예시",
    "summary": "□ (미리보기) 이 문서는 글자 크기, 줄 간격과 표 색상을 비교하기 위한 고정 합성 예시입니다. 실제 업무자료가 아닙니다.",
    "sections": [
        {"heading": "문단 계층", "paragraphs": [
            "□ (핵심 항목) 하나의 논점은 한 항목으로 작성하고, 필요한 설명은 본문에 담아 제목과 내용을 쉽게 구분합니다.",
            "ㅇ (세부 설명) 상위 항목에 이어지는 내용은 한 단계 들여써 문단 사이의 관계를 보여 줍니다.",
            "- (확인 사항) 하위 조건과 확인할 내용은 같은 계층의 서식으로 표현합니다.",
        ]},
        {"heading": "표 서식", "paragraphs": [], "tables": [{
            "caption": "합성 업무 항목",
            "columns": ["항목", "담당", "확인 기준"],
            "rows": [["자료 정리", "작성자", "필수 항목 확인"], ["내용 검토", "검토자", "사실과 표현 확인"]],
        }]},
        {"heading": "출처와 주석", "paragraphs": [
            "□ (참고) 등록한 서식은 새 보고서 작성에서 선택합니다. 기존 문서와 이미 확정한 초안은 그대로 유지됩니다.",
        ]},
    ],
    "sources": [], "limitations": ["서식 확인용 고정 합성 예시이며 실제 보고서나 모델 생성 결과가 아닙니다."],
    "emphasis": [],
}


def render_preview(seed, settings):
    """Render synthetic content; all report production uses the same style functions."""
    if settings.get("preset_id") in reference_standard.PRESETS:
        return reference_standard.render_preview(seed, settings)
    sample = SAMPLE
    if "citation_style" in settings:
        sample = copy.deepcopy(SAMPLE)
        sample["sources"] = [{"id": "S1", "title": "합성 예시 자료", "url": "https://example.com/synthetic-standard", "published": "2026. 10. 18."}]
        sample["sections"][2]["paragraphs"][0] += " [S1]"
    blocks = report_blocks(sample, concise=True, rich=True, visual_settings=settings)
    data, plan = scaffold(seed, blocks, visual_settings=settings)
    section, _ = paragraphs(data)
    for item in plan:
        section[item["paragraph"]].find(f"{{{HP}}}run/{{{HP}}}t").text = item["text"]
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
        for entry in source.infolist():
            target.writestr(copy.copy(entry), ET.tostring(section, encoding="utf-8", xml_declaration=True)
                            if entry.filename == "Contents/section0.xml" else source.read(entry))
    try:
        result = layout_rich_report(output.getvalue(), plan, visual_settings=settings)
    except ValueError as exc:
        raise HTTPException(422, "이 설정으로 예시를 배치할 수 없습니다. 수치를 조정하세요.") from exc
    validate(result)
    return result


def snapshot_value(row):
    try:
        return VisualSelection.model_validate_json(row.snapshot).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(503, "등록한 표준 설정을 확인할 수 없습니다.") from exc


def verified_file(folder, row):
    target = (folder / row.storage_key).resolve()
    if target.parent != folder.resolve():
        raise HTTPException(503, "등록한 표준의 파일 경로를 확인할 수 없습니다.")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise HTTPException(503, "등록한 표준 파일을 읽지 못했습니다. 다른 서식으로 대체하지 않았습니다.") from exc
    if len(data) != row.size or digest(data) != snapshot_value(row)["preview_sha256"]:
        raise HTTPException(422, "등록한 표준 파일의 크기 또는 해시가 일치하지 않습니다.")
    validate(data)
    return data


def validate_visual_selection(db, user, value, *, folder=None):
    """Only new report tasks resolve a registration; frozen retries keep their input."""
    supplied = value.model_dump(mode="json")
    # Historical built-in choices retain their exact settings and renderer.
    # They are accepted for old inputs, but only the new policy is advertised.
    candidate = next((item for item in [*builtin_presets(), *builtin_presets(legacy=True)]
                      if item["id"] == str(value.id)), None) if value.revision == 3 else None
    if candidate is not None:
        if _selection_only(_complete_builtin(candidate)) != supplied:
            raise HTTPException(409, "선택한 원문 별첨 표준의 고정 값이 다릅니다. 목록을 다시 확인하세요.")
        matches_base(supplied["base_standard"])
        return supplied
    row = db.get(VisualStandard, str(value.id))
    if not row or row.owner_id != user:
        raise HTTPException(404, "등록한 문서 표준을 찾을 수 없습니다.")
    if snapshot_value(row) != supplied:
        raise HTTPException(409, "선택한 문서 표준의 고정 값이 다릅니다. 목록을 다시 확인하세요.")
    if folder is not None:
        verified_file(folder, row)
    matches_base(supplied["base_standard"])
    return supplied


def write_immutable(folder, key, data):
    try:
        with (folder / key).open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if os.name != "nt":
            fd = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except OSError as exc:
        raise HTTPException(503, "표준 파일 저장을 완료하지 못했습니다. 같은 등록을 다시 확인하세요.") from exc


def install_visual_standards(app):
    engine, folder = app.state.engine, app.state.folder
    from .standard_samples import install_standard_samples
    install_standard_samples(app)
    from .standard_library import install_standard_library
    install_standard_library(app)

    @app.get("/api/visual-standards")
    def catalog(request: Request):
        with Session(engine) as db:
            from .db import VisualStandardState
            inactive = set(db.scalars(select(VisualStandardState.standard_id).where(
                (VisualStandardState.hidden == True) | (VisualStandardState.replacement_id.is_not(None)))))
            items = [snapshot_value(row) for row in db.scalars(select(VisualStandard)
                     .where(VisualStandard.owner_id == request.state.user).order_by(VisualStandard.created_at.desc())) if row.id not in inactive]
        presets = builtin_selections()
        return {"defaults": reference_standard.defaults(), "fields": reference_standard.field_catalog(), "items": items,
                "revision": 3, "legacy": {
                    "1": {"defaults": LEGACY_DEFAULTS, "fields": [field for field in field_catalog() if field["key"] in LEGACY_DEFAULTS]},
                    "2": {"defaults": DEFAULTS, "fields": field_catalog()},
                }, "presets": presets, "default_id": presets[0]["id"], "max_pages": reference_standard.MAX_PAGES,
                "source": reference_standard.source_metadata(), "rules": reference_standard.rules(),
                "reference_preview_url": "/api/document-standard/preview",
                "parenthetical_scopes": [{"value": key, "label": label} for key, label in PARENTHETICAL_SCOPES]}

    @app.get("/api/document-standard/preview")
    def original_preview():
        standard = base_standard()
        data = reference_standard.original_projection()
        matches_base(standard)
        return {"name": metadata()["name"], "preview_base64": base64.b64encode(data).decode(),
                "preview_sha256": digest(data), "reference_sha256": standard["reference_sha256"],
                "max_pages": reference_standard.MAX_PAGES}

    @app.post("/api/visual-standards/preview")
    def preview(value: PreviewVisual):
        settings = value.settings.model_dump(mode="json")
        standard = base_standard()
        data = render_preview(decoded_seed(value.seed_base64), settings)
        matches_base(standard)
        return {"settings_sha256": report_hash(settings), "preview_sha256": digest(data),
                "preview_base64": base64.b64encode(data).decode(), "base_standard": standard,
                "max_pages": reference_standard.MAX_PAGES if isinstance(value.settings, VisualSettingsV3) else 6}

    @app.post("/api/visual-standards")
    def register(value: RegisterVisual, request: Request):
        seed = decoded_seed(value.seed_base64)
        frozen = value.model_dump(mode="json")
        registration_sha256 = report_hash({**frozen, "seed_base64": digest(seed)})
        with transaction(engine) as db:
            if engine.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                           {"key": int(digest(("visual-standard:" + str(value.id)).encode())[:15], 16)})
            old = db.get(VisualStandard, str(value.id))
            if old:
                if old.owner_id != request.state.user:
                    raise HTTPException(404, "표준 등록 ID를 사용할 수 없습니다.")
                if old.registration_sha256 != registration_sha256:
                    raise HTTPException(409, "같은 표준 등록 ID에 다른 입력을 사용할 수 없습니다.")
                verified_file(folder, old)
                return snapshot_value(old)
            if str(value.id) in {item["id"] for item in [*builtin_presets(), *builtin_presets(legacy=True)]}:
                raise HTTPException(409, "원문 별첨의 고정 ID는 새 표준 등록에 사용할 수 없습니다.")
            settings = value.settings.model_dump(mode="json")
            if report_hash(settings) != value.settings_sha256:
                raise HTTPException(409, "설정이 변경되었습니다. 현재 예시를 다시 확인하세요.")
            standard = value.base_standard.model_dump(mode="json")
            matches_base(standard)
            data = render_preview(seed, settings)
            if digest(data) != value.preview_sha256:
                raise HTTPException(409, "확인한 예시 파일과 등록할 예시가 다릅니다. 현재 예시를 다시 확인하세요.")
            matches_base(standard)
            snapshot = VisualSelection.model_validate({"id": str(value.id), "revision": settings_revision(value.settings), "name": value.name,
                "settings": settings, "settings_sha256": value.settings_sha256,
                "base_standard": standard, "preview_sha256": value.preview_sha256}).model_dump(mode="json")
            key = "visual-" + str(uuid.uuid4()) + ".hwpx"
            write_immutable(folder, key, data)
            row = VisualStandard(id=str(value.id), owner_id=request.state.user, snapshot=canonical(snapshot),
                registration_sha256=registration_sha256, storage_key=key, size=len(data), created_at=time.time())
            db.add(row)
            db.flush()
            result = snapshot_value(row)
        return result

    def owned(db, identifier, user):
        row = db.get(VisualStandard, identifier)
        if not row or row.owner_id != user:
            raise HTTPException(404, "등록한 문서 표준을 찾을 수 없습니다.")
        return row

    @app.get("/api/visual-standards/{identifier}")
    def detail(identifier: str, request: Request):
        with Session(engine) as db:
            row = owned(db, identifier, request.state.user)
            verified_file(folder, row)
            return snapshot_value(row)

    @app.get("/api/visual-standards/{identifier}/file")
    def file(identifier: str, request: Request):
        with Session(engine) as db:
            row = owned(db, identifier, request.state.user)
            snapshot = snapshot_value(row)
            data = verified_file(folder, row)
        return Response(data, media_type="application/hwp+zip", headers={
            "ETag": '"' + snapshot["preview_sha256"] + '"', "Cache-Control": "no-store",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(snapshot["name"] + ".hwpx", safe=""),
        })
