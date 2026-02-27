"""
PsycheOS Worker — async job processor.

Runs as a separate process:
    python -m app.worker

Event loop:
  1. Claim one pending job from the `jobs` table (FOR UPDATE SKIP LOCKED).
  2. Dispatch it to the appropriate handler from REGISTRY.
  3. Mark the job done or failed (exponential backoff via mark_failed).
  4. Drain up to OUTBOX_BURST pending outbox messages.
  5. Sleep JOB_POLL_INTERVAL seconds if the queue was empty, then repeat.

Periodic maintenance (once per _CLEANUP_INTERVAL):
  - Recover stuck jobs: running → pending for jobs idle > _STUCK_JOB_TIMEOUT.
  - Clean up old telegram_update_dedup rows (older than 1 hour).

Graceful shutdown on SIGTERM / SIGINT: finishes the current job, then exits.
"""
import asyncio
import logging
import signal
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, update as sa_update, text
from telegram import Bot

from app.config import settings
from app.database import async_session
from app.models.job import Job as JobModel
from app.services.billing import commit_by_run_id, cancel_by_run_id, TERMINAL_JOB_TYPES
from app.services.job_queue import claim_next, mark_done, mark_failed
from app.services.outbox import dispatch_one
from app.worker.handlers import REGISTRY

logger = logging.getLogger(__name__)

# ── Tuning ────────────────────────────────────────────────────────────────────

JOB_POLL_INTERVAL = 1.0       # seconds to sleep when the job queue is empty
OUTBOX_BURST = 10             # max outbox messages dispatched per event-loop tick

# A job in 'running' status for longer than this is assumed to be orphaned
# (worker crashed mid-execution). It will be reset to 'pending' so another
# worker instance can pick it up.
_STUCK_JOB_TIMEOUT = timedelta(minutes=10)

# How often to run periodic maintenance (stuck-job sweep + dedup cleanup).
_CLEANUP_INTERVAL = timedelta(hours=1)

# ── Shutdown flag (mutated by signal handler) ─────────────────────────────────

_running = True


def _handle_signal(signum, _frame) -> None:
    global _running
    logger.info("[worker] received signal %s — stopping after current job", signum)
    _running = False


# ── Maintenance helpers ────────────────────────────────────────────────────────

async def _recover_stuck_jobs() -> None:
    """Reset jobs stuck in 'running' status back to 'pending'.

    This handles the case where the worker process crashed mid-job.  The job
    was left in status='running' with no one to complete it.  We detect these
    by their started_at timestamp and reschedule them so any worker instance
    can pick them up.  The existing attempts counter is preserved, so the
    normal max_attempts / exponential-backoff logic still applies.
    """
    cutoff = datetime.now(timezone.utc) - _STUCK_JOB_TIMEOUT
    try:
        async with async_session() as db:
            result = await db.execute(
                sa_update(JobModel)
                .where(
                    JobModel.status == "running",
                    JobModel.started_at <= cutoff,
                )
                .values(status="pending", started_at=None)
            )
            recovered = result.rowcount
            await db.commit()
            if recovered:
                logger.warning(
                    "[worker] recovered %d stuck job(s) (running > %s)",
                    recovered, _STUCK_JOB_TIMEOUT,
                )
    except Exception:
        logger.exception("[worker] error recovering stuck jobs")


async def _cleanup_dedup_table() -> None:
    """Delete Telegram update dedup entries older than 1 hour.

    The dedup table has no TTL or auto-vacuum: without periodic cleanup it
    grows indefinitely.  Entries older than 1 hour are safe to delete because
    Telegram stops retrying updates after ~60 seconds.
    """
    try:
        async with async_session() as db:
            result = await db.execute(
                text(
                    "DELETE FROM telegram_update_dedup"
                    " WHERE received_at < now() - interval '1 hour'"
                )
            )
            deleted = result.rowcount
            await db.commit()
            if deleted:
                logger.info("[worker] dedup cleanup: deleted %d old entries", deleted)
    except Exception:
        logger.exception("[worker] error cleaning up dedup table")


# ── Core helpers ──────────────────────────────────────────────────────────────

