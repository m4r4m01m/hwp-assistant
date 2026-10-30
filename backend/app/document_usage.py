"""Admin-only logical HWPX storage totals; no document identities or blob reads."""
import time
from sqlalchemy import select, func, case, and_
from sqlalchemy.orm import aliased
from .db import Document, Version, document_is_trashed
from .document_branches import DocumentBranch
from .workspaces import Workspace, WorkspaceDocument

FIELDS = ("documents", "trashed_documents", "work_copies", "current_bytes", "stored_bytes", "versions")

def document_usage(db):
    history = select(Version.document_id.label("document_id"), func.sum(Version.size).label("bytes"),
                     func.count(Version.id).label("versions")).group_by(Version.document_id).subquery()
    current = aliased(Version)
    source_space = aliased(WorkspaceDocument)
    scope = func.coalesce(WorkspaceDocument.workspace_id, source_space.workspace_id)
    trashed = document_is_trashed(Document.id)
    query = select(scope.label("workspace_id"),
        func.sum(case((~trashed, 1), else_=0)), func.sum(case((trashed, 1), else_=0)),
        func.sum(case((and_(~trashed, DocumentBranch.document_id.is_not(None)), 1), else_=0)),
        func.sum(case((~trashed, func.coalesce(current.size, 0)), else_=0)),
        func.sum(func.coalesce(history.c.bytes, 0)), func.sum(func.coalesce(history.c.versions, 0))
    ).select_from(Document).outerjoin(WorkspaceDocument, WorkspaceDocument.document_id == Document.id
    ).outerjoin(DocumentBranch, DocumentBranch.document_id == Document.id
    ).outerjoin(source_space, source_space.document_id == DocumentBranch.source_document_id
    ).outerjoin(current, and_(current.id == Document.current_version, current.document_id == Document.id)
    ).outerjoin(history, history.c.document_id == Document.id).group_by(scope)
    counts = {row[0]: dict(zip(FIELDS, (int(v or 0) for v in row[1:]))) for row in db.execute(query)}
    rows = []
    for space in db.scalars(select(Workspace).where(Workspace.kind.in_(["department", "team"])).order_by(Workspace.name, Workspace.id)):
        rows.append({"id": space.id, "name": space.name, "kind": space.kind, "parent_id": space.parent_id,
                     **counts.pop(space.id, dict.fromkeys(FIELDS, 0))})
    other = {key: sum(c[key] for c in counts.values()) for key in FIELDS}
    rows.append({"id": "other", "name": "개인·기타 문서", "kind": "other", "parent_id": None, **other})
    return {"rows": rows, "totals": {key: sum(row[key] for row in rows) for key in FIELDS},
            "measured_at": time.time()}
