"""An ACL-scoped catalog over immutable saved HWPX template versions."""
import json
import re
from collections import Counter
from typing import Literal
from urllib.parse import quote

from fastapi import HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_access
from .db import Access, Template, TemplateCatalog, TemplateFavorite, Version, transaction
from .hwpx import digest, validate

Category = Literal["보고서", "회의록", "계획서", "점검표", "기타"]
CATEGORIES = ("보고서", "회의록", "계획서", "점검표", "기타")


def inferred_category(row):
    text = (row.name + " " + " ".join(json.loads(row.tags))).casefold()
    for category, terms in (
        ("회의록", ("회의", "minutes")),
        ("점검표", ("점검", "체크리스트", "checklist")),
        ("계획서", ("계획", "plan")),
        ("보고서", ("보고", "report")),
    ):
        if any(term in text for term in terms):
            return category
    return "기타"


def category_value(db, row):
    catalog = db.get(TemplateCatalog, row.id)
    return (catalog.category, catalog.revision) if catalog else (inferred_category(row), 0)


def registration_hash(value):
    frozen = value.model_dump(mode="json", exclude={"id"})
    try:
        encoded = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    except UnicodeError as exc:
        raise HTTPException(422, "템플릿 정보에 유효한 UTF-8 문자를 사용하세요.") from exc
    return digest(encoded)


def source_version(db, row):
    version = db.get(Version, row.version_id)
    if not version or version.document_id != row.document_id:
        raise HTTPException(404, "템플릿 기준 버전을 찾을 수 없습니다.")
    return version


def verified_bytes(folder, version):
    # Keys originate in the server's immutable storage; reject damaged path metadata too.
    target = (folder / version.storage_key).resolve()
    if target.parent != folder.resolve():
        raise HTTPException(422, "템플릿 저장 정보가 올바르지 않습니다.")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise HTTPException(503, "템플릿 저장 파일을 읽지 못했습니다.") from exc
    if len(data) != version.size or digest(data) != version.sha256:
        raise HTTPException(422, "템플릿 파일 크기 또는 해시 검증 실패")
    validate(data)
    return data


def display_source_name(doc):
    return re.split(r"[/\\]", doc.name)[-1]


def item_value(db, row, user, doc=None):
    doc = doc or require_access(db, row.document_id, user)
    version = source_version(db, row)
    category, revision = category_value(db, row)
    return {
        **{key: getattr(row, key) for key in ("id", "name", "description", "document_id", "version_id", "created_at")},
        "tags": json.loads(row.tags), "category": category, "revision": revision,
        "favorite": db.get(TemplateFavorite, (row.id, user)) is not None,
        "can_manage": db.get(Access, (row.document_id, user)).role in ("owner", "editor"),
        "sha256": version.sha256, "size": version.size, "source_name": display_source_name(doc),
    }


class MarketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def valid_encoding(cls, value):
        try:
            json.dumps(value, ensure_ascii=False).encode("utf-8")
        except (UnicodeError, TypeError) as exc:
            raise HTTPException(422, "템플릿 정보에 유효한 UTF-8 문자를 사용하세요.") from exc
        return value


class EditTemplate(MarketInput):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    category: Category
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def visible_metadata(self):
        values = [self.name, self.description, *self.tags]
        if not self.name.strip() or any(not tag.strip() or len(tag) > 40 for tag in self.tags):
            raise ValueError("이름과 태그에는 표시할 내용을 입력하세요. 태그는 1~40자입니다.")
        if any(any(ord(char) < 32 and char not in "\n\t" for char in value) for value in values):
            raise ValueError("메타데이터에 제어 문자를 사용할 수 없습니다.")
        try:
            json.dumps(values, ensure_ascii=False).encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("유효한 UTF-8 문자를 사용하세요.") from exc
        return self


class FavoriteTemplate(MarketInput):
    favorite: bool


