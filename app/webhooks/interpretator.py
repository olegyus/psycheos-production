"""
Webhook handler for Interpretator bot (Phase 4).

State machine (bot_chat_state.state):
  active            — session opened via /start {jti}, awaiting first material
  intake            — Claude asked a clarifying question in INTAKE; awaiting response
  clarification_loop — material is partial/fragmentary; Claude asks phenomenological questions
  completed         — interpretation sent; session closed

state_payload keys:
  run_id, mode, iteration_count, repair_attempts,
  material_type, completeness, accumulated_material[], clarifications_received[]
"""
import base64
import io
import json
import logging
from datetime import datetime, timezone

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, InputFile, Update

from app.config import settings
from app.models.bot_chat_state import BotChatState
from app.services.interpreter.policy_engine import PolicyEngine
from app.services.interpreter.prompts import assemble_prompt
from app.services.interpreter.structured_results import (
    format_to_txt,
    validate_structured_results,
)
from app.services.links import LinkVerifyError, verify_link
from app.webhooks.common import upsert_chat_state

logger = logging.getLogger(__name__)

BOT_ID = "interpretator"
_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
_MAX_TOKENS = 4000
_MAX_CLARIFICATION_ITERATIONS = 2
_MAX_REPAIR_ATTEMPTS = 2

_policy = PolicyEngine()


# ── Entry point ───────────────────────────────────────────────────────────────

async def handle_interpretator(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    msg = update.message
    if not msg:
        return

    if msg.text and msg.text.startswith("/start"):
        parts = msg.text.split(" ", 1)
        if len(parts) == 2 and parts[1].strip():
            await _start_session(bot, db, chat_id, user_id, parts[1].strip())
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ Запустите инструмент через бот Pro.",
            )
        return

    if msg.photo:
        await _handle_photo(bot, db, msg, state, chat_id, user_id)
        return

    if msg.text:
        await _handle_text(bot, db, msg.text, state, chat_id, user_id)


# ── Session start ─────────────────────────────────────────────────────────────

async def _start_session(
    bot: Bot, db: AsyncSession,
    chat_id: int, user_id: int | None, raw_token: str,
) -> None:
    """Verify link token and (re)initialise FSM. Resets any existing session."""
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

    await upsert_chat_state(
        db,
        bot_id=BOT_ID,
        chat_id=chat_id,
        state="active",
        state_payload={
            "run_id": str(token.run_id),
            "mode": "STANDARD",
            "iteration_count": 0,
            "repair_attempts": 0,
            "material_type": "unknown",
            "completeness": "unknown",
            "accumulated_material": [],
            "clarifications_received": [],
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
            "🧠 <b>PsycheOS Interpreter</b>\n\n"
            "Сессия открыта.\n\n"
            "Отправьте описание символического материала:\n"
            "• Сон\n"
            "• Рисунок (текстом или изображением)\n"
            "• Проективный образ"
        ),
        parse_mode="HTML",
    )


# ── Text handling ─────────────────────────────────────────────────────────────

async def _handle_text(
    bot: Bot, db: AsyncSession, text: str,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if state is None or state.state not in ("active", "intake", "clarification_loop"):
        if state and state.state == "completed":
            await bot.send_message(
                chat_id=chat_id,
                text="Сессия завершена. Запустите новую через бот Pro.",
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="Для запуска используйте ссылку из бота Pro.",
            )
        return

    payload = dict(state.state_payload or {})
    payload.setdefault("accumulated_material", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content": text,
    })
    # When specialist answers any clarifying question, record it separately
    if state.state in ("intake", "clarification_loop"):
        payload.setdefault("clarifications_received", []).append(text)

    await bot.send_chat_action(chat_id=chat_id, action="typing")
    if state.state == "clarification_loop":
        await _run_clarification_loop(bot, db, payload, state, chat_id, user_id)
    else:
        await _run_intake(bot, db, payload, state, chat_id, user_id)


# ── Photo handling ────────────────────────────────────────────────────────────

