"""Private document work copies and frozen, explicitly reviewed integration requests."""
import io
import time
import uuid
import zipfile
from xml.etree import ElementTree as ET

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Float, ForeignKey, String, select, text
from sqlalchemy.orm import Mapped, mapped_column
from .auth import require_access, bind_lease
from .changes import describe_changes
from .coedit_merge import merge_hwpx
from .db import Access, Base, Document, Version, document_is_trashed, transaction
from .document_copy import CopyDocument
from .hwpx import digest, loss_warnings
from .library_access import document_role
from .rewrite_jobs import invalidate_running
from .version_compare import author_dict

MAX_BYTES = 10 * 1024 * 1024


class DocumentBranch(Base):
    __tablename__ = "document_branches"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    base_version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    owner_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(240))
    created_at: Mapped[float] = mapped_column(Float)
    request_id: Mapped[str] = mapped_column(String(36))


class IntegrationRequest(Base):
    __tablename__ = "integration_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    branch_document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    target_version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    author_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[float] = mapped_column(Float)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    merged_version_id: Mapped[str | None] = mapped_column(ForeignKey("versions.id"), nullable=True)
    merged_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Submit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID
    source_version: uuid.UUID
    target_version: uuid.UUID


class Accept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    merged_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _stable(data):
    """Generated ZIP headers must not make a preview hash depend on wall-clock time."""
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
        for item in source.infolist():
            item.date_time = (1980, 1, 1, 0, 0, 0)
            target.writestr(item, source.read(item))
    return output.getvalue()


def _text(data):
    def own_text(paragraph):
        # A table is nested inside its host paragraph in HWPX. Cell paragraphs
        # are visited separately below; including them in the host duplicates
        # the same cell content in the textual review.
        def descend(node):
            for child in node:
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == "p":
                    continue
                if tag == "t":
                    yield child.text or ""
                yield from descend(child)
        return "".join(descend(paragraph))

    paragraphs = []
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        for name in sorted(source.namelist()):
            if name.startswith("Contents/section") and name.endswith(".xml"):
                root = ET.fromstring(source.read(name))
                for node in root.iter():
                    if node.tag.rsplit("}", 1)[-1] == "p":
                        paragraphs.append(own_text(node))
    result = "\n".join(paragraphs)
    return result[:60000], len(result) > 60000


