"""
Smoke tests — one happy-path test per bot.

Strategy
--------
* All Claude API calls are mocked (AsyncAnthropic is never instantiated).
* The Telegram Bot object is an AsyncMock (send_message / answer / etc.)
* The DB session is an AsyncMock with a sequential side_effect list for
  db.execute() — each entry is a mock Result object seeded with the
  appropriate scalar value.
* High-level service functions that would hit external resources
  (verify_link, upsert_chat_state, get_user_by_tg, …) are patched at
  the *importer* namespace (i.e. app.webhooks.pro.verify_link, not
  app.services.links.verify_link).

Running
-------
    pip install pytest pytest-asyncio
    pytest tests/test_smoke.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── App imports (env vars are already set by conftest.py) ──────────────────────
from app.webhooks.pro import create_case, handle_invite_start
from app.webhooks.screen import _handle_start_token
from app.webhooks.interpretator import _start_session as interp_start
from app.webhooks.conceptualizator import (
    _handle_data_collection,
    _start_session as concept_start,
)
from app.webhooks.simulator import _start_session as sim_start

from app.models.bot_chat_state import BotChatState
from app.models.context import Context
from app.models.invite import Invite
from app.models.link_token import LinkToken
from app.models.screening_assessment import ScreeningAssessment
from app.models.user import User
from app.services.conceptualizer.enums import SessionStateEnum
from app.services.conceptualizer.models import SessionState

# ── Shared constants ───────────────────────────────────────────────────────────

CHAT_ID = 9_999_999
USER_ID = 111_111_111


# ── Builder helpers ────────────────────────────────────────────────────────────


def _mk_invite(token: str = "deadbeef") -> MagicMock:
    inv = MagicMock(spec=Invite)
    inv.token = token
    inv.used_count = 0
    inv.max_uses = 1
    inv.expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    return inv


def _mk_user() -> MagicMock:
    u = MagicMock(spec=User)
    u.user_id = uuid.uuid4()
    u.telegram_id = USER_ID
    u.full_name = "Тест Специалист"
    u.username = "test_specialist"
    u.role = "specialist"
    return u


def _mk_tg_user() -> MagicMock:
    """Minimal telegram.User-like object."""
    u = MagicMock()
    u.id = USER_ID
    u.username = "test_specialist"
    u.full_name = "Тест Специалист"
    return u


def _mk_link_token(service_id: str, role: str = "specialist") -> MagicMock:
    tok = MagicMock(spec=LinkToken)
    tok.jti = uuid.uuid4()
    tok.run_id = uuid.uuid4()
    tok.service_id = service_id
    tok.context_id = uuid.uuid4()
    tok.role = role
    tok.subject_id = USER_ID
    tok.used_at = None
    tok.expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    return tok


def _mk_assessment(link_token_jti: uuid.UUID | None = None) -> MagicMock:
    a = MagicMock(spec=ScreeningAssessment)
    a.id = uuid.uuid4()
    a.status = "created"
    a.specialist_user_id = USER_ID
    a.link_token_jti = link_token_jti or uuid.uuid4()
    return a


def _mk_state(state: str, bot_id: str = "pro", payload: dict | None = None) -> MagicMock:
    s = MagicMock(spec=BotChatState)
    s.bot_id = bot_id
    s.chat_id = CHAT_ID
    s.state = state
    s.state_payload = payload or {}
    s.role = "specialist"
    s.context_id = uuid.uuid4()
    return s


def _mk_db(*scalars) -> AsyncMock:
    """
    Build an AsyncMock DB session where db.execute() returns mock Result
    objects in order.

    Each element of *scalars* becomes the return value of
    result.scalar_one_or_none().  Pass an int to get a rowcount-only
    result (for INSERT … ON CONFLICT statements).
    """
    db = AsyncMock()
    # session.add() is synchronous in SQLAlchemy; make it a plain MagicMock
    # so callers don't get "coroutine never awaited" warnings.
    db.add = MagicMock()
    results: list[MagicMock] = []
    for val in scalars:
        r = MagicMock()
        r.rowcount = 1
        if isinstance(val, int):
            r.rowcount = val
            r.scalar_one_or_none.return_value = None
            r.scalars.return_value.all.return_value = []
        elif val is None:
            r.scalar_one_or_none.return_value = None
            r.scalars.return_value.all.return_value = []
        else:
            r.scalar_one_or_none.return_value = val
            r.scalars.return_value.all.return_value = [val]
        results.append(r)
    db.execute.side_effect = results
    return db


def _sent_text(bot_mock: AsyncMock) -> str:
    """Return the `text=` kwarg from the most recent bot.send_message call."""
    call = bot_mock.send_message.call_args
    if call is None:
        return ""
    return call.kwargs.get("text", "")


# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — Pro bot: invite registration → main menu, then create a case
# ══════════════════════════════════════════════════════════════════════════════

async def test_pro_registration_and_case_creation() -> None:
    """Specialist registers via invite, then creates a case."""
    user = _mk_user()
    invite = _mk_invite()
    chat_state = _mk_state("main_menu")
    bot = AsyncMock()

    # ── Part 1: invite registration ──────────────────────────────────────────
    with (
        patch("app.webhooks.pro.get_user_by_tg", AsyncMock(return_value=None)),
        patch("app.webhooks.pro.validate_invite", AsyncMock(return_value=invite)),
        patch("app.webhooks.pro.register_user", AsyncMock(return_value=user)),
        patch("app.webhooks.pro.consume_invite", AsyncMock(return_value=None)),
        patch("app.webhooks.pro.upsert_chat_state", AsyncMock(return_value=chat_state)),
    ):
        await handle_invite_start(bot, AsyncMock(), CHAT_ID, _mk_tg_user(), "invite_deadbeef")

    bot.send_message.assert_called_once()
    assert "Добро пожаловать" in _sent_text(bot), (
        f"Expected welcome message; got: {_sent_text(bot)!r}"
    )

    # ── Part 2: case creation ─────────────────────────────────────────────────
    bot.reset_mock()
    state = _mk_state("waiting_case_name")

    # Context is created inside create_case() without server-defaults being
    # populated (no real DB).  We patch the class to return a pre-populated
    # mock so ctx.created_at.strftime() doesn't blow up.
    mock_ctx = MagicMock(spec=Context)
    mock_ctx.client_ref = "Иванова Анна"
    mock_ctx.context_id = uuid.uuid4()
    mock_ctx.created_at = datetime.now(timezone.utc)
    mock_ctx.status = "active"

    with (
        patch("app.webhooks.pro.get_user_by_tg", AsyncMock(return_value=user)),
        patch("app.webhooks.pro.Context", return_value=mock_ctx),
        patch("app.webhooks.pro.upsert_chat_state", AsyncMock(return_value=chat_state)),
    ):
        await create_case(bot, _mk_db(), state, CHAT_ID, USER_ID, "Иванова Анна")

    bot.send_message.assert_called_once()
    assert "Иванова Анна" in _sent_text(bot), (
        f"Expected case name in message; got: {_sent_text(bot)!r}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 2 — Screen bot: /start {jti} → welcome + "Начать скрининг" button
# ══════════════════════════════════════════════════════════════════════════════

async def test_screen_start_token() -> None:
    """Client opens a valid screening link → welcome message with start button."""
    token = _mk_link_token("screen", role="client")
    assessment = _mk_assessment(link_token_jti=token.jti)
    chat_state = _mk_state("active", bot_id="screen")
    bot = AsyncMock()

    # Only one real db.execute() call survives (ScreeningAssessment lookup):
    # verify_link and upsert_chat_state are both patched.
    db = _mk_db(assessment)

    with (
        patch("app.webhooks.screen.verify_link", AsyncMock(return_value=token)),
        patch("app.webhooks.screen.upsert_chat_state", AsyncMock(return_value=chat_state)),
    ):
        await _handle_start_token(bot, db, CHAT_ID, USER_ID, str(token.jti))

    bot.send_message.assert_called_once()
    text = _sent_text(bot)
    assert "скрининг" in text.lower() or "Начать" in text, (
        f"Expected screening welcome; got: {text!r}"
    )
    # Verify the reply_markup contains the "start_screening" callback button
    markup = bot.send_message.call_args.kwargs.get("reply_markup")
    assert markup is not None, "send_message must include a reply_markup"
    button_datas = [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
    ]
    assert "start_screening" in button_datas, (
        f"Expected 'start_screening' button; found: {button_datas}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 3 — Interpreter bot: /start {jti} → session opened, welcome sent
# ══════════════════════════════════════════════════════════════════════════════

async def test_interpreter_session_start() -> None:
    """Specialist opens interpreter via valid link → 'active' FSM state, welcome msg."""
    token = _mk_link_token("interpretator")
    chat_state = _mk_state("active", bot_id="interpretator")
    bot = AsyncMock()

    captured_state: dict = {}

    async def fake_upsert(db, bot_id, chat_id, state, state_payload=None, **kw):
        captured_state.update({"state": state, "payload": state_payload or {}})
        return chat_state

    with (
        patch("app.webhooks.interpretator.verify_link", AsyncMock(return_value=token)),
        patch("app.webhooks.interpretator.upsert_chat_state", fake_upsert),
    ):
        await interp_start(bot, AsyncMock(), CHAT_ID, USER_ID, str(token.jti))

    # Session should transition to 'active'
    assert captured_state.get("state") == "active", (
        f"Expected FSM state 'active'; got {captured_state.get('state')!r}"
    )
    # Payload must carry the run_id for the worker
    assert "run_id" in captured_state.get("payload", {}), (
        "state_payload must contain run_id"
    )

    bot.send_message.assert_called_once()
    text = _sent_text(bot)
    assert "Interpreter" in text or "Сессия открыта" in text, (
        f"Expected interpreter welcome; got: {text!r}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 4 — Conceptualizer bot: data collection → first Socratic question
# ══════════════════════════════════════════════════════════════════════════════

async def test_conceptualizer_starts_socratic_dialogue() -> None:
    """
    After data collection text containing 'готово' (> 50 chars), the bot
    transitions to socratic_dialogue and sends the first question.
    """
    session = SessionState(
        session_id="test_cnc_session",
        specialist_id=str(USER_ID),
    )
    # Replicate what _start_session does:
    session.transition_to(SessionStateEnum.DATA_COLLECTION)

    state = _mk_state("data_collection", bot_id="conceptualizator", payload={
        "run_id": str(uuid.uuid4()),
        "session": session.model_dump(mode="json"),
    })
    bot = AsyncMock()

    # Long enough text (>50 chars) that ends with "готово" to trigger
    # the DATA_COLLECTION → ANALYSIS → SOCRATIC_DIALOGUE transition.
    narrative = (
        "Клиент, 35 лет, жалобы на хроническую тревогу и сложности с принятием решений. "
        "Наблюдения по L1: соматические реакции (учащённый пульс, потливость). "
        "L2: избегание конфликтов, выученная беспомощность. готово"
    )
    assert "готов" in narrative.lower() and len(narrative) > 50

    captured_state: dict = {}

    async def fake_upsert(db, bot_id, chat_id, state, state_payload=None, **kw):
        captured_state.update({"state": state})
        return _mk_state(state, bot_id=bot_id, payload=state_payload)

    with patch("app.webhooks.conceptualizator.upsert_chat_state", fake_upsert):
        await _handle_data_collection(
            bot, AsyncMock(), narrative, session, state, CHAT_ID, USER_ID
        )

    # Must have transitioned to socratic_dialogue
    assert captured_state.get("state") == "socratic_dialogue", (
        f"Expected 'socratic_dialogue'; got {captured_state.get('state')!r}"
    )

    bot.send_message.assert_called_once()
    text = _sent_text(bot)
    assert "❓" in text, (
        f"Expected Socratic question with ❓ marker; got: {text!r}"
    )
    assert "Сократовский диалог" in text or "данные собраны" in text.lower(), (
        f"Expected phase-transition header; got: {text!r}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 5 — Simulator bot: /start {jti} → mode selection keyboard shown
# ══════════════════════════════════════════════════════════════════════════════

async def test_simulator_session_start() -> None:
    """Specialist opens simulator via valid link → 'setup' FSM state, mode keyboard."""
    token = _mk_link_token("simulator")
    chat_state = _mk_state("setup", bot_id="simulator")
    bot = AsyncMock()

    captured_state: dict = {}

    async def fake_upsert(db, bot_id, chat_id, state, state_payload=None, **kw):
        captured_state.update({"state": state, "payload": state_payload or {}})
        return chat_state

    with (
        patch("app.webhooks.simulator.verify_link", AsyncMock(return_value=token)),
        patch("app.webhooks.simulator.upsert_chat_state", fake_upsert),
    ):
        await sim_start(bot, AsyncMock(), CHAT_ID, USER_ID, str(token.jti))

    # FSM must be 'setup'
    assert captured_state.get("state") == "setup", (
        f"Expected FSM state 'setup'; got {captured_state.get('state')!r}"
    )
    # Payload must carry setup_step=mode
    assert captured_state.get("payload", {}).get("setup_step") == "mode", (
        f"Expected setup_step='mode'; payload={captured_state.get('payload')}"
    )

    bot.send_message.assert_called_once()
    text = _sent_text(bot)
    assert "Simulator" in text, (
        f"Expected Simulator in welcome text; got: {text!r}"
    )
    # Markup must include the TRAINING and PRACTICE buttons
    markup = bot.send_message.call_args.kwargs.get("reply_markup")
    assert markup is not None, "send_message must include reply_markup with mode buttons"
    button_datas = [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
    ]
    assert any("TRAINING" in d for d in button_datas), (
        f"Expected TRAINING mode button; found: {button_datas}"
    )
    assert any("PRACTICE" in d for d in button_datas), (
        f"Expected PRACTICE mode button; found: {button_datas}"
    )
