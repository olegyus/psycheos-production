"""
Outbox service — Telegram delivery layer decoupled from Claude execution.

After a Job completes, worker handlers call enqueue_message() for each
Telegram message to send.  The worker's main loop then calls dispatch_one()
to deliver them.

Document encoding:
  JSONB can't store raw bytes.  Binary files (docx, txt, json) are stored
  as base64 strings under the key "document_b64" alongside "filename".
  dispatch_one() reconstructs InputFile before calling Bot.send_document().

tg_method values:
  "send_message"    → Bot.send_message(**payload)
  "send_document"   → Bot.send_document(**payload)  [see document_b64]
  "edit_message"    → Bot.edit_message_text(**payload)
"""
import base64
import io
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, InputFile

from app.models.outbox_message import OutboxMessage

logger = logging.getLogger(__name__)

_MAX_ERROR_LEN = 2000


# ── Enqueue ───────────────────────────────────────────────────────────────────

async def enqueue_message(
    db: AsyncSession,
    bot_id: str,
    chat_id: int,
    tg_method: str,
    payload: dict,
    *,
    job_id: uuid.UUID | None = None,
    seq: int = 0,
) -> OutboxMessage:
    """
    Persist one outbox message and return it.

    For send_document with binary content, pass payload with:
      "document_b64": base64.b64encode(bytes).decode()
      "filename": "report.docx"
    All other keys are forwarded as kwargs to the Bot method.
    """
    msg = OutboxMessage(
        job_id=job_id,
        bot_id=bot_id,
        chat_id=chat_id,
        tg_method=tg_method,
        payload=payload,
        seq=seq,
    )
    db.add(msg)
    await db.flush()
    logger.debug(
        "outbox.enqueue msg_id=%s method=%s bot=%s chat=%s seq=%d",
        msg.msg_id, tg_method, bot_id, chat_id, seq,
    )
    return msg


# ── Dispatch ──────────────────────────────────────────────────────────────────

async def dispatch_one(db: AsyncSession, bots: dict[str, Bot]) -> bool:
    """
    Claim and send one pending outbox message.

    Returns True if a message was processed (success or permanent fail),
    False if the queue was empty.  The caller loops until False.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(OutboxMessage)
        .where(OutboxMessage.status == "pending")
        .order_by(OutboxMessage.created_at, OutboxMessage.seq)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    msg = result.scalar_one_or_none()
    if msg is None:
        return False

    msg.attempts += 1
    bot = bots.get(msg.bot_id)
    if bot is None:
        msg.status = "failed"
        msg.last_error = f"no Bot object for bot_id={msg.bot_id!r}"
        await db.commit()
        logger.error("outbox.no_bot msg_id=%s bot_id=%s", msg.msg_id, msg.bot_id)
        return True

    try:
        await _send(bot, msg.tg_method, dict(msg.payload))
        msg.status = "sent"
        msg.sent_at = now
        logger.info(
            "outbox.sent msg_id=%s method=%s bot=%s chat=%s",
            msg.msg_id, msg.tg_method, msg.bot_id, msg.chat_id,
        )
    except Exception as exc:
        error = str(exc)[:_MAX_ERROR_LEN]
        msg.last_error = error
        if msg.attempts >= msg.max_attempts:
            msg.status = "failed"
            logger.error(
                "outbox.failed msg_id=%s method=%s all %d attempts: %s",
                msg.msg_id, msg.tg_method, msg.attempts, error[:120],
            )
        else:
            # Leave status=pending — will be retried on next loop tick.
            logger.warning(
                "outbox.error msg_id=%s method=%s attempt=%d/%d: %s",
                msg.msg_id, msg.tg_method, msg.attempts, msg.max_attempts, error[:120],
            )

    await db.commit()
    return True


# ── Internal ──────────────────────────────────────────────────────────────────

async def _send(bot: Bot, tg_method: str, payload: dict) -> None:
    """Dispatch a single Telegram API call, decoding documents if needed."""
    if tg_method == "send_message":
        await bot.send_message(**payload)

    elif tg_method == "send_document":
        # Reconstruct InputFile from base64-encoded bytes stored in JSONB.
        doc_b64 = payload.pop("document_b64", None)
        filename = payload.pop("filename", "file")
        if doc_b64 is not None:
            raw = base64.b64decode(doc_b64)
            payload["document"] = InputFile(io.BytesIO(raw), filename=filename)
        await bot.send_document(**payload)

    elif tg_method == "edit_message":
        await bot.edit_message_text(**payload)

    else:
        raise ValueError(f"unknown tg_method: {tg_method!r}")


# ── Helper — build document payload ──────────────────────────────────────────

def make_document_payload(
    chat_id: int,
    file_bytes: bytes,
    filename: str,
    caption: str | None = None,
    parse_mode: str | None = None,
) -> dict:
    """
    Build a payload dict for a send_document outbox message.

    Encodes file_bytes as base64 for JSONB storage.
    """
    payload: dict = {
        "chat_id": chat_id,
        "document_b64": base64.b64encode(file_bytes).decode(),
        "filename": filename,
    }
    if caption:
        payload["caption"] = caption
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return payload
