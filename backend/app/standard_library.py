"""Mutable catalog state around immutable standards, and reusable style fragments."""
import json
import time
import uuid
from typing import Literal
from fastapi import HTTPException, Request
from pydantic import Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .db import VisualStandard, VisualStandardState, DesignFragment, transaction
from .block_contract import BlockInput
from .reference_standard import VisualSettingsV3
from .report_contract import canonical, paragraph
from .hwpx import digest


def active_standard(db, identifier, user):
    row = db.scalar(select(VisualStandard).where(VisualStandard.id == identifier).with_for_update())
    if not row or row.owner_id != user:
        raise HTTPException(404, "사용자 표준을 찾을 수 없습니다. 기본 별첨은 변경할 수 없습니다.")
    state = db.get(VisualStandardState, identifier)
    if state and (state.hidden or state.replacement_id):
        raise HTTPException(409, "표준이 이미 변경되거나 삭제되었습니다. 목록을 새로 확인하세요.")
    return state


def fragment_settings(settings, kind):
    prefixes = ("table_", "caption_", "unit_") if kind == "table" else ("title_", "cover_")
    # Composition belongs to standards, not title designs.
    return {key: value for key, value in settings.items() if key.startswith(prefixes) and key != "cover_mode"}


class SaveFragment(BlockInput):
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["table", "title"]
    settings: VisualSettingsV3
    _name = field_validator("name")(paragraph)


def install_standard_library(app):
    engine = app.state.engine

    @app.delete("/api/visual-standards/{identifier}")
    def archive(identifier: uuid.UUID, request: Request):
        with transaction(engine) as db:
            row = db.get(VisualStandard, str(identifier))
            if not row or row.owner_id != request.state.user:
                raise HTTPException(404, "사용자 표준을 찾을 수 없습니다.")
            state = db.get(VisualStandardState, str(identifier))
            if state and state.hidden:
                return {"hidden": True}
            state = active_standard(db, str(identifier), request.state.user)
            if state is None:
                state = VisualStandardState(standard_id=str(identifier)); db.add(state)
            state.hidden = True
        return {"hidden": True}

    @app.get("/api/design-fragments")
    def fragments(request: Request):
        with Session(engine) as db:
            return {"items": [dict(id=row.id, name=row.name, kind=row.kind, settings=json.loads(row.settings))
                for row in db.scalars(select(DesignFragment).where(DesignFragment.owner_id == request.state.user,
                    DesignFragment.hidden == False).order_by(DesignFragment.created_at.desc()))]}

    @app.post("/api/design-fragments")
    def save(value: SaveFragment, request: Request):
        settings = canonical(fragment_settings(value.settings.model_dump(mode="json"), value.kind))
        with transaction(engine) as db:
            if engine.dialect.name == "postgresql":
                # The UUID is also the retry key. Lock absent rows as well as
                # existing ones so concurrent submissions share one receipt.
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                           {"key": int(digest(("design-fragment:" + str(value.id)).encode())[:15], 16)})
            row = db.get(DesignFragment, str(value.id))
            if row:
                if row.owner_id != request.state.user:
                    raise HTTPException(404, "디자인을 찾을 수 없습니다.")
                if (row.name, row.kind, row.settings) != (value.name, value.kind, settings):
                    raise HTTPException(409, "같은 저장 ID에 다른 디자인을 사용할 수 없습니다.")
            else:
                row = DesignFragment(id=str(value.id), owner_id=request.state.user, name=value.name,
                    kind=value.kind, settings=settings, created_at=time.time())
                db.add(row); db.flush()
            result = dict(id=row.id, name=row.name, kind=row.kind, settings=json.loads(row.settings))
        return result

    @app.delete("/api/design-fragments/{identifier}")
    def remove(identifier: uuid.UUID, request: Request):
        with transaction(engine) as db:
            row = db.get(DesignFragment, str(identifier))
            if not row or row.owner_id != request.state.user:
                raise HTTPException(404, "디자인을 찾을 수 없습니다.")
            row.hidden = True
        return {"hidden": True}
