"""
Worker handler for Screen v2 report generation.

job_type: screen_report

Offloads the 3-Claude-call report pipeline from the webhook thread so Telegram
never times out waiting for a response.

job.payload keys:
  assessment_id  str  — UUID of the ScreeningAssessment row
  context_id     str  — UUID of the Context (may be None)

job.chat_id — client's Telegram chat_id (for outbox delivery)
job.context_id  — same as payload["context_id"] (set by enqueue)
"""
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from app.models.artifact import Artifact
from app.models.context import Context
from app.models.job import Job
from app.models.screening_assessment import ScreeningAssessment
from app.services.outbox import enqueue_message
from app.services.screen.orchestrator import ScreenOrchestrator

logger = logging.getLogger(__name__)

BOT_ID = "screen"


# ── Handler ───────────────────────────────────────────────────────────────────

async def handle_screen_report(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """Generate the final screening report, save artifact, notify specialist."""
    p = job.payload
    assessment_id = UUID(p["assessment_id"])
    context_id: UUID | None = UUID(p["context_id"]) if p.get("context_id") else job.context_id

    # 1. Generate report — 3 sequential Claude calls (sonnet).
    #    Also updates ScreeningAssessment: report_json, report_text,
    #    status="completed", completed_at.
    orchestrator = ScreenOrchestrator(db)
    result = await orchestrator._generate_report(assessment_id)
    # result = {"report_json": dict, "report_text": str}

    # 2. Save artifact (idempotent via UNIQUE(run_id, service_id)).
    await _save_artifact(db, assessment_id, context_id, result)

    # 3. Send final "✅ Скрининг завершён!" to the client via outbox.
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {
            "chat_id": job.chat_id,
            "text": (
                "✅ *Скрининг завершён!*\n\n"
                "Спасибо за ваши ответы. Результаты переданы вашему специалисту.\n\n"
                "_Специалист свяжется с вами для обсуждения результатов._"
            ),
            "parse_mode": "Markdown",
        },
        job_id=job.job_id,
        seq=0,
    )

    # 4. Notify specialist via Pro bot — uses bots["pro"] (no new Bot instance).
    await _notify_specialist(db, assessment_id, context_id, bots)

    logger.info("[worker/screen] report complete assessment=%s chat=%s", assessment_id, job.chat_id)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _save_artifact(
    db: AsyncSession,
    assessment_id: UUID,
    context_id: UUID | None,
    result: dict,
) -> None:
    """Persist completed screening result as an Artifact row.

    Idempotent: ON CONFLICT DO NOTHING on UNIQUE(run_id, service_id).
    payload structure: {"report_json": dict, "report_text": str}
    pro.py reads: a.payload.get("report_json", a.payload)
    """
    try:
        res = await db.execute(
            select(ScreeningAssessment).where(ScreeningAssessment.id == assessment_id)
        )
        assessment = res.scalar_one_or_none()
        if not assessment:
            logger.warning("[worker/screen] _save_artifact: assessment %s not found", assessment_id)
            return

        run_id = assessment.link_token_jti or assessment.id
        effective_context_id = context_id or assessment.context_id
        report_text: str = result.get("report_text") or ""
        summary = report_text[:200].strip() or None

        stmt = pg_insert(Artifact).values(
            context_id=effective_context_id,
            service_id="screen",
            run_id=run_id,
            specialist_telegram_id=assessment.specialist_user_id,
            payload=result,
            summary=summary,
        ).on_conflict_do_nothing(constraint="uq_artifacts_run_service")
        await db.execute(stmt)
        await db.flush()
        logger.info("[worker/screen] artifact saved for assessment %s", assessment_id)
    except Exception:
        logger.warning("[worker/screen] failed to save artifact for %s", assessment_id, exc_info=True)


async def _notify_specialist(
    db: AsyncSession,
    assessment_id: UUID,
    context_id: UUID | None,
    bots: dict[str, Bot],
) -> None:
    """Send a completion notification to the specialist in the Pro bot.

    Uses bots["pro"] — no new Bot instance created per call.
    specialist_user_id is taken from ScreeningAssessment (BigInteger Telegram ID).
    Context is loaded only to get client_ref for the label.
    """
    try:
        res = await db.execute(
            select(ScreeningAssessment).where(ScreeningAssessment.id == assessment_id)
        )
        assessment = res.scalar_one_or_none()
        if not assessment:
            logger.warning("[worker/screen] _notify_specialist: assessment %s not found", assessment_id)
            return

        specialist_telegram_id: int = assessment.specialist_user_id

        label: str = str(assessment_id)[:8]
        effective_context_id = context_id or assessment.context_id
        if effective_context_id:
            ctx_res = await db.execute(
                select(Context).where(Context.context_id == effective_context_id)
            )
            ctx = ctx_res.scalar_one_or_none()
            if ctx and ctx.client_ref:
                label = ctx.client_ref

        pro_bot = bots.get("pro")
        if not pro_bot:
            logger.warning("[worker/screen] pro bot unavailable for specialist notification")
            return

        await pro_bot.send_message(
            chat_id=specialist_telegram_id,
            text=(
                f"✅ *Скрининг завершён*\n\n"
                f"Кейс: {label}\n\n"
                f"Для просмотра результатов откройте кейс в меню."
            ),
            parse_mode="Markdown",
        )
    except Exception:
        logger.warning("[worker/screen] failed to notify specialist", exc_info=True)
