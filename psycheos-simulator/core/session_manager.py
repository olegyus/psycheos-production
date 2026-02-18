"""Менеджер сессий v1.1 — хранение состояния + iteration log + профиль специалиста."""

import json
import logging
import os
from typing import Optional

from data.schemas import (
    SessionData, SessionMode, SessionGoal, CrisisFlag,
    FSMState, HiddenState, BuiltinCase, IterationLog,
    SpecialistProfile, TSIComponents,
)

logger = logging.getLogger(__name__)

# ── Хранилища ──────────────────────────────────────────────────────────────

_sessions: dict[int, SessionData] = {}
_custom_prompts: dict[int, str] = {}

# Путь для сохранения профилей специалистов
PROFILES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "profiles")
os.makedirs(PROFILES_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# SESSIONS
# ═══════════════════════════════════════════════════════════════════════════

def create_session(
    user_id: int,
    case: BuiltinCase,
    goal: SessionGoal,
    mode: SessionMode,
) -> SessionData:
    """Создаёт новую сессию."""
    if user_id in _sessions:
        logger.info("Closing previous session for user %d", user_id)
        del _sessions[user_id]
    _custom_prompts.pop(user_id, None)

    dyn = case.dynamics
    session = SessionData(
        user_id=user_id,
        case_id=case.case_id,
        case_name=case.case_name,
        mode=mode,
        session_goal=goal,
        crisis_flag=case.crisis_flag,
        fsm_state=FSMState.S1_CONTACT,
        hidden_state=HiddenState(
            tension_L0=dyn.baseline_tension_L0,
            cognitive_access=dyn.baseline_cognitive_access,
            uncertainty_index=dyn.baseline_uncertainty,
            trust_level=dyn.baseline_trust,
            defense_activation=40,
            active_layer="L0",
        ),
        messages=[],
        signal_log=[],
        fsm_log=["S1"],
        iteration_log=[],
    )

    _sessions[user_id] = session
    logger.info(
        "Session created: user=%d, case=%s, goal=%s, mode=%s",
        user_id, case.case_id, goal.value, mode.value,
    )
    return session


def get_session(user_id: int) -> Optional[SessionData]:
    session = _sessions.get(user_id)
    if session and session.active:
        return session
    return None


def close_session(user_id: int) -> Optional[SessionData]:
    session = _sessions.get(user_id)
    if session:
        session.active = False
        logger.info("Session closed: user=%d, iterations=%d", user_id, len(session.iteration_log))
        return session
    return None


def delete_session(user_id: int) -> None:
    _sessions.pop(user_id, None)
    _custom_prompts.pop(user_id, None)


def add_message(user_id: int, role: str, content: str) -> None:
    session = _sessions.get(user_id)
    if session:
        session.messages.append({"role": role, "content": content})


def add_signal(user_id: int, signal: str) -> None:
    session = _sessions.get(user_id)
    if session:
        session.signal_log.append(signal)


def store_system_prompt(user_id: int, prompt: str) -> None:
    _custom_prompts[user_id] = prompt


def get_system_prompt(user_id: int) -> Optional[str]:
    return _custom_prompts.get(user_id)


# ═══════════════════════════════════════════════════════════════════════════
# ITERATION LOG (v1.1)
# ═══════════════════════════════════════════════════════════════════════════

def add_iteration(user_id: int, iteration: IterationLog) -> None:
    """Добавляет структурированную запись итерации."""
    session = _sessions.get(user_id)
    if session:
        session.iteration_log.append(iteration)
        logger.debug(
            "Iteration %d: signal=%s, Δtrust=%+d, Δtension=%+d, cascade=%.2f",
            iteration.replica_id,
            iteration.signal.value,
            iteration.delta.trust,
            iteration.delta.tension_L0,
            iteration.cascade_probability,
        )


def get_next_replica_id(user_id: int) -> int:
    """Возвращает ID следующей реплики."""
    session = _sessions.get(user_id)
    if session:
        return len(session.iteration_log) + 1
    return 1


# ═══════════════════════════════════════════════════════════════════════════
# SPECIALIST PROFILE (v1.1)
# ═══════════════════════════════════════════════════════════════════════════

def _profile_path(user_id: int) -> str:
    return os.path.join(PROFILES_DIR, f"specialist_{user_id}.json")


def load_profile(user_id: int) -> SpecialistProfile:
    """Загружает профиль специалиста или создаёт новый."""
    path = _profile_path(user_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return SpecialistProfile(**data)
        except Exception as e:
            logger.warning("Failed to load profile for %d: %s", user_id, e)

    return SpecialistProfile(specialist_id=str(user_id))


def save_profile(user_id: int, profile: SpecialistProfile) -> None:
    """Сохраняет профиль специалиста на диск."""
    path = _profile_path(user_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile.model_dump(), f, ensure_ascii=False, indent=2)
        logger.info("Profile saved for user %d", user_id)
    except Exception as e:
        logger.error("Failed to save profile for %d: %s", user_id, e)


def update_profile_after_session(
    user_id: int,
    session: SessionData,
    tsi: Optional[TSIComponents] = None,
) -> SpecialistProfile:
    """Обновляет накопительный профиль после завершения сессии."""
    profile = load_profile(user_id)

    profile.sessions_count += 1
    profile.cases_completed.append(session.case_id)

    # Обновляем TSI
    if tsi:
        profile.tsi_history.append(tsi.tsi)
        profile.average_tsi = round(
            sum(profile.tsi_history) / len(profile.tsi_history), 2
        )

    # Обновляем ratio сигналов
    total_signals = len(session.signal_log)
    if total_signals > 0:
        yellows = session.signal_log.count("🟡")
        reds = session.signal_log.count("🔴")

        # Скользящее среднее
        n = profile.sessions_count
        profile.yellow_ratio = round(
            ((profile.yellow_ratio * (n - 1)) + yellows / total_signals) / n, 3
        )
        profile.red_ratio = round(
            ((profile.red_ratio * (n - 1)) + reds / total_signals) / n, 3
        )

    # Средний Δtrust из iteration_log
    if session.iteration_log:
        trust_deltas = [it.delta.trust for it in session.iteration_log]
        avg_delta = sum(trust_deltas) / len(trust_deltas)
        n = profile.sessions_count
        profile.average_delta_trust = round(
            ((profile.average_delta_trust * (n - 1)) + avg_delta) / n, 1
        )

    # Рекомендуемая сложность (подстройка по TSI)
    if tsi:
        # Если TSI высокий → можно сложнее, если низкий → проще
        profile.recommended_case_complexity = round(
            min(0.95, max(0.35, tsi.tsi - 0.05)), 2
        )

    save_profile(user_id, profile)
    return profile
