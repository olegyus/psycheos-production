"""
Job queue service — the contract between webhook handlers and the worker.

Webhook handlers call enqueue() and immediately return 200 to Telegram.
The worker calls claim_next() in a tight loop, executes the Claude call,
then calls mark_done() or mark_failed().

Retry backoff (exponential, base 30s):
  attempt 1 fail → scheduled_at + 30s
  attempt 2 fail → scheduled_at + 60s
  attempt 3 fail → marked 'failed' permanently (Sentry sees it)
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, update, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job

logger = logging.getLogger(__name__)

_BACKOFF_BASE_SECONDS = 30


# ── Public API ────────────────────────────────────────────────────────────────

async def enqueue(
    db: AsyncSession,
    job_type: str,
    bot_id: str,
    chat_id: int,
    payload: dict,
    *,
    user_id: int | None = None,
    context_id: uuid.UUID | str | None = None,
    run_id: uuid.UUID | str | None = None,
    priority: int = 5,
) -> Job:
    """
    Persist a new Job and return it.

    The caller is responsible for committing the surrounding transaction so
    the worker can see the job.  In the webhook pipeline db.commit() happens
    in router_factory right after the handler returns — no extra work needed.
    """
    # Normalise UUIDs (may arrive as strings from state_payload).
    if isinstance(context_id, str):
        context_id = uuid.UUID(context_id)
    if isinstance(run_id, str):
        run_id = uuid.UUID(run_id)

    job = Job(
        job_type=job_type,
        bot_id=bot_id,
        chat_id=chat_id,
        user_id=user_id,
        context_id=context_id,
        run_id=run_id,
        payload=payload,
        priority=priority,
    )
    db.add(job)
    await db.flush()  # populate job_id; caller commits
    logger.info(
        "job.enqueue job_id=%s type=%s bot=%s chat=%s",
        job.job_id, job_type, bot_id, chat_id,
    )
    return job


async def claim_next(db: AsyncSession) -> Job | None:
    """
    Atomically claim one pending job: status pending → running.

    Uses FOR UPDATE SKIP LOCKED so multiple worker replicas are safe.
    Returns None when the queue is empty or all pending jobs are locked.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(Job)
        .where(
            Job.status == "pending",
            Job.scheduled_at <= now,
        )
        .order_by(Job.priority, Job.scheduled_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        return None

    job.status = "running"
    job.started_at = now
    job.attempts += 1
    await db.flush()
    logger.info(
        "job.claim job_id=%s type=%s attempt=%d",
        job.job_id, job.job_type, job.attempts,
    )
    return job


async def mark_done(db: AsyncSession, job: Job) -> None:
    """Mark a running job as successfully completed."""
    job.status = "done"
    job.completed_at = datetime.now(timezone.utc)
    await db.flush()
    logger.info("job.done job_id=%s type=%s", job.job_id, job.job_type)


async def mark_failed(db: AsyncSession, job: Job, error: str) -> None:
    """
    Record a failure.

    If attempts < max_attempts: reschedule with exponential backoff (pending).
    Otherwise: mark permanently failed so Sentry / on-call can inspect it.
    """
    job.last_error = error[:2000]  # cap to avoid oversized TEXT values

    if job.attempts < job.max_attempts:
        delay = timedelta(seconds=_BACKOFF_BASE_SECONDS * (2 ** (job.attempts - 1)))
        job.scheduled_at = datetime.now(timezone.utc) + delay
        job.status = "pending"  # back to queue; worker will re-claim
        logger.warning(
            "job.retry job_id=%s type=%s attempt=%d/%d retry_in=%s error=%r",
            job.job_id, job.job_type, job.attempts, job.max_attempts, delay, error[:120],
        )
    else:
        job.status = "failed"
        job.completed_at = datetime.now(timezone.utc)
        logger.error(
            "job.failed job_id=%s type=%s all %d attempts exhausted error=%r",
            job.job_id, job.job_type, job.attempts, error[:120],
        )

    await db.flush()


async def is_job_pending_for_chat(db: AsyncSession, bot_id: str, chat_id: int) -> bool:
    """
    Check whether this chat already has an active (pending|running) job.

    Webhook handlers can call this to avoid double-enqueueing when a user
    rapidly sends messages while a job is in flight.
    """
    result = await db.execute(
        select(Job.job_id)
        .where(
            Job.bot_id == bot_id,
            Job.chat_id == chat_id,
            Job.status.in_(["pending", "running"]),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None
