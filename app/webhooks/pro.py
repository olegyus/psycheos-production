"""
Pro Bot webhook handler — Phase 2 + Phase 3 (tool launcher).

Flows:
- /start (no args, unregistered) → "Access restricted"
- /start invite_XXXXX → validate invite → register → main menu
- /start (registered) → main menu
- /admin (admin only) → admin panel
- Callback queries for menu navigation
- case_{id} → case view with tool launch buttons
- launch_{service_id}_{context_id} → issue link token → deep link
"""
import io
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, LabeledPrice

from app.config import settings
from app.webhooks.common import upsert_chat_state
from app.models.bot_chat_state import BotChatState
from app.models.user import User
from app.models.invite import Invite
from app.models.context import Context
from app.models.screening_assessment import ScreeningAssessment
from app.models.artifact import Artifact
from app.models.wallet import Wallet
from app.models.usage_ledger import UsageLedger
from app.services.job_queue import enqueue
from app.services.links import issue_link
from app.services.screen.report import format_report_txt, generate_report_docx
from app.services.billing import (
    get_or_create_wallet, credit_stars,
    reserve_stars, get_stars_price, InsufficientBalance,
)

logger = logging.getLogger(__name__)


# ──────────────────── Helpers ────────────────────

async def get_user_by_tg(db: AsyncSession, telegram_id: int) -> User | None:
    result = await db.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def register_user(
    db: AsyncSession, telegram_id: int, username: str | None, full_name: str | None
) -> User:
    stmt = pg_insert(User).values(
        telegram_id=telegram_id,
        role="specialist",
        username=username,
        full_name=full_name,
        status="active",
    ).on_conflict_do_nothing(index_elements=["telegram_id"])
    await db.execute(stmt)
    await db.flush()
    return await get_user_by_tg(db, telegram_id)


async def validate_invite(db: AsyncSession, token: str) -> Invite | None:
    result = await db.execute(
        select(Invite).where(Invite.token == token)
    )
    invite = result.scalar_one_or_none()
    if not invite:
        return None
    if invite.used_count >= invite.max_uses:
        return None
    if invite.expires_at and datetime.now(timezone.utc) > invite.expires_at:
        return None
    return invite


async def consume_invite(db: AsyncSession, token: str) -> None:
    result = await db.execute(
        select(Invite).where(Invite.token == token)
    )
    invite = result.scalar_one_or_none()
    if invite:
        invite.used_count += 1
        await db.flush()


def is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_ids


# ──────────────────── Keyboards ────────────────────

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Мои кейсы", callback_data="cases_list")],
        [InlineKeyboardButton("➕ Новый кейс", callback_data="case_new")],
        [InlineKeyboardButton("📚 Справочник", callback_data="open_reference")],
        [InlineKeyboardButton("💰 Баланс", callback_data="show_balance")],
    ])


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Создать приглашение", callback_data="adm_invite_new")],
        [InlineKeyboardButton("👥 Пользователи", callback_data="adm_users")],
        [InlineKeyboardButton("📊 Финансы", callback_data="adm_finance")],
        [InlineKeyboardButton("💳 Начислить Stars", callback_data="adm_credit_new")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")],
    ])


def back_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")],
    ])


def back_to_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Админ-панель", callback_data="admin_panel")],
    ])


def exit_reference_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Выйти из справочника", callback_data="exit_reference")],
    ])