async def _handle_photo(
    bot: Bot, db: AsyncSession, msg,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if state is None or state.state not in ("active", "intake"):
        await bot.send_message(
            chat_id=chat_id,
            text="Для запуска используйте ссылку из бота Pro.",
        )
        return

    await bot.send_message(chat_id=chat_id, text="📸 Изображение получено. Анализирую рисунок...")
    await bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        file_obj = await bot.get_file(msg.photo[-1].file_id)
        photo_bytes = await file_obj.download_as_bytearray()
        photo_b64 = base64.b64encode(photo_bytes).decode()

        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        vision_resp = await client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": photo_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Опишите этот рисунок для психологической интерпретации.\n\n"
                            "Укажите:\n"
                            "- Основные элементы и объекты\n"
                            "- Композицию (расположение, размеры)\n"
                            "- Цвета и их использование\n"
                            "- Линии (чёткие, размытые, прерывистые)\n"
                            "- Пространство (заполненность, пустоты)\n"
                            "- Общее впечатление\n\n"
                            "Описание должно быть феноменологическим, без интерпретаций."
                        ),
                    },
                ],
            }],
        )
        description = vision_resp.content[0].text

        payload = dict(state.state_payload or {})
        payload.setdefault("accumulated_material", []).append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content": f"[Рисунок]\n\n{description}",
            "type": "image_analysis",
        })
        payload["material_type"] = "drawing"

        await upsert_chat_state(
            db, bot_id=BOT_ID, chat_id=chat_id, state="intake",
            state_payload=payload, user_id=user_id,
            role=state.role, context_id=state.context_id,
        )
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"✓ Рисунок проанализирован:\n\n{description}\n\n"
                "Добавьте контекст от себя или напишите «продолжить» для интерпретации."
            ),
        )
    except Exception:
        logger.exception(f"[{BOT_ID}] Vision API error user={user_id}")
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Ошибка при обработке изображения. Попробуйте описать рисунок текстом.",
        )


# ── INTAKE orchestration ──────────────────────────────────────────────────────

async def _run_intake(
    bot: Bot, db: AsyncSession, payload: dict,
    state: BotChatState, chat_id: int, user_id: int | None,
) -> None:
    """
    Call Claude with INTAKE prompt.
    If Claude returns a short clarifying question → stay in `intake`.
    Otherwise → interpret immediately.
    """
    context = {
        "session_id": _session_id(state),
        "mode": payload.get("mode", "STANDARD"),
        "iteration_count": payload.get("iteration_count", 0),
        "max_iterations": _MAX_CLARIFICATION_ITERATIONS,
        "material_type": payload.get("material_type", "unknown"),
        "completeness": payload.get("completeness", "unknown"),
    }
    system_prompt = assemble_prompt("INTAKE", context)
    last_message = payload["accumulated_material"][-1]["content"]

    try:
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": last_message}],
        )
        clean = _extract_message(resp.content[0].text)
    except Exception:
        logger.exception(f"[{BOT_ID}] Claude INTAKE error user={user_id}")
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Ошибка при обращении к AI. Попробуйте ещё раз.",
        )
        return

    if "?" in clean and len(clean) < 200:
        # Claude asked a clarifying question — persist material, stay in intake
        await upsert_chat_state(
            db, bot_id=BOT_ID, chat_id=chat_id, state="intake",
            state_payload=payload, user_id=user_id,
            role=state.role, context_id=state.context_id,
        )
        await bot.send_message(chat_id=chat_id, text=clean)
    else:
        # Material accepted — assess completeness before interpreting
        await _run_material_check(bot, db, payload, state, chat_id, user_id)


# ── Material check ────────────────────────────────────────────────────────────

async def _run_material_check(
    bot: Bot, db: AsyncSession, payload: dict,
    state: BotChatState, chat_id: int, user_id: int | None,
) -> None:
    """
    Call Claude with MATERIAL_CHECK_PROMPT to assess completeness.
    Sets payload['completeness'] to 'sufficient' / 'partial' / 'fragmentary'.
    Routes to _run_interpretation or _run_clarification_loop accordingly.
    """
    context = {
        "session_id": _session_id(state),
        "mode": payload.get("mode", "STANDARD"),
        "iteration_count": payload.get("iteration_count", 0),
        "max_iterations": _MAX_CLARIFICATION_ITERATIONS,
        "material_type": payload.get("material_type", "unknown"),
        "completeness": payload.get("completeness", "unknown"),
    }
    # Append JSON output instruction so we can reliably parse the verdict
    system_prompt = (
        assemble_prompt("MATERIAL_CHECK", context)
        + '\n\nReturn JSON: {"completeness": "sufficient|partial|fragmentary",'
        ' "message": "brief statement or clarifying question for the specialist"}'
    )
    material_text = "\n\n".join(
        m["content"] for m in payload.get("accumulated_material", [])
    )

    try:
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": material_text}],
        )
        response_text = resp.content[0].text
    except Exception:
        logger.exception(f"[{BOT_ID}] Claude MATERIAL_CHECK error user={user_id}")
        # Fall back to interpretation so session doesn't get stuck
        await _run_interpretation(bot, db, payload, state, chat_id, user_id)
        return

    # Parse completeness verdict (JSON first, keyword fallback)
    completeness = _parse_completeness(response_text)
    user_message = _extract_message(response_text)

    payload["completeness"] = completeness
    logger.info(f"[{BOT_ID}] material_check completeness={completeness} user={user_id}")

    if completeness == "sufficient":
        await _run_interpretation(bot, db, payload, state, chat_id, user_id)
    else:
        # Partial/fragmentary — enter clarification loop
        payload.setdefault("iteration_count", 0)
        await upsert_chat_state(
            db, bot_id=BOT_ID, chat_id=chat_id, state="clarification_loop",
            state_payload=payload, user_id=user_id,
            role=state.role, context_id=state.context_id,
        )
        if user_message:
            await bot.send_message(chat_id=chat_id, text=user_message)


