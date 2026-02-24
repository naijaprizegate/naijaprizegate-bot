# ===============================================================
# db.py — Central async SQLAlchemy setup (Supabase-safe + asyncpg SSL)
# ===============================================================
import os
import logging
import ssl
import certifi
from contextlib import asynccontextmanager
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

# Import Base and models cleanly
from base import Base
from models import User, Play, Payment, Proof, TransactionLog, GlobalCounter, GameState

logger = logging.getLogger(__name__)

# -------------------------------------------------
# Database URL setup
# -------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL not set in environment variables")

# Ensure asyncpg driver is used
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


def _sanitize_asyncpg_url(url: str) -> str:
    """
    asyncpg does NOT accept libpq-style params like sslmode=require.
    Supabase pooler URLs sometimes include params like:
      - sslmode=require
      - pgbouncer=true
      - pool_mode=...
    We remove them from the URL and handle SSL via connect_args instead.
    """
    parsed = urlparse(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)

    remove_keys = {"sslmode", "pgbouncer", "pool_mode", "ssl"}
    kept = [(k, v) for (k, v) in pairs if k.lower() not in remove_keys]

    new_query = urlencode(kept) if kept else ""
    return urlunparse(parsed._replace(query=new_query))


DATABASE_URL = _sanitize_asyncpg_url(DATABASE_URL)

# -------------------------------------------------
# Engine & Async Session Factory (with SSL)
# -------------------------------------------------
# Supabase requires SSL. Some environments have incomplete OS CA roots,
# so we load BOTH system roots + certifi bundle (more compatible).
ssl_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)

# Add certifi CA bundle on top (helps when system bundle is incomplete)
ssl_context.load_verify_locations(cafile=certifi.where())

# Keep verification ON
ssl_context.check_hostname = True
ssl_context.verify_mode = ssl.CERT_REQUIRED

engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True,
    connect_args={"ssl": ssl_context},
)

# Async session factory
async_sessionmaker = sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

# Backward-compatible alias for FastAPI dependencies
AsyncSessionLocal = async_sessionmaker

# -------------------------------------------------
# FastAPI Dependencies
# -------------------------------------------------
async def get_session() -> AsyncSession:
    """FastAPI database session dependency."""
    async with async_sessionmaker() as session:
        yield session


@asynccontextmanager
async def get_async_session():
    """Use in background tasks or outside FastAPI context."""
    async with async_sessionmaker() as session:
        yield session


# -------------------------------------------------
# Database Initialization (development only)
# -------------------------------------------------
async def init_db():
    """Create tables manually — not for production (use Alembic instead)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Database initialized (development use only)")


# -------------------------------------------------
# Game State Initialization Helpers
# -------------------------------------------------
async def init_game_state():
    """Ensure default GlobalCounter & GameState exist."""
    async with async_sessionmaker() as session:
        result = await session.execute(select(GlobalCounter))
        gc = result.scalars().first()
        if not gc:
            session.add(GlobalCounter(paid_tries_total=0))

        result = await session.execute(select(GameState))
        gs = result.scalars().first()
        if not gs:
            session.add(GameState(current_cycle=1, paid_tries_this_cycle=0))

        await session.commit()
        logger.info("🎯 init_game_state: ensured baseline game data")


# -------------------------------------------------
# Health Check Utility
# -------------------------------------------------
async def test_connection():
    """Quick check if DB is reachable."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda _: None)
        logger.info("🔌 Database connection OK")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        raise
