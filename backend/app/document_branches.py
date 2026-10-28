"""Private document work copies and frozen, explicitly reviewed integration requests."""
import io
import time
import uuid
import zipfile
from xml.etree import ElementTree as ET

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Float, ForeignKey, String, or_, select, text
from sqlalchemy.orm import Mapped, mapped_column
from .auth import require_access, bind_lease
from .changes import describe_changes
from .coedit_merge import merge_hwpx
from .db import Access, Base, Document, Version, VersionAuthor, document_is_trashed, transaction
from .document_copy import CopyDocument
from .hwpx import digest, loss_warnings
from .library_access import document_role
from .rewrite_jobs import invalidate_running
from .version_compare import author_dict, restore_source_dict

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


class BranchParticipant(Base):
    __tablename__ = "branch_participants"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[float] = mapped_column(Float)


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

    def independent_reviewer(db, item, user):
        if item.author_id == user or document_role(db, item.source_document_id, user) != "owner":
            return False
        chain = select(Version.id, Version.base_version).where(Version.id == item.source_version_id).cte(recursive=True)
        chain = chain.union_all(select(Version.id, Version.base_version).join(chain, Version.id == chain.c.base_version))
        # Attribution is frozen in immutable versions: removing an invitation
        # later must not let a contributor approve their own submitted changes.
        return not db.scalar(select(VersionAuthor.version_id).where(VersionAuthor.version_id.in_(select(chain.c.id)),
            VersionAuthor.user_id == user).limit(1))

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
        result["can_review"] = independent_reviewer(db, item, user)
        return result

    def branch_dict(db, item, user):
        doc = db.get(Document, item.document_id)
        result = {name: getattr(item, name) for name in (
            "document_id", "source_document_id", "base_version_id", "owner_id", "name", "created_at")}
        result["can_edit"] = document_role(db, item.document_id, user) in ("owner", "editor")
        result["can_manage_participants"] = item.owner_id == user
        result["can_submit"] = item.owner_id == user
        # A reviewer sees provenance, not unsubmitted draft state.
        result["current_version"] = doc.current_version if result["can_edit"] else None
        return result

    def version_dict(item):
        return {**{name: getattr(item, name) for name in (
            "id", "document_id", "base_version", "size", "sha256", "created_at", "cause")},
            "author": author_dict(item), "restore_source": restore_source_dict(item)}

    def document_dict(db, doc, role):
        from .workspaces import document_workspace_info
        branch = db.get(DocumentBranch, doc.id)
        return {**doc_dict(doc), "role": role, **document_workspace_info(db, doc.id),
            "branch_source_id": branch.source_document_id if branch else None}

    def graph(db, source, branches, requests, user, root_versions, truncated):
        nodes, edges = {}, {}
        def edge(parent, child, kind, document_id):
            if not parent or parent == child:
                return
            if parent not in nodes:
                # An anchor preserves the actual parent identity without leaking
                # private content/author metadata or inventing a path across gaps.
                nodes[parent] = {"id": parent, "document_id": document_id, "kind": "anchor",
                    "can_read": False, "can_edit": False, "can_restore": False, "file_url": None, "current": False}
            edges[(parent, child, kind)] = {"from": parent, "to": child, "kind": kind}

        def node(item, doc, role, file_url=None):
            nodes[item.id] = {**version_dict(item), "kind": "version", "current": item.id == doc.current_version if role else False,
                "can_read": True, "can_edit": role in ("editor", "owner") and item.id == doc.current_version,
                "can_restore": role in ("editor", "owner"),
                "file_url": file_url or f"/documents/{doc.id}/versions/{item.id}/file",
                "document": document_dict(db, doc, role) if role else None}
            if not role:
                nodes[item.id]["restore_source"] = None
            edge(item.base_version, item.id, "save", doc.id)
            restored = restore_source_dict(item)
            if restored:
                edge(restored["version_id"], item.id, "restore", doc.id)

        source_role = document_role(db, source.id, user)
        for version in root_versions:
            node(version, source, source_role)
        for branch in branches:
            role = document_role(db, branch.document_id, user)
            if role not in ("editor", "owner"):
                continue
            doc = db.get(Document, branch.document_id)
            rows = list(db.scalars(select(Version).where(Version.document_id == doc.id)
                .order_by(Version.created_at.desc(), Version.id).limit(51)))
            truncated |= len(rows) > 50
            for version in reversed(rows[:50]):
                node(version, doc, role)
            first = db.scalar(select(Version).where(Version.document_id == doc.id, Version.base_version.is_(None))
                .order_by(Version.created_at, Version.id).limit(1))
            if first:
                if first.id not in nodes:
                    nodes[first.id] = {"id": first.id, "document_id": doc.id, "kind": "anchor", "can_read": False,
                        "can_edit": False, "can_restore": False, "file_url": None, "current": False}
                edge(branch.base_version_id, first.id, "branch", source.id)
        for item in requests:
            branch = db.get(DocumentBranch, item.branch_document_id)
            doc = db.get(Document, item.branch_document_id)
            role = document_role(db, doc.id, user)
            version = db.get(Version, item.source_version_id)
            # Managers may inspect only the exact submitted version, never the
            # branch's unpublished predecessors or current head.
            if version.id not in nodes or nodes[version.id]["kind"] == "anchor":
                node(version, doc, role, None if role else f"/integration-requests/{item.id}/source")
            first = db.scalar(select(Version).where(Version.document_id == doc.id, Version.base_version.is_(None))
                .order_by(Version.created_at, Version.id).limit(1))
            if first:
                if first.id not in nodes:
                    nodes[first.id] = {"id": first.id, "document_id": doc.id, "kind": "anchor", "can_read": False,
                        "can_edit": False, "can_restore": False, "file_url": None, "current": False}
                edge(branch.base_version_id, first.id, "branch", source.id)
            if item.merged_version_id:
                merged = db.get(Version, item.merged_version_id)
                if merged.id not in nodes or nodes[merged.id]["kind"] == "anchor":
                    node(merged, source, source_role)
                edge(version.id, merged.id, "merge", doc.id)
        return {"nodes": list(nodes.values()), "edges": list(edges.values()), "truncated": truncated,
            "limits": {"root_versions": 100, "branches": 50, "branch_versions": 50, "requests": 100}}

    def get_request(db, identity, user, *, approve=False, require_author=True):
        item = db.get(IntegrationRequest, identity)
        if not item:
            raise HTTPException(404, "통합 요청을 찾을 수 없습니다.")
        target = require_access(db, item.source_document_id, user, lock=True, fresh=True)
        # Another approval may have completed while this transaction waited for
        # the document lock. Never act on the pre-lock ORM snapshot.
        item = db.get(IntegrationRequest, identity, populate_existing=True)
        owner = document_role(db, target.id, user, fresh=True) == "owner"
        reviewer = owner and independent_reviewer(db, item, user)
        if approve and not reviewer:
            raise HTTPException(403, "이 작업본 수정에 참여하지 않은 문서 관리자가 검토 후 통합할 수 있습니다.")
        branch_role = document_role(db, item.branch_document_id, user)
        if not owner and item.author_id != user and branch_role not in ("owner", "editor"):
            raise HTTPException(404, "통합 요청을 찾을 수 없습니다.")
        # Revoked authors must not be able to publish through a review made earlier.
        if require_author:
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
            invited = select(BranchParticipant.document_id).where(BranchParticipant.user_id == request.state.user)
            accessible = select(DocumentBranch.document_id).where(DocumentBranch.source_document_id == source.id,
                or_(DocumentBranch.owner_id == request.state.user,
                    DocumentBranch.document_id.in_(invited) if role in ("owner", "editor") else False))
            request_scope = IntegrationRequest.source_document_id == source.id
            if role != "owner":
                request_scope = request_scope & or_(IntegrationRequest.author_id == request.state.user,
                    IntegrationRequest.branch_document_id.in_(accessible))
            requests = list(db.scalars(select(IntegrationRequest).where(request_scope)
                .order_by(IntegrationRequest.created_at.desc(), IntegrationRequest.id).limit(101)))
            truncated = len(requests) > 100
            requests = list(reversed(requests[:100]))
            submitted = {item.branch_document_id for item in requests}
            branches = list(db.scalars(select(DocumentBranch).where(DocumentBranch.source_document_id == source.id,
                ~document_is_trashed(DocumentBranch.document_id), or_(DocumentBranch.document_id.in_(accessible),
                    DocumentBranch.document_id.in_(submitted) if role == "owner" else False))
                .order_by(DocumentBranch.created_at.desc(), DocumentBranch.document_id).limit(51)))
            truncated |= len(branches) > 50
            branches = list(reversed(branches[:50]))
            versions = list(db.scalars(select(Version).where(Version.document_id == source.id)
                .order_by(Version.created_at.desc(), Version.id).limit(101)))
            truncated |= len(versions) > 100
            versions = list(reversed(versions[:100]))
            return {"source_document": document_dict(db, source, role),
                "branches": [branch_dict(db, item, request.state.user) for item in branches],
                "requests": [request_dict(db, item, request.state.user) for item in requests],
                "versions": [version_dict(item) for item in versions],
                "graph": graph(db, source, branches, requests, request.state.user, versions, truncated)}

    def managed_branch(db, branch_id, user):
        require_access(db, branch_id, user, "owner", lock=True, fresh=True)
        branch = db.get(DocumentBranch, branch_id)
        if not branch or branch.owner_id != user:
            raise HTTPException(403, "작업본 소유자만 참여자를 관리할 수 있습니다.")
        return branch

    @app.get("/api/documents/{branch_id}/branch-participants")
    def participants(branch_id: str, request: Request):
        with transaction(engine) as db:
            managed_branch(db, branch_id, request.state.user)
            return [{"user_id": row.user_id, "role": "editor", "created_by": row.created_by, "created_at": row.created_at}
                for row in db.scalars(select(BranchParticipant).where(BranchParticipant.document_id == branch_id).order_by(BranchParticipant.user_id))]

    @app.put("/api/documents/{branch_id}/branch-participants/{user_id}")
    def invite(branch_id: str, user_id: str, request: Request):
        from .accounts import UserAccount
        with transaction(engine) as db:
            branch = managed_branch(db, branch_id, request.state.user)
            if user_id == branch.owner_id:
                raise HTTPException(409, "작업본 소유자는 이미 참여 중입니다.")
            account = db.get(UserAccount, user_id, populate_existing=True)
            if not account or not account.is_active or account.approval_status != "approved":
                raise HTTPException(422, "활성 승인 계정만 초대할 수 있습니다.")
            try:
                require_access(db, branch.source_document_id, user_id, "editor", fresh=True)
            except HTTPException as exc:
                if exc.status_code in (401, 403, 404):
                    raise HTTPException(422, "공통 문서의 현재 편집 권한이 있는 계정만 초대할 수 있습니다.") from None
                raise
            row = db.get(BranchParticipant, (branch_id, user_id))
            if not row:
                db.add(BranchParticipant(document_id=branch_id, user_id=user_id, created_by=request.state.user, created_at=time.time()))
            return {"ok": True}

    @app.delete("/api/documents/{branch_id}/branch-participants/{user_id}")
    def remove_participant(branch_id: str, user_id: str, request: Request):
        from .coedit import Member, Room
        from .db import LeaseOwner
        with transaction(engine) as db:
            branch = managed_branch(db, branch_id, request.state.user)
            if user_id == branch.owner_id:
                raise HTTPException(409, "작업본 소유자는 제외할 수 없습니다.")
            row = db.get(BranchParticipant, (branch_id, user_id))
            if row:
                db.delete(row)
            doc = db.get(Document, branch_id)
            room = db.get(Room, branch_id)
            if room and doc.lease_token == "coedit:" + room.generation:
                for member in db.scalars(select(Member).where(Member.document_id == branch_id,
                        Member.generation == room.generation, Member.user_id == user_id)):
                    member.expires_at = 0
                db.flush()
                if not db.scalar(select(Member.id).where(Member.document_id == branch_id,
                        Member.generation == room.generation, Member.expires_at > time.time()).limit(1)):
                    doc.lease_token = None; doc.lease_until = 0
            else:
                owner = db.get(LeaseOwner, branch_id)
                if owner and owner.user_id == user_id:
                    doc.lease_token = None; doc.lease_until = 0
            return {"ok": True}

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

    @app.get("/api/integration-requests/{identity}/source")
    def submitted_source(identity: str, request: Request):
        with transaction(engine) as db:
            # The caller's current permission still fences this archived snapshot.
            # Revoking its author blocks integration, not a manager's history read.
            item, _ = get_request(db, identity, request.state.user, require_author=False)
            data = read(db, item.source_version_id, item.branch_document_id)
            return Response(data, media_type="application/hwp+zip", headers={"Cache-Control": "no-store"})

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
