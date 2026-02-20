"""
Artifact service — persists tool-bot session outputs to the artifacts table.

Usage in tool-bot handlers (after sending final output to Telegram):

    from app.services.artifacts import save_artifact

    await save_artifact(
        db=db,
        run_id=state.state_payload.get("run_id"),
        service_id="interpretator",
        context_id=state.context_id,
        specialist_telegram_id=user_id,
        payload={...},
        summary="Интерпретация сна. Материал фрагментарный.",
    )

The write is idempotent: UNIQUE(run_id, service_id) with ON CONFLICT DO NOTHING
ensures that webhook retries or concurrent calls produce at most one row.
If run_id is None or context_id is None the call is a no-op (logged as warning).
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.artifact import Artifact

logger = logging.getLogger(__name__)


async def save_artifact(
    db: AsyncSession,
    run_id: str | uuid.UUID | None,
    service_id: str,
    context_id: uuid.UUID | None,
    specialist_telegram_id: int,
    payload: dict,
    summary: str | None = None,
) -> None:
    """
    Persist a tool-bot output as an artifact.

    Idempotent: duplicate calls (same run_id + service_id) are silently ignored.
    Raises no exceptions — errors are logged so the caller can proceed.
    """
    if run_id is None or context_id is None:
        logger.warning(
            "save_artifact: missing run_id=%s or context_id=%s for service=%s — skipping",
            run_id, context_id, service_id,
        )
        return

    # Normalise run_id to UUID (state_payload stores it as a string).
    if isinstance(run_id, str):
        try:
            run_id = uuid.UUID(run_id)
        except ValueError:
            logger.warning(
                "save_artifact: invalid run_id=%r for service=%s — skipping",
                run_id, service_id,
            )
            return

    try:
        stmt = (
            pg_insert(Artifact)
            .values(
                context_id=context_id,
                service_id=service_id,
                run_id=run_id,
                specialist_telegram_id=specialist_telegram_id,
                payload=payload,
                summary=summary,
            )
            .on_conflict_do_nothing(constraint="uq_artifacts_run_service")
        )
        await db.execute(stmt)
        await db.flush()
        logger.info(
            "save_artifact: persisted service=%s run_id=%s context_id=%s",
            service_id, run_id, context_id,
        )
    except Exception:
        logger.exception(
            "save_artifact: DB error for service=%s run_id=%s", service_id, run_id
        )
