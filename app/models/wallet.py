"""
Wallet — per-user Telegram Stars balance.

Two-phase accounting:
  balance_stars  — total stars (including reserved)
  reserved_stars — locked for in-flight jobs
  available      = balance_stars - reserved_stars

Invariants:
  balance_stars  >= 0
  reserved_stars >= 0
  reserved_stars <= balance_stars
"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Wallet(Base):
    __tablename__ = "wallets"

    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Stars available in total (including reserved portion)
    balance_stars: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    # Stars locked for pending/running jobs (subset of balance_stars)
    reserved_stars: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    # Lifetime counters for analytics
    lifetime_in: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )  # total stars ever received (top-ups + admin credits)
    lifetime_out: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )  # total stars ever charged (committed reservations)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=datetime.utcnow,
    )