def install_template_market(app, locked):
    engine, folder = app.state.engine, app.state.folder

    def accessible(db, template_id, user, role="reader", lock=False):
        row = db.get(Template, template_id)
        if not row:
            raise HTTPException(404, "템플릿을 찾을 수 없습니다.")
        doc = require_access(db, row.document_id, user, role, lock=lock, fresh=lock)
        if lock:
            row = db.get(Template, template_id, populate_existing=True)
        source_version(db, row)
        return row, doc

    @app.get("/api/template-market")
    def catalog(request: Request, q: str = Query("", max_length=200), category: Category | None = None,
                favorite: bool = False, sort: Literal["newest", "name", "relevance"] = "newest",
                offset: int = Query(0, ge=0), limit: int = Query(24, ge=1, le=100)):
        with Session(engine) as db:
            rows = db.scalars(select(Template).join(Access, Access.document_id == Template.document_id)
                              .where(Access.user_id == request.state.user))
            results = []
            for row in rows:
                version = db.get(Version, row.version_id)
                if not version or version.document_id != row.document_id:
                    continue
                item = item_value(db, row, request.state.user)
                if favorite and not item["favorite"]:
                    continue
                haystack = " ".join([row.name, row.description, *item["tags"]]).casefold()
                matched = [term for term in q.casefold().split() if term in haystack]
                if q.strip() and not matched:
                    continue
                results.append((item, len(matched)))
            counts = Counter(item["category"] for item, _ in results)
            if category:
                results = [(item, score) for item, score in results if item["category"] == category]
            if sort == "name":
                results.sort(key=lambda pair: (pair[0]["name"].casefold(), pair[0]["id"]))
            elif sort == "relevance":
                results.sort(key=lambda pair: (-pair[1], -pair[0]["created_at"], pair[0]["id"]))
            else:
                results.sort(key=lambda pair: (-pair[0]["created_at"], pair[0]["id"]))
            return {"items": [item for item, _ in results[offset:offset + limit]], "total": len(results),
                    "categories": [{"name": name, "count": counts[name]} for name in CATEGORIES]}

    @app.get("/api/template-market/{template_id}")
    def detail(template_id: str, request: Request):
        with Session(engine) as db:
            row, doc = accessible(db, template_id, request.state.user)
            return item_value(db, row, request.state.user, doc)

    @app.get("/api/template-market/{template_id}/file")
    def preview_file(template_id: str, request: Request):
        with transaction(engine) as db:
            row, doc = accessible(db, template_id, request.state.user, lock=True)
            version = source_version(db, row)
            data = verified_bytes(folder, version)
            name = display_source_name(doc)
            return Response(data, media_type="application/hwp+zip", headers={
                "ETag": f'"{version.sha256}"', "Cache-Control": "no-store",
                "Content-Disposition": "inline; filename*=UTF-8''" + quote(name, safe=""),
            })

    @app.put("/api/template-market/{template_id}")
    def edit(template_id: str, value: EditTemplate, request: Request):
        with transaction(engine) as db:
            row, doc = accessible(db, template_id, request.state.user, "editor", lock=True)
            catalog = db.get(TemplateCatalog, row.id, populate_existing=True)
            revision = catalog.revision if catalog else 0
            if revision != value.expected_revision:
                raise HTTPException(409, "다른 사용자가 템플릿 정보를 변경했습니다. 다시 불러오세요.")
            if catalog:
                catalog.category, catalog.revision = value.category, revision + 1
            else:
                db.add(TemplateCatalog(template_id=row.id, category=value.category, revision=revision + 1))
            row.name, row.description = value.name, value.description
            row.tags = json.dumps(value.tags, ensure_ascii=False)
            db.flush()
            result = item_value(db, row, request.state.user, doc)
        return result

    @app.put("/api/template-market/{template_id}/favorite")
    def favorite_template(template_id: str, value: FavoriteTemplate, request: Request):
        with transaction(engine) as db:
            row, doc = accessible(db, template_id, request.state.user, lock=True)
            favorite = db.get(TemplateFavorite, (row.id, request.state.user))
            if value.favorite and not favorite:
                db.add(TemplateFavorite(template_id=row.id, user_id=request.state.user))
            elif not value.favorite and favorite:
                db.delete(favorite)
            db.flush()
            result = item_value(db, row, request.state.user, doc)
        return result
