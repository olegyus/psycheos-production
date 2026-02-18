"""Configuration for PsycheOS Conceptualizer Bot."""

import os
import logging
from typing import Optional
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Settings(BaseSettings):
    """Application settings."""
    
    # Telegram
    telegram_bot_token: str
    
    # Anthropic
    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4000
    
    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    
    # Session
    session_ttl: int = 86400  # 24 hours
    
    # Application
    environment: str = "development"
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


def setup_logging(level: str = "INFO") -> None:
    """Setup logging configuration."""
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=getattr(logging, level.upper()),
    )


def get_settings() -> Settings:
    """Get application settings."""
    try:
        settings = Settings()
        return settings
    except Exception as e:
        print(f"❌ Error loading settings: {e}")
        print("\n📝 Check your .env file:")
        print("   - TELEGRAM_BOT_TOKEN should be set")
        print("   - ANTHROPIC_API_KEY should be set")
        raise


# Global settings instance
settings: Optional[Settings] = None


def init_config() -> Settings:
    """Initialize configuration."""
    global settings
    settings = get_settings()
    setup_logging(settings.log_level)
    return settings


def get_config() -> Settings:
    """Get global settings instance."""
    if settings is None:
        return init_config()
    return settings


if __name__ == "__main__":
    # Test configuration
    try:
        cfg = get_settings()
        print("✅ Configuration loaded successfully!")
        print(f"\nSettings:")
        print(f"  Environment: {cfg.environment}")
        print(f"  Log Level: {cfg.log_level}")
        print(f"  Anthropic Model: {cfg.anthropic_model}")
        print(f"  Max Tokens: {cfg.max_tokens}")
        print(f"  Redis: {cfg.redis_host}:{cfg.redis_port}")
        print(f"  Session TTL: {cfg.session_ttl}s")
        print(f"  Bot Token: {cfg.telegram_bot_token[:10]}...")
        print(f"  API Key: {cfg.anthropic_api_key[:10]}...")
    except Exception as e:
        print(f"❌ Configuration error: {e}")
