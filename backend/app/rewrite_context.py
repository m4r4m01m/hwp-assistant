"""Explicitly confirmed writing context and immutable request-time model input."""
import json
import time
import uuid
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .db import Context, Version
from .hwpx import digest

CONTEXT_SCHEMA = "rewrite-context-v1"
CONTEXT_PROMPT_CONTRACT = "rewrite-paragraph-pair-context-v1"


class ConfirmedFactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    text: str = Field(min_length=1, max_length=2000)
    source: Literal["user_statement", "document_version", "user_confirmed_ai_excerpt"]
    source_note: str = Field(default="", max_length=500)
    version_id: str | None = Field(default=None, max_length=36)

    @field_validator("text")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("확인 사실에는 실제 문장을 입력하세요.")
        return value


def confirmed_content(db, doc, user, value):
    """Called with the document lock. Confirmation identity comes only from auth."""
    facts = [fact.model_dump(mode="json") for fact in value.confirmed_facts]
    if len({f["id"] for f in facts}) != len(facts):
        raise HTTPException(422, "확인 사실 ID가 중복됩니다.")
    for fact in facts:
        if fact["source"] == "document_version":
            version = db.get(Version, fact["version_id"]) if fact["version_id"] else None
            if not version or version.document_id != doc.id:
                raise HTTPException(422, "사실의 출처 버전은 이 문서의 저장 버전이어야 합니다.")
        elif fact["version_id"] is not None:
            raise HTTPException(422, "문서 버전 출처에만 버전 ID를 지정할 수 있습니다.")
    confirmation = None
    if value.confirm_for_rewrite:
        if not (value.purpose.strip() or facts or value.writing_criteria.strip()):
            raise HTTPException(422, "작성 목적, 확인 사실 또는 작성 기준을 입력하세요.")
        confirmation = {"id": str(uuid.uuid4()), "by": user, "at": time.time()}
    return {"schema": CONTEXT_SCHEMA, "purpose": value.purpose, "facts": value.facts,
            "selection": value.selection, "writing_criteria": value.writing_criteria,
            "confirmed_facts": facts, "confirmation": confirmation}


def freeze_context(db, thread, doc, revision):
    if revision is None:
        return None
    row = db.get(Context, thread.id, populate_existing=True)
    if not row or row.revision != revision:
        raise HTTPException(409, "작성 정보가 변경되었습니다. 최신 내용을 확인하고 다시 요청하세요.")
    if row.base_version != doc.current_version:
        raise HTTPException(409, "작성 정보의 문서 기준이 오래되었습니다. 내용을 다시 확인해 저장하세요.")
    content = json.loads(row.content)
    if content.get("schema") != CONTEXT_SCHEMA or not content.get("confirmation"):
        raise HTTPException(409, "후보 생성에 사용할 작성 정보를 먼저 확인하고 저장하세요.")
    # Never include legacy free-form notes, selections, general messages or AI answers.
    return {"schema": CONTEXT_SCHEMA, "id": thread.id, "revision": row.revision,
            "document_id": doc.id, "base_version": row.base_version,
            **{key: content[key] for key in ("purpose", "writing_criteria", "confirmed_facts", "confirmation")}}


def frozen_context(task):
    return json.loads(task.execution).get("_context_snapshot")


def context_stale(db, task):
    snapshot = frozen_context(task)
    if snapshot is None:
        return False
    row = db.get(Context, task.conversation_id, populate_existing=True)
    return not row or row.revision != snapshot["revision"] or row.base_version != snapshot["base_version"]


def generation_input(original, instruction, context=None):
    value = {"selected_paragraph": original, "instruction": instruction}
    if context is not None:
        value["writing_context"] = context
    return value


def input_json(value):
    # The exact UTF-8 data string sent to the provider is also the hashed input.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def input_hash(value):
    return digest(input_json(value).encode("utf-8"))
