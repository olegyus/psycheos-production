"""
PsycheOS Backend — Configuration
All settings loaded from environment variables.
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # --- Database ---
    # In production (Railway), use pooler URL for connection pooling
    DATABASE_URL_POOLER: str
    # Direct URL — only for migrations (Alembic)
    DATABASE_URL: str

    # Connection pool limits per process (keep low — multiple replicas share DB)
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5

    # --- Sentry ---
    SENTRY_DSN: Optional[str] = None

    # --- Claude API ---
    ANTHROPIC_API_KEY: str

    # --- Telegram Bot Tokens ---
    TG_TOKEN_PRO: str
    TG_TOKEN_SCREEN: str
    TG_TOKEN_INTERPRETATOR: str
    TG_TOKEN_CONCEPTUALIZATOR: str
    TG_TOKEN_SIMULATOR: str

    # --- Telegram Webhook Secrets ---
    TG_WEBHOOK_SECRET_PRO: str
    TG_WEBHOOK_SECRET_SCREEN: str
    TG_WEBHOOK_SECRET_INTERPRETATOR: str
    TG_WEBHOOK_SECRET_CONCEPTUALIZATOR: str
    TG_WEBHOOK_SECRET_SIMULATOR: str

    # --- App ---
    WEBHOOK_BASE_URL: str = ""
    DEBUG: bool = False

    # --- Convenience mappings ---
    @property
    def bot_config(self) -> dict:
        """Returns mapping: bot_id -> (token, webhook_secret)"""
        return {
            "pro": (self.TG_TOKEN_PRO, self.TG_WEBHOOK_SECRET_PRO),
            "screen": (self.TG_TOKEN_SCREEN, self.TG_WEBHOOK_SECRET_SCREEN),
            "interpretator": (self.TG_TOKEN_INTERPRETATOR, self.TG_WEBHOOK_SECRET_INTERPRETATOR),
            "conceptualizator": (self.TG_TOKEN_CONCEPTUALIZATOR, self.TG_WEBHOOK_SECRET_CONCEPTUALIZATOR),
            "simulator": (self.TG_TOKEN_SIMULATOR, self.TG_WEBHOOK_SECRET_SIMULATOR),
        }

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