def install_document_branches(app, publish, doc_dict):
    engine, folder = app.state.engine, app.state.folder

    def read(db, version_id, document_id):
        version = db.get(Version, version_id)
        if not version or version.document_id != document_id:
            raise HTTPException(404, "해당 문서의 저장 버전을 찾을 수 없습니다.")
        if version.size > MAX_BYTES:
            raise HTTPException(413, "작업본 통합은 10MB 이하 문서를 지원합니다.")
        try:
            data = (folder / version.storage_key).read_bytes()
        except OSError:
            raise HTTPException(503, "저장 파일을 읽을 수 없습니다.") from None
        if len(data) != version.size or digest(data) != version.sha256:
            raise HTTPException(422, "저장본의 크기 또는 해시가 다릅니다.")
        return data

    def request_dict(db, item, user):
        result = {name: getattr(item, name) for name in (
            "id", "branch_document_id", "source_document_id", "source_version_id", "target_version_id",
            "author_id", "status", "created_at", "reviewed_by", "reviewed_at", "merged_version_id")}
        result["can_review"] = item.author_id != user and document_role(db, item.source_document_id, user) == "owner"
        return result

    def branch_dict(db, item, user):
        doc = db.get(Document, item.document_id)
        result = {name: getattr(item, name) for name in (
            "document_id", "source_document_id", "base_version_id", "owner_id", "name", "created_at")}
        result["can_edit"] = item.owner_id == user
        # A reviewer sees provenance, not unsubmitted draft state.
        result["current_version"] = doc.current_version if item.owner_id == user else None
        return result

    def get_request(db, identity, user, *, approve=False):
        item = db.get(IntegrationRequest, identity)
        if not item:
            raise HTTPException(404, "통합 요청을 찾을 수 없습니다.")
        target = require_access(db, item.source_document_id, user, lock=True, fresh=True)
        # Another approval may have completed while this transaction waited for
        # the document lock. Never act on the pre-lock ORM snapshot.
        item = db.get(IntegrationRequest, identity, populate_existing=True)
        reviewer = document_role(db, target.id, user, fresh=True) == "owner" and item.author_id != user
        if approve and not reviewer:
            raise HTTPException(403, "작성자와 다른 문서 관리자가 검토 후 통합할 수 있습니다.")
        if not reviewer and item.author_id != user:
            raise HTTPException(404, "통합 요청을 찾을 수 없습니다.")
        # Revoked authors must not be able to publish through a review made earlier.
        try:
            require_access(db, item.source_document_id, item.author_id, fresh=True)
        except HTTPException as exc:
            if exc.status_code in (401, 403, 404):
                # This is the author's permission, not the reviewer's login.
                # Returning 401 would clear the current reviewer's valid cookie.
                raise HTTPException(409, "작성자의 접근 권한이 변경되어 통합할 수 없습니다.") from None
            raise
        return item, target

    def candidate(db, item, target):
        if target.current_version != item.target_version_id:
            raise HTTPException(409, "공통 문서가 변경됐습니다. 최신 버전을 기준으로 새 통합 요청을 만들어 주세요.")
        branch = db.get(DocumentBranch, item.branch_document_id)
        before = read(db, item.target_version_id, target.id)
        original = read(db, branch.base_version_id, target.id)
        incoming = read(db, item.source_version_id, branch.document_id)
        if loss_warnings(original, incoming):
            raise HTTPException(422, "작업본의 개체 손실이 감지되어 통합을 중단했습니다.")
        merged = merge_hwpx(original, before, incoming)
        if merged != before:
            merged = _stable(merged)
        if loss_warnings(before, merged):
            raise HTTPException(422, "통합 결과의 개체 손실이 감지됐습니다.")
        return before, merged

    @app.post("/api/documents/{source_id}/branches")
    def create(source_id: str, value: CopyDocument, request: Request):
        destination = str(value.document_id)
        with transaction(engine) as db:
            source = require_access(db, source_id, request.state.user, lock=True)
            if db.get(DocumentBranch, source.id):
                raise HTTPException(409, "공통 문서에서 새 작업본을 만들어 주세요.")
            data = read(db, str(value.source_version), source.id)
            if engine.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(digest(destination.encode())[:15], 16)})
            old = db.get(DocumentBranch, destination)
            if old:
                if (old.source_document_id, old.base_version_id, old.owner_id, old.name, old.request_id) != (source.id, str(value.source_version), request.state.user, value.name, str(value.request_id)):
                    raise HTTPException(409, "같은 작업본 ID에 다른 생성 요청을 사용할 수 없습니다.")
                doc = require_access(db, destination, request.state.user, "owner")
                return {"document": {**doc_dict(doc), "role": "owner"}, "branch": branch_dict(db, old, request.state.user)}
            if db.get(Document, destination):
                raise HTTPException(409, "이미 사용된 문서 ID입니다.")
            now = time.time()
            doc = Document(id=destination, name=value.name, created_at=now, lease_until=0)
            db.add(doc); db.flush()
            db.add(Access(document_id=doc.id, user_id=request.state.user, role="owner"))
            branch = DocumentBranch(document_id=doc.id, source_document_id=source.id, base_version_id=str(value.source_version),
                owner_id=request.state.user, name=value.name, created_at=now, request_id=str(value.request_id))
            db.add(branch)
            publish(db, doc, data, None, None, "branch:" + str(value.request_id), "upload", author_id=request.state.user)
            return {"document": {**doc_dict(doc), "role": "owner"}, "branch": branch_dict(db, branch, request.state.user)}

    @app.get("/api/documents/{doc_id}/branches")
    def flow(doc_id: str, request: Request):
        with transaction(engine) as db:
            require_access(db, doc_id, request.state.user)
            own = db.get(DocumentBranch, doc_id)
            source = require_access(db, own.source_document_id if own else doc_id, request.state.user)
            role = document_role(db, source.id, request.state.user)
            branches = list(db.scalars(select(DocumentBranch).where(DocumentBranch.source_document_id == source.id,
                ~document_is_trashed(DocumentBranch.document_id)).order_by(DocumentBranch.created_at)))
            requests = list(db.scalars(select(IntegrationRequest).where(IntegrationRequest.source_document_id == source.id).order_by(IntegrationRequest.created_at)))
            requests = [item for item in requests if role == "owner" or item.author_id == request.state.user]
            submitted = {item.branch_document_id for item in requests}
            branches = [item for item in branches if item.owner_id == request.state.user or (role == "owner" and item.document_id in submitted)]
            versions = list(db.scalars(select(Version).where(Version.document_id == source.id).order_by(Version.created_at.desc()).limit(100)))
            return {"source_document": {**doc_dict(source), "role": role},
                "branches": [branch_dict(db, item, request.state.user) for item in branches],
                "requests": [request_dict(db, item, request.state.user) for item in requests],
                "versions": [{"id": item.id, "base_version": item.base_version, "created_at": item.created_at, "author": author_dict(item)} for item in reversed(versions)]}

    @app.post("/api/documents/{branch_id}/integration-requests")
    def submit(branch_id: str, value: Submit, request: Request):
        with transaction(engine) as db:
            branch = db.get(DocumentBranch, branch_id)
            if not branch or branch.owner_id != request.state.user:
                raise HTTPException(404, "내 작업본을 찾을 수 없습니다.")
            target = require_access(db, branch.source_document_id, request.state.user, lock=True, fresh=True)
            doc = require_access(db, branch_id, request.state.user, "owner", lock=True, fresh=True)
            old = db.get(IntegrationRequest, str(value.request_id))
            if old:
                if (old.branch_document_id, old.author_id, old.source_version_id, old.target_version_id) != (branch_id, request.state.user, str(value.source_version), str(value.target_version)):
                    raise HTTPException(409, "같은 요청 ID에 다른 통합 요청을 사용할 수 없습니다.")
                return request_dict(db, old, request.state.user)
            if doc.current_version != str(value.source_version) or target.current_version != str(value.target_version):
                raise HTTPException(409, "문서가 변경됐습니다. 저장 상태를 새로 확인해 주세요.")
            item = IntegrationRequest(id=str(value.request_id), branch_document_id=branch_id, source_document_id=target.id,
                source_version_id=doc.current_version, target_version_id=target.current_version, author_id=request.state.user,
                status="pending", created_at=time.time())
            before, merged = candidate(db, item, target)
            if merged == before:
                raise HTTPException(409, "공통 문서에 반영할 변경이 없습니다.")
            db.add(item); db.flush()
            return request_dict(db, item, request.state.user)

    @app.get("/api/integration-requests/{identity}/review")
    def review(identity: str, request: Request):
        with transaction(engine) as db:
            item, target = get_request(db, identity, request.state.user)
            if item.status != "pending":
                raise HTTPException(409, "이미 처리된 통합 요청입니다.")
            before, merged = candidate(db, item, target)
            before_text, old_truncated = _text(before); after_text, new_truncated = _text(merged)
            return {"request": request_dict(db, item, request.state.user), "changes": describe_changes(before, merged),
                "before_text": before_text, "after_text": after_text, "truncated": old_truncated or new_truncated,
                "merged_sha256": digest(merged)}

    @app.get("/api/integration-requests/{identity}/preview")
    def preview(identity: str, request: Request):
        with transaction(engine) as db:
            item, target = get_request(db, identity, request.state.user)
            if item.status != "pending":
                raise HTTPException(409, "이미 처리된 통합 요청입니다.")
            _, merged = candidate(db, item, target)
            return Response(merged, media_type="application/hwp+zip", headers={"Cache-Control": "no-store"})

    @app.post("/api/integration-requests/{identity}/accept")
    def accept(identity: str, value: Accept, request: Request):
        with transaction(engine) as db:
            item, target = get_request(db, identity, request.state.user, approve=True)
            if item.status == "merged":
                if item.reviewed_by != request.state.user or item.merged_sha256 != value.merged_sha256:
                    raise HTTPException(409, "기존 통합 승인과 다른 요청입니다.")
                return request_dict(db, item, request.state.user)
            if item.status != "pending":
                raise HTTPException(409, "이미 반려된 통합 요청입니다.")
            if target.lease_token and target.lease_until > time.time():
                raise HTTPException(409, "공통 문서를 편집 중입니다. 편집자가 저장하고 닫은 뒤 통합하세요.")
            _, merged = candidate(db, item, target)
            if digest(merged) != value.merged_sha256:
                raise HTTPException(409, "검토한 통합 결과와 다릅니다. 다시 비교해 주세요.")
            target.lease_token = "integration:" + item.id; target.lease_until = time.time() + 120
            bind_lease(db, target, request.state.user)
            invalidate_running(db, target.id, "작업본 통합으로 이전 편집 후보가 만료됐습니다.")
            version = publish(db, target, merged, item.target_version_id, target.lease_token,
                "integration:" + item.id, "web-edit", author_id=request.state.user)
            target.lease_token = None; target.lease_until = 0
            item.status = "merged"; item.reviewed_by = request.state.user; item.reviewed_at = time.time()
            item.merged_version_id = version.id; item.merged_sha256 = value.merged_sha256
            return request_dict(db, item, request.state.user)

    @app.post("/api/integration-requests/{identity}/reject")
    def reject(identity: str, request: Request):
        with transaction(engine) as db:
            item, _ = get_request(db, identity, request.state.user, approve=True)
            if item.status == "merged":
                raise HTTPException(409, "이미 통합된 요청입니다.")
            if item.status == "rejected" and item.reviewed_by != request.state.user:
                raise HTTPException(409, "이미 다른 검토자가 처리한 요청입니다.")
            if item.status == "rejected":
                return request_dict(db, item, request.state.user)
            item.status = "rejected"; item.reviewed_by = request.state.user; item.reviewed_at = time.time()
            return request_dict(db, item, request.state.user)