def case_tools_kb(context_id: str) -> InlineKeyboardMarkup:
    """Keyboard for case view — tool launch buttons + archive + back."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Интерпретатор",    callback_data=f"launch_interpretator_{context_id}")],
        [InlineKeyboardButton("💡 Концептуализатор", callback_data=f"launch_conceptualizator_{context_id}")],
        [InlineKeyboardButton("🎭 Симулятор",        callback_data=f"launch_simulator_{context_id}")],
        [InlineKeyboardButton("📊 Скрининг",           callback_data=f"screen_menu_{context_id}")],
        [InlineKeyboardButton("📊 История результатов", callback_data=f"case_artifacts_{context_id}")],
        [InlineKeyboardButton("📤 Ссылка для клиента", callback_data=f"screen_link_{context_id}")],
        [InlineKeyboardButton("🗄 Архивировать",     callback_data=f"case_archive_{context_id}")],
        [InlineKeyboardButton("◀️ Мои кейсы",       callback_data="cases_list")],
    ])


_SERVICE_LABEL = {
    "interpretator": "🧠 Интерпретация",
    "conceptualizator": "💡 Концептуализация",
    "simulator": "🎭 Симуляция",
    "screen": "📊 Скрининг",
}


# ──────────────────── Main Handler ────────────────────

async def handle_pro(
    update: Update,
    bot: Bot,
    db: AsyncSession,
    state: BotChatState | None,
    chat_id: int,
    user_id: int | None,
) -> None:
    # ── Telegram Stars payment events ────────────────────────────────────────
    if update.pre_checkout_query:
        # Must answer within 10 s — auto-approve all PsycheOS invoices.
        await update.pre_checkout_query.answer(ok=True)
        return

    if update.message and update.message.successful_payment:
        await handle_successful_payment(update, bot, db, chat_id, user_id)
        return

    if update.message and update.message.text:
        await handle_text(update, bot, db, state, chat_id, user_id)
        return

    if update.callback_query:
        await handle_callback(update, bot, db, state, chat_id, user_id)
        return


# ──────────────────── Text Commands ────────────────────

async def handle_text(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    text = update.message.text.strip()
    tg_user = update.message.from_user

    # ── /start with invite ──
    if text.startswith("/start invite_"):
        invite_token = text.replace("/start ", "").strip()
        await handle_invite_start(bot, db, chat_id, tg_user, invite_token)
        return

    # ── /start (no args) ──
    if text == "/start":
        user = await get_user_by_tg(db, user_id)
        if user:
            await upsert_chat_state(db, "pro", chat_id, "main_menu", user_id=user_id)
            await bot.send_message(
                chat_id=chat_id,
                text=f"С возвращением, {user.full_name or 'специалист'}!",
                reply_markup=main_menu_kb(),
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="🔒 Доступ к PsycheOS ограничен.\n\n"
                     "Для регистрации необходима ссылка-приглашение от администратора.",
            )
        return

    # ── /admin ──
    if text == "/admin":
        if not is_admin(user_id):
            await bot.send_message(chat_id=chat_id, text="Нет доступа.")
            return
        await upsert_chat_state(db, "pro", chat_id, "admin_panel", user_id=user_id)
        await bot.send_message(
            chat_id=chat_id,
            text="⚙️ Админ-панель",
            reply_markup=admin_menu_kb(),
        )
        return

    # ── FSM: waiting for case name ──
    if state and state.state == "waiting_case_name":
        await create_case(bot, db, state, chat_id, user_id, text)
        return

    # ── FSM: waiting for invite note ──
    if state and state.state == "waiting_invite_note":
        await create_invite_with_note(bot, db, chat_id, user_id, text)
        return

    # ── FSM: reference chat ──
    if state and state.state == "reference_chat":
        await handle_reference_chat(bot, db, state, chat_id, user_id, text)
        return

    # ── FSM: admin credit input ──
    if state and state.state == "waiting_admin_credit":
        if is_admin(user_id):
            await handle_admin_credit_input(bot, db, chat_id, user_id, text)
        return

    # ── Default ──
    user = await get_user_by_tg(db, user_id)
    if user:
        await bot.send_message(
            chat_id=chat_id,
            text="Используйте меню или 📚 Справочник для вопросов о системе PsycheOS.",
            reply_markup=main_menu_kb(),
        )
    else:
        await bot.send_message(
            chat_id=chat_id, text="🔒 Доступ ограничен. Нужна ссылка-приглашение.",
        )


# ──────────────────── Invite Registration ────────────────────

async def handle_invite_start(bot, db, chat_id, tg_user, invite_token):
    existing = await get_user_by_tg(db, tg_user.id)
    if existing:
        await upsert_chat_state(db, "pro", chat_id, "main_menu", user_id=tg_user.id)
        await bot.send_message(
            chat_id=chat_id, text="Вы уже зарегистрированы!", reply_markup=main_menu_kb(),
        )
        return

    token_value = invite_token.replace("invite_", "")
    invite = await validate_invite(db, token_value)
    if not invite:
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Приглашение недействительно или истекло.\n"
                 "Обратитесь к администратору за новой ссылкой.",
        )
        return

    user = await register_user(db, tg_user.id, tg_user.username, tg_user.full_name)
    await consume_invite(db, token_value)
    await upsert_chat_state(db, "pro", chat_id, "main_menu", user_id=tg_user.id)

    await bot.send_message(
        chat_id=chat_id,
        text=f"✅ Добро пожаловать в PsycheOS, {tg_user.full_name or 'специалист'}!\n\n"
             f"Ваш аккаунт активирован.",
        reply_markup=main_menu_kb(),
    )
    logger.info(f"New user registered: tg_id={tg_user.id}, invite={token_value}")


# ──────────────────── Callback Queries ────────────────────

async def handle_callback(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "main_menu":
        await upsert_chat_state(db, "pro", chat_id, "main_menu", user_id=user_id)
        await query.edit_message_text(text="📱 Главное меню", reply_markup=main_menu_kb())
        return

    if data == "cases_list":
        user = await get_user_by_tg(db, user_id)
        if not user:
            return
        result = await db.execute(
            select(Context)
            .where(Context.specialist_user_id == user.user_id, Context.status == "active")
            .order_by(Context.created_at.desc()).limit(20)
        )
        cases = result.scalars().all()

        if not cases:
            await query.edit_message_text(
                text="У вас пока нет кейсов.\nСоздайте первый!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Новый кейс", callback_data="case_new")],
                    [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")],
                ]),
            )
            return

        lines = ["📋 *Ваши кейсы:*\n"]
        buttons = []
        for c in cases:
            label = c.client_ref or str(c.context_id)[:8]
            lines.append(f"• {label}")
            buttons.append([InlineKeyboardButton(f"📄 {label}", callback_data=f"case_{c.context_id}")])
        buttons.append([InlineKeyboardButton("➕ Новый кейс", callback_data="case_new")])
        buttons.append([InlineKeyboardButton("📦 Архив", callback_data="cases_list_archived")])
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="main_menu")])

        await query.edit_message_text(
            text="\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown",
        )
        return

    if data == "case_new":
        await upsert_chat_state(db, "pro", chat_id, "waiting_case_name", user_id=user_id)
        await query.edit_message_text(
            text="Введите название/метку для кейса\n(например, имя клиента или код):",
            reply_markup=back_to_main_kb(),
        )
        return

    if data.startswith("case_artifacts_"):
        context_id_str = data[len("case_artifacts_"):]
        await show_case_artifacts(query, db, context_id_str)
        return

    if data.startswith("artifact_"):
        artifact_id_str = data[len("artifact_"):]
        await show_artifact_detail(query, bot, db, artifact_id_str, chat_id)
        return

    if data.startswith("case_") and data != "case_new":
        context_id = data.replace("case_", "")
        result = await db.execute(select(Context).where(Context.context_id == context_id))
        ctx = result.scalar_one_or_none()
        if not ctx:
            await query.edit_message_text("Кейс не найден.", reply_markup=back_to_main_kb())
            return

        user = await get_user_by_tg(db, user_id)
        if not user or ctx.specialist_user_id != user.user_id:
            await query.edit_message_text("Нет доступа к этому кейсу.", reply_markup=back_to_main_kb())
            return

        label = ctx.client_ref or str(ctx.context_id)[:8]
        created = ctx.created_at.strftime("%d.%m.%Y")

        await query.edit_message_text(
            text=f"📄 *Кейс: {label}*\n"
                 f"Создан: {created}\n"
                 f"Статус: {ctx.status}\n\n"
                 f"🛠 Выберите инструмент для запуска:",
            reply_markup=case_tools_kb(str(ctx.context_id)),
            parse_mode="Markdown",
        )
        return

    if data.startswith("case_archive_"):
        context_id_str = data[len("case_archive_"):]
        await handle_case_archive(query, db, user_id, context_id_str)
        return

    if data == "cases_list_archived":
        user = await get_user_by_tg(db, user_id)
        if not user:
            return
        result = await db.execute(
            select(Context)
            .where(Context.specialist_user_id == user.user_id, Context.status == "archived")
            .order_by(Context.created_at.desc()).limit(20)
        )
        archived = result.scalars().all()

        if not archived:
            await query.edit_message_text(
                text="📦 Архив пуст.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ Мои кейсы", callback_data="cases_list")],
                ]),
            )
            return

        lines = ["📦 *Архивированные кейсы:*\n"]
        buttons = []
        for c in archived:
            label = c.client_ref or str(c.context_id)[:8]
            lines.append(f"• {label}")
            buttons.append([InlineKeyboardButton(f"📄 {label}", callback_data=f"case_{c.context_id}")])
        buttons.append([InlineKeyboardButton("◀️ Мои кейсы", callback_data="cases_list")])

        await query.edit_message_text(
            text="\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown",
        )
        return

    if data.startswith("launch_"):
        _, service_id, context_id_str = data.split("_", 2)
        logger.info(f"[pro] callback launch_ matched: data={data}")
        await handle_launch_tool(query, bot, db, chat_id, user_id, service_id, context_id_str)
        return

    if data.startswith("screen_menu_"):
        context_id_str = data[len("screen_menu_"):]
        await handle_screen_menu(query, bot, db, chat_id, context_id_str)
        return
    if data.startswith("screen_create_"):
        context_id_str = data[len("screen_create_"):]
        await handle_screen_create(query, bot, db, chat_id, user_id, context_id_str)
        return
    if data.startswith("screen_results_"):
        assessment_id_str = data[len("screen_results_"):]
        await handle_screen_results(query, bot, db, chat_id, assessment_id_str)
        return
    if data.startswith("screen_link_"):
        context_id_str = data[len("screen_link_"):]
        await handle_screen_link(query, bot, db, chat_id, user_id, context_id_str)
        return
    if data == "show_balance":
        user = await get_user_by_tg(db, user_id)
        if not user:
            await query.answer("Нет доступа.", show_alert=True)
            return
        wallet = await get_or_create_wallet(db, user.user_id, user_id)
        await query.edit_message_text(
            text=(
                f"💰 *Баланс*\n\n"
                f"Доступно: {wallet.balance_stars} ⭐\n"
                f"Зарезервировано: {wallet.reserved_stars} ⭐\n\n"
                f"_Пополнение происходит автоматически при запуске инструмента, "
                f"если средств недостаточно._"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")],
            ]),
            parse_mode="Markdown",
        )
        return

    # ── Reference chat ──
    if data == "open_reference":
        user = await get_user_by_tg(db, user_id)
        if not user:
            await query.answer("Нет доступа.", show_alert=True)
            return
        await upsert_chat_state(
            db, "pro", chat_id, "reference_chat", user_id=user_id,
            state_payload={"reference_history": []},
        )
        await query.edit_message_text(
            text="📚 *Справочник PsycheOS*\n\n"
                 "Задайте вопрос о модели — я объясню концепции, термины и логику инструментов.\n\n"
                 "_Я не даю клинических рекомендаций по конкретным клиентам._",
            reply_markup=exit_reference_kb(),
            parse_mode="Markdown",
        )
        return
    if data == "exit_reference":
        await upsert_chat_state(db, "pro", chat_id, "main_menu", user_id=user_id)
        await query.edit_message_text(
            text="Вы вышли из справочника. Выберите действие в меню.",
            reply_markup=main_menu_kb(),
        )
        return

    # ── Admin callbacks ──
    if data == "admin_panel":
        if not is_admin(user_id):
            return
        await upsert_chat_state(db, "pro", chat_id, "admin_panel", user_id=user_id)
        await query.edit_message_text(text="⚙️ Админ-панель", reply_markup=admin_menu_kb())
        return

    if data == "adm_invite_new":
        if not is_admin(user_id):
            return
        await upsert_chat_state(db, "pro", chat_id, "waiting_invite_note", user_id=user_id)
        await query.edit_message_text(
            text="Введите заметку для приглашения\n(например, «Для Анны, психолог»):",
            reply_markup=back_to_admin_kb(),
        )
        return

    if data == "adm_users":
        if not is_admin(user_id):
            return
        result = await db.execute(select(User).order_by(User.created_at.desc()).limit(30))
        users = result.scalars().all()
        count_result = await db.execute(select(func.count(User.user_id)))
        total = count_result.scalar()

        lines = [f"👥 *Пользователи* (всего: {total})\n"]
        for u in users:
            name = u.full_name or u.username or str(u.telegram_id)
            date = u.created_at.strftime("%d.%m.%Y")
            lines.append(f"• {name} — {date}")

        await query.edit_message_text(
            text="\n".join(lines), reply_markup=back_to_admin_kb(), parse_mode="Markdown",
        )
        return

    if data == "adm_finance":
        if not is_admin(user_id):
            return
        # Aggregate wallet stats
        agg = await db.execute(
            select(
                func.count(Wallet.wallet_id),
                func.coalesce(func.sum(Wallet.balance_stars), 0),
                func.coalesce(func.sum(Wallet.reserved_stars), 0),
                func.coalesce(func.sum(Wallet.lifetime_out), 0),
                func.coalesce(func.sum(Wallet.lifetime_in), 0),
            )
        )
        w_count, total_bal, total_res, total_out, total_in = agg.one()
        available_total = total_bal - total_res

        # Recent charges (last 7)
        recent_q = await db.execute(
            select(UsageLedger)
            .where(UsageLedger.kind == "charge")
            .order_by(UsageLedger.created_at.desc())
            .limit(7)
        )
        recent = recent_q.scalars().all()

        lines = [
            "📊 *Финансы*\n",
            f"👥 Кошельков: {w_count}",
            f"💰 Суммарный баланс: {total_bal}⭐",
            f"🔓 Доступно (без резерва): {available_total}⭐",
            f"🔒 Зарезервировано: {total_res}⭐",
            f"📥 Всего пополнено: {total_in}⭐",
            f"💸 Всего потрачено: {total_out}⭐",
        ]
        if recent:
            lines.append("\n*Последние списания:*")
            for e in recent:
                date_str = e.created_at.strftime("%d.%m %H:%M")
                svc = e.service_id or "—"
                lines.append(f"• {date_str}  {svc}  {abs(e.stars)}⭐  #{e.telegram_id}")

        await query.edit_message_text(
            text="\n".join(lines),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Начислить Stars", callback_data="adm_credit_new")],
                [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")],
            ]),
            parse_mode="Markdown",
        )
        return

    if data == "adm_credit_new":
        if not is_admin(user_id):
            return
        await upsert_chat_state(db, "pro", chat_id, "waiting_admin_credit", user_id=user_id)
        await query.edit_message_text(
            text="💳 *Начисление Stars*\n\n"
                 "Введите Telegram ID и количество Stars через двоеточие:\n\n"
                 "`123456789:100`\n\n"
                 "_Минимум: 1 Star. Изменение необратимо._",
            reply_markup=back_to_admin_kb(),
            parse_mode="Markdown",
        )
        return


# ──────────────────── Payment Handlers ────────────────────

async def handle_successful_payment(
    update: Update, bot: Bot, db: AsyncSession, chat_id: int, user_id: int | None,
) -> None:
    """Credit the user's wallet after a successful Telegram Stars purchase."""
    if user_id is None:
        return

    payment = update.message.successful_payment
    stars = payment.total_amount          # XTR: 1 unit = 1 Star
    charge_id = payment.provider_payment_charge_id

    user = await get_user_by_tg(db, user_id)
    if not user:
        logger.warning("successful_payment: unknown user tg_id=%d", user_id)
        return

    wallet = await get_or_create_wallet(db, user.user_id, user_id)
    await credit_stars(
        db, wallet, user_id, stars, kind="topup",
        payment_charge_id=charge_id,
        note="Stars purchase via Telegram",
    )

    new_balance = wallet.balance_stars
    await bot.send_message(
        chat_id=chat_id,
        text=f"✅ Оплата получена!\n\n"
             f"Начислено: {stars}⭐\n"
             f"Ваш баланс: {new_balance}⭐\n\n"
             f"Теперь вы можете запустить инструменты PsycheOS.",
        reply_markup=main_menu_kb(),
    )
    logger.info("topup: tg_id=%d stars=%d charge_id=%s", user_id, stars, charge_id)


