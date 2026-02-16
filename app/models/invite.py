"""
Invite model — controls access to PsycheOS.
Admin creates invite → specialist uses link → gets registered.
"""
import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Invite(Base):
    __tablename__ = "invites"

    token: Mapped[str] = mapped_column(
        String(32), primary_key=True
    )  # short random token
    created_by: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # admin telegram_id
    max_uses: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    used_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    note: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # e.g. "Для Анны, психолог"
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
