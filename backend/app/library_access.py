"""Document access policy; shared access never rewrites historical ownership.

Keep this seam for later personal/shared workspaces. Account roles only govern
account administration; every approved member has the same shared-library role.
"""
import os
from fastapi import HTTPException


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
    if shared_library(db, user):
        return "owner"
    from .db import Access
    grant = db.get(Access, (document_id, user), populate_existing=fresh)
    return grant.role if grant else None
