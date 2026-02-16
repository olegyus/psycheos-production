"""
Set Telegram webhooks for all PsycheOS bots.
Run once after deploying to Railway:

    python -m scripts.set_webhooks
"""
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import Bot
from app.config import settings


async def set_webhooks():
    base_url = settings.WEBHOOK_BASE_URL
    if not base_url:
        print("ERROR: WEBHOOK_BASE_URL is not set")
        return

    print(f"Setting webhooks with base URL: {base_url}\n")

    for bot_id, (token, secret) in settings.bot_config.items():
        bot = Bot(token=token)
        webhook_url = f"{base_url}/webhook/{bot_id}"

        try:
            result = await bot.set_webhook(
                url=webhook_url,
                secret_token=secret,
                drop_pending_updates=True,  # clean start
            )
            info = await bot.get_webhook_info()
            print(f"  [{bot_id}] URL: {webhook_url}")
            print(f"  [{bot_id}] Result: {result}")
            print(f"  [{bot_id}] Pending updates: {info.pending_update_count}")
            print()
        except Exception as e:
            print(f"  [{bot_id}] ERROR: {e}\n")


if __name__ == "__main__":
    asyncio.run(set_webhooks())
