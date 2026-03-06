"""
Load test — 30 concurrent Interpreter sessions (happy path, all mocked).

What this tests
---------------
* Correctness: all 30 sessions reach FSM=completed with a saved artifact.
* Data isolation: no cross-user contamination (each chat_id stays independent).
* Timing: per-phase p50 / p95 / max to pinpoint bottlenecks.

Architecture
------------
* All service calls are replaced in-process (no HTTP, no real DB):
    - verify_link, upsert_chat_state, enqueue, is_job_pending_for_chat
      → SharedState (asyncio.Lock-protected in-memory store)
    - save_artifact, enqueue_message → SharedState
    - AsyncAnthropic.messages.create → smart mock (inspects max_tokens)
* Worker jobs are processed inline per user immediately after they are
  enqueued (no real worker loop — exercises handler logic directly).
* 30 coroutines run concurrently via asyncio.gather.

Running
-------
    pytest tests/load_test.py -v -s
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── App imports (env vars set by conftest.py) ──────────────────────────────────
from app.models.bot_chat_state import BotChatState
from app.webhooks.interpretator import (
    _handle_clarification_answer,
    _handle_text,
    _start_session,
)
from app.worker.handlers.interpretator import (
    handle_interp_intake,
    handle_interp_questions,
    handle_interp_run,
)

# ── Constants ──────────────────────────────────────────────────────────────────

NUM_USERS = 30
BASE_CHAT_ID = 10_000_000
BASE_USER_ID = 20_000_000

DREAM_TEXT = (
    "Мне приснилось, что я лечу над ночным городом. Внизу мерцают тысячи огней. "
    "Я чувствую абсолютную свободу и лёгкость, никакого страха. "
    "Вдруг появляется огромная птица, она летит рядом. Потом я начинаю медленно опускаться."
)

# ── Valid mock Claude responses ────────────────────────────────────────────────

# interp_intake: acceptance — no "?", length > 200 chars
_INTAKE_ACCEPTANCE = (
    "Символический материал принят к анализу. Образ полёта над городом несёт "
    "богатый архетипический потенциал: тема трансформации, свободы и перехода "
    "хорошо представлена. Появление птицы добавляет архетип проводника или "
    "alter ego. Нисхождение в конце создаёт диалектику подъём–спуск. "
    "Материала достаточно для углублённой интерпретации."
)

# interp_questions: must contain {"questions": [...]}
_QUESTIONS_JSON = json.dumps({
    "questions": [
        "Что вы чувствовали в момент появления птицы?",
        "Был ли ночной город знакомым вам местом?",
        "Как вы воспринимали опускание — как угрозу или как завершение?",
    ]
})

# interp_run: full valid structured JSON
_INTERP_JSON = json.dumps({
    "meta": {
        "session_id": "placeholder",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "state": "INTERPRETATION_GENERATION",
        "mode": "STANDARD",
        "iteration_count": 0,
    },
    "input_summary": {
        "material_type": "dream",
        "source": "client_report",
        "completeness": "sufficient",
        "clarifications_received": [],
    },
    "phenomenological_summary": {
        "text": "Полёт над ночным городом с чувством свободы; появление птицы-спутника.",
        "key_elements": [
            {"element": "полёт", "prominence": "high", "description": "Главная тема"},
            {"element": "ночной город", "prominence": "medium", "description": "Контекст"},
            {"element": "птица", "prominence": "high", "description": "Архетипический спутник"},
        ],
    },
    "interpretative_hypotheses": [
        {
            "hypothesis_text": (
                "Стремление к освобождению от ограничений и желание целостности."
            ),
            "supporting_evidence": ["полёт", "свобода", "птица как alter ego"],
            "limitations": "Один сон без серийного контекста.",
            "alternatives": ["Компенсаторный образ", "Регрессия к архаической свободе"],
        }
    ],
    "focus_of_tension": {
        "domains": ["autonomy_and_control", "change_and_uncertainty"],
        "indicators": ["полёт", "медленное опускание"],
    },
    "compensatory_patterns": [
        {
            "pattern": "idealization",
            "confidence": "medium",
            "evidence": "Идеализированный образ свободного полёта",
        }
    ],
    "uncertainty_profile": {
        "overall_confidence": "medium",
        "data_gaps": ["Актуальный жизненный контекст", "Повторяемость образа"],
        "ambiguities": ["Значение птицы: проводник или тень?"],
        "cautions": ["Единственный сон без серии"],
    },
    "clarification_directions": [
        {
            "direction": "Личный смысл птицы",
            "priority": "high",
            "rationale": "Архетипический элемент требует уточнения",
        }
    ],
    "policy_flags": {},
})


def _claude_response(system: str, messages: list, max_tokens: int) -> str:
    """Return the appropriate mock text based on the call context."""
    if max_tokens == 800:
        # handle_interp_questions uses max_tokens=800
        return _QUESTIONS_JSON
    user_content = messages[-1]["content"] if messages else ""
    if "Создайте структурированную интерпретацию" in user_content:
        # handle_interp_run appends this phrase
        return _INTERP_JSON
    # handle_interp_intake — return acceptance (>200 chars, no "?")
    return _INTAKE_ACCEPTANCE


# ── In-memory shared state ─────────────────────────────────────────────────────

@dataclass
class _StoredJob:
    job_type: str
    chat_id: int
    payload: dict
    user_id: int | None
    context_id: uuid.UUID | None
    run_id: uuid.UUID | None
    job_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class _StoredState:
    state: str
    state_payload: dict
    role: str
    context_id: uuid.UUID | None


class SharedState:
    """asyncio.Lock-protected in-memory replacement for the DB + service layer."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.chat_states: dict[int, _StoredState] = {}
        self.jobs_by_chat: dict[int, list[_StoredJob]] = {}
        self.artifacts: dict[str, dict] = {}     # str(run_id) → dict
        self.outbox: list[dict] = []
        self.job_type_totals: dict[str, int] = {}

    # ── Service function replacements ──────────────────────────────────────────

    async def fake_upsert(
        self,
        db: Any,
        bot_id: str,
        chat_id: int,
        state: str,
        state_payload: dict | None = None,
        user_id: int | None = None,
        role: str = "specialist",
        context_id: Any = None,
    ) -> MagicMock:
        payload = dict(state_payload or {})
        if isinstance(context_id, str):
            try:
                context_id = uuid.UUID(context_id)
            except ValueError:
                pass
        async with self._lock:
            self.chat_states[chat_id] = _StoredState(
                state=state,
                state_payload=payload,
                role=role,
                context_id=context_id,
            )
        m = MagicMock(spec=BotChatState)
        m.state = state
        m.state_payload = payload
        m.role = role
        m.context_id = context_id
        m.bot_id = bot_id
        m.chat_id = chat_id
        return m

    async def fake_enqueue(
        self,
        db: Any,
        job_type: str,
        bot_id: str,
        chat_id: int,
        payload: dict,
        *,
        user_id: int | None = None,
        context_id: Any = None,
        run_id: Any = None,
        priority: int = 5,
    ) -> _StoredJob:
        if isinstance(context_id, str):
            try:
                context_id = uuid.UUID(context_id)
            except ValueError:
                pass
        if isinstance(run_id, str):
            try:
                run_id = uuid.UUID(run_id)
            except ValueError:
                pass
        job = _StoredJob(
            job_type=job_type,
            chat_id=chat_id,
            payload=payload,
            user_id=user_id,
            context_id=context_id,
            run_id=run_id,
        )
        async with self._lock:
            self.jobs_by_chat.setdefault(chat_id, []).append(job)
            self.job_type_totals[job_type] = self.job_type_totals.get(job_type, 0) + 1
        return job

    async def fake_save_artifact(
        self,
        db: Any,
        run_id: Any,
        service_id: str,
        context_id: Any,
        specialist_telegram_id: int,
        payload: dict,
        summary: str | None = None,
    ) -> None:
        async with self._lock:
            self.artifacts[str(run_id)] = {
                "service_id": service_id,
                "payload": payload,
                "summary": summary,
            }

    async def fake_enqueue_message(
        self,
        db: Any,
        bot_id: str,
        chat_id: int,
        msg_type: str,
        payload: dict,
        *,
        job_id: Any = None,
        seq: int = 0,
    ) -> None:
        async with self._lock:
            self.outbox.append({"chat_id": chat_id, "type": msg_type})

    async def fake_is_job_pending(
        self, db: Any, bot_id: str, chat_id: int
    ) -> bool:
        return False  # never throttle in load test

    # ── Accessors ──────────────────────────────────────────────────────────────

    def get_state(self, chat_id: int) -> _StoredState | None:
        return self.chat_states.get(chat_id)

    def pop_job(self, job_type: str, chat_id: int) -> _StoredJob | None:
        """Remove and return the first job of the given type for this chat."""
        jobs = self.jobs_by_chat.get(chat_id, [])
        for i, j in enumerate(jobs):
            if j.job_type == job_type:
                return jobs.pop(i)
        return None


