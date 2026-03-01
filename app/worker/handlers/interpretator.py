"""
Worker handlers for Interpretator bot async jobs.

job_types:
  interp_photo   — Claude vision analysis of a drawing
  interp_intake  — INTAKE clarifying question / material evaluation
  interp_run     — Full JSON interpretation → .txt + .json files

job.payload keys (all types):
  state_payload  dict  — full current bot_chat_state.state_payload
  role           str   — "specialist" | "client"

interp_photo additionally:
  image_b64          str  — base64-encoded JPEG bytes
  image_media_type   str  — "image/jpeg"

interp_run additionally:
  run_mode   str  — "STANDARD" | "LOW_DATA" (retry in simplified mode)
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from app.config import settings
from app.models.job import Job
from app.services.artifacts import save_artifact
from app.services.interpreter.policy_engine import PolicyEngine
from app.services.interpreter.prompts import assemble_prompt
from app.services.interpreter.structured_results import (
    format_to_txt,
    validate_structured_results,
)
from app.services.job_queue import enqueue
from app.services.outbox import enqueue_message, make_document_payload
from app.webhooks.common import upsert_chat_state

logger = logging.getLogger(__name__)

BOT_ID = "interpretator"
_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
_MAX_TOKENS = 4000
_MAX_CLARIFICATION_ITERATIONS = 2
_MAX_REPAIR_ATTEMPTS = 2

_policy = PolicyEngine()

_VISION_PROMPT = (
    "Опишите этот рисунок для психологической интерпретации.\n\n"
    "Укажите:\n"
    "- Основные элементы и объекты\n"
    "- Композицию (расположение, размеры)\n"
    "- Цвета и их использование\n"
    "- Линии (чёткие, размытые, прерывистые)\n"
    "- Пространство (заполненность, пустоты)\n"
    "- Общее впечатление\n\n"
    "Описание должно быть феноменологическим, без интерпретаций."
)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_interp_photo(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """Analyse an image with Claude vision and update accumulated_material."""
    p = job.payload
    image_b64: str = p["image_b64"]
    image_media_type: str = p.get("image_media_type", "image/jpeg")
    state_payload = dict(p["state_payload"])

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_media_type,
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": _VISION_PROMPT},
            ],
        }],
    )
    description = resp.content[0].text

    state_payload.setdefault("accumulated_material", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content": f"[Рисунок]\n\n{description}",
        "type": "image_analysis",
    })
    state_payload["material_type"] = "drawing"

    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=job.chat_id, state="intake",
        state_payload=state_payload, user_id=job.user_id,
        role=p.get("role", "specialist"), context_id=job.context_id,
    )

    # Enqueue questions generation — description is stored internally, not shown to user.
    await enqueue(
        db, "interp_questions", BOT_ID, job.chat_id,
        payload={"state_payload": state_payload, "role": p.get("role", "specialist")},
        user_id=job.user_id, context_id=job.context_id, run_id=job.run_id,
        priority=3,
    )
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {
            "chat_id": job.chat_id,
            "text": "✓ Рисунок проанализирован. Подготавливаю уточняющие вопросы...",
        },
        job_id=job.job_id, seq=0,
    )


async def handle_interp_intake(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """
    Run Claude INTAKE prompt.
    If Claude asks a clarifying question → update state, enqueue question.
    If material is accepted → enqueue interp_run job.
    """
    p = job.payload
    state_payload = dict(p["state_payload"])

    run_id_str = str(job.run_id).replace("-", "")[:8] if job.run_id else ""
    session_id = f"int_{job.chat_id}_{run_id_str}" if run_id_str else f"int_{job.chat_id}"

    context = {
        "session_id": session_id,
        "mode": state_payload.get("mode", "STANDARD"),
        "iteration_count": state_payload.get("iteration_count", 0),
        "max_iterations": _MAX_CLARIFICATION_ITERATIONS,
        "material_type": state_payload.get("material_type", "unknown"),
        "completeness": state_payload.get("completeness", "unknown"),
    }
    system_prompt = assemble_prompt("INTAKE", context)
    last_message = state_payload["accumulated_material"][-1]["content"]

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": last_message}],
    )
    clean = _extract_message(resp.content[0].text)

    if "?" in clean and len(clean) < 200:
        # Claude asked a clarifying question — stay in intake
        await upsert_chat_state(
            db, bot_id=BOT_ID, chat_id=job.chat_id, state="intake",
            state_payload=state_payload, user_id=job.user_id,
            role=p.get("role", "specialist"), context_id=job.context_id,
        )
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {"chat_id": job.chat_id, "text": clean},
            job_id=job.job_id, seq=0,
        )
    else:
        # Material accepted — queue clarifying questions generation
        await enqueue(
            db, "interp_questions", BOT_ID, job.chat_id,
            payload={"state_payload": state_payload, "role": p.get("role", "specialist")},
            user_id=job.user_id, context_id=job.context_id, run_id=job.run_id,
            priority=3,
        )
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {"chat_id": job.chat_id, "text": "⏳ Подготавливаю уточняющие вопросы..."},
            job_id=job.job_id, seq=0,
        )


async def handle_interp_questions(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """
    Generate 3-4 clarifying questions from the accumulated symbolic material.

    Saves the questions list to state_payload, transitions FSM to
    'clarification_questions', and sends the first question to the specialist.
    If question generation fails entirely, falls back to enqueueing interp_run
    directly so the session is never silently stuck.
    """
    p = job.payload
    state_payload = dict(p["state_payload"])

    run_id_str = str(job.run_id).replace("-", "")[:8] if job.run_id else ""
    session_id = f"int_{job.chat_id}_{run_id_str}" if run_id_str else f"int_{job.chat_id}"

    context = {
        "session_id": session_id,
        "mode": state_payload.get("mode", "STANDARD"),
        "iteration_count": state_payload.get("iteration_count", 0),
        "max_iterations": _MAX_CLARIFICATION_ITERATIONS,
        "material_type": state_payload.get("material_type", "unknown"),
        "completeness": state_payload.get("completeness", "unknown"),
    }
    system_prompt = assemble_prompt("QUESTIONS_GENERATION", context)
    material_text = "\n\n".join(
        m["content"] for m in state_payload.get("accumulated_material", [])
    )

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=800,
        system=system_prompt,
        messages=[{"role": "user", "content": f"Символический материал:\n{material_text}"}],
    )
    questions = _extract_questions(resp.content[0].text)

    if not questions:
        # Fallback: if no questions generated, proceed directly to interpretation
        logger.warning("[worker/interp] questions generation returned empty — falling back to interp_run")
        await enqueue(
            db, "interp_run", BOT_ID, job.chat_id,
            payload={"state_payload": state_payload, "role": p.get("role", "specialist")},
            user_id=job.user_id, context_id=job.context_id, run_id=job.run_id,
            priority=3,
        )
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {"chat_id": job.chat_id, "text": "⏳ Формирую интерпретацию..."},
            job_id=job.job_id, seq=0,
        )
        return

    state_payload["questions"] = questions
    state_payload["question_index"] = 0
    state_payload.setdefault("clarification_qa", [])

    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=job.chat_id, state="clarification_questions",
        state_payload=state_payload, user_id=job.user_id,
        role=p.get("role", "specialist"), context_id=job.context_id,
    )

    total = len(questions)
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {
            "chat_id": job.chat_id,
            "text": (
                f"🔍 Перед интерпретацией — несколько уточняющих вопросов "
                f"(1 из {total}):\n\n{questions[0]}"
            ),
        },
        job_id=job.job_id, seq=0,
    )


async def handle_interp_run(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """
    Generate full JSON interpretation, validate/repair, send .txt + .json files,
    save artifact.
    """
    p = job.payload
    state_payload = dict(p["state_payload"])
    run_mode = p.get("run_mode", state_payload.get("mode", "STANDARD"))

    run_id_str = str(job.run_id).replace("-", "")[:8] if job.run_id else ""
    session_id = f"int_{job.chat_id}_{run_id_str}" if run_id_str else f"int_{job.chat_id}"

    context = {
        "session_id": session_id,
        "mode": run_mode,
        "iteration_count": state_payload.get("iteration_count", 0),
        "max_iterations": _MAX_CLARIFICATION_ITERATIONS,
        "material_type": state_payload.get("material_type", "unknown"),
        "completeness": state_payload.get("completeness", "unknown"),
    }
    prompt_state = "LOW_DATA_MODE" if run_mode == "LOW_DATA" else "INTERPRETATION_GENERATION"
    system_prompt = assemble_prompt(prompt_state, context)

    material_text = "\n\n".join(
        m["content"] for m in state_payload.get("accumulated_material", [])
    )

    # Build Q&A block from structured clarification_qa (new flow) or
    # plain clarifications_received list (legacy intake/clarification_loop flow).
    clarification_qa = state_payload.get("clarification_qa", [])
    clarifications = state_payload.get("clarifications_received", [])

    if clarification_qa:
        qa_lines = [
            f"В: {item['question']}\nО: {item['answer']}"
            for item in clarification_qa
        ]
        user_content = (
            f"Символический материал:\n{material_text}\n\n"
            f"Уточняющие вопросы и ответы специалиста:\n\n"
            + "\n\n".join(qa_lines)
            + "\n\nСоздайте структурированную интерпретацию в формате JSON."
        )
    elif clarifications:
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

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    response_text = resp.content[0].text

    # Extract JSON
    output = _extract_json(response_text)
    if output is None:
        if run_mode != "LOW_DATA":
            # Retry in LOW_DATA mode via a new job
            state_payload["mode"] = "LOW_DATA"
            state_payload["repair_attempts"] = state_payload.get("repair_attempts", 0) + 1
            await enqueue(
                db, "interp_run", BOT_ID, job.chat_id,
                payload={
                    "state_payload": state_payload,
                    "role": p.get("role", "specialist"),
                    "run_mode": "LOW_DATA",
                },
                user_id=job.user_id, context_id=job.context_id, run_id=job.run_id,
                priority=3,
            )
            await enqueue_message(
                db, BOT_ID, job.chat_id, "send_message",
                {
                    "chat_id": job.chat_id,
                    "text": "⚠ Не удалось разобрать ответ AI. Повторяю в упрощённом режиме...",
                },
                job_id=job.job_id, seq=0,
            )
        else:
            await enqueue_message(
                db, BOT_ID, job.chat_id, "send_message",
                {
                    "chat_id": job.chat_id,
                    "text": (
                        "❌ Критическая ошибка: AI не вернул структурированный результат.\n\n"
                        "Запустите новую сессию через Pro."
                    ),
                },
                job_id=job.job_id, seq=0,
            )
        return

    # Patch meta fields if Claude left them empty (session_id, timestamp, mode
    # are known to the worker but Claude may echo "string" or omit them).
    output.setdefault("meta", {})
    _meta = output["meta"]
    if not _meta.get("session_id") or _meta.get("session_id") == "string":
        _meta["session_id"] = session_id
    if not _meta.get("timestamp"):
        _meta["timestamp"] = datetime.now(timezone.utc).isoformat()
    if not _meta.get("mode"):
        _meta["mode"] = run_mode
    if "iteration_count" not in _meta:
        _meta["iteration_count"] = context["iteration_count"]

    # PolicyEngine: validate → repair
    validation = _policy.validate(output)
    if not validation["valid"]:
        repair_attempts = state_payload.get("repair_attempts", 0)
        if repair_attempts < _MAX_REPAIR_ATTEMPTS:
            output, _ = _policy.repair(output, validation)

    # Schema validation — optional fields are injected with defaults inside the call.
    # Only block delivery if truly essential fields (hypotheses / summary) are absent.
    valid, errors = validate_structured_results(output)
    if not valid:
        fatal = [
            e for e in errors
            if "interpretative_hypotheses" in e or "phenomenological_summary" in e
        ]
        if fatal:
            logger.error("[worker/interp] fatal validation failure: %s", fatal)
            await enqueue_message(
                db, BOT_ID, job.chat_id, "send_message",
                {
                    "chat_id": job.chat_id,
                    "text": "⚠ Ошибка структуры результата. Запустите новую сессию через Pro.",
                },
                job_id=job.job_id, seq=0,
            )
            return
        # Non-fatal issues — log and continue delivering the result
        logger.warning("[worker/interp] structure validation warnings (proceeding): %s", errors)

    # Format and enqueue documents
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_name = f"interpretation_{session_id}_{timestamp}"

    txt_bytes = format_to_txt(output).encode("utf-8")
    json_bytes = json.dumps(output, indent=2, ensure_ascii=False).encode("utf-8")

    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {"chat_id": job.chat_id, "text": "✅ Интерпретация завершена!"},
        job_id=job.job_id, seq=0,
    )
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_document",
        make_document_payload(job.chat_id, txt_bytes, f"{base_name}.txt", "📄 Результаты интерпретации"),
        job_id=job.job_id, seq=1,
    )
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_document",
        make_document_payload(job.chat_id, json_bytes, f"{base_name}.json", "📋 Структурированные данные (JSON)"),
        job_id=job.job_id, seq=2,
    )
    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {"chat_id": job.chat_id, "text": "Сессия завершена. Запустите новую через бот Pro."},
        job_id=job.job_id, seq=3,
    )

    # Update FSM state
    final_payload = dict(state_payload)
    final_payload["mode"] = run_mode
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=job.chat_id, state="completed",
        state_payload=final_payload, user_id=job.user_id,
        role=p.get("role", "specialist"), context_id=job.context_id,
    )

    # Save artifact
    _raw_type = state_payload.get("material_type", "")
    material_type = _raw_type if _raw_type and _raw_type != "unknown" else "текст"
    _raw_comp = state_payload.get("completeness", "")
    # completeness is never updated in the Phase-7+ flow — material reached interp_run
    # only when accepted, so "sufficient" is always accurate at this point.
    completeness = _raw_comp if _raw_comp and _raw_comp != "unknown" else "sufficient"
    await save_artifact(
        db=db,
        run_id=job.run_id,
        service_id=BOT_ID,
        context_id=job.context_id,
        specialist_telegram_id=job.user_id or job.chat_id,
        payload={
            "meta": {
                "material_type": material_type,
                "completeness": completeness,
                "mode": run_mode,
                "iteration_count": state_payload.get("iteration_count", 0),
            },
            "txt_report": txt_bytes.decode("utf-8"),
            "structured": output,
        },
        summary=f"Интерпретация: {material_type}. Полнота: {completeness}.",
    )
    logger.info("[worker/interp] interpretation complete chat=%s", job.chat_id)


# ── Utilities (shared with webhook handler until full migration) ───────────────

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


def _extract_questions(response_text: str) -> list[str]:
    """Extract the questions list from Claude's QUESTIONS_GENERATION response."""
    try:
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            json_str = response_text[start:response_text.find("```", start)].strip()
        elif "{" in response_text:
            json_str = response_text[response_text.find("{"):response_text.rfind("}") + 1]
        else:
            return []
        data = json.loads(json_str)
        questions = data.get("questions", [])
        return [str(q).strip() for q in questions if q][:4]
    except Exception:
        return []


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
            diff = json_str.count("{") - json_str.count("}")
            if diff > 0:
                json_str += "}" * diff
            last_comma = json_str.rfind(",")
            if last_comma > 0:
                json_str = json_str[:last_comma] + "\n}"
            return json.loads(json_str)
    except Exception:
        return None
