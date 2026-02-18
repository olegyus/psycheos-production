"""
LinkToken — one-time pass that Pro issues for tool bots.
Implementation: UUID + DB record (not JWT), per Phase 3 spec.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, BigInteger, text, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LinkToken(Base):
    __tablename__ = "link_tokens"

    jti: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )  # ties issue → verify → artifact → billing (cross-phase)
    service_id: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )  # screen | interpretator | conceptualizator | simulator
    context_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contexts.context_id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )  # specialist | client
    subject_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )  # telegram_id of the intended user
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )  # NULL = not yet used
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        # One active session per (service, run) — prevents replay
        UniqueConstraint("service_id", "run_id", name="uq_link_tokens_service_run"),
        Index("idx_link_tokens_context", "context_id"),
        Index("idx_link_tokens_expires", "expires_at"),
    )
