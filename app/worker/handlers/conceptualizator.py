"""
Worker handlers for Conceptualizator bot async jobs.

job_types:
  concept_pre_hypotheses — generate preliminary hypotheses from Screen/Interpreter artifacts
  concept_hypothesis     — extract hypothesis from specialist message; ask next question
  concept_output         — assemble three-layer output (A/B/C) and send results

job.payload keys:
  session  dict  — SessionState.model_dump(mode="json")
  role     str   — "specialist"

concept_hypothesis additionally:
  message_text  str  — the specialist's message to extract hypothesis from
"""
import json
import logging
from datetime import datetime, timezone

from anthropic import AsyncAnthropic
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
from app.services.conceptualizer.enums import (
    ConfidenceLevel,
    HypothesisType,
    PsycheLevelEnum,
    SessionStateEnum,
)
from app.services.conceptualizer.models import Hypothesis, SessionState
from app.services.conceptualizer.output import assemble_output
from app.services.conceptualizer.report import generate_concept_docx
from app.services.job_queue import enqueue
from app.services.outbox import enqueue_message, make_document_payload
from app.webhooks.common import upsert_chat_state

logger = logging.getLogger(__name__)

BOT_ID = "conceptualizator"
_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"

_PRE_HYPOTHESES_PROMPT = """\
Ты - эксперт по психотерапевтической концептуализации в рамках PsycheOS framework.

Задача: на основе данных скрининга и/или интерпретации разработать предварительные гипотезы о системе клиента ДО детальной сессии с терапевтом.

# PsycheOS Framework (слои L0-L4):
- L0: Базовая регуляция (энергия, сон, тело)
- L1: Рефлексивный контроль (автоматизмы, защиты)
- L2: Сознательный выбор (произвольная регуляция)
- L3: Социально-ролевой контроль (отношения, роли)
- L4: Смыслы и идентичность (ценности, нарратив)

# Типы гипотез:
- structural: конфигурация системы (как устроено)
- functional: функция паттерна (зачем)
- dynamic: механизмы поддержания (петли A→B→C)
- managerial: точки управления (где/как можно влиять) ← ОБЯЗАТЕЛЬНО включить хотя бы 1

# Задача:
Создай 2-4 предварительные гипотезы (включая хотя бы 1 managerial) на основе предоставленных данных.

# Формат ответа (JSON):

{
  "hypotheses": [
    {
      "type": "structural|functional|dynamic|managerial",
      "levels": ["L0", "L1", ...],
      "formulation": "чёткая формулировка гипотезы (1-2 предложения)",
      "confidence": "weak|working|dominant",
      "reasoning": "на чём основана гипотеза"
    }
  ],
  "summary": "краткое (2-3 предложения) резюме паттерна на основе данных"
}

ОТВЕТ ТОЛЬКО JSON, БЕЗ ДОПОЛНИТЕЛЬНОГО ТЕКСТА.\
"""


