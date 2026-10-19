from contextlib import contextmanager
from sqlalchemy import (
    create_engine,
    String,
    Integer,
    Float,
    ForeignKey,
    UniqueConstraint,
    text,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(240))
    current_version: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("document_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    base_version: Mapped[str | None] = mapped_column(String(36), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float)
    cause: Mapped[str] = mapped_column(String(20))
    request_id: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    session_hash: Mapped[str] = mapped_column(String(64))


class Access(Base):
    __tablename__ = "document_access"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    role: Mapped[str] = mapped_column(String(12))


class VersionAuthor(Base):
    """Additive, immutable save attribution. Missing rows are deliberately unknown."""
    __tablename__ = "version_authors"
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80))
    identity_kind: Mapped[str] = mapped_column(String(16))


class LeaseOwner(Base):
    __tablename__ = "lease_owners"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80))


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(80))
    visibility: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[float] = mapped_column(Float)


class Message(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    author_id: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float)


class Context(Base):
    __tablename__ = "conversation_contexts"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    base_version: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[float] = mapped_column(Float)


class ChangeSet(Base):
    __tablename__ = "version_changes"
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"), primary_key=True)
    base_sha256: Mapped[str] = mapped_column(String(64))
    entries: Mapped[str] = mapped_column(Text)


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(1000))
    tags: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float)


class TemplateCatalog(Base):
    __tablename__ = "template_catalog"
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id"), primary_key=True)
    category: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    # Registration retries remain bound to the initial input after metadata edits.
    registration_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TemplateFavorite(Base):
    __tablename__ = "template_favorites"
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)


class VisualStandard(Base):
    __tablename__ = "visual_standards"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    snapshot: Mapped[str] = mapped_column(Text)
    registration_sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float)


class Proposal(Base):
    __tablename__ = "text_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    context_revision: Mapped[int] = mapped_column(Integer)
    base_version: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    snapshot: Mapped[str] = mapped_column(Text)
    replacement: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[float] = mapped_column(Float)


class RewriteTask(Base):
    __tablename__ = "rewrite_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    requested_by: Mapped[str] = mapped_column(String(80))
    base_version: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    snapshot: Mapped[str] = mapped_column(Text)
    instruction: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64))
    execution: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float)


class RewriteCandidate(Base):
    __tablename__ = "rewrite_candidates"
    __table_args__ = (UniqueConstraint("task_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("rewrite_tasks.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    replacement: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)


class ReportTask(Base):
    __tablename__ = "report_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    input: Mapped[str] = mapped_column(Text)
    input_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution: Mapped[str] = mapped_column(Text)
    approval: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float)


class Application(Base):
    __tablename__ = "rewrite_applications"
    __table_args__ = (UniqueConstraint("task_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("rewrite_tasks.id"))
    candidate_id: Mapped[str] = mapped_column(ForeignKey("rewrite_candidates.id"))
    approved_by: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16))
    receipt: Mapped[str | None] = mapped_column(Text, nullable=True)
    export_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    saved_version: Mapped[str | None] = mapped_column(ForeignKey("versions.id"), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)


def make_engine(url):
    return create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 20}
        if url.startswith("sqlite")
        else {},
        pool_pre_ping=True,
    )


@contextmanager
def transaction(engine):
    with Session(engine, expire_on_commit=False) as db:
        try:
            # SQLite fallback serializes writers; production uses row locks in PostgreSQL.
            if engine.dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
