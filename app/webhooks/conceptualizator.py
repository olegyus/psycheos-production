"""
Webhook handler for Conceptualizator bot (Phase 4).

State machine (bot_chat_state.state):
  data_collection  — specialist submitting case observations
  socratic_dialogue — iterative hypothesis extraction dialogue
  complete         — three-layer output sent; session closed

state_payload keys:
  run_id    — UUID from link token
  session   — full SessionState serialised as dict (model_dump)
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, Update

from app.config import settings
from app.models.bot_chat_state import BotChatState
from app.services.conceptualizer.decision_policy import select_next_question
from app.services.conceptualizer.models import DataMap, SessionState
from app.services.conceptualizer.enums import SessionStateEnum
from app.services.job_queue import enqueue, is_job_pending_for_chat
from app.services.links import LinkVerifyError, verify_link
from app.webhooks.common import upsert_chat_state

logger = logging.getLogger(__name__)

BOT_ID = "conceptualizator"


# ── Session state helpers ──────────────────────────────────────────────────────

def _load_session(state: BotChatState) -> SessionState | None:
    if not state or not state.state_payload:
        return None
    session_data = state.state_payload.get("session")
    if not session_data:
        return None
    try:
        return SessionState.model_validate(session_data)
    except Exception:
        logger.exception(f"[{BOT_ID}] Failed to deserialise session state")
        return None


async def _save_session(
    db: AsyncSession,
    session: SessionState,
    bot_state_name: str,
    bot_state: BotChatState,
    chat_id: int,
    user_id: int | None,
) -> None:
    payload = dict(bot_state.state_payload or {})
    payload["session"] = session.model_dump(mode="json")
    await upsert_chat_state(
        db,
        bot_id=BOT_ID,
        chat_id=chat_id,
        state=bot_state_name,
        state_payload=payload,
        user_id=user_id,
        role=bot_state.role,
        context_id=bot_state.context_id,
    )


def _session_id(chat_id: int, state: BotChatState) -> str:
    run_id = (state.state_payload or {}).get("run_id", "")
    short = run_id.replace("-", "")[:8] if run_id else ""
    return f"cnc_{chat_id}_{short}" if short else f"cnc_{chat_id}"


def _is_clarification_request(message: str) -> bool:
    keywords = [
        "что значит", "уточните", "поясните", "не понял",
        "непонятно", "объясните", "что имеется в виду",
        "как это", "что это означает",
    ]
    ml = message.lower()
    return ("?" in message or any(kw in ml for kw in keywords)) and len(message) < 150


# ── Entry point ────────────────────────────────────────────────────────────────

async def handle_conceptualizator(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text.strip()

    if text.startswith("/start"):
        parts = text.split(" ", 1)
        if len(parts) == 2 and parts[1].strip():
            await _start_session(bot, db, chat_id, user_id, parts[1].strip())
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ Запустите инструмент через бот Pro.",
            )
        return

    if text.startswith("/status"):
        await _handle_status(bot, state, chat_id)
        return

    if text.startswith("/reset"):
        await _handle_reset(bot, db, state, chat_id, user_id)
        return

    if text.startswith("/help"):
        await _handle_help(bot, chat_id)
        return

    await _handle_message(bot, db, text, state, chat_id, user_id)


# ── Session start ──────────────────────────────────────────────────────────────

async def _start_session(
    bot: Bot, db: AsyncSession,
    chat_id: int, user_id: int | None, raw_token: str,
) -> None:
    try:
        token = await verify_link(
            db, raw_token=raw_token, service_id=BOT_ID, subject_id=user_id,
        )
    except LinkVerifyError as e:
        logger.info(f"[{BOT_ID}] verify_link failed user={user_id}: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Доступ закрыт: {e}\n\nВернитесь в Pro и запросите новую ссылку.",
        )
        return

    session = SessionState(
        session_id=f"cnc_{chat_id}_{str(token.run_id).replace('-', '')[:8]}",
        specialist_id=str(user_id or chat_id),
    )
    # Skip INIT → go straight to DATA_COLLECTION
    session.transition_to(SessionStateEnum.DATA_COLLECTION)

    await upsert_chat_state(
        db,
        bot_id=BOT_ID,
        chat_id=chat_id,
        state="data_collection",
        state_payload={
            "run_id": str(token.run_id),
            "session": session.model_dump(mode="json"),
        },
        user_id=user_id,
        role=token.role,
        context_id=token.context_id,
    )
    logger.info(
        f"[{BOT_ID}] Session started: user={user_id} "
        f"context={token.context_id} run_id={token.run_id}"
    )
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "🎯 <b>PsycheOS Conceptualizer</b>\n\n"
            "Сессия открыта.\n\n"
            "<b>Этап 1: Сбор данных</b>\n"
            "Предоставьте информацию о клиенте:\n"
            "• Основные жалобы\n"
            "• Наблюдения по слоям (L0–L4)\n"
            "• Ключевые маркеры\n\n"
            "Напишите <b>«готово»</b> когда закончите."
        ),
        parse_mode="HTML",
    )


# ── Message routing ────────────────────────────────────────────────────────────

async def _handle_message(
    bot: Bot, db: AsyncSession, text: str,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if state is None or state.state == "complete":
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "Сессия завершена. Запустите новую через бот Pro."
                if state and state.state == "complete"
                else "Для запуска используйте ссылку из Pro."
            ),
        )
        return

    session = _load_session(state)
    if session is None:
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Не удалось загрузить сессию. Запустите новую через Pro.",
        )
        return

    if state.state == "data_collection":
        await _handle_data_collection(bot, db, text, session, state, chat_id, user_id)
    elif state.state == "socratic_dialogue":
        await _handle_dialogue(bot, db, text, session, state, chat_id, user_id)
    else:
        await bot.send_message(chat_id=chat_id, text="Для запуска используйте ссылку из Pro.")


# ── Data collection ────────────────────────────────────────────────────────────

async def _handle_data_collection(
    bot: Bot, db: AsyncSession, text: str,
    session: SessionState, state: BotChatState,
    chat_id: int, user_id: int | None,
) -> None:
    if session.data_map is None:
        session.data_map = DataMap(specialist_observations=text)
    else:
        obs = session.data_map.specialist_observations or ""
        session.data_map.specialist_observations = (obs + "\n" + text).strip()

    if "готов" in text.lower() and len(session.data_map.specialist_observations or "") > 50:
        session.progress.data_collection_complete = True
        # DATA_COLLECTION → ANALYSIS → SOCRATIC_DIALOGUE (two transitions)
        session.transition_to(SessionStateEnum.ANALYSIS)
        session.transition_to(SessionStateEnum.SOCRATIC_DIALOGUE)

        selection = select_next_question(session)
        session.progress.increment_dialogue_turns()

        await _save_session(db, session, "socratic_dialogue", state, chat_id, user_id)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "✅ Данные собраны.\n\n"
                "💬 <b>Сократовский диалог</b>\n\n"
                f"❓ {selection.question_text}"
            ),
            parse_mode="HTML",
        )
    else:
        await _save_session(db, session, "data_collection", state, chat_id, user_id)
        await bot.send_message(chat_id=chat_id, text="Принято. Продолжайте или напишите «готово».")


# ── Socratic dialogue ─────────────────────────────────────────────────────────

async def _handle_dialogue(
    bot: Bot, db: AsyncSession, text: str,
    session: SessionState, state: BotChatState,
    chat_id: int, user_id: int | None,
) -> None:
    # Clarification request → rephrase synchronously (no Claude needed)
    if _is_clarification_request(text):
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "Давайте конкретизирую вопрос:\n\n"
                "Подумайте о системе клиента и ответьте:\n"
                "• На каком слое (L0–L4) можно реально влиять?\n"
                "• Что можно изменить без коллапса?\n"
                "• С чего стоит начать?\n\n"
                "Отвечайте своими словами, думайте вслух."
            ),
        )
        return

    # Substantive response → persist current session, enqueue concept_hypothesis job.
    # Worker (app/worker/handlers/conceptualizator.py) extracts hypothesis via Claude,
    # checks should_continue_dialogue, and either asks the next question or triggers output.
    run_id_str = (state.state_payload or {}).get("run_id")
    if await is_job_pending_for_chat(db, bot_id="conceptualizator", chat_id=chat_id):
        await bot.send_message(chat_id, "⏳ Предыдущий запрос ещё обрабатывается, подождите...")
        return
    await _save_session(db, session, "socratic_dialogue", state, chat_id, user_id)
    await enqueue(
        db, "concept_hypothesis", BOT_ID, chat_id,
        payload={
            "session": session.model_dump(mode="json"),
            "message_text": text,
            "role": state.role or "specialist",
        },
        user_id=user_id, context_id=state.context_id, run_id=run_id_str,
    )
    await bot.send_message(chat_id=chat_id, text="⏳ Анализирую ответ...")


# ── Utility commands ───────────────────────────────────────────────────────────

async def _handle_status(
    bot: Bot, state: BotChatState | None, chat_id: int,
) -> None:
    if not state or not state.state_payload or not state.state_payload.get("session"):
        await bot.send_message(
            chat_id=chat_id,
            text="У вас нет активной сессии. Запустите через бот Pro.",
        )
        return

    session = _load_session(state)
    if not session:
        await bot.send_message(chat_id=chat_id, text="Не удалось загрузить сессию.")
        return

    total = len(session.get_active_hypotheses())
    managerial = len(session.get_managerial_hypotheses())
    type_counts: dict[str, int] = {}
    for h in session.get_active_hypotheses():
        type_counts[h.type.value] = type_counts.get(h.type.value, 0) + 1

    lines = [
        "📊 <b>Статус сессии</b>\n",
        f"Состояние: {session.state.value}",
        f"Диалог: {session.progress.dialogue_turns} вопросов\n",
        f"<b>Гипотезы: {total}</b>",
    ]
    for htype, cnt in type_counts.items():
        lines.append(f"  • {htype}: {cnt}")

    if session.can_proceed_to_output():
        lines.append("\n✅ Готово к формированию концептуализации!")
    elif managerial == 0:
        lines.append("\n⚠️ Нужна управленческая гипотеза")

    await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")


async def _handle_reset(
    bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if state:
        await upsert_chat_state(
            db,
            bot_id=BOT_ID,
            chat_id=chat_id,
            state="reset",
            state_payload={},
            user_id=user_id,
            role=state.role,
            context_id=state.context_id,
        )
    await bot.send_message(
        chat_id=chat_id,
        text="🔄 Сессия сброшена. Запустите новую через бот Pro.",
    )


async def _handle_help(bot: Bot, chat_id: int) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "🆘 <b>Справка — Conceptualizer</b>\n\n"
            "<b>Команды:</b>\n"
            "/status — статус текущей сессии\n"
            "/reset — сбросить сессию\n"
            "/help — эта справка\n\n"
            "<b>Как работает:</b>\n"
            "1. Сбор данных о клиенте (наблюдения по L0–L4)\n"
            "2. Сократовский диалог с извлечением гипотез\n"
            "3. Трёхслойная концептуализация (A/B/C) через Claude AI\n\n"
            "<b>Советы:</b>\n"
            "• Думайте вслух — отвечайте развёрнуто\n"
            "• Упоминайте слои (L0–L4)\n"
            "• Для управленческих гипотез используйте: «можно», «стоит начать с»"
        ),
        parse_mode="HTML",
    )