async def _mark_job_failed(job_id: uuid.UUID, error: str) -> None:
    """Open a fresh session and mark the job as failed."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(JobModel).where(JobModel.job_id == job_id)
            )
            job_row = result.scalar_one_or_none()
            if job_row:
                await mark_failed(db, job_row, error)

                # ── Billing: cancel reservation on permanent failure ───────
                if (
                    job_row.status == "failed"  # permanently failed, not rescheduled
                    and job_row.job_type in TERMINAL_JOB_TYPES
                    and job_row.run_id is not None
                ):
                    try:
                        cancelled = await cancel_by_run_id(
                            db, job_row.run_id, job_row.user_id or 0
                        )
                        if cancelled:
                            logger.info(
                                "[worker] billing cancel run_id=%s job_id=%s",
                                job_row.run_id, job_id,
                            )
                    except Exception:
                        logger.exception(
                            "[worker] billing cancel failed for run_id=%s", job_row.run_id
                        )

                await db.commit()
    except Exception:
        logger.exception("[worker] could not mark job_id=%s as failed", job_id)


async def _process_one_job(bots: dict[str, Bot]) -> bool:
    """
    Claim and process one pending job.

    Returns True if a job was found (even if it failed), False if the queue
    is empty so the caller knows to sleep.
    """
    # ── Phase 1: claim a job atomically ──────────────────────────────────────
    async with async_session() as db:
        job = await claim_next(db)
        if job is None:
            return False
        job_id: uuid.UUID = job.job_id
        job_type: str = job.job_type
        await db.commit()  # status='running' committed; handler gets a fresh session

    # ── Phase 2: dispatch to handler ─────────────────────────────────────────
    handler = REGISTRY.get(job_type)
    if handler is None:
        logger.error("[worker] unknown job_type=%s job_id=%s", job_type, job_id)
        await _mark_job_failed(job_id, f"unknown job_type: {job_type}")
        return True

    logger.info("[worker] → job_type=%s job_id=%s", job_type, job_id)
    try:
        async with async_session() as db:
            result = await db.execute(
                select(JobModel).where(JobModel.job_id == job_id)
            )
            job_row = result.scalar_one_or_none()
            if job_row is None:
                logger.warning("[worker] claimed job vanished: %s", job_id)
                return True

            await handler(job_row, db, bots)
            await mark_done(db, job_row)

            # ── Billing: commit reservation for terminal jobs ─────────────
            if (
                job_row.job_type in TERMINAL_JOB_TYPES
                and job_row.run_id is not None
            ):
                try:
                    committed = await commit_by_run_id(
                        db, job_row.run_id, job_row.user_id or 0
                    )
                    if committed:
                        logger.info(
                            "[worker] billing commit run_id=%s job_id=%s",
                            job_row.run_id, job_id,
                        )
                except Exception:
                    logger.exception(
                        "[worker] billing commit failed for run_id=%s", job_row.run_id
                    )

            await db.commit()

        logger.info("[worker] ✓ done job_id=%s", job_id)

    except Exception as exc:
        logger.exception("[worker] ✗ job failed job_id=%s: %s", job_id, exc)
        await _mark_job_failed(job_id, str(exc))

    return True


async def _drain_outbox(bots: dict[str, Bot]) -> None:
    """Dispatch up to OUTBOX_BURST pending outbox messages."""
    for _ in range(OUTBOX_BURST):
        try:
            async with async_session() as db:
                sent = await dispatch_one(db, bots)
                if sent:
                    await db.commit()
                else:
                    break  # queue empty
        except Exception:
            logger.exception("[worker] outbox dispatch error")
            break


# ── Main event loop ───────────────────────────────────────────────────────────

async def _run(bots: dict[str, Bot]) -> None:
    global _running
    logger.info("[worker] started — bots: %s", sorted(bots.keys()))

    # Recover any jobs left in 'running' status from a previous crash.
    await _recover_stuck_jobs()

    last_maintenance = datetime.now(timezone.utc)

    while _running:
        try:
            had_job = await _process_one_job(bots)
            await _drain_outbox(bots)
            if not had_job:
                await asyncio.sleep(JOB_POLL_INTERVAL)

            # Periodic maintenance: run once per _CLEANUP_INTERVAL.
            now = datetime.now(timezone.utc)
            if (now - last_maintenance) >= _CLEANUP_INTERVAL:
                await _recover_stuck_jobs()
                await _cleanup_dedup_table()
                last_maintenance = now

        except Exception:
            logger.exception("[worker] unhandled error in event loop — continuing")
            await asyncio.sleep(JOB_POLL_INTERVAL)

    logger.info("[worker] shutdown complete")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG if settings.DEBUG else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _setup_sentry() -> None:
    if not settings.SENTRY_DSN:
        return
    try:
        import sentry_sdk  # type: ignore
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment="development" if settings.DEBUG else "production",
        )
        logger.info("[worker] Sentry initialised")
    except ImportError:
        logger.warning("[worker] sentry_sdk not installed — Sentry disabled")


def _build_bots() -> dict[str, Bot]:
    return {
        bot_id: Bot(token=token)
        for bot_id, (token, _secret) in settings.bot_config.items()
    }


def main() -> None:
    _setup_logging()
    _setup_sentry()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    bots = _build_bots()
    asyncio.run(_run(bots))


if __name__ == "__main__":
    main()