# ──────────────────── FSM Actions ────────────────────

# Maximum number of user/assistant message pairs to keep in history.
_REFERENCE_MAX_PAIRS = 10


async def handle_reference_chat(bot, db, state, chat_id, user_id, text: str) -> None:
    """
    FSM handler for reference_chat state.
    Appends user turn, persists history, enqueues pro_reference worker job.
    Worker (app/worker/handlers/pro.py) calls Claude Haiku and sends the reply.
    """
    payload: dict = state.state_payload or {}
    history: list = list(payload.get("reference_history", []))

    # Append user turn and trim to max window.
    history.append({"role": "user", "content": text})
    if len(history) > _REFERENCE_MAX_PAIRS * 2:
        history = history[-(_REFERENCE_MAX_PAIRS * 2):]

    # Persist updated history (with user turn) before enqueuing.
    await upsert_chat_state(
        db, "pro", chat_id, "reference_chat", user_id=user_id,
        state_payload={"reference_history": history},
    )

    # Enqueue Claude Haiku job; worker sends the response.
    await enqueue(
        db, "pro_reference", "pro", chat_id,
        payload={"history": history},
        user_id=user_id, context_id=None, run_id=None,
    )

    await bot.send_message(
        chat_id=chat_id,
        text="⏳ Ищу ответ...",
        reply_markup=exit_reference_kb(),
    )


