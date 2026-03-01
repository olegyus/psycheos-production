"""
Worker handlers for Conceptualizator bot async jobs.

job_types:
  concept_hypothesis — extract hypothesis from specialist message; ask next question
  concept_output     — assemble three-layer output (A/B/C) and send results

job.payload keys:
  session  dict  — SessionState.model_dump(mode="json")
  role     str   — "specialist"

concept_hypothesis additionally:
  message_text  str  — the specialist's message to extract hypothesis from
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from app.config import settings
from app.models.job import Job
from app.services.artifacts import save_artifact
from app.services.conceptualizer.analysis import extract_hypothesis_from_response
from app.services.conceptualizer.decision_policy import (
    select_next_question,
    should_continue_dialogue,
)
from app.services.conceptualizer.enums import SessionStateEnum
from app.services.conceptualizer.models import SessionState
from app.services.conceptualizer.output import assemble_output
from app.services.conceptualizer.report import generate_concept_docx
from app.services.job_queue import enqueue
from app.services.outbox import enqueue_message, make_document_payload
from app.webhooks.common import upsert_chat_state

logger = logging.getLogger(__name__)

BOT_ID = "conceptualizator"


async def handle_concept_hypothesis(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """
    Extract a hypothesis from the specialist's message, update session,
    then either ask the next Socratic question or trigger output assembly.
    """
    p = job.payload
    session = SessionState.model_validate(p["session"])
    text: str = p["message_text"]

    hypothesis = await extract_hypothesis_from_response(text, session)
    session.add_hypothesis(hypothesis)

    type_emoji = {
        "structural": "🏗",
        "functional": "⚙️",
        "dynamic": "🔄",
        "managerial": "🎯",
    }
    emoji = type_emoji.get(hypothesis.type.value, "📝")
    levels_str = ", ".join(lv.value for lv in hypothesis.levels)

    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {
            "chat_id": job.chat_id,
            "text": (
                f"✅ {emoji} Гипотеза извлечена\n"
                f"<b>Тип:</b> {hypothesis.type.value}\n"
                f"<b>Слои:</b> {levels_str}\n"
                f"<b>Формулировка:</b> {hypothesis.formulation}\n\n"
                f"<i>Всего гипотез: {len(session.get_active_hypotheses())} "
                f"(управленческих: {len(session.get_managerial_hypotheses())})</i>"
            ),
            "parse_mode": "HTML",
        },
        job_id=job.job_id, seq=0,
    )

    should_continue, reason = should_continue_dialogue(session)

    if not should_continue:
        # Save current session state, then queue output assembly
        await _persist_session(db, session, "socratic_dialogue", job)
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {
                "chat_id": job.chat_id,
                "text": f"📋 {reason}\n\n⏳ Формирую концептуализацию...",
            },
            job_id=job.job_id, seq=1,
        )
        await enqueue(
            db, "concept_output", BOT_ID, job.chat_id,
            payload={"session": session.model_dump(mode="json"), "role": p.get("role", "specialist")},
            user_id=job.user_id, context_id=job.context_id, run_id=job.run_id,
            priority=3,
        )
    else:
        selection = select_next_question(session)
        session.progress.increment_dialogue_turns()
        await _persist_session(db, session, "socratic_dialogue", job)
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {
                "chat_id": job.chat_id,
                "text": (
                    f"💬 <b>Вопрос {session.progress.dialogue_turns}</b>\n\n"
                    f"❓ {selection.question_text}"
                ),
                "parse_mode": "HTML",
            },
            job_id=job.job_id, seq=1,
        )


async def handle_concept_output(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """
    Assemble three-layer conceptualization output (Layer A/B/C),
    send each layer as a Telegram message, update FSM, save artifact.
    """
    p = job.payload
    session = SessionState.model_validate(p["session"])

    output = await assemble_output(session)

    # Transition state
    session.transition_to(SessionStateEnum.OUTPUT_ASSEMBLY)
    session.transition_to(SessionStateEnum.COMPLETE)
    await _persist_session(db, session, "complete", job)

    # Layer A
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {
            "chat_id": job.chat_id,
            "text": (
                "📊 <b>LAYER A: Концептуальная модель</b>\n\n"
                f"<b>Ведущая гипотеза:</b>\n{output.layer_a.leading_formulation}\n\n"
                f"<b>Доминирующий слой:</b> {output.layer_a.dominant_layer.value}\n\n"
                f"<b>Конфигурация:</b>\n{output.layer_a.configuration_summary}\n\n"
                f"<b>Цена системы:</b>\n{output.layer_a.system_cost}"
            ),
            "parse_mode": "HTML",
        },
        job_id=job.job_id, seq=0,
    )

    # Layer B
    b_lines = ["🎯 <b>LAYER B: Мишени вмешательства</b>\n"]
    for t in output.layer_b.targets:
        b_lines.append(f"<b>{t.priority}. {t.layer}</b>\n{t.direction}\n")
    b_lines.append(f"\n<b>Последовательность:</b>\n{output.layer_b.sequencing_notes}")
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {"chat_id": job.chat_id, "text": "\n".join(b_lines), "parse_mode": "HTML"},
        job_id=job.job_id, seq=1,
    )

    # Layer C
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {
            "chat_id": job.chat_id,
            "text": (
                "🎭 <b>LAYER C: Метафорический нарратив</b>\n\n"
                f"<b>Метафора:</b> <i>{output.layer_c.core_metaphor}</i>\n\n"
                f"{output.layer_c.narrative}\n\n"
                f"<b>Направление изменения:</b>\n{output.layer_c.direction_of_change}"
            ),
            "parse_mode": "HTML",
        },
        job_id=job.job_id, seq=2,
    )

    # DOCX report
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    docx_buf = generate_concept_docx(output, meta={"date": date_str})
    filename = f"concept_{output.session_id[:8]}_{date_str.replace('-', '')}.docx"
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_document",
        make_document_payload(job.chat_id, docx_buf.read(), filename, "📋 Концептуализация случая"),
        job_id=job.job_id, seq=3,
    )

    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {
            "chat_id": job.chat_id,
            "text": "✅ <b>Концептуализация завершена!</b>\n\nЗапустите новую сессию через бот Pro.",
            "parse_mode": "HTML",
        },
        job_id=job.job_id, seq=4,
    )

    # Save artifact
    leading = output.layer_a.leading_formulation
    summary = leading[:150] + ("…" if len(leading) > 150 else "")
    await save_artifact(
        db=db,
        run_id=job.run_id,
        service_id=BOT_ID,
        context_id=job.context_id,
        specialist_telegram_id=job.user_id or job.chat_id,
        payload={
            "layer_a": output.layer_a.model_dump(mode="json"),
            "layer_b": output.layer_b.model_dump(mode="json"),
            "layer_c": output.layer_c.model_dump(mode="json"),
            "meta": {
                "session_id": output.session_id,
                "hypothesis_count": len(session.hypotheses),
                "red_flags": [str(f) for f in session.red_flags],
            },
        },
        summary=summary,
    )
    logger.info("[worker/concept] output complete chat=%s", job.chat_id)


# ── Utility ───────────────────────────────────────────────────────────────────

async def _persist_session(
    db: AsyncSession,
    session: SessionState,
    bot_state_name: str,
    job: Job,
) -> None:
    """Write updated session back to bot_chat_state."""
    current_payload = {"session": session.model_dump(mode="json")}
    if job.run_id:
        current_payload["run_id"] = str(job.run_id)
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=job.chat_id, state=bot_state_name,
        state_payload=current_payload, user_id=job.user_id,
        role=job.payload.get("role", "specialist"), context_id=job.context_id,
    )
