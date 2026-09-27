"""Configured opaque credentials. No identity is accepted from a user-id header."""
import json
import os
import re
import secrets
from fastapi import HTTPException
from sqlalchemy import select
from .db import Access, Document, LeaseOwner

ROLES = {"reader": 1, "editor": 2, "owner": 3}
COOKIE = "hwp_session"


def credentials():
    raw = os.getenv("HWP_AUTH_TOKENS", "")
    if not raw:
        return {}
    values = json.loads(raw)
    if not isinstance(values, dict) or not values:
        raise ValueError("HWP_AUTH_TOKENS must be a nonempty user-to-token object")
    if any(not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", u) or
           not isinstance(t, str) or len(t) < 24 for u, t in values.items()):
        raise ValueError("Invalid HWP_AUTH_TOKENS user or token (minimum 24 characters)")
    if len(set(values.values())) != len(values):
        raise ValueError("Each user needs a unique token")
    return values


def authenticate(token, users):
    for user, expected in users.items():
        if secrets.compare_digest(token.encode(), expected.encode()):
            return user
    raise HTTPException(401, "로그인이 필요하거나 인증 토큰이 만료되었습니다.")


def require_access(db, doc_id, user, role="reader", *, lock=False):
    query = select(Document).where(Document.id == doc_id)
    doc = db.scalar(query.with_for_update() if lock else query)
    access = db.get(Access, (doc_id, user))
    if not doc or not access:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    if ROLES[access.role] < ROLES[role]:
        raise HTTPException(403, "이 작업을 수행할 문서 권한이 없습니다.")
    return doc


def bind_lease(db, doc, user):
    owner = db.get(LeaseOwner, doc.id)
    if owner:
        owner.user_id = user
    else:
        db.add(LeaseOwner(document_id=doc.id, user_id=user))


def require_lease_owner(db, doc, user):
    owner = db.get(LeaseOwner, doc.id)
    if not owner or owner.user_id != user:
        raise HTTPException(409, "다른 사용자의 편집 세션입니다.")