async def handle_case_archive(query, db, user_id, context_id_str):
    """Archive a case after verifying ownership."""
    try:
        context_id = uuid.UUID(context_id_str)
    except ValueError:
        await query.answer("Ошибка: неверный ID кейса.", show_alert=True)
        return

    result = await db.execute(select(Context).where(Context.context_id == context_id))
    ctx = result.scalar_one_or_none()
    if not ctx:
        await query.answer("Кейс не найден.", show_alert=True)
        return

    user = await get_user_by_tg(db, user_id)
    if not user or ctx.specialist_user_id != user.user_id:


        await query.answer("Нет доступа к этому кейсу.", show_alert=True)
        return

    ctx.status = "archived"
    await db.flush()

    label = ctx.client_ref or str(ctx.context_id)[:8]
    await query.edit_message_text(
        text=f"🗄 Кейс «{label}» перемещён в архив.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Мои кейсы", callback_data="cases_list")],
        ]),
    )


async def create_case(bot, db, state, chat_id, user_id, case_name):
    user = await get_user_by_tg(db, user_id)
    if not user:
        return
    ctx = Context(specialist_user_id=user.user_id, client_ref=case_name.strip()[:255], status="active")
    db.add(ctx)
    await db.flush()
    await upsert_chat_state(db, "pro", chat_id, "main_menu", user_id=user_id)

    label = ctx.client_ref or str(ctx.context_id)[:8]
    created = ctx.created_at.strftime("%d.%m.%Y")
    await bot.send_message(
        chat_id=chat_id,
        text=f"✅ Кейс «{label}» создан.\n\n"
             f"📄 *Кейс: {label}*\n"
             f"Создан: {created}\n"
             f"Статус: {ctx.status}\n\n"
             f"🛠 Выберите инструмент для запуска:",
        reply_markup=case_tools_kb(str(ctx.context_id)),
        parse_mode="Markdown",
    )


