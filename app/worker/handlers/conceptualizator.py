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

Задача: на основе данных скрининга и/или интерпретации разработать предварительные гипотезы о состоянии клиента ДО детальной сессии с терапевтом.

# Внутренняя классификация (используй для JSON-полей, НЕ в текстах):
- structural: конфигурация (как устроено)
- functional: функция (зачем)
- dynamic: механизмы поддержания (петли)
- managerial: точки управления (где/как можно влиять) ← ОБЯЗАТЕЛЬНО включить хотя бы 1

# КРИТИЧЕСКИ ВАЖНО — язык формулировок:
- В полях "formulation" и "summary": пиши на профессиональном разговорном языке
- НЕ используй в тексте: "L0", "L1", "L2", "L3", "L4", "слой", "система", "паттерн системы", "регуляция", "маркер"
- Говори конкретно о клиенте: "клиент физически истощён и плохо спит", "избегает ситуаций, где нет ясного ответа", "логически понимает что нужно изменить, но не может"
- Поля "levels" в JSON заполни правильно (L0-L4) — но в сам текст формулировок они не входят
- "summary" — 2-3 предложения простым языком о том, что видно в данных; специалист должен прочитать и сразу узнать картину

# Задача:
Создай 2-4 предварительные гипотезы (включая хотя бы 1 managerial) на основе предоставленных данных.

# Формат ответа (JSON):

{
  "hypotheses": [
    {
      "type": "structural|functional|dynamic|managerial",
      "levels": ["L0", "L1", ...],
      "formulation": "чёткая формулировка на разговорном языке (1-2 предложения, без L0-L4)",
      "confidence": "weak|working|dominant",
      "reasoning": "на чём основана гипотеза"
    }
  ],
  "summary": "2-3 предложения о картине случая, простым профессиональным языком"
}

ОТВЕТ ТОЛЬКО JSON, БЕЗ ДОПОЛНИТЕЛЬНОГО ТЕКСТА.\
"""

_SOCRATIC_QUESTION_PROMPT = """\
Ты — опытный супервизор, помогающий специалисту прояснить его концептуализацию случая клиента.

Твоя задача: сформулировать ОДИН конкретный вопрос специалисту.

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать слова:
"система", "уровень", "слой", "маркер", "функция паттерна", "регуляция", "L0", "L1", "L2", "L3", "L4"
Слово "паттерн" заменяй на: "привычка", "реакция", "поведение", "способ справляться"

Правила:
- Один вопрос. Без вступления. Без пояснений после вопроса.
- Привязывай к конкретным деталям из описания случая (ситуации, поведение, слова клиента)
- Говори как коллега-супервизор, а не как система
- Вопрос адресован специалисту (о его взгляде, ощущениях, оценке) — не клиенту

Как переводить направление в конкретный вопрос:

НАПРАВЛЕНИЕ: level_check
Спроси, как специалист понимает природу этой реакции — автоматическая она (запускается сама) или осознанная, телесная или смысловая.
Пример (адаптируй под детали случая): "Вы упомянули [деталь]. Это, как вам кажется, скорее автоматическая реакция — как тело само реагирует — или клиент в этот момент осознаёт что происходит и делает выбор?"

НАПРАВЛЕНИЕ: function_check
Спроси, что случится или чего опасается клиент, если это изменится. Конкретно, через детали.
Пример: "Если клиент вдруг перестал бы [конкретное поведение] — что, по вашему ощущению, произошло бы? Чего он, возможно, боится?"

НАПРАВЛЕНИЕ: alternatives_check
Мягко предложи альтернативное объяснение тех же данных.
Пример: "Вы смотрите на это как на [суть гипотезы]. А если предположить что дело больше в [альтернатива из контекста] — это что-то меняет в картине?"

НАПРАВЛЕНИЕ: control_check
Спроси что реально можно начать менять уже сейчас. Конкретно и практично.
Пример: "Из всего что мы обсудили — что клиент мог бы начать делать иначе уже сейчас, даже небольшой шаг?"

НАПРАВЛЕНИЕ: dynamics_check
Спроси что поддерживает ситуацию или не даёт ей меняться.
Пример: "Что, по вашим наблюдениям, не даёт этому меняться? Что подпитывает эту ситуацию?"

Верни ТОЛЬКО текст вопроса. Без кавычек. Без форматирования.\
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


async def _generate_socratic_question(
    direction: str,
    hypothesis_formulation: str,
    specialist_observations: str,
) -> str | None:
    """Call Claude to produce one concrete, case-grounded Socratic question.

    Falls back to None so callers can use the template text instead.
    """
    user_message = (
        f"Направление: {direction}\n\n"
        f"Последняя гипотеза специалиста: {hypothesis_formulation}\n\n"
        f"Описание случая (что рассказал специалист):\n{specialist_observations[:800]}"
    )
    try:
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=200,
            system=_SOCRATIC_QUESTION_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            timeout=60.0,
        )
        q = resp.content[0].text.strip().strip('"').strip("'")
        return q if q else None
    except Exception:
        logger.warning("[worker/concept] Socratic question Claude call failed; using template")
        return None


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
    seeded_lines: list[str] = []
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
            seeded_lines.append(f"• {hyp_data['formulation']}")
        except Exception:
            logger.warning("[worker/concept] Could not parse pre-hypothesis: %s", hyp_data)

    await _persist_session(db, session, "data_collection", job)

    summary = data.get("summary", "")
    hyp_block = "\n".join(seeded_lines)

    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {
            "chat_id": job.chat_id,
            "text": (
                "🔍 <b>Предварительный анализ</b>\n\n"
                f"{summary}\n\n"
                f"<b>Что уже видно:</b>\n{hyp_block}\n\n"
                "<b>Теперь расскажите о случае подробнее:</b>\n"
                "Опишите основные жалобы клиента, ваши наблюдения, "
                "ключевые ситуации.\n\n"
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

    # Minimal confirmation — just acknowledge receipt, no internal labels
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {
            "chat_id": job.chat_id,
            "text": f"✅ Принято: {hypothesis.formulation}",
        },
        job_id=job.job_id, seq=0,
    )

    should_continue, reason = should_continue_dialogue(session)

    if not should_continue:
        # Save current session state, then queue output assembly
        await _persist_session(db, session, "socratic_dialogue", job)
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {"chat_id": job.chat_id, "text": "⏳ Формирую концептуализацию..."},
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

        # Translate the internal direction into a concrete, case-grounded question
        specialist_observations = (
            (session.data_map.specialist_observations or "") if session.data_map else ""
        )
        concrete_q = await _generate_socratic_question(
            direction=selection.question_type.value,
            hypothesis_formulation=hypothesis.formulation,
            specialist_observations=specialist_observations,
        )
        question_text = concrete_q or selection.question_text  # template fallback

        await _persist_session(db, session, "socratic_dialogue", job)
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {
                "chat_id": job.chat_id,
                "text": f"❓ {question_text}",
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
