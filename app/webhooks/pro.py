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
import logging
import secrets
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.webhooks.common import upsert_chat_state
from app.models.bot_chat_state import BotChatState
from app.models.user import User
from app.models.invite import Invite
from app.models.context import Context
from app.services.links import issue_link

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
    ])


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Создать приглашение", callback_data="adm_invite_new")],
        [InlineKeyboardButton("👥 Пользователи", callback_data="adm_users")],
        [InlineKeyboardButton("📊 Финансы", callback_data="adm_finance")],
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


def case_tools_kb(context_id: str) -> InlineKeyboardMarkup:
    """Keyboard for case view — tool launch buttons + archive + back."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Интерпретатор",    callback_data=f"launch_interpretator_{context_id}")],
        [InlineKeyboardButton("💡 Концептуализатор", callback_data=f"launch_conceptualizator_{context_id}")],
        [InlineKeyboardButton("🎭 Симулятор",        callback_data=f"launch_simulator_{context_id}")],
        [InlineKeyboardButton("📤 Ссылка для клиента", callback_data=f"screen_link_{context_id}")],
        [InlineKeyboardButton("🗄 Архивировать",     callback_data=f"case_archive_{context_id}")],
        [InlineKeyboardButton("◀️ Мои кейсы",       callback_data="cases_list")],
    ])


# ──────────────────── Main Handler ────────────────────

async def handle_pro(
    update: Update,
    bot: Bot,
    db: AsyncSession,
    state: BotChatState | None,
    chat_id: int,
    user_id: int | None,
) -> None:
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
        await handle_launch_tool(query, bot, db, chat_id, user_id, service_id, context_id_str)
        return

    if data.startswith("screen_link_"):
        context_id_str = data[len("screen_link_"):]
        await handle_screen_link(query, bot, db, chat_id, user_id, context_id_str)
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
        await query.edit_message_text(
            text="📊 *Финансы*\n\n"
                 "_Будет доступно после подключения биллинга (Фаза 7)._\n\n"
                 "• Total Stars Liability: —\n"
                 "• Available Stars: —\n"
                 "• Burn Rate: —",
            reply_markup=back_to_admin_kb(), parse_mode="Markdown",
        )
        return


# ──────────────────── FSM Actions ────────────────────

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

    # subject_id=0 — open token: client's Telegram ID is unknown at issue time
    token = await issue_link(
        db,
        service_id="screen",
        context_id=context_id,
        role="client",
        subject_id=0,
    )

    deep_link = f"https://t.me/{username}?start={token.jti}"

    await bot.send_message(
        chat_id=chat_id,
        text=f"📤 *Ссылка для клиента (Screen)*\n\n"
             f"Отправьте клиенту эту ссылку:\n`{deep_link}`\n\n"
             f"_Пропуск действует 24 часа._",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Открыть Screen", url=deep_link)],
        ]),
        parse_mode="Markdown",
    )
    await query.answer()


async def handle_launch_tool(query, bot, db, chat_id, user_id, service_id, context_id_str):
    """Issue a link token and send the deep link to the specialist."""
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

    token = await issue_link(
        db,
        service_id=service_id,
        context_id=context_id,
        role="specialist",
        subject_id=user_id,
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