_TOOL_LABELS = {
    "interpretator":    "Интерпретатор",
    "conceptualizator": "Концептуализатор",
    "simulator":        "Симулятор",
}


async def handle_screen_menu(query, bot, db, chat_id, context_id_str):
    """Screen v2 — not yet implemented."""
    await query.answer("Скрининг в разработке.", show_alert=True)
    return


async def handle_screen_create(query, bot, db, chat_id, user_id, context_id_str):
    """Screen v2 — not yet implemented."""
    await query.answer("Скрининг в разработке.", show_alert=True)
    return


async def handle_screen_link(query, bot, db, chat_id, user_id, context_id_str):
    """Issue an open client token for Screen and send the link to the specialist."""
    username = settings.tool_bot_usernames.get("screen", "")
    if not username:
        await query.answer("Screen не настроен. Обратитесь к администратору.", show_alert=True)
        return
    try:
        context_id = uuid.UUID(context_id_str)
    except ValueError:
        await query.answer("Ошибка: неверный ID кейса.", show_alert=True)
        return
    result = await db.execute(select(Context).where(Context.context_id == context_id))
    ctx = result.scalar_one_or_none()
    if not ctx:
        await query.answer("Кейс не найден.", show_alert=True)
        return
    user = await get_user_by_tg(db, user_id)
    if not user or ctx.specialist_user_id != user.user_id:
        await query.answer("Нет доступа к этому кейсу.", show_alert=True)
        return
    token = await issue_link(
        db,
        service_id="screen",
        context_id=context_id,
        role="client",
        subject_id=0,
    )
    assessment = ScreeningAssessment(
        context_id=context_id,
        specialist_user_id=user_id,
        link_token_jti=token.jti,
        status="created",
    )
    db.add(assessment)
    await db.flush()
    deep_link = f"https://t.me/{username}?start={token.jti}"
    await query.edit_message_text(
        text=(
            f"✅ *Ссылка для клиента*\n\n"
            f"`{deep_link}`\n\n"
            f"_Ссылка действует 24 часа._"
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Открыть Screen", url=deep_link)],
            [InlineKeyboardButton("◀️ Назад к кейсу", callback_data=f"case_{context_id_str}")],
        ]),
        parse_mode="Markdown",
    )

