"""
Worker handlers for Simulator bot async jobs.

job_types:
  sim_launch        — initialise built-in case session (first client replica)
  sim_launch_custom — initialise practice/custom case session
  sim_report        — generate final analytical report → .docx + TSI/CCI

job.payload keys for sim_launch:
  case_key      str   — "1" | "2" | "3"
  goal          str   — SessionGoal.value
  mode          str   — "TRAINING" | "PRACTICE"
  crisis        str   — CrisisFlag.value
  state_payload dict  — existing bot_chat_state.state_payload (has run_id etc.)
  role          str   — "specialist"

job.payload keys for sim_launch_custom:
  custom_data   str   — uploaded case text
  goal          str
  mode          str
  crisis_value  str   — CrisisFlag.value
  state_payload dict
  role          str

job.payload keys for sim_report:
  session       dict  — SessionData.model_dump(mode="json")
  state_payload dict  — full current state_payload (for profile)
  role          str
"""
import logging
import re
from datetime import datetime
from typing import Optional

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from app.config import settings
from app.models.job import Job
from app.services.artifacts import save_artifact
from app.services.outbox import enqueue_message, make_document_payload
from app.services.simulator.cases import BUILTIN_CASES
from app.services.simulator.formatter import (
    _escape_html,
    build_iteration_log,
    format_for_telegram,
    format_intro,
    parse_claude_response,
)
from app.services.simulator.goals import GOAL_LABELS, MODE_LABELS
from app.services.simulator.report_generator import generate_report_docx
from app.services.simulator.schemas import (
    CrisisFlag, SessionData, SessionGoal, SessionMode,
    SpecialistProfile, TSIComponents, CCIComponents,
)
from app.services.simulator.system_prompt import build_system_prompt
from app.webhooks.common import upsert_chat_state

logger = logging.getLogger(__name__)

BOT_ID = "simulator"
_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
_MAX_SESSION_HISTORY = 50


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_sim_turn(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """Process one specialist message during an active simulation session.

    job.payload keys:
      text          str   — specialist's message text
      state_payload dict  — current state_payload (must contain 'session')
      role          str   — "specialist"
    """
    p = job.payload
    text: str = p["text"]
    state_payload = dict(p["state_payload"])

    if "session" not in state_payload:
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {"chat_id": job.chat_id, "text": "❌ Данные сессии не найдены. Запустите через /start."},
            job_id=job.job_id, seq=0,
        )
        return

    session_data = SessionData.model_validate(state_payload["session"])
    system_prompt = _get_system_prompt(state_payload, session_data)

    session_data.messages.append({"role": "user", "content": text})
    if len(session_data.messages) > _MAX_SESSION_HISTORY:
        session_data.messages = (
            session_data.messages[:1]
            + session_data.messages[-(_MAX_SESSION_HISTORY - 1):]
        )

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        resp = await client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=session_data.messages,
        )
        claude_response = resp.content[0].text
    except Exception:
        logger.exception("[worker/sim] Claude error in sim_turn chat=%s", job.chat_id)
        session_data.messages.pop()
        new_payload = {**state_payload, "session": session_data.model_dump(mode="json")}
        await upsert_chat_state(
            db, bot_id=BOT_ID, chat_id=job.chat_id, state="active",
            state_payload=new_payload, user_id=job.user_id,
            context_id=job.context_id,
        )
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {"chat_id": job.chat_id, "text": "❌ Ошибка при обращении к Claude. Попробуйте ещё раз."},
            job_id=job.job_id, seq=0,
        )
        return

    session_data.messages.append({"role": "assistant", "content": claude_response})
    parsed = parse_claude_response(claude_response)
    if parsed.signal:
        session_data.signal_log.append(parsed.signal)
    if parsed.fsm_state:
        session_data.fsm_log.append(parsed.fsm_state)

    replica_id = len(session_data.iteration_log) + 1
    iteration = build_iteration_log(parsed=parsed, replica_id=replica_id, specialist_input=text)
    session_data.iteration_log.append(iteration)

    new_payload = {**state_payload, "session": session_data.model_dump(mode="json")}
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=job.chat_id, state="active",
        state_payload=new_payload, user_id=job.user_id,
        context_id=job.context_id,
    )

    formatted = format_for_telegram(parsed)
    seq = 0
    if len(formatted) > 4000:
        client_msg = f"🗣 <b>Клиент:</b>\n{_escape_html(parsed.client_text)}"
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {"chat_id": job.chat_id, "text": client_msg, "parse_mode": "HTML"},
            job_id=job.job_id, seq=seq,
        )
        seq += 1
        if parsed.supervisor_block:
            sup_msg = f"{'─' * 30}\n{_escape_html(parsed.supervisor_block)}"
            await enqueue_message(
                db, BOT_ID, job.chat_id, "send_message",
                {"chat_id": job.chat_id, "text": sup_msg, "parse_mode": "HTML"},
                job_id=job.job_id, seq=seq,
            )
    else:
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {"chat_id": job.chat_id, "text": formatted, "parse_mode": "HTML"},
            job_id=job.job_id, seq=seq,
        )


