"""Exact binary splice against an immutable base; XML is never regenerated."""
import base64
import binascii
import io
import zipfile
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .hwpx import digest, validate, MAX_FILE


class Delta(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prefix: int = Field(ge=0, le=MAX_FILE)
    remove: int = Field(ge=0, le=MAX_FILE)
    removed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inserted_base64: str = Field(max_length=MAX_FILE * 4 // 3 + 4)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def apply_delta(before: bytes, raw: bytes):
    try:
        patch = Delta.model_validate_json(raw)
        inserted = base64.b64decode(patch.inserted_base64, validate=True)
    except (ValidationError, ValueError, binascii.Error) as exc:
        raise HTTPException(422, "변경분 형식이 올바르지 않습니다.") from exc
    end = patch.prefix + patch.remove
    if (digest(before) != patch.base_sha256 or end > len(before) or
            digest(before[patch.prefix:end]) != patch.removed_sha256):
        raise HTTPException(409, "변경분의 기준 파일 또는 원문 해시가 다릅니다.")
    if len(before) - patch.remove + len(inserted) > MAX_FILE:
        raise HTTPException(413, "저장 결과가 30MB 한도를 초과합니다.")
    after = before[:patch.prefix] + inserted + before[end:]
    if digest(after) != patch.result_sha256:
        raise HTTPException(422, "변경분 결과 해시 검증 실패")
    validate(after)
    return after


def describe_changes(before, after):
    def entries(data):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return {name: digest(archive.read(name)) for name in archive.namelist()}
    old, new = entries(before), entries(after)
    return [{"path": name, "before_sha256": old.get(name), "after_sha256": new.get(name)}
            for name in sorted(old.keys() | new.keys()) if old.get(name) != new.get(name)]