# ── Builder helpers ────────────────────────────────────────────────────────────

def _to_bcs(stored: _StoredState) -> MagicMock:
    """Build a BotChatState-compatible mock from stored state."""
    m = MagicMock(spec=BotChatState)
    m.state = stored.state
    m.state_payload = dict(stored.state_payload)
    m.role = stored.role
    m.context_id = stored.context_id
    return m


def _to_job(stored: _StoredJob) -> MagicMock:
    """Build a Job-compatible mock from a stored job."""
    m = MagicMock()
    m.job_id = stored.job_id
    m.job_type = stored.job_type
    m.chat_id = stored.chat_id
    m.user_id = stored.user_id
    m.context_id = stored.context_id
    m.run_id = stored.run_id
    m.payload = stored.payload
    return m


def _make_claude_client() -> MagicMock:
    """Return a fake AsyncAnthropic client with smart messages.create."""
    client = MagicMock()

    async def _create(**kwargs: Any) -> MagicMock:
        text = _claude_response(
            system=kwargs.get("system", ""),
            messages=kwargs.get("messages", []),
            max_tokens=kwargs.get("max_tokens", 4000),
        )
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        return resp

    client.messages.create = _create
    return client


# ── Per-user scenario ──────────────────────────────────────────────────────────

async def _run_user(
    user_idx: int,
    shared: SharedState,
    phase_times: dict[str, list[float]],
    errors: list[str],
) -> None:
    chat_id = BASE_CHAT_ID + user_idx
    user_id = BASE_USER_ID + user_idx
    context_id = uuid.uuid4()
    run_id = uuid.uuid4()

    token = MagicMock()
    token.run_id = run_id
    token.context_id = context_id
    token.role = "specialist"
    token.subject_id = user_id

    bot = AsyncMock()
    db = AsyncMock()
    db.add = MagicMock()

    # ── Phase 1: /start {jti} → FSM active ─────────────────────────────────────
    t0 = time.perf_counter()
    try:
        with patch("app.webhooks.interpretator.verify_link", AsyncMock(return_value=token)):
            await _start_session(bot, db, chat_id, user_id, str(run_id))
    except Exception as exc:
        errors.append(f"user={user_idx} phase=start_session exc={exc!r}")
        return
    phase_times["1_start_session"].append(time.perf_counter() - t0)

    st = shared.get_state(chat_id)
    if st is None or st.state != "active":
        errors.append(f"user={user_idx} FSM not 'active' after start: state={st and st.state!r}")
        return

    # ── Phase 2: Send dream text → enqueues interp_intake ──────────────────────
    t0 = time.perf_counter()
    try:
        await _handle_text(bot, db, DREAM_TEXT, _to_bcs(st), chat_id, user_id)
    except Exception as exc:
        errors.append(f"user={user_idx} phase=send_material exc={exc!r}")
        return
    phase_times["2_send_material"].append(time.perf_counter() - t0)

    intake_stored = shared.pop_job("interp_intake", chat_id)
    if intake_stored is None:
        errors.append(f"user={user_idx} interp_intake job not enqueued")
        return

    # ── Phase 3: Worker → handle_interp_intake ─────────────────────────────────
    t0 = time.perf_counter()
    try:
        await handle_interp_intake(_to_job(intake_stored), db, {})
    except Exception as exc:
        errors.append(f"user={user_idx} phase=interp_intake exc={exc!r}")
        return
    phase_times["3_interp_intake"].append(time.perf_counter() - t0)

    questions_stored = shared.pop_job("interp_questions", chat_id)
    if questions_stored is None:
        errors.append(f"user={user_idx} interp_questions job not enqueued after intake")
        return

    # ── Phase 4: Worker → handle_interp_questions ──────────────────────────────
    t0 = time.perf_counter()
    try:
        await handle_interp_questions(_to_job(questions_stored), db, {})
    except Exception as exc:
        errors.append(f"user={user_idx} phase=interp_questions exc={exc!r}")
        return
    phase_times["4_interp_questions"].append(time.perf_counter() - t0)

    st = shared.get_state(chat_id)
    if st is None or st.state != "clarification_questions":
        errors.append(
            f"user={user_idx} FSM not 'clarification_questions': {st and st.state!r}"
        )
        return

    num_questions = len(st.state_payload.get("questions", []))
    if num_questions == 0:
        errors.append(f"user={user_idx} no questions generated in state_payload")
        return

    # ── Phase 5: Answer all questions → enqueues interp_run on last answer ──────
    t0 = time.perf_counter()
    try:
        for i in range(num_questions):
            st = shared.get_state(chat_id)
            await _handle_clarification_answer(
                bot, db, f"Ответ на вопрос {i + 1}", _to_bcs(st), chat_id, user_id
            )
    except Exception as exc:
        errors.append(f"user={user_idx} phase=answer_questions exc={exc!r}")
        return
    phase_times["5_answer_questions"].append(time.perf_counter() - t0)

    run_stored = shared.pop_job("interp_run", chat_id)
    if run_stored is None:
        errors.append(f"user={user_idx} interp_run job not enqueued after all answers")
        return

    # ── Phase 6: Worker → handle_interp_run ────────────────────────────────────
    t0 = time.perf_counter()
    try:
        await handle_interp_run(_to_job(run_stored), db, {})
    except Exception as exc:
        errors.append(f"user={user_idx} phase=interp_run exc={exc!r}")
        return
    phase_times["6_interp_run"].append(time.perf_counter() - t0)

    # ── Verify outcome ─────────────────────────────────────────────────────────
    if str(run_id) not in shared.artifacts:
        errors.append(f"user={user_idx} artifact not saved (run_id={run_id})")
        return

    st = shared.get_state(chat_id)
    if st is None or st.state != "completed":
        errors.append(f"user={user_idx} FSM not 'completed': {st and st.state!r}")