async def handle_sim_launch(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """Initialise a built-in case session and send the first client replica."""
    p = job.payload
    state_payload = dict(p["state_payload"])

    case_key = str(p.get("case_key", "1"))
    case = BUILTIN_CASES.get(case_key, list(BUILTIN_CASES.values())[0])
    goal = SessionGoal(p["goal"])
    mode = SessionMode(p["mode"])

    system_prompt = build_system_prompt(case, goal, mode)
    session_data = SessionData(
        user_id=job.user_id or 0,
        case_id=case.case_id,
        case_name=case.case_name,
        mode=mode,
        session_goal=goal,
        crisis_flag=case.crisis_flag,
    )

    first_msg = (
        "Сессия начинается. Клиент входит в кабинет. "
        "Сгенерируй первую реплику клиента и начальный блок супервизора."
    )
    session_data.messages.append({"role": "user", "content": first_msg})

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=session_data.messages,
    )
    claude_response = resp.content[0].text

    session_data.messages.append({"role": "assistant", "content": claude_response})
    parsed = parse_claude_response(claude_response)
    if parsed.signal:
        session_data.signal_log.append(parsed.signal)
    if parsed.fsm_state:
        session_data.fsm_log.append(parsed.fsm_state)
    iteration = build_iteration_log(parsed=parsed, replica_id=1, specialist_input=first_msg)
    session_data.iteration_log.append(iteration)

    goal_label = GOAL_LABELS.get(goal, goal.value)
    mode_label = MODE_LABELS.get(mode.value, mode.value)
    client_info = f"{case.client.gender}, {case.client.age} лет"
    formatted = format_intro(
        case_name=case.case_name,
        client_info=client_info,
        crisis=case.crisis_flag.value,
        goal=goal_label,
        mode=mode_label,
        first_reply=claude_response,
        cci=case.cci.cci,
    )

    new_payload = {
        **state_payload,
        "session": session_data.model_dump(mode="json"),
        "setup_step": None,
    }
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=job.chat_id, state="active",
        state_payload=new_payload, user_id=job.user_id,
        context_id=job.context_id,
    )

    for seq, chunk in enumerate(_split_text(formatted)):
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {"chat_id": job.chat_id, "text": chunk, "parse_mode": "HTML"},
            job_id=job.job_id, seq=seq,
        )


