"""
PsycheOS Backend — Main Application

Single FastAPI service handling webhooks for all 5 Telegram bots.
"""
import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI

from app.config import settings
from app.database import engine, Base
import app.models  # noqa: F401 — registers all ORM models in Base.metadata (needed for create_all)
from app.webhooks.router_factory import create_webhook_router
from app.webhooks.pro import handle_pro
from app.webhooks.interpretator import handle_interpretator
from app.webhooks.conceptualizator import handle_conceptualizator
from app.webhooks.simulator import handle_simulator
from app.webhooks.screen import handle_screen
from app.routers.links import router as links_router
from app.routers.artifacts import router as artifacts_router

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


# --- Startup migration: ensure archived_at / deleted_at exist on contexts ---
def _run_pending_migrations() -> None:
    url = os.environ.get("DATABASE_URL_DIRECT") or os.environ.get("DATABASE_URL", "")
    if not url:
        logger.warning("_run_pending_migrations: no DATABASE_URL_DIRECT / DATABASE_URL — skipping")
        return
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='contexts' AND column_name='archived_at'"
            )
            if not cur.fetchone():
                cur.execute("ALTER TABLE contexts ADD COLUMN archived_at TIMESTAMPTZ")
                cur.execute("ALTER TABLE contexts ADD COLUMN deleted_at TIMESTAMPTZ")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS ix_contexts_archived_at ON contexts(archived_at)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS ix_contexts_deleted_at ON contexts(deleted_at)"
                )
                conn.commit()
                logger.info("Migration applied: archived_at + deleted_at added to contexts")
            else:
                logger.info("Migration check: archived_at already exists, skipping")
    except Exception as e:
        logger.error(f"_run_pending_migrations failed: {e}")


# --- Lifespan: run migrations, then keep engine alive ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting PsycheOS Backend...")
    _run_pending_migrations()
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

# --- Artifacts API ---
app.include_router(artifacts_router)


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
