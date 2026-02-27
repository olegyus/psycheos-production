"""
PsycheOS Backend — Database setup (async SQLAlchemy)
Uses Supabase pooler (port 6543, PgBouncer transaction mode) for production connections.
"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings


engine = create_async_engine(
    settings.database_url_async,
    # ── Connection pool ───────────────────────────────────────────────────────
    # Sized for 30 concurrent users across the web process.
    # Supabase PgBouncer (port 6543, transaction mode) multiplexes these into
    # a smaller number of real Postgres connections on its side.
    pool_size=10,        # base pool — 10 idle connections kept alive
    max_overflow=20,     # up to 20 extra connections under peak load
    pool_timeout=30,     # raise TimeoutError after 30 s waiting for a slot
    pool_recycle=1800,   # recycle connections every 30 min (avoids stale TCP)
    pool_pre_ping=True,  # SELECT 1 before checkout — drops dead connections early
    # ── psycopg3 + PgBouncer (transaction mode) ───────────────────────────────
    # PgBouncer in transaction mode does not support server-side prepared
    # statements: each transaction may land on a different backend connection.
    # prepare_threshold=None disables psycopg3's automatic statement preparation.
    connect_args={"prepare_threshold": None},
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