async def handle_sim_launch_custom(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """Initialise a PRACTICE session with specialist-uploaded case data."""
    p = job.payload
    state_payload = dict(p["state_payload"])

    custom_data: str = p["custom_data"]
    goal = SessionGoal(p["goal"])
    mode = SessionMode(p["mode"])
    crisis = CrisisFlag(p.get("crisis_value", "NONE"))

    # Build a placeholder case for system prompt scaffolding
    placeholder_case = _build_placeholder_case(crisis)
    system_prompt = build_system_prompt(placeholder_case, goal, mode)
    custom_block = (
        "\n\n═══════════════════════════════════════════\n"
        "ДАННЫЕ КЛИЕНТА (загружены специалистом):\n"
        "═══════════════════════════════════════════\n"
        f"{custom_data}\n"
        "═══════════════════════════════════════════\n"
        "Используй ЭТИ данные как основу для симуляции. "
        "Извлеки из них Screen-профиль, L0–L4, Layer A/B/C и все остальные параметры. "
        "Если данные неполные — заполни пробелы логически на основе имеющегося.\n"
    )
    full_system_prompt = system_prompt + custom_block

    session_data = SessionData(
        user_id=job.user_id or 0,
        case_id="CUSTOM",
        case_name="Пользовательский кейс",
        mode=mode,
        session_goal=goal,
        crisis_flag=crisis,
    )
    first_msg = (
        "Сессия начинается. Клиент входит в кабинет. "
        "Сгенерируй первую реплику клиента и начальный блок супервизора."
    )
    session_data.messages.append({"role": "user", "content": first_msg})

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=2048,
        system=full_system_prompt,
        messages=session_data.messages,
    )
    claude_response = resp.content[0].text

    session_data.messages.append({"role": "assistant", "content": claude_response})
    parsed = parse_claude_response(claude_response)
    if parsed.signal:
        session_data.signal_log.append(parsed.signal)
    if parsed.fsm_state:
        session_data.fsm_log.append(parsed.fsm_state)
    iteration = build_iteration_log(parsed=parsed, replica_id=1, specialist_input=first_msg)
    session_data.iteration_log.append(iteration)

    goal_label = GOAL_LABELS.get(goal, goal.value)
    mode_label = MODE_LABELS.get(mode.value, mode.value)
    formatted = format_intro(
        case_name="Пользовательский кейс",
        client_info="Данные клиента загружены специалистом",
        crisis=crisis.value,
        goal=goal_label,
        mode=mode_label,
        first_reply=claude_response,
        cci=None,
    )

    new_payload = {
        **state_payload,
        "session": session_data.model_dump(mode="json"),
        "custom_prompt": full_system_prompt,
        "setup_step": None,
    }
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=job.chat_id, state="active",
        state_payload=new_payload, user_id=job.user_id,
        context_id=job.context_id,
    )

    for seq, chunk in enumerate(_split_text(formatted)):
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_message",
            {"chat_id": job.chat_id, "text": chunk, "parse_mode": "HTML"},
            job_id=job.job_id, seq=seq,
        )


