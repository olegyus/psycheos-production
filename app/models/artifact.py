"""
Artifact — persisted output of a completed tool-bot session.

One artifact is created per run (run_id ≡ link_token.jti) per service.
The UNIQUE(run_id, service_id) constraint makes writes idempotent:
a webhook retry or race condition just silently loses the duplicate.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, BigInteger, DateTime, Text, text, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Artifact(Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    context_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contexts.context_id", ondelete="CASCADE"),
        nullable=False,
    )
    service_id: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )  # interpretator | conceptualizator | simulator
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )  # = link_token.jti — idempotency key
    specialist_telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )  # denormalised from BotChatState.user_id (Telegram ID)

    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
    )  # full structured output (service-specific, see Phase 5 spec)
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )  # 1-2 line human-readable description for Pro bot list view

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        # One artifact per run — safe for webhook retries and concurrent writes.
        UniqueConstraint("run_id", "service_id", name="uq_artifacts_run_service"),
        # Primary access pattern: list artifacts for a case, newest first.
        Index("idx_artifacts_context_time", "context_id", "created_at"),
        # Secondary: all sessions by a specialist (analytics / admin view).
        Index("idx_artifacts_specialist", "specialist_telegram_id", "created_at"),
    )
