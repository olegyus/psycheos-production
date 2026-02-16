"""
TelegramUpdateDedup — ensures each Telegram update is processed exactly once.
On webhook retry or duplicate delivery, INSERT conflict = skip.
"""
from datetime import datetime
from sqlalchemy import String, BigInteger, DateTime, text, PrimaryKeyConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class TelegramUpdateDedup(Base):
    __tablename__ = "telegram_update_dedup"

    bot_id: Mapped[str] = mapped_column(String(50), nullable=False)
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        PrimaryKeyConstraint("bot_id", "update_id"),
        Index("idx_telegram_update_dedup_chat", "bot_id", "chat_id", "update_id"),
    )