async def handle_screen_results(query, bot, db, chat_id, assessment_id_str):
    """Screen v2 — not yet implemented."""
    await query.answer("Скрининг в разработке.", show_alert=True)
    return


async def handle_launch_tool(query, bot, db, chat_id, user_id, service_id, context_id_str):
    """
    Issue a link token and send the deep link to the specialist.
    Phase 7: checks Stars balance; sends an invoice if insufficient.
    """
    logger.info(f"[pro] handle_launch_tool called: service={service_id} context={context_id_str} user={user_id}")
    if service_id not in _TOOL_LABELS:
        await query.answer("Неизвестный инструмент.", show_alert=True)
        return

    username = settings.tool_bot_usernames.get(service_id, "")
    if not username:
        await query.answer("Бот не настроен. Обратитесь к администратору.", show_alert=True)
        return

    try:
        context_id = uuid.UUID(context_id_str)
    except ValueError:
        await query.answer("Ошибка: неверный ID кейса.", show_alert=True)
        return

    result = await db.execute(select(Context).where(Context.context_id == context_id))
    ctx = result.scalar_one_or_none()
    if not ctx:
        await query.answer("Кейс не найден.", show_alert=True)
        return
    user = await get_user_by_tg(db, user_id)
    if not user or ctx.specialist_user_id != user.user_id:
        await query.answer("Нет доступа к этому кейсу.", show_alert=True)
        return

    # ── Billing: check Stars balance ──────────────────────────────────────────
    stars_price = await get_stars_price(db, service_id)
    wallet = None
    if stars_price is not None:
        wallet = await get_or_create_wallet(db, user.user_id, user_id)
        available = wallet.balance_stars - wallet.reserved_stars
        if available < stars_price:
            shortfall = stars_price - available
            # Suggest top-up: round up to nearest 10, minimum 10 Stars
            top_up = max(10, ((shortfall + 9) // 10) * 10)
            label = _TOOL_LABELS[service_id]
            await query.answer()
            await bot.send_invoice(
                chat_id=chat_id,
                title=f"Запуск «{label}»",
                description=(
                    f"Для запуска необходимо {stars_price}⭐.\n"
                    f"Ваш баланс: {available}⭐  |  Не хватает: {shortfall}⭐"
                ),
                payload=f"topup:{user_id}",
                currency="XTR",
                prices=[LabeledPrice(
                    label=f"PsycheOS Stars ×{top_up}",
                    amount=top_up,
                )],
            )
            return

    # ── Issue link token ──────────────────────────────────────────────────────
    token = await issue_link(
        db,
        service_id=service_id,
        context_id=context_id,
        role="specialist",
        subject_id=user_id,
    )

    # ── Reserve Stars for this session (run_id = link_token.jti) ─────────────
    if stars_price is not None and wallet is not None:
        await reserve_stars(
            db, wallet, user_id, stars_price,
            run_id=token.jti, service_id=service_id,
        )

    deep_link = f"https://t.me/{username}?start={token.jti}"
    label = _TOOL_LABELS[service_id]

    await bot.send_message(
        chat_id=chat_id,
        text=f"🔗 *{label}* готов к запуску\n\nПропуск действует 24 часа.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"▶️ Открыть {label}", url=deep_link)],
        ]),
        parse_mode="Markdown",
    )
    await query.answer()


