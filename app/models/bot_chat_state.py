"""
BotChatState — FSM state for every (bot, chat) pair.
This replaces in-memory state and survives restarts / replica switches.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, BigInteger, DateTime, text, PrimaryKeyConstraint, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class BotChatState(Base):
    __tablename__ = "bot_chat_state"

    bot_id: Mapped[str] = mapped_column(String(50), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="specialist"
    )  # specialist | client
    state: Mapped[str] = mapped_column(
        String(100), nullable=False, default="idle"
    )  # FSM state name
    state_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )  # local step data
    context_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # active case/context
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("bot_id", "chat_id"),
        Index("idx_bot_chat_state_context", "context_id"),
    )
