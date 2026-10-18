"""Verified report writing profiles; every profile retains the pinned HWPX standard."""
import json
from pathlib import Path
from fastapi import HTTPException
from pydantic import Field, field_validator
from .document_standard import load_standard
from .report_contract import ReportValue, paragraph, report_hash

ASSET = Path(__file__).parent / "assets" / "report-templates" / "profiles.json"


class ProfileValues(ReportValue):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=180)
    writing_criteria: list[str] = Field(min_length=1, max_length=6)

    _plain = field_validator("name", "description")(paragraph)

    @field_validator("writing_criteria")
    @classmethod
    def bounded_criteria(cls, values):
        for value in values:
            paragraph(value)
            if len(value) > 300:
                raise ValueError("작성 기준은 항목당 300자 이내여야 합니다.")
        return values


class ProfileDefinition(ReportValue):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    revision: int = Field(ge=1, le=1000000)
    values: ProfileValues


class ReportTemplate(ProfileDefinition):
    standard_id: str = Field(min_length=1, max_length=100)
    reference_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    standard_profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def catalog():
    standard, _ = load_standard()
    try:
        raw = json.loads(ASSET.read_text(encoding="utf-8"))
        if set(raw) != {"profiles"} or not 1 <= len(raw["profiles"]) <= 10:
            raise ValueError("Invalid report profile catalog")
        definitions = [ProfileDefinition.model_validate(item).model_dump() for item in raw["profiles"]]
        if len({item["id"] for item in definitions}) != len(definitions):
            raise ValueError("Duplicate report profile ID")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(503, "보고서 템플릿 기준을 확인할 수 없습니다. 기존 입력은 보존되어 있습니다.") from exc
    items = []
    for definition in definitions:
        frozen = {**definition, "standard_id": standard["id"], "reference_sha256": standard["reference_sha256"],
                  "standard_profile_sha256": standard["profile_sha256"]}
        items.append({**frozen, "profile_sha256": report_hash(frozen)})
    return {"items": items, "default_id": items[0]["id"], "format": "pinned-document-standard"}


def validate_selection(value: ReportTemplate):
    supplied = value.model_dump()
    expected = next((item for item in catalog()["items"] if item["id"] == value.id), None)
    if supplied != expected:
        raise HTTPException(409, "보고서 템플릿 기준이 바뀌었습니다. 새 요청 전에 템플릿 목록을 다시 확인하세요.")
    return supplied