def _parse_completeness(response_text: str) -> str:
    """Extract completeness verdict from MATERIAL_CHECK response. Falls back to keywords."""
    j = _extract_json(response_text)
    if j and j.get("completeness") in ("sufficient", "partial", "fragmentary"):
        return j["completeness"]
    lower = response_text.lower()
    if "fragmentary" in lower or "фрагментарн" in lower:
        return "fragmentary"
    if "partial" in lower or "частичн" in lower:
        return "partial"
    return "sufficient"


# ── Clarification loop ─────────────────────────────────────────────────────────

async def _run_clarification_loop(
    bot: Bot, db: AsyncSession, payload: dict,
    state: BotChatState, chat_id: int, user_id: int | None,
) -> None:
    """
    Call Claude with CLARIFICATION_LOOP_PROMPT to ask one phenomenological question.
    Tracks iteration_count; when limit reached → proceed to interpretation.
    """
    iteration_count = payload.get("iteration_count", 0)

    if iteration_count >= _MAX_CLARIFICATION_ITERATIONS:
        logger.info(f"[{BOT_ID}] clarification_loop max iterations reached user={user_id}")
        await bot.send_message(
            chat_id=chat_id,
            text="⏳ Формирую интерпретацию на основе имеющихся данных...",
        )
        await _run_interpretation(bot, db, payload, state, chat_id, user_id)
        return

    context = {
        "session_id": _session_id(state),
        "mode": payload.get("mode", "STANDARD"),
        "iteration_count": iteration_count,
        "max_iterations": _MAX_CLARIFICATION_ITERATIONS,
        "material_type": payload.get("material_type", "unknown"),
        "completeness": payload.get("completeness", "unknown"),
    }
    system_prompt = assemble_prompt("CLARIFICATION_LOOP", context)

    material_text = "\n\n".join(
        m["content"] for m in payload.get("accumulated_material", [])
    )
    clarifications = payload.get("clarifications_received", [])
    if clarifications:
        clar_block = "\n".join(f"- {c}" for c in clarifications)
        user_content = (
            f"Символический материал:\n{material_text}\n\n"
            f"Полученные уточнения:\n{clar_block}"
        )
    else:
        user_content = f"Символический материал:\n{material_text}"

    try:
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=200,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        question = resp.content[0].text.strip()
    except Exception:
        logger.exception(f"[{BOT_ID}] Claude CLARIFICATION_LOOP error user={user_id}")
        await _run_interpretation(bot, db, payload, state, chat_id, user_id)
        return

    payload["iteration_count"] = iteration_count + 1
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state="clarification_loop",
        state_payload=payload, user_id=user_id,
        role=state.role, context_id=state.context_id,
    )
    await bot.send_message(chat_id=chat_id, text=question)


# ── Interpretation ────────────────────────────────────────────────────────────