async def handle_sim_report(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """
    Generate the final analytical report, compute TSI/CCI, produce .docx,
    save artifact, update FSM state.
    """
    p = job.payload
    session_data = SessionData.model_validate(p["session"])
    state_payload = dict(p["state_payload"])

    system_prompt = _get_system_prompt(state_payload, session_data)

    end_messages = list(session_data.messages) + [{"role": "user", "content": "/end"}]

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=end_messages,
    )
    report_text = resp.content[0].text

    tsi = _parse_tsi_from_report(report_text)
    session_data.tsi = tsi
    cci = _get_cci(session_data.case_id)
    specialist_profile = _update_profile(state_payload, job.user_id, session_data, tsi)

    goal_label = GOAL_LABELS.get(session_data.session_goal, session_data.session_goal.value)
    mode_label = MODE_LABELS.get(session_data.mode.value, session_data.mode.value)

    tsi_text = f"TSI: {tsi.tsi:.2f} ({tsi.interpretation})" if tsi else "TSI: н/д"
    cci_text = f"CCI: {cci.cci:.2f}" if cci else ""

    # Try docx generation
    seq = 0
    try:
        docx_buf = generate_report_docx(
            report_text=report_text,
            case_name=session_data.case_name,
            case_id=session_data.case_id,
            session_goal=goal_label,
            mode=mode_label,
            crisis_flag=session_data.crisis_flag.value,
            signal_log=session_data.signal_log,
            fsm_log=session_data.fsm_log,
            iteration_log=session_data.iteration_log,
            tsi=tsi,
            cci=cci,
            specialist_profile=specialist_profile,
        )
        exchanges = len(session_data.iteration_log)
        greens = session_data.signal_log.count("🟢")
        yellows = session_data.signal_log.count("🟡")
        reds = session_data.signal_log.count("🔴")
        caption = (
            f"📋 <b>Аналитический отчёт v1.1</b>\n\n"
            f"Кейс: {_escape_html(session_data.case_name)}\n"
            f"Реплик: {exchanges} | 🟢{greens} 🟡{yellows} 🔴{reds}\n"
            f"📊 {tsi_text}"
        )
        if cci_text:
            caption += f" | {cci_text}"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"PsycheOS_Report_{session_data.case_id}_{timestamp}.docx"
        await enqueue_message(
            db, BOT_ID, job.chat_id, "send_document",
            make_document_payload(job.chat_id, docx_buf.getvalue(), filename, caption, "HTML"),
            job_id=job.job_id, seq=seq,
        )
        seq += 1
    except Exception as exc:
        logger.error("[worker/sim] docx generation failed: %s", exc)
        # Fall back to plain text report
        fallback_header = f"📊 {tsi_text}\n\n"
        fallback_text = fallback_header + report_text
        for chunk in _split_text(fallback_text):
            await enqueue_message(
                db, BOT_ID, job.chat_id, "send_message",
                {"chat_id": job.chat_id, "text": chunk},
                job_id=job.job_id, seq=seq,
            )
            seq += 1

    await enqueue_message(
        db, BOT_ID, job.chat_id, "send_message",
        {"chat_id": job.chat_id, "text": "✅ Сессия завершена. Используйте /start для новой симуляции."},
        job_id=job.job_id, seq=seq,
    )

    final_payload = {
        "profile": specialist_profile.model_dump(mode="json") if specialist_profile else {}
    }
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=job.chat_id, state="complete",
        state_payload=final_payload, user_id=job.user_id,
        context_id=job.context_id,
    )

    await save_artifact(
        db=db,
        run_id=job.run_id,
        service_id=BOT_ID,
        context_id=job.context_id,
        specialist_telegram_id=job.user_id or job.chat_id,
        payload={
            "tsi": tsi.model_dump(mode="json") if tsi else None,
            "cci": cci.model_dump(mode="json") if cci else None,
            "session_turns": len(session_data.iteration_log),
            "report_text": report_text,
            "profile": specialist_profile.model_dump(mode="json") if specialist_profile else None,
        },
        summary=f"Симуляция. {tsi_text}{' | ' + cci_text if cci_text else ''}.",
    )
    logger.info("[worker/sim] report complete chat=%s", job.chat_id)


# ── Utilities (adapted from webhooks/simulator.py) ────────────────────────────

def _get_system_prompt(payload: dict, session_data: SessionData) -> str:
    custom = payload.get("custom_prompt")
    if custom:
        return custom
    case_map = {v.case_id: k for k, v in BUILTIN_CASES.items()}
    case_key = case_map.get(session_data.case_id, "1")
    case = BUILTIN_CASES.get(case_key, list(BUILTIN_CASES.values())[0])
    return build_system_prompt(case, session_data.session_goal, session_data.mode)


def _parse_tsi_from_report(report_text: str) -> Optional[TSIComponents]:
    try:
        def _extract(pattern: str, text: str) -> float:
            m = re.search(pattern, text)
            return float(m.group(1)) if m else 0.0

        r_match = _extract(r'R_match:\s*([\d.]+)', report_text)
        l_cons = _extract(r'L_consistency:\s*([\d.]+)', report_text)
        alliance = _extract(r'Alliance_score:\s*([\d.]+)', report_text)
        unc_mod = _extract(r'Uncertainty_modulation:\s*([\d.]+)', report_text)
        reactivity = _extract(r'Therapist_reactivity:\s*([\d.]+)', report_text)

        if sum(1 for v in [r_match, l_cons, alliance, unc_mod, reactivity] if v > 0) < 3:
            return None

        return TSIComponents(
            R_match=min(1.0, r_match),
            L_consistency=min(1.0, l_cons),
            Alliance_score=min(1.0, alliance),
            Uncertainty_modulation=min(1.0, unc_mod),
            Therapist_reactivity=min(1.0, reactivity),
        )
    except Exception as exc:
        logger.error("[worker/sim] TSI parse failed: %s", exc)
        return None


