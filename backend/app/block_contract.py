"""Small manual-input blocks; no model-generated content or document publication."""
import base64
import binascii
import json
import uuid
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .document_standard import load_standard
from .hwpx import digest

MAX_SOURCE = 10 * 1024 * 1024
MAX_JSON = 30 * 1024 * 1024
MAX_BASE64 = 4 * ((MAX_SOURCE + 2) // 3)


def field(name, label, maximum, required=True):
    return {"name": name, "label": label, "required": required, "max_length": maximum}


DEFINITIONS = (
    {"id": "title-frame", "revision": 1, "name": "파란 상하선 제목", "description": "기본 문서 서식의 제목 상자", "kind": "title",
     "fields": [field("title", "제목", 120)]},
    {"id": "summary", "revision": 1, "name": "요약 문단", "description": "□ 말머리와 15pt 본문·내어쓰기", "kind": "paragraph",
     "fields": [field("label", "말머리", 24, False), field("text", "내용", 240)]},
    {"id": "action-table", "revision": 1, "name": "담당·기한 조치표", "description": "머리글과 조치 두 행을 가진 편집 가능한 표", "kind": "table",
     "fields": [field("caption", "표 제목", 90, False), *[field(name + str(i), label + str(i), 52 if name == "action" else 26)
           for i in (1, 2) for name, label in (("action", "조치"), ("owner", "담당"), ("due", "기한"))]]},
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def catalog():
    profile, _ = load_standard()
    blocks = []
    for definition in DEFINITIONS:
        frozen = {**definition, "standard_reference_sha256": profile["reference_sha256"],
                  "standard_profile_sha256": profile["profile_sha256"], "policy": "manual-standard-block-v1"}
        blocks.append({**definition, "sha256": digest(canonical(frozen).encode())})
    return {"blocks": blocks, "standard_reference_sha256": profile["reference_sha256"],
            "standard_profile_sha256": profile["profile_sha256"]}


class BlockInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def safe_json(cls, value):
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (UnicodeError, TypeError, ValueError) as exc:
            raise HTTPException(422, "블록 입력은 유효한 UTF-8 JSON이어야 합니다.") from exc
        return value


class BlockTarget(BlockInput):
    section: Literal[0]
    paragraph: int = Field(ge=0, le=100000)
    expected_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PreviewBlock(BlockInput):
    request_id: uuid.UUID
    base_version: str = Field(min_length=1, max_length=36)
    block_id: str = Field(min_length=1, max_length=80)
    block_revision: int = Field(ge=1)
    block_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_base64: str = Field(min_length=1, max_length=MAX_BASE64)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target: BlockTarget
    variables: dict[str, str] = Field(max_length=10)


class VerifyBlock(PreviewBlock):
    engine_output_base64: str = Field(min_length=1, max_length=MAX_BASE64)


class CheckBlock(BlockInput):
    base_version: str = Field(min_length=1, max_length=36)


def decoded(value):
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "HWPX 데이터의 Base64가 올바르지 않습니다.") from exc
    if not raw or len(raw) > MAX_SOURCE:
        raise HTTPException(413, "블록 삽입은 10MB 이하의 HWPX만 지원합니다.")
    return raw


def selected_block(value):
    spec = next((block for block in catalog()["blocks"] if block["id"] == value.block_id), None)
    if not spec:
        raise HTTPException(404, "블록을 찾을 수 없습니다.")
    if spec["revision"] != value.block_revision or spec["sha256"] != value.block_sha256:
        raise HTTPException(409, "블록 기준이 변경되었습니다. 새로 선택하세요.")
    declared = {field["name"] for field in spec["fields"]}
    if set(value.variables) - declared:
        raise HTTPException(422, "블록에 선언되지 않은 입력값이 있습니다.")
    for field in spec["fields"]:
        text = value.variables.get(field["name"], "")
        if field["required"] and not text.strip():
            raise HTTPException(422, field["label"] + " 입력이 필요합니다.")
        if len(text) > field["max_length"] or any(ord(char) < 32 or char in "\u2028\u2029" for char in text):
            raise HTTPException(422, field["label"] + " 길이 또는 줄바꿈을 확인하세요.")
    return spec


def block_texts(spec, values):
    if spec["id"] == "title-frame":
        return [{"text": values["title"], "kind": "title"}]
    if spec["id"] == "summary":
        label = values.get("label", "").strip()
        return [{"text": "□ " + ("(" + label + ") " if label else "") + values["text"], "kind": "body", "style_kind": "square"}]
    result = []
    if values.get("caption", "").strip(): result.append({"text": values["caption"], "kind": "source", "caption": True})
    rows = [["조치", "담당", "기한"], *[[values[key + str(i)] for key in ("action", "owner", "due")] for i in (1, 2)]]
    for y, row in enumerate(rows):
        for x, text in enumerate(row):
            result.append({"text": text, "kind": "source", "cell_row": y, "cell_column": x})
    return result


def frozen_hash(value):
    frozen = value.model_dump(mode="json", exclude={"source_base64", "engine_output_base64"})
    return digest(canonical(frozen).encode())