async def create_invite_with_note(bot, db, chat_id, user_id, note):
    token = secrets.token_hex(8)
    invite = Invite(
        token=token, created_by=user_id, max_uses=1, used_count=0,
        note=note.strip()[:255],
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(invite)
    await db.flush()

    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=invite_{token}"

    await upsert_chat_state(db, "pro", chat_id, "admin_panel", user_id=user_id)
    await bot.send_message(
        chat_id=chat_id,
        text=f"🔗 Приглашение создано!\n\n"
             f"Заметка: {note}\n"
             f"Действует: 7 дней\n"
             f"Использований: 1\n\n"
             f"Ссылка:\n`{link}`",
        reply_markup=admin_menu_kb(), parse_mode="Markdown",
    )


async def handle_admin_credit_input(bot, db, chat_id, user_id, text: str) -> None:
    """
    FSM handler for waiting_admin_credit state.
    Parses 'telegram_id:stars' and credits the target user's wallet.
    """
    try:
        parts = text.strip().split(":")
        if len(parts) != 2:
            raise ValueError("wrong format")
        target_tg_id = int(parts[0].strip())
        stars = int(parts[1].strip())
        if stars <= 0:
            raise ValueError("stars must be positive")
    except ValueError:
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Неверный формат.\n\nВведите: `telegram_id:stars`\nПример: `123456789:100`",
            parse_mode="Markdown",
            reply_markup=back_to_admin_kb(),
        )
        return

    target_user = await get_user_by_tg(db, target_tg_id)
    if not target_user:
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Пользователь `{target_tg_id}` не найден в системе.",
            parse_mode="Markdown",
            reply_markup=back_to_admin_kb(),
        )
        return

    wallet = await get_or_create_wallet(db, target_user.user_id, target_tg_id)
    await credit_stars(
        db, wallet, target_tg_id, stars, kind="admin_credit",
        note=f"Admin credit by tg:{user_id}",
    )

    name = target_user.full_name or target_user.username or str(target_tg_id)
    new_balance = wallet.balance_stars

    await upsert_chat_state(db, "pro", chat_id, "admin_panel", user_id=user_id)
    await bot.send_message(
        chat_id=chat_id,
        text=f"✅ Начислено *{stars}⭐* пользователю {name}\n\n"
             f"Новый баланс: {new_balance}⭐",
        parse_mode="Markdown",
        reply_markup=admin_menu_kb(),
    )
    logger.info("admin_credit: by=%d → to=%d stars=%d", user_id, target_tg_id, stars)


