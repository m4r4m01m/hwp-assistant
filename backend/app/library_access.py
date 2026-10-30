"""Current document scope: private branches, explicit spaces, then legacy policy.

Space membership and new document invitations override legacy grants/shared-library access. Account admins
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
    from .document_branches import BranchParticipant, DocumentBranch
    from .workspaces import WorkspaceDocument, WorkspaceMember, WorkspaceShare
    branch = db.get(DocumentBranch, document_id, populate_existing=True)
    if branch:
        if db.get(DocumentBranch, branch.source_document_id):
            return None
        from .db import document_is_trashed
        if db.scalar(select(document_is_trashed(branch.source_document_id))):
            return None
        source_role = document_role(db, branch.source_document_id, user, fresh=fresh)
        if branch.owner_id == user:
            return "owner" if source_role else None
        if source_role not in {"owner", "editor"}:
            return None
        query = select(BranchParticipant).where(BranchParticipant.document_id == document_id,
            BranchParticipant.user_id == user).execution_options(populate_existing=True)
        participant = db.scalar(query.with_for_update(read=True) if fresh else query)
        return "editor" if participant else None
    scoped = db.get(WorkspaceDocument, document_id, populate_existing=True)
    if scoped:
        query = select(WorkspaceMember).where(WorkspaceMember.workspace_id == scoped.workspace_id,
                                              WorkspaceMember.user_id == user).execution_options(populate_existing=True)
        # Concurrent document writes may read the same membership. FOR SHARE
        # holds revocation until publication without deadlocking a source->work
        # copy integration against an ordinary work-copy save.
        member = db.scalar(query.with_for_update(read=True) if fresh else query)
        invitation = select(WorkspaceShare).where(WorkspaceShare.document_id == document_id,
            WorkspaceShare.workspace_id == scoped.workspace_id, WorkspaceShare.user_id == user).execution_options(populate_existing=True)
        share = db.scalar(invitation.with_for_update(read=True) if fresh else invitation)
        roles = [{"manager": "owner", "editor": "editor", "reader": "reader"}.get(member.role) if member else None,
                 share.role if share else None]
        return next((role for role in ("owner", "editor", "reader") if role in roles), None)
    from .document_library import PersonalDocument
    if shared_library(db, user) and not db.get(PersonalDocument, document_id):
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
    from .document_branches import BranchParticipant, DocumentBranch
    from .workspaces import WorkspaceDocument, WorkspaceMember, WorkspaceShare
    from .document_library import PersonalDocument
    if os.getenv("HWP_ACCOUNTS_ENABLED", "0") == "1":
        from .accounts import UserAccount
        account = db.get(UserAccount, user, populate_existing=True)
        if account and (not account.is_active or account.approval_status != "approved"):
            return false()
    branches = select(DocumentBranch.document_id)
    scoped = select(WorkspaceDocument.document_id)
    def ordinary(level):
        grant_roles = {"read": ("reader", "editor", "owner"), "edit": ("editor", "owner"), "manage": ("owner",)}[level]
        space_roles = {"read": ("reader", "editor", "manager"), "edit": ("editor", "manager"), "manage": ("manager",)}[level]
        explicit = Document.id.in_(select(Access.document_id).where(
            Access.user_id == user, Access.role.in_(grant_roles)).correlate(None))
        legacy = or_(Document.id.not_in(select(PersonalDocument.document_id)), explicit) if shared_library(db, user) else explicit
        membership = select(WorkspaceDocument.document_id).join(WorkspaceMember,
            WorkspaceMember.workspace_id == WorkspaceDocument.workspace_id).where(
            WorkspaceMember.user_id == user, WorkspaceMember.role.in_(space_roles)).correlate(None)
        invitations = select(WorkspaceShare.document_id).join(WorkspaceDocument,
            and_(WorkspaceDocument.document_id == WorkspaceShare.document_id,
                 WorkspaceDocument.workspace_id == WorkspaceShare.workspace_id)).where(
            WorkspaceShare.user_id == user, WorkspaceShare.role.in_(grant_roles)).correlate(None)
        return and_(Document.id.not_in(branches), or_(and_(Document.id.not_in(scoped), legacy),
                     Document.id.in_(membership), Document.id.in_(invitations)))
    readable_sources = select(Document.id).where(ordinary("read"), ~document_is_trashed(Document.id)).correlate(None)
    own_branches = select(DocumentBranch.document_id).where(DocumentBranch.owner_id == user,
        DocumentBranch.source_document_id.in_(readable_sources)).correlate(None)
    visible = or_(ordinary("manage" if manage else "read"), Document.id.in_(own_branches))
    if manage:
        return visible
    editable_sources = select(Document.id).where(ordinary("edit"), ~document_is_trashed(Document.id)).correlate(None)
    invited_branches = select(DocumentBranch.document_id).join(BranchParticipant,
        BranchParticipant.document_id == DocumentBranch.document_id).where(BranchParticipant.user_id == user,
        DocumentBranch.source_document_id.in_(editable_sources)).correlate(None)
    return or_(visible, Document.id.in_(invited_branches))


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
        raise HTTPException(409, "작업본 참여자는 작업본·통합 화면에서 관리하세요.")