async def _run_interpretation(
    bot: Bot, db: AsyncSession, payload: dict,
    state: BotChatState, chat_id: int, user_id: int | None,
) -> None:
    """
    Call Claude for full JSON interpretation.
    Validate/repair with PolicyEngine, then send TXT + JSON as documents.
    """
    await bot.send_message(chat_id=chat_id, text="⏳ Формирую интерпретацию...")
    await bot.send_chat_action(chat_id=chat_id, action="upload_document")

    mode = payload.get("mode", "STANDARD")
    context = {
        "session_id": _session_id(state),
        "mode": mode,
        "iteration_count": payload.get("iteration_count", 0),
        "max_iterations": _MAX_CLARIFICATION_ITERATIONS,
        "material_type": payload.get("material_type", "unknown"),
        "completeness": payload.get("completeness", "unknown"),
    }
    prompt_state = "LOW_DATA_MODE" if mode == "LOW_DATA" else "INTERPRETATION_GENERATION"
    system_prompt = assemble_prompt(prompt_state, context)

    material_text = "\n\n".join(
        m["content"] for m in payload.get("accumulated_material", [])
    )
    clarifications = payload.get("clarifications_received", [])
    if clarifications:
        clar_block = "\n".join(f"- {c}" for c in clarifications)
        user_content = (
            f"Символический материал:\n{material_text}\n\n"
            f"Полученные уточнения:\n{clar_block}\n\n"
            "Создайте структурированную интерпретацию в формате JSON."
        )
    else:
        user_content = (
            f"Символический материал:\n{material_text}\n\n"
            "Создайте структурированную интерпретацию в формате JSON."
        )

    try:
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        response_text = resp.content[0].text
    except Exception:
        logger.exception(f"[{BOT_ID}] Claude interpretation error user={user_id}")
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Ошибка AI при интерпретации. Попробуйте позже.",
        )
        return

    # --- Extract JSON ---
    output = _extract_json(response_text)
    if output is None:
        if mode != "LOW_DATA":
            payload["mode"] = "LOW_DATA"
            payload["repair_attempts"] = payload.get("repair_attempts", 0) + 1
            await bot.send_message(
                chat_id=chat_id,
                text="⚠ Не удалось разобрать ответ AI. Повторяю в упрощённом режиме...",
            )
            await _run_interpretation(bot, db, payload, state, chat_id, user_id)
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "❌ Критическая ошибка: AI не вернул структурированный результат.\n\n"
                    "Запустите новую сессию через Pro."
                ),
            )
        return

    # --- PolicyEngine: validate → repair ---
    validation = _policy.validate(output)
    if not validation["valid"]:
        repair_attempts = payload.get("repair_attempts", 0)
        if repair_attempts < _MAX_REPAIR_ATTEMPTS:
            output, _ = _policy.repair(output, validation)
            payload["repair_attempts"] = repair_attempts + 1
            logger.info(f"[{BOT_ID}] PolicyEngine repair applied user={user_id}")

    # --- Schema validation ---
    valid, errors = validate_structured_results(output)
    if not valid:
        logger.warning(f"[{BOT_ID}] Structure validation failed: {errors} user={user_id}")
        await bot.send_message(
            chat_id=chat_id,
            text="⚠ Ошибка структуры результата. Запустите новую сессию через Pro.",
        )
        return

    # --- Format and send files ---
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_name = f"interpretation_{_session_id(state)}_{timestamp}"

    txt_bytes = format_to_txt(output).encode("utf-8")
    json_bytes = json.dumps(output, indent=2, ensure_ascii=False).encode("utf-8")

    await bot.send_message(chat_id=chat_id, text="✅ Интерпретация завершена!")
    await bot.send_document(
        chat_id=chat_id,
        document=InputFile(io.BytesIO(txt_bytes), filename=f"{base_name}.txt"),
        caption="📄 Результаты интерпретации",
    )
    await bot.send_document(
        chat_id=chat_id,
        document=InputFile(io.BytesIO(json_bytes), filename=f"{base_name}.json"),
        caption="📋 Структурированные данные (JSON)",
    )

    # --- Mark session completed ---
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state="completed",
        state_payload=payload, user_id=user_id,
        role=state.role, context_id=state.context_id,
    )
    logger.info(f"[{BOT_ID}] Interpretation complete user={user_id}")
    await bot.send_message(
        chat_id=chat_id,
        text="Сессия завершена. Запустите новую через бот Pro.",
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_id(state: BotChatState) -> str:
    run_id = (state.state_payload or {}).get("run_id", "")
    short = run_id.replace("-", "")[:8] if run_id else ""
    return f"int_{state.chat_id}_{short}" if short else f"int_{state.chat_id}"


def _extract_message(response_text: str) -> str:
    """Extract user-facing text from Claude response (may be JSON or plain text)."""
    try:
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            json_str = response_text[start:response_text.find("```", start)].strip()
        elif "{" in response_text:
            json_str = response_text[response_text.find("{"):response_text.rfind("}") + 1]
        else:
            return response_text

        data = json.loads(json_str)
        for key in ("clarifying_question", "message", "question"):
            if data.get(key):
                return str(data[key])
        if isinstance(data.get("acknowledgment"), dict):
            return str(data["acknowledgment"].get("text", response_text))
        if isinstance(data.get("acknowledgment"), str):
            return data["acknowledgment"]
        if isinstance(data.get("text"), str):
            return data["text"]
        return response_text
    except Exception:
        return response_text


def _extract_json(response_text: str) -> dict | None:
    """Extract and parse JSON from Claude response; attempt repair on truncation."""
    try:
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            json_str = response_text[start:response_text.find("```", start)].strip()
        elif "{" in response_text:
            json_str = response_text[response_text.find("{"):response_text.rfind("}") + 1]
        else:
            return None

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Attempt basic repair: close unclosed braces
            diff = json_str.count("{") - json_str.count("}")
            if diff > 0:
                json_str += "}" * diff
            last_comma = json_str.rfind(",")
            if last_comma > 0:
                json_str = json_str[:last_comma] + "\n}"
            return json.loads(json_str)
    except Exception:
        return None