def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```json"):
        t = t[7:]
    if t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return json.loads(t.strip())


async def handle_concept_pre_hypotheses(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """
    Generate preliminary hypotheses from Screen + Interpreter artifact data.
    Seeds them into the session, sends a summary message, then asks for case description.
    """
    p = job.payload
    session = SessionState.model_validate(p["session"])

    context_parts = []
    if session.screen_context:
        context_parts.append(f"## Данные скрининга (Screen):\n{session.screen_context}")
    if session.interpreter_context:
        context_parts.append(f"## Данные интерпретации (Interpreter):\n{session.interpreter_context}")

    if not context_parts:
        # No context available — just ask for description directly
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {
                "chat_id": job.chat_id,
                "text": (
                    "✅ Сессия готова к работе.\n\n"
                    "<b>Этап 1: Сбор данных</b>\n"
                    "Опишите случай клиента:\n"
                    "• Основные жалобы\n"
                    "• Наблюдения по слоям (L0–L4)\n"
                    "• Ключевые маркеры\n\n"
                    "Напишите <b>«готово»</b> когда закончите."
                ),
                "parse_mode": "HTML",
            },
            job_id=job.job_id, seq=0,
        )
        return

    user_message = "\n\n".join(context_parts) + "\n\nСгенерируй предварительные гипотезы."

    try:
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=2000,
            system=_PRE_HYPOTHESES_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            timeout=120.0,
        )
        data = _parse_json(resp.content[0].text)
    except Exception:
        logger.exception("[worker/concept] pre_hypotheses Claude error")
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {
                "chat_id": job.chat_id,
                "text": (
                    "✅ Сессия готова к работе.\n\n"
                    "<b>Этап 1: Сбор данных</b>\n"
                    "Опишите случай клиента:\n"
                    "• Основные жалобы\n"
                    "• Наблюдения по слоям (L0–L4)\n"
                    "• Ключевые маркеры\n\n"
                    "Напишите <b>«готово»</b> когда закончите."
                ),
                "parse_mode": "HTML",
            },
            job_id=job.job_id, seq=0,
        )
        await _persist_session(db, session, "data_collection", job)
        return

    # Seed preliminary hypotheses into session
    _type_emoji = {"structural": "🏗", "functional": "⚙️", "dynamic": "🔄", "managerial": "🎯"}
    seeded: list[str] = []
    for hyp_data in data.get("hypotheses", []):
        try:
            hyp_id = f"pre_{session.progress.hypotheses_added + 1:03d}"
            hypothesis = Hypothesis(
                id=hyp_id,
                type=HypothesisType(hyp_data["type"]),
                levels=[PsycheLevelEnum(lv) for lv in hyp_data["levels"]],
                formulation=hyp_data["formulation"],
                confidence=ConfidenceLevel(hyp_data["confidence"]),
                foundations=[hyp_data.get("reasoning", "pre-analysis")],
            )
            session.add_hypothesis(hypothesis)
            emoji = _type_emoji.get(hyp_data["type"], "📝")
            seeded.append(f"{emoji} <b>{hyp_data['type']}</b>: {hyp_data['formulation']}")
        except Exception:
            logger.warning("[worker/concept] Could not parse pre-hypothesis: %s", hyp_data)

    await _persist_session(db, session, "data_collection", job)

    summary = data.get("summary", "")
    hyp_block = "\n".join(seeded)

    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {
            "chat_id": job.chat_id,
            "text": (
                "🔍 <b>Предварительный анализ</b>\n\n"
                f"{summary}\n\n"
                f"<b>Предварительные гипотезы:</b>\n{hyp_block}\n\n"
                "<b>Теперь расскажите о случае подробнее:</b>\n"
                "• Основные жалобы клиента\n"
                "• Ваши наблюдения по слоям (L0–L4)\n"
                "• Ключевые маркеры поведения\n\n"
                "Напишите <b>«готово»</b> когда закончите."
            ),
            "parse_mode": "HTML",
        },
        job_id=job.job_id, seq=0,
    )
    logger.info(
        "[worker/concept] pre_hypotheses complete chat=%s seeded=%d",
        job.chat_id, len(seeded),
    )


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

    # Notification
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {"chat_id": job.chat_id, "text": "📋 Концептуализация готова"},
        job_id=job.job_id, seq=0,
    )

    # DOCX report
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    docx_buf = generate_concept_docx(output, meta={"date": date_str})
    context_short = str(job.context_id)[:8] if job.context_id else output.session_id[:8]
    filename = f"concept_{context_short}_{date_str.replace('-', '')}.docx"
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_document",
        make_document_payload(job.chat_id, docx_buf.read(), filename, "📋 Концептуализация случая"),
        job_id=job.job_id, seq=1,
    )

    # History hint
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {"chat_id": job.chat_id, "text": "Результат также доступен в Истории результатов в боте @PsycheOS_Pro"},
        job_id=job.job_id, seq=2,
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