# ──────────────────── Artifacts UI ────────────────────

async def show_case_artifacts(query, db: AsyncSession, context_id_str: str) -> None:
    """Show list of artifacts for a case (newest first, max 10)."""
    try:
        context_id = uuid.UUID(context_id_str)
    except ValueError:
        await query.edit_message_text("Неверный ID кейса.", reply_markup=back_to_main_kb())
        return

    result = await db.execute(
        select(Artifact)
        .where(Artifact.context_id == context_id)
        .order_by(Artifact.created_at.desc())
        .limit(10)
    )
    artifacts = result.scalars().all()

    back_btn = InlineKeyboardButton("◀️ К кейсу", callback_data=f"case_{context_id_str}")

    if not artifacts:
        await query.edit_message_text(
            "📊 *История результатов*\n\nПо этому кейсу ещё нет сохранённых артефактов.\n\n"
            "_Артефакты появятся после завершения сессии в инструментах._",
            reply_markup=InlineKeyboardMarkup([[back_btn]]),
            parse_mode="Markdown",
        )
        return

    buttons = []
    for a in artifacts:
        label = _SERVICE_LABEL.get(a.service_id, a.service_id)
        date_str = a.created_at.strftime("%d.%m.%y")
        buttons.append([InlineKeyboardButton(
            f"{label}  {date_str}",
            callback_data=f"artifact_{a.artifact_id}",
        )])
    buttons.append([back_btn])

    await query.edit_message_text(
        f"📊 *История результатов*\n\nВсего: {len(artifacts)} запись(ей). Нажмите для просмотра.",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


async def show_artifact_detail(
    query, bot: Bot, db: AsyncSession, artifact_id_str: str, chat_id: int
) -> None:
    """Show artifact detail.

    Screen artifacts: edit message to a brief header then send JSON + TXT files.
    Other services: show text summary with back button.
    """
    try:
        artifact_id = uuid.UUID(artifact_id_str)
    except ValueError:
        await query.edit_message_text("Неверный ID артефакта.", reply_markup=back_to_main_kb())
        return

    result = await db.execute(
        select(Artifact).where(Artifact.artifact_id == artifact_id)
    )
    a = result.scalar_one_or_none()
    if a is None:
        await query.edit_message_text("Артефакт не найден.", reply_markup=back_to_main_kb())
        return

    label = _SERVICE_LABEL.get(a.service_id, a.service_id)
    date_str = a.created_at.strftime("%d.%m.%Y %H:%M")
    context_id_str = str(a.context_id)
    back_btn = InlineKeyboardButton("◀️ К списку", callback_data=f"case_artifacts_{context_id_str}")

    if a.service_id == "screen":
        await query.edit_message_text(
            f"📊 *{label}*\n🗓 {date_str}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[back_btn]]),
        )
        date_prefix = a.created_at.strftime("%Y%m%d")
        ctx_prefix = context_id_str[:8]

        report_json = a.payload.get("report_json", a.payload)
        json_bytes = json.dumps(report_json, ensure_ascii=False, indent=2).encode("utf-8")
        await bot.send_document(
            chat_id=chat_id,
            document=InputFile(io.BytesIO(json_bytes), filename=f"screen_{ctx_prefix}_{date_prefix}.json"),
            caption="📊 Скрининг — структурированные данные (JSON)",
        )

        report_text: str = a.payload.get("report_text") or json.dumps(report_json, ensure_ascii=False, indent=2)
        txt_bytes = report_text.encode("utf-8")
        await bot.send_document(
            chat_id=chat_id,
            document=InputFile(io.BytesIO(txt_bytes), filename=f"screen_{ctx_prefix}_{date_prefix}.txt"),
            caption="📋 Скрининг — отчёт для специалиста (TXT)",
        )
        return

    summary_text = a.summary or "_Краткое описание недоступно._"
    await query.edit_message_text(
        f"📊 *{label}*\n\n"
        f"🗓 {date_str}\n\n"
        f"{summary_text}",
        reply_markup=InlineKeyboardMarkup([[back_btn]]),
        parse_mode="Markdown",
    )