def _get_cci(case_id: str) -> Optional[CCIComponents]:
    case_map = {v.case_id: v for v in BUILTIN_CASES.values()}
    case = case_map.get(case_id)
    return case.cci if case else None


def _update_profile(
    payload: dict,
    user_id: int | None,
    session_data: SessionData,
    tsi: Optional[TSIComponents],
) -> Optional[SpecialistProfile]:
    existing = payload.get("profile")
    profile = (
        SpecialistProfile.model_validate(existing)
        if existing
        else SpecialistProfile(specialist_id=str(user_id or 0))
    )
    profile.sessions_count += 1
    profile.cases_completed.append(session_data.case_id)

    if tsi:
        profile.tsi_history.append(tsi.tsi)
        profile.average_tsi = round(sum(profile.tsi_history) / len(profile.tsi_history), 2)

    total_signals = len(session_data.signal_log)
    if total_signals > 0:
        yellows = session_data.signal_log.count("🟡")
        reds = session_data.signal_log.count("🔴")
        prev = profile.sessions_count - 1
        if prev > 0:
            profile.yellow_ratio = round(
                (profile.yellow_ratio * prev + yellows / total_signals) / profile.sessions_count, 2
            )
            profile.red_ratio = round(
                (profile.red_ratio * prev + reds / total_signals) / profile.sessions_count, 2
            )
        else:
            profile.yellow_ratio = round(yellows / total_signals, 2)
            profile.red_ratio = round(reds / total_signals, 2)

    if session_data.iteration_log:
        avg_delta = sum(it.delta.trust for it in session_data.iteration_log) / len(session_data.iteration_log)
        prev = profile.sessions_count - 1
        if prev > 0:
            profile.average_delta_trust = round(
                (profile.average_delta_trust * prev + avg_delta) / profile.sessions_count, 2
            )
        else:
            profile.average_delta_trust = round(avg_delta, 2)

    return profile


def _split_text(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_pos = text.rfind("\n", 0, max_len)
        if split_pos == -1:
            split_pos = max_len
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")
    return chunks


def _build_placeholder_case(crisis: CrisisFlag):
    """Build a minimal placeholder BuiltinCase for the PRACTICE mode system prompt."""
    from app.services.simulator.schemas import (
        BuiltinCase, ScreenProfile, ContinuumScore, CaseDynamics,
        Conceptualization, LayerA, LayerB, LayerC, LayerDescription,
        Layers, Target, ClientInfo,
    )
    return BuiltinCase(
        case_id="CUSTOM",
        case_name="Пользовательский кейс",
        difficulty="moderate",
        client=ClientInfo(
            id="custom",
            gender="клиент",
            age=35,
            presenting_complaints=["описание из загруженных данных"],
        ),
        screen_profile=ScreenProfile(
            economy_exploration=ContinuumScore(value=50),
            protection_contact=ContinuumScore(value=50),
            retention_movement=ContinuumScore(value=50),
            survival_development=ContinuumScore(value=50),
        ),
        layers=Layers(
            L0=LayerDescription(description=""),
            L1=LayerDescription(description=""),
            L2=LayerDescription(description=""),
            L3=LayerDescription(description=""),
            L4=LayerDescription(description=""),
        ),
        conceptualization=Conceptualization(
            layer_a=LayerA(
                leading_hypothesis="",
                dominant_layer="L2",
                configuration="",
            ),
            layer_b=LayerB(
                targets=[Target(level="L2", description="")],
                sequence="",
            ),
            layer_c=LayerC(metaphor="", narrative="", change_direction=""),
        ),
        dynamics=CaseDynamics(
            baseline_tension_L0=50,
            baseline_cognitive_access=50,
            baseline_uncertainty=50,
            baseline_trust=50,
            L0_reactivity="moderate",
            L2_strength="moderate",
            L3_accessibility="moderate",
            interpretation_tolerance="moderate",
            uncertainty_tolerance="moderate",
            cognitive_window="moderate",
            escalation_speed="moderate",
            intervention_range="moderate",
            recovery_rate=0.5,
            volatility=0.4,
        ),
        crisis_flag=crisis,
    )
