"""Additive bot receipts. No usable credentials or callback URLs are persisted."""
from sqlalchemy import Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class MattermostLink(Base):
    __tablename__ = "mattermost_links"
    __table_args__ = (UniqueConstraint("instance_id", "user_id"),)
    instance_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    mattermost_user_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80))
    generation: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[float] = mapped_column(Float)


class MattermostChallenge(Base):
    __tablename__ = "mattermost_challenges"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(64))
    team_id: Mapped[str] = mapped_column(String(26))
    mattermost_user_id: Mapped[str] = mapped_column(String(26))
    channel_id: Mapped[str] = mapped_column(String(26))
    kind: Mapped[str] = mapped_column(String(12))
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    consumed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    generation: Mapped[str | None] = mapped_column(String(36), nullable=True)


class MattermostRequest(Base):
    __tablename__ = "mattermost_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    external_key: Mapped[str] = mapped_column(String(64), unique=True)
    instance_id: Mapped[str] = mapped_column(String(64))
    team_id: Mapped[str] = mapped_column(String(26))
    mattermost_user_id: Mapped[str] = mapped_column(String(26))
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    generation: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(12))
    # User-authorized request content is private runtime data, never a log field.
    payload: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[float] = mapped_column(Float)
    next_attempt_at: Mapped[float] = mapped_column(Float, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(40), default="accepted")
    delivered_post_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
