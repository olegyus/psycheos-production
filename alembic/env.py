from logging.config import fileConfig

from sqlalchemy import create_engine, pool
from alembic import context

# Alembic Config object
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all models so their tables are registered in Base.metadata
from app.database import Base
import app.models  # noqa: F401

target_metadata = Base.metadata


def get_url() -> str:
    """Return DATABASE_URL_DIRECT with psycopg3 sync scheme (postgresql+psycopg://)."""
    from app.config import settings
    url = settings.DATABASE_URL_DIRECT
    # Normalise to psycopg3 sync dialect — same driver the app uses
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    # postgresql+psycopg:// already correct — leave as-is
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
