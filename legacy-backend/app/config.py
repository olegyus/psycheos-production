"""
Application configuration using pydantic-settings.
All settings are loaded from environment variables or .env file.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Database
    database_url: str = "sqlite+aiosqlite:///./psycheos.db"
    
    # Claude API
    anthropic_api_key: str = ""
    
    # Telegram Bots
    telegram_pro_bot_token: str = ""
    telegram_client_bot_token: str = ""
    
    # Application
    debug: bool = True
    log_level: str = "INFO"
    
    # Session
    session_expiry_hours: int = 48
    free_tokens_on_register: int = 1
    
    # Backend URL (for bots)
    backend_url: str = "http://localhost:8000"
    
    # Claude model
    claude_model: str = "claude-haiku-4-5"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
