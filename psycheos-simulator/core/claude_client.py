"""Обёртка над Anthropic API для PsycheOS Simulator."""

import logging
from anthropic import AsyncAnthropic

from core.config import settings

logger = logging.getLogger(__name__)

_client = AsyncAnthropic(api_key=settings.anthropic_api_key)


async def send_to_claude(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 2048,
) -> str:
    """
    Отправляет сообщения в Claude и возвращает текст ответа.

    Args:
        system_prompt: Системный промт
        messages: История [{"role": "user"/"assistant", "content": "..."}]
        max_tokens: Лимит токенов

    Returns:
        Текст ответа Claude
    """
    try:
        response = await _client.messages.create(
            model=settings.claude_model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        )
        text = response.content[0].text
        logger.debug(
            "Claude: %d chars | stop=%s | in=%d out=%d",
            len(text),
            response.stop_reason,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        return text

    except Exception as e:
        logger.error("Claude API error: %s", e)
        raise
