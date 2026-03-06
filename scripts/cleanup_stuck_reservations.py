"""
Cleanup stuck (unsettled) billing reservations.

A reservation is "stuck" when there is a usage_ledger row with kind='reserve'
but no corresponding 'charge' or 'refund' row for the same run_id.

This happens in two scenarios:
  1. Jobs that failed permanently but cancel_by_run_id was not called
     (e.g., pre-fix jobs, jobs not in TERMINAL_JOB_TYPES).
  2. Orphaned reservations with no matching job at all
     (e.g., jti/run_id mismatch from an earlier bug).

Resolution rules (per run_id):
  - job.status = 'done'    → commit_by_run_id  (Stars were earned, deduct them)
  - job.status = 'failed'  → cancel_by_run_id  (permanent failure, return Stars)
  - job not found          → cancel_by_run_id  (orphaned entry, return Stars)
  - job.status = 'pending' or 'running' → skip (job is still active)

Run:
    python -m scripts.cleanup_stuck_reservations [--dry-run]

Options:
    --dry-run   Print what would be done without modifying the database.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from typing import Optional

from sqlalchemy import select, text

from app.database import async_session
from app.models.job import Job as JobModel
from app.models.usage_ledger import UsageLedger
from app.services.billing import commit_by_run_id, cancel_by_run_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv


async def _find_stuck_reservations() -> list[UsageLedger]:
    """
    Return all reserve-ledger entries that have no paired charge/refund.

    Uses a NOT EXISTS subquery so we don't pull the entire ledger into memory.
    """
    async with async_session() as db:
        result = await db.execute(
            select(UsageLedger)
            .where(
                UsageLedger.kind == "reserve",
                ~select(UsageLedger.entry_id)
                .where(
                    UsageLedger.run_id == UsageLedger.run_id,  # correlated
                    UsageLedger.kind.in_(["charge", "refund"]),
                )
                .correlate(UsageLedger)
                .exists(),
            )
            .order_by(UsageLedger.created_at)
        )
        return list(result.scalars().all())


async def _find_stuck_reservations_raw() -> list[dict]:
    """
    Return dicts with run_id, wallet_id, telegram_id, stars for each stuck reserve.

    Uses raw SQL with NOT EXISTS correlated subquery for correctness.
    """
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                SELECT
                    ul.entry_id,
                    ul.run_id,
                    ul.wallet_id,
                    ul.telegram_id,
                    ul.stars,
                    ul.service_id,
                    ul.created_at
                FROM usage_ledger ul
                WHERE ul.kind = 'reserve'
                  AND NOT EXISTS (
                      SELECT 1 FROM usage_ledger ul2
                      WHERE ul2.run_id = ul.run_id
                        AND ul2.kind IN ('charge', 'refund')
                  )
                ORDER BY ul.created_at
                """
            )
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]


async def _find_job_by_run_id(run_id: uuid.UUID) -> Optional[JobModel]:
    """Look up the job whose run_id matches the reservation."""
    async with async_session() as db:
        result = await db.execute(
            select(JobModel).where(JobModel.run_id == run_id).limit(1)
        )
        return result.scalar_one_or_none()


async def _settle(run_id: uuid.UUID, telegram_id: int, action: str, stars: int) -> bool:
    """Call commit or cancel in a fresh session and commit the transaction."""
    async with async_session() as db:
        if action == "commit":
            settled = await commit_by_run_id(db, run_id, telegram_id)
        else:
            settled = await cancel_by_run_id(db, run_id, telegram_id)

        if settled:
            await db.commit()
        return settled


async def main() -> None:
    logger.info("=== cleanup_stuck_reservations %s===", "[DRY RUN] " if DRY_RUN else "")

    rows = await _find_stuck_reservations_raw()

    if not rows:
        logger.info("No stuck reservations found. All good!")
        return

    stars_total = sum(abs(r["stars"]) for r in rows)
    logger.info(
        "Found %d stuck reservation(s) totalling %d Stars", len(rows), stars_total
    )

    committed = 0
    cancelled = 0
    skipped = 0
    errors = 0

    for r in rows:
        run_id: Optional[uuid.UUID] = r["run_id"]
        entry_id = r["entry_id"]
        telegram_id: int = r["telegram_id"]
        stars: int = abs(r["stars"])
        service_id: Optional[str] = r["service_id"]
        created_at = r["created_at"]

        prefix = f"  run_id={run_id} entry={entry_id} service={service_id} stars={stars} created={created_at}"

        if run_id is None:
            # No run_id — cannot look up job; cancel the orphan
            action = "cancel"
            reason = "no run_id (orphaned)"
        else:
            job = await _find_job_by_run_id(run_id)
            if job is None:
                action = "cancel"
                reason = "no matching job (orphaned)"
            elif job.status in ("pending", "running"):
                logger.info("%s → SKIP (job status=%s, still active)", prefix, job.status)
                skipped += 1
                continue
            elif job.status == "done":
                action = "commit"
                reason = f"job done (job_id={job.job_id})"
            elif job.status == "failed":
                action = "cancel"
                reason = f"job failed permanently (job_id={job.job_id})"
            else:
                logger.warning("%s → SKIP (unexpected job status=%s)", prefix, job.status)
                skipped += 1
                continue

        logger.info("%s → %s [%s]", prefix, action.upper(), reason)

        if DRY_RUN:
            if action == "commit":
                committed += 1
            else:
                cancelled += 1
            continue

        try:
            settled = await _settle(run_id, telegram_id, action, stars)
            if settled:
                if action == "commit":
                    committed += 1
                else:
                    cancelled += 1
            else:
                logger.warning("%s → already settled (concurrent run?)", prefix)
                skipped += 1
        except Exception:
            logger.exception("%s → ERROR during %s", prefix, action)
            errors += 1

    logger.info(
        "Done. committed=%d cancelled=%d skipped=%d errors=%d%s",
        committed, cancelled, skipped, errors,
        " [DRY RUN — no changes written]" if DRY_RUN else "",
    )

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
