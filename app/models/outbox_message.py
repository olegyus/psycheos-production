"""
OutboxMessage — Telegram API calls queued for delivery.

After a Job completes, the worker writes one or more OutboxMessages.
The outbox dispatcher polls this table and calls Bot.send_* methods.

Separating compute (jobs) from delivery (outbox) means:
- Claude API errors don't prevent sending what was already computed.
- Telegram API errors don't block the next Claude job.
- Retries are scoped correctly: re-call Claude vs re-send message.

tg_method values (subset of python-telegram-bot Bot methods):
  send_message    — Bot.send_message(**payload)
  send_document   — Bot.send_document(**payload)  [file bytes in payload]
  edit_message    — Bot.edit_message_text(**payload)
"""
import uuid
from datetime import datetime

from sqlalchemy import String, SmallInteger, BigInteger, DateTime, Text, text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"

    msg_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # Source job (nullable: outbox can also receive standalone messages,
    # e.g. "⏳ processing…" acks enqueued directly from webhook).
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # intentionally no FK — jobs may be deleted before outbox drains

    # ── Destination ───────────────────────────────────────────────────────────
    bot_id: Mapped[str] = mapped_column(String(50), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # ── Telegram call ─────────────────────────────────────────────────────────
    tg_method: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # send_message | send_document | edit_message
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False
    )  # kwargs forwarded to Bot method; documents stored as base64 bytes

    # ── Delivery state ────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )  # pending | sent | failed

    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("5")
    )  # Telegram is flaky; allow more retries than Claude jobs
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Ordering ──────────────────────────────────────────────────────────────
    seq: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )  # within a job, send messages in seq order (0, 1, 2 …)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Dispatcher polling: pending messages, oldest first within a chat.
        Index(
            "idx_outbox_pending",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        # Per-chat ordering: dispatch messages in correct sequence.
        Index("idx_outbox_chat_seq", "bot_id", "chat_id", "seq", "created_at"),
    )