# ── Main test ──────────────────────────────────────────────────────────────────

@pytest.mark.slow
async def test_30_concurrent_interpreter_sessions() -> None:
    """
    Simulate NUM_USERS concurrent Interpreter sessions (all mocked).
    Verifies correctness + reports timing bottlenecks.
    """
    shared = SharedState()
    phase_times: dict[str, list[float]] = {
        "1_start_session":    [],
        "2_send_material":    [],
        "3_interp_intake":    [],
        "4_interp_questions": [],
        "5_answer_questions": [],
        "6_interp_run":       [],
    }
    errors: list[str] = []

    patches = [
        # Webhook handler patches
        patch("app.webhooks.interpretator.upsert_chat_state",      new=shared.fake_upsert),
        patch("app.webhooks.interpretator.enqueue",                 new=shared.fake_enqueue),
        patch("app.webhooks.interpretator.is_job_pending_for_chat", new=shared.fake_is_job_pending),
        # Worker handler patches
        patch("app.worker.handlers.interpretator.upsert_chat_state", new=shared.fake_upsert),
        patch("app.worker.handlers.interpretator.enqueue",            new=shared.fake_enqueue),
        patch("app.worker.handlers.interpretator.enqueue_message",    new=shared.fake_enqueue_message),
        patch("app.worker.handlers.interpretator.save_artifact",      new=shared.fake_save_artifact),
        patch(
            "app.worker.handlers.interpretator.AsyncAnthropic",
            new=lambda *a, **kw: _make_claude_client(),
        ),
    ]

    for p in patches:
        p.start()

    t_wall = time.perf_counter()
    try:
        await asyncio.gather(
            *[_run_user(i, shared, phase_times, errors) for i in range(NUM_USERS)]
        )
    finally:
        for p in patches:
            p.stop()

    t_wall = time.perf_counter() - t_wall

    # ── Report ─────────────────────────────────────────────────────────────────

    def _stats(times: list[float]) -> str:
        if not times:
            return "n/a"
        s = sorted(times)
        n = len(s)
        p50 = s[n // 2]
        p95 = s[min(int(n * 0.95), n - 1)]
        return (
            f"p50={p50 * 1000:6.2f}ms  "
            f"p95={p95 * 1000:6.2f}ms  "
            f"max={s[-1] * 1000:6.2f}ms  "
            f"n={n}"
        )

    print(f"\n{'=' * 66}")
    print(f"  LOAD TEST — {NUM_USERS} concurrent Interpreter sessions (all mocked)")
    print(f"{'=' * 66}")
    print(f"  Wall time (all {NUM_USERS} users): {t_wall * 1000:.1f} ms\n")

    print("  Per-phase timing (per-user, sorted):")
    for phase, times in phase_times.items():
        label = phase.replace("_", " ").strip()
        print(f"    {label:<24}  {_stats(times)}")

    print(f"\n  Jobs enqueued by type:")
    for jtype, cnt in sorted(shared.job_type_totals.items()):
        print(f"    {jtype:<28}  {cnt}")

    print(f"\n  Artifacts saved:      {len(shared.artifacts)} / {NUM_USERS}")
    print(f"  Outbox messages:      {len(shared.outbox)}")

    all_states = [shared.get_state(BASE_CHAT_ID + i) for i in range(NUM_USERS)]
    completed = sum(1 for s in all_states if s and s.state == "completed")
    print(f"  FSM completed:        {completed} / {NUM_USERS}")

    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    ✗ {e}")
    else:
        print(f"\n  ✓ All {NUM_USERS} sessions completed successfully")

    # ── Bottleneck analysis ────────────────────────────────────────────────────
    # Phases 3 / 4 / 6 each make one real Claude API call in production.
    # In this mock test they take ~1 ms; in production ~3-10 s each.
    # A single worker (WORKER_CONCURRENCY=1) processes them serially:
    #   30 users × 3 Claude calls × avg 7 s = 630 s wait for the last user.
    # With WORKER_CONCURRENCY=3 the three calls overlap → ~210 s.
    # With WORKER_CONCURRENCY=3 + 2 worker replicas (Variant A) → ~70 s.
    _CLAUDE_PHASES = {"3_interp_intake", "4_interp_questions", "6_interp_run"}
    _EST_CLAUDE_S = 7.0   # conservative production estimate per Claude call (seconds)
    _CONCURRENCY  = 1     # reflects current WORKER_CONCURRENCY in tested code

    total_claude_jobs = NUM_USERS * len(_CLAUDE_PHASES)
    serial_wall_s     = total_claude_jobs * _EST_CLAUDE_S
    concurrent_wall_s = serial_wall_s / _CONCURRENCY

    print(f"\n  Production bottleneck estimate (WORKER_CONCURRENCY={_CONCURRENCY}):")
    print(f"    Claude jobs total:  {total_claude_jobs}  ({NUM_USERS} users × {len(_CLAUDE_PHASES)} jobs)")
    print(f"    Est. per Claude call: ~{_EST_CLAUDE_S:.0f} s")
    print(f"    Serial wall-time:   ~{serial_wall_s:.0f} s  (worst-case, last user waits)")
    print(f"    With WORKER_CONCURRENCY=3: ~{serial_wall_s / 3:.0f} s")
    print(f"    Fix → set WORKER_CONCURRENCY=3 in Railway env (already implemented in worker)")

    print(f"{'=' * 66}\n")

    # ── Assertions ─────────────────────────────────────────────────────────────
    assert not errors, f"{len(errors)} session(s) failed:\n" + "\n".join(errors)

    assert len(shared.artifacts) == NUM_USERS, (
        f"Expected {NUM_USERS} artifacts, got {len(shared.artifacts)}"
    )

    # Data isolation: every chat_id must reach 'completed'
    for i in range(NUM_USERS):
        st = shared.get_state(BASE_CHAT_ID + i)
        assert st is not None and st.state == "completed", (
            f"User {i}: FSM state={st and st.state!r}, expected 'completed'"
        )

    # Job counts must match exactly
    for jtype, expected in {
        "interp_intake":    NUM_USERS,
        "interp_questions": NUM_USERS,
        "interp_run":       NUM_USERS,
    }.items():
        actual = shared.job_type_totals.get(jtype, 0)
        assert actual == expected, (
            f"Job '{jtype}': expected {expected}, got {actual}"
        )
