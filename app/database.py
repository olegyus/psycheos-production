"""
PsycheOS Backend — Database setup (async SQLAlchemy)
Uses Supabase pooler (port 6543) for production connections.
"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from app.config import settings


engine = create_async_engine(
    settings.database_url_async,
    poolclass=NullPool,
    echo=settings.DEBUG,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """Dependency: yields a DB session, auto-closes after use."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
