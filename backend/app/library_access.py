"""Current document scope: private branches, explicit spaces, then legacy policy.

Space membership overrides legacy grants/shared-library access. Account admins
manage accounts but receive no implicit access to space documents or branches.
"""
import os
from fastapi import HTTPException
from sqlalchemy import and_, false, or_, select, true


def library_mode():
    mode = os.getenv("HWP_LIBRARY_ACCESS_MODE", "private")
    if mode not in {"private", "shared"}:
        raise ValueError("HWP_LIBRARY_ACCESS_MODE must be private or shared")
    return mode


def shared_library(db, user):
    if library_mode() != "shared" or os.getenv("HWP_ACCOUNTS_ENABLED", "0") != "1":
        return False
    from .accounts import UserAccount
    account = db.get(UserAccount, user, populate_existing=True)
    return bool(account and account.is_active and account.approval_status == "approved")


def document_role(db, document_id, user, *, fresh=False):
    from .document_branches import DocumentBranch
    from .workspaces import WorkspaceDocument, WorkspaceMember
    branch = db.get(DocumentBranch, document_id, populate_existing=True)
    if branch:
        if branch.owner_id != user or db.get(DocumentBranch, branch.source_document_id):
            return None
        from .db import document_is_trashed
        if db.scalar(select(document_is_trashed(branch.source_document_id))):
            return None
        return "owner" if document_role(db, branch.source_document_id, user, fresh=fresh) else None
    scoped = db.get(WorkspaceDocument, document_id, populate_existing=True)
    if scoped:
        query = select(WorkspaceMember).where(WorkspaceMember.workspace_id == scoped.workspace_id,
                                              WorkspaceMember.user_id == user).execution_options(populate_existing=True)
        # Concurrent document writes may read the same membership. FOR SHARE
        # holds revocation until publication without deadlocking a source->work
        # copy integration against an ordinary work-copy save.
        member = db.scalar(query.with_for_update(read=True) if fresh else query)
        return {"manager": "owner", "editor": "editor", "reader": "reader"}.get(member.role) if member else None
    if shared_library(db, user):
        return "owner"
    from .db import Access
    grant = db.get(Access, (document_id, user), populate_existing=fresh)
    return grant.role if grant else None


def _scope(db, user, *, manage=False):
    """Current reversible-management permission, without changing ownership rows.

    Use the same predicate for capabilities, trash listings and mutations. A
    mutation evaluates it again after locking its document, never trusting a
    capability returned by an earlier listing or a previous trash receipt.
    """
    from .db import Access, Document, document_is_trashed
    from .document_branches import DocumentBranch
    from .workspaces import WorkspaceDocument, WorkspaceMember
    if os.getenv("HWP_ACCOUNTS_ENABLED", "0") == "1":
        from .accounts import UserAccount
        account = db.get(UserAccount, user, populate_existing=True)
        if account and (not account.is_active or account.approval_status != "approved"):
            return false()
    branches = select(DocumentBranch.document_id)
    scoped = select(WorkspaceDocument.document_id)
    def ordinary(management):
        legacy = true() if shared_library(db, user) else Document.id.in_(select(Access.document_id).where(
            Access.user_id == user, Access.role == "owner" if management else true()).correlate(None))
        membership = select(WorkspaceDocument.document_id).join(WorkspaceMember,
            WorkspaceMember.workspace_id == WorkspaceDocument.workspace_id).where(
            WorkspaceMember.user_id == user, WorkspaceMember.role == "manager" if management else true()).correlate(None)
        return and_(Document.id.not_in(branches), or_(and_(Document.id.not_in(scoped), legacy), Document.id.in_(membership)))
    readable_sources = select(Document.id).where(ordinary(False), ~document_is_trashed(Document.id)).correlate(None)
    own_branches = select(DocumentBranch.document_id).where(DocumentBranch.owner_id == user,
        DocumentBranch.source_document_id.in_(readable_sources)).correlate(None)
    return or_(ordinary(manage), Document.id.in_(own_branches))


def document_read_scope(db, user):
    return _scope(db, user)


def document_management_scope(db, user):
    return _scope(db, user, manage=True)


def require_individual_grants(db, document_id):
    from .document_branches import DocumentBranch
    from .workspaces import WorkspaceDocument
    if db.get(WorkspaceDocument, document_id):
        raise HTTPException(409, "공간 문서의 권한은 공간 구성원에서 관리하세요.")
    if db.get(DocumentBranch, document_id):
        raise HTTPException(409, "개인 작업본은 작성자만 편집합니다. 통합 요청으로 검토를 공유하세요.")
