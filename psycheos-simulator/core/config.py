from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Telegram
    telegram_bot_token: str

    # Claude API
    anthropic_api_key: str
    claude_model: str = "claude-haiku-4-5"

    # Storage
    storage_type: str = "memory"  # "memory" | "redis"
    redis_url: str = "redis://localhost:6379/0"

    # App
    log_level: str = "INFO"
    max_session_history: int = 100


settings = Settings()
