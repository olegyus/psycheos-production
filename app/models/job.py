"""
Job — persistent task queue entry for async Claude API work.

Webhook handlers enqueue a Job and immediately return 200.
The worker process claims jobs with FOR UPDATE SKIP LOCKED,
executes the Claude call, and writes results to outbox_messages.

status lifecycle:
  pending → running → done
                    → failed (attempts < max_attempts → scheduled_at advances → pending again)
"""
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, SmallInteger, BigInteger, DateTime, Text, text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # ── Routing ──────────────────────────────────────────────────────────────
    job_type: Mapped[str] = mapped_column(
        String(60), nullable=False,
    )
    # Examples: "interp_photo", "interp_intake", "interp_run",
    #           "concept_hypothesis", "concept_output",
    #           "sim_launch", "sim_launch_custom", "sim_report",
    #           "pro_reference"

    bot_id: Mapped[str] = mapped_column(String(50), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # ── Context (denormalised for zero extra joins in worker) ────────────────
    context_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # ── Payload — all inputs needed to execute the job ───────────────────────
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # ── Queue state ───────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )  # pending | running | done | failed

    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("5")
    )  # 1 = highest, 9 = lowest

    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("3")
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Billing (Phase 7) ─────────────────────────────────────────────────────
    stars_reserved: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True
    )  # Stars locked at tool-launch; None = free operation (e.g. reference chat legacy)
    wallet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.wallet_id", ondelete="SET NULL"),
        nullable=True,
    )  # Which wallet was charged; used by worker to commit/cancel

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )  # retry: set to now() + backoff
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Worker polling index: pick next eligible job cheaply.
        # Partial index covers only actionable rows.
        Index(
            "idx_jobs_queue",
            "priority", "scheduled_at",
            postgresql_where=text("status = 'pending'"),
        ),
        # Lookup by chat for dedup / status checks.
        Index("idx_jobs_chat", "bot_id", "chat_id", "status"),
    )
