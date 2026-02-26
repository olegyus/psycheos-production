"""
UsageLedger — immutable audit log of every financial event.

kind values:
  topup        — successful_payment: user bought Stars
  reserve      — stars locked at tool launch
  charge       — reservation committed (job done successfully)
  refund       — reservation cancelled (job failed permanently)
  admin_credit — manual credit by admin
"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UsageLedger(Base):
    __tablename__ = "usage_ledger"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.wallet_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Denormalised for fast admin queries without JOIN to users
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    kind: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # topup | reserve | charge | refund | admin_credit

    # Positive = credit; negative = debit (reserve/charge are negative, refund positive)
    stars: Mapped[int] = mapped_column(Integer, nullable=False)

    # Session correlation — link_token.jti
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    service_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    operation: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # From Telegram SuccessfulPayment.provider_payment_charge_id (topup only)
    payment_charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
