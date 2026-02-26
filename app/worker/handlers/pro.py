"""
Worker handlers for Pro bot async jobs.

job_type: "pro_reference"
"""
import logging

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from app.config import settings
from app.models.job import Job
from app.services.outbox import enqueue_message, make_inline_keyboard
from app.services.pro.reference_prompt import REFERENCE_SYSTEM_PROMPT
from app.webhooks.common import upsert_chat_state

logger = logging.getLogger(__name__)

_REFERENCE_MAX_PAIRS = 10
_REFERENCE_MODEL = "claude-haiku-4-5-20251001"
_REFERENCE_MAX_TOKENS = 1024

_EXIT_KB = make_inline_keyboard([
    [{"text": "◀️ Выйти из справочника", "callback_data": "exit_reference"}],
])


async def handle_pro_reference(
    job: Job, db: AsyncSession, bots: dict[str, Bot],
) -> None:
    """
    Call Claude Haiku with the reference chat history and enqueue the response.

    job.payload:
      history  list  — reference_history with the new user turn already appended
    """
    history = list(job.payload["history"])

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model=_REFERENCE_MODEL,
        max_tokens=_REFERENCE_MAX_TOKENS,
        system=REFERENCE_SYSTEM_PROMPT,
        messages=history,
    )
    assistant_text = response.content[0].text

    history.append({"role": "assistant", "content": assistant_text})
    if len(history) > _REFERENCE_MAX_PAIRS * 2:
        history = history[-(_REFERENCE_MAX_PAIRS * 2):]

    await upsert_chat_state(
        db, "pro", job.chat_id, "reference_chat",
        user_id=job.user_id,
        state_payload={"reference_history": history},
    )

    await enqueue_message(
        db, job.bot_id, job.chat_id, "send_message",
        {
            "chat_id": job.chat_id,
            "text": assistant_text,
            "reply_markup": _EXIT_KB,
            "parse_mode": "Markdown",
        },
        job_id=job.job_id,
        seq=0,
    )
