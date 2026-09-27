"""002/003: additive tables, preserving existing documents, versions and all blob keys."""
import os
from sqlalchemy import select, update
from .db import Base, Access, Conversation, Document, transaction


def migrate(engine, users):
    Base.metadata.create_all(engine)
    # Existing public documents are never silently assigned to an authenticated user.
    owner = os.getenv("HWP_LEGACY_OWNER") if users else "local"
    if owner and users and owner not in users:
        raise ValueError("HWP_LEGACY_OWNER must name a configured user")
    with transaction(engine) as db:
        legacy = list(db.scalars(select(Document).where(~select(Access.document_id)
                      .where(Access.document_id == Document.id).exists())))
        if legacy and not owner:
            raise ValueError("Set HWP_LEGACY_OWNER explicitly to migrate existing public documents")
        if users and "local" not in users and not owner and db.scalar(select(Access).where(Access.user_id == "local")):
            raise ValueError("Set HWP_LEGACY_OWNER explicitly to migrate the local workspace")
        for doc in legacy:
            db.add(Access(document_id=doc.id, user_id=owner, role="owner"))
            doc.lease_token = None
            doc.lease_until = 0
        # Explicit transition from the single-user development workspace.
        if users and owner:
            for grant in db.scalars(select(Access).where(Access.user_id == "local")):
                if "local" not in users:
                    existing = db.get(Access, (grant.document_id, owner))
                    if existing:
                        existing.role = "owner"
                        db.delete(grant)
                    else:
                        grant.user_id = owner
                    doc = db.get(Document, grant.document_id)
                    doc.lease_token = None
                    doc.lease_until = 0
            if "local" not in users:
                db.execute(update(Conversation).where(Conversation.owner_id == "local").values(owner_id=owner))
