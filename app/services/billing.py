"""
Billing service — Telegram Stars two-phase accounting.

Balance model:
  wallet.balance_stars   — total stars in custody (available + reserved)
  wallet.reserved_stars  — portion locked for in-flight jobs
  available              = balance_stars - reserved_stars

Operation lifecycle:
  1. Tool launch     → reserve_stars()   : reserved_stars += N
  2a. Job done       → commit_reservation(): balance_stars  -= N, reserved_stars -= N, lifetime_out += N
  2b. Job perm-fail  → cancel_reservation(): reserved_stars -= N  (stars return to available)

Top-up / admin credit  → credit_stars()  : balance_stars += N, lifetime_in += N

Ledger (audit trail, non-additive):
  topup / admin_credit : stars = +N
  reserve              : stars = -N  (informational — locked, not yet spent)
  charge               : stars = -N  (committed — actually deducted from balance)
  refund               : stars = +N  (reservation cancelled — returned to available)
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_rate import AIRate
from app.models.usage_ledger import UsageLedger
from app.models.wallet import Wallet

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class InsufficientBalance(Exception):
    """Raised by reserve_stars when available balance < requested amount."""

    def __init__(self, available: int, required: int) -> None:
        self.available = available
        self.required = required
        super().__init__(
            f"Insufficient Stars: available={available}, required={required}"
        )


# ---------------------------------------------------------------------------
# Wallet helpers
# ---------------------------------------------------------------------------


async def get_or_create_wallet(
    db: AsyncSession,
    user_id: uuid.UUID,
    telegram_id: int,
) -> Wallet:
    """Return the wallet for the user, creating one with zero balance if absent."""
    result = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id)
    )
    wallet = result.scalar_one_or_none()
    if wallet is None:
        wallet = Wallet(user_id=user_id)
        db.add(wallet)
        await db.flush()  # populate wallet_id
        logger.info("Created wallet %s for user %s (tg=%d)", wallet.wallet_id, user_id, telegram_id)
    return wallet


# ---------------------------------------------------------------------------
# Rate helpers
# ---------------------------------------------------------------------------


async def get_rate(
    db: AsyncSession,
    service_id: str,
    operation: str = "session",
) -> Optional[AIRate]:
    """Return the AIRate row for (service_id, operation), or None if missing."""
    result = await db.execute(
        select(AIRate).where(
            AIRate.service_id == service_id,
            AIRate.operation == operation,
        )
    )
    return result.scalar_one_or_none()


async def get_stars_price(
    db: AsyncSession,
    service_id: str,
    operation: str = "session",
) -> Optional[int]:
    """Convenience: return the Stars price int or None."""
    rate = await get_rate(db, service_id, operation)
    return rate.stars_price if rate else None


# ---------------------------------------------------------------------------
# Two-phase accounting
# ---------------------------------------------------------------------------


async def reserve_stars(
    db: AsyncSession,
    wallet: Wallet,
    telegram_id: int,
    stars: int,
    run_id: uuid.UUID,
    service_id: str,
    operation: str = "session",
) -> None:
    """
    Lock *stars* for an in-flight job.

    Raises InsufficientBalance if available balance < stars.
    Modifies wallet in place; caller must db.flush() / db.commit().
    """
    available = wallet.balance_stars - wallet.reserved_stars
    if available < stars:
        raise InsufficientBalance(available=available, required=stars)

    wallet.reserved_stars += stars

    await _log_ledger(
        db,
        wallet=wallet,
        telegram_id=telegram_id,
        kind="reserve",
        stars=-stars,
        run_id=run_id,
        service_id=service_id,
        operation=operation,
    )


async def commit_reservation(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    telegram_id: int,
    stars_reserved: int,
    run_id: Optional[uuid.UUID],
    service_id: Optional[str],
    operation: Optional[str],
) -> None:
    """
    Job completed successfully: deduct stars from balance and release reservation.

    balance_stars  -= stars_reserved
    reserved_stars -= stars_reserved
    lifetime_out   += stars_reserved
    """
    wallet = await _load_wallet(db, wallet_id)
    if wallet is None:
        logger.error("commit_reservation: wallet %s not found", wallet_id)
        return

    wallet.balance_stars -= stars_reserved
    wallet.reserved_stars -= stars_reserved
    wallet.lifetime_out += stars_reserved

    await _log_ledger(
        db,
        wallet=wallet,
        telegram_id=telegram_id,
        kind="charge",
        stars=-stars_reserved,
        run_id=run_id,
        service_id=service_id,
        operation=operation,
    )


async def cancel_reservation(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    telegram_id: int,
    stars_reserved: int,
    run_id: Optional[uuid.UUID],
    service_id: Optional[str],
    operation: Optional[str],
) -> None:
    """
    Job permanently failed: release reservation, leaving balance intact.

    reserved_stars -= stars_reserved  (stars return to available)
    balance_stars  unchanged
    """
    wallet = await _load_wallet(db, wallet_id)
    if wallet is None:
        logger.error("cancel_reservation: wallet %s not found", wallet_id)
        return

    wallet.reserved_stars -= stars_reserved

    await _log_ledger(
        db,
        wallet=wallet,
        telegram_id=telegram_id,
        kind="refund",
        stars=stars_reserved,
        run_id=run_id,
        service_id=service_id,
        operation=operation,
    )


# ---------------------------------------------------------------------------
# Top-up / admin credit
# ---------------------------------------------------------------------------


async def credit_stars(
    db: AsyncSession,
    wallet: Wallet,
    telegram_id: int,
    stars: int,
    kind: str,
    *,
    payment_charge_id: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """
    Add stars to the wallet (top-up from payment or admin manual credit).

    kind must be "topup" or "admin_credit".
    """
    if kind not in ("topup", "admin_credit"):
        raise ValueError(f"Invalid credit kind: {kind!r}")

    wallet.balance_stars += stars
    wallet.lifetime_in += stars

    await _log_ledger(
        db,
        wallet=wallet,
        telegram_id=telegram_id,
        kind=kind,
        stars=stars,
        payment_charge_id=payment_charge_id,
        note=note,
    )


# ---------------------------------------------------------------------------
# Worker helpers — settle reservations by run_id
# ---------------------------------------------------------------------------

# Job types that represent the terminal (final) step of a billed session.
# Only these should trigger commit / cancel of the reservation.
TERMINAL_JOB_TYPES: frozenset[str] = frozenset({
    "interp_run",          # Interpretator — final analysis step
    "concept_output",      # Conceptualizator — 3-layer output step
    "sim_launch",          # Simulator — full session launch
    "sim_launch_custom",   # Simulator — custom launch (may lack run_id; safe to skip)
})


async def commit_by_run_id(
    db: AsyncSession,
    run_id: uuid.UUID,
    telegram_id: int,
) -> bool:
    """
    Called by the worker after a terminal job completes successfully.

    Looks up the pending reservation for *run_id* in usage_ledger and calls
    commit_reservation() to deduct from balance and release the lock.

    Returns True if a reservation was found and committed, False otherwise.
    Idempotent: a second call for the same run_id finds no pending reservation
    (charge entry already exists) and returns False.
    """
    # Guard against double-commit (e.g., worker retry after network timeout)
    settled = await db.execute(
        select(UsageLedger).where(
            UsageLedger.run_id == run_id,
            UsageLedger.kind.in_(["charge", "refund"]),
        ).limit(1)
    )
    if settled.scalar_one_or_none() is not None:
        return False  # already settled

    result = await db.execute(
        select(UsageLedger).where(
            UsageLedger.run_id == run_id,
            UsageLedger.kind == "reserve",
        ).limit(1)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        return False  # no reservation — free operation or unknown run_id

    stars = abs(entry.stars)  # reserve entries are stored as negative
    await commit_reservation(
        db,
        wallet_id=entry.wallet_id,
        telegram_id=telegram_id,
        stars_reserved=stars,
        run_id=run_id,
        service_id=entry.service_id,
        operation=entry.operation,
    )
    return True


async def cancel_by_run_id(
    db: AsyncSession,
    run_id: uuid.UUID,
    telegram_id: int,
) -> bool:
    """
    Called by the worker after a terminal job is permanently failed.

    Releases the reservation so stars return to available balance.

    Returns True if a reservation was found and cancelled, False otherwise.
    Idempotent (same guard as commit_by_run_id).
    """
    settled = await db.execute(
        select(UsageLedger).where(
            UsageLedger.run_id == run_id,
            UsageLedger.kind.in_(["charge", "refund"]),
        ).limit(1)
    )
    if settled.scalar_one_or_none() is not None:
        return False

    result = await db.execute(
        select(UsageLedger).where(
            UsageLedger.run_id == run_id,
            UsageLedger.kind == "reserve",
        ).limit(1)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        return False

    stars = abs(entry.stars)
    await cancel_reservation(
        db,
        wallet_id=entry.wallet_id,
        telegram_id=telegram_id,
        stars_reserved=stars,
        run_id=run_id,
        service_id=entry.service_id,
        operation=entry.operation,
    )
    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_wallet(db: AsyncSession, wallet_id: uuid.UUID) -> Optional[Wallet]:
    result = await db.execute(select(Wallet).where(Wallet.wallet_id == wallet_id))
    return result.scalar_one_or_none()


async def _log_ledger(
    db: AsyncSession,
    *,
    wallet: Wallet,
    telegram_id: int,
    kind: str,
    stars: int,
    run_id: Optional[uuid.UUID] = None,
    service_id: Optional[str] = None,
    operation: Optional[str] = None,
    payment_charge_id: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Insert an immutable audit-log entry into usage_ledger."""
    entry = UsageLedger(
        wallet_id=wallet.wallet_id,
        telegram_id=telegram_id,
        kind=kind,
        stars=stars,
        run_id=run_id,
        service_id=service_id,
        operation=operation,
        payment_charge_id=payment_charge_id,
        note=note,
    )
    db.add(entry)
    # No flush here — caller controls transaction boundaries
