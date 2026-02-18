"""
PsycheOS Backend — Main Application

Single FastAPI service handling webhooks for all 5 Telegram bots.
"""
import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI

from app.config import settings
from app.database import engine, Base
from app.webhooks.router_factory import create_webhook_router
from app.webhooks.pro import handle_pro
from app.webhooks.stubs import (
    handle_screen,
    handle_interpretator,
    handle_conceptualizator,
    handle_simulator,
)
from app.routers.links import router as links_router

# --- Logging ---
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# --- Sentry ---
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=0.1,
        environment="development" if settings.DEBUG else "production",
    )
    logger.info("Sentry initialized")


# --- Lifespan: create tables on startup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting PsycheOS Backend...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured.")
    yield
    logger.info("Shutting down PsycheOS Backend...")
    await engine.dispose()


# --- App ---
app = FastAPI(
    title="PsycheOS Backend",
    version="0.1.0",
    lifespan=lifespan,
)


# --- Health check ---
@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# --- Links API ---
app.include_router(links_router)


# --- Webhook routers ---
bot_handlers = {
    "pro": handle_pro,
    "screen": handle_screen,
    "interpretator": handle_interpretator,
    "conceptualizator": handle_conceptualizator,
    "simulator": handle_simulator,
}

for bot_id, handler in bot_handlers.items():
    token, secret = settings.bot_config[bot_id]
    router = create_webhook_router(
        bot_id=bot_id,
        token=token,
        webhook_secret=secret,
        handler=handler,
    )
    app.include_router(router)
    logger.info(f"Webhook router registered: /webhook/{bot_id}")
