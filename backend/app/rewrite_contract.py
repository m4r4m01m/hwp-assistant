"""Untrusted candidate text only. Task identity and approval never come from a model."""
import json
import re
import unicodedata
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROMPT_CONTRACT = "rewrite-paragraph-pair-v1"


class CandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    # The pinned rhwp 0.8.6 exact-command contract accepts <=4000 code points.
    replacement: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("replacement", "reason")
    @classmethod
    def visible_text(cls, value):
        # Do not silently trim or repair model output before binding it to approval.
        if not value.strip() or any(unicodedata.category(c) in ("Cs", "Cc") and c not in "\n\t\r" for c in value):
            raise ValueError("후보와 이유에는 유효한 텍스트가 필요합니다.")
        if not any(not c.isspace() and unicodedata.category(c) != "Cf" for c in value):
            raise ValueError("보이지 않는 문자만 있는 후보는 허용하지 않습니다.")
        return value

    @field_validator("replacement")
    @classmethod
    def engine_paragraph(cls, value):
        if re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("단일 문단 후보에는 줄바꿈·탭·제어 문자를 넣을 수 없습니다.")
        return value


class CandidatePair(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidates: list[CandidateInput] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def distinct(self):
        def normalized(value):
            visible = "".join(c for c in unicodedata.normalize("NFC", value) if unicodedata.category(c) != "Cf")
            return re.sub(r"\s+", " ", visible).strip()
        if normalized(self.candidates[0].replacement) == normalized(self.candidates[1].replacement):
            raise ValueError("서로 다른 후보 두 개가 필요합니다.")
        return self


def parse_candidate_pair(content):
    if not isinstance(content, str) or len(content) > 65000:
        raise ValueError("invalid response size")

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("non-finite JSON value")

    return CandidatePair.model_validate(json.loads(content, object_pairs_hook=unique_fields,
                                                   parse_constant=invalid_constant))
