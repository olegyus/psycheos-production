"""
PsycheOS Backend — Database setup (async SQLAlchemy)
Uses Supabase pooler (port 6543) for production connections.
"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings


# Engine — connects through Supabase pooler (transaction mode)
# statement_cache_size=0 required for pgbouncer/Supavisor compatibility
engine = create_async_engine(
    settings.DATABASE_URL_POOLER,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    echo=settings.DEBUG,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    },
)

# Session factory
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# Base class for all models
class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """Dependency: yields a DB session, auto-closes after use."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
