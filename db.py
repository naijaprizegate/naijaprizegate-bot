# ===============================================================
# db.py — Central async SQLAlchemy setup
# ===============================================================
import os
import logging
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy import select

# ✅ Import Base and models cleanly
from base import Base
from models import User, Play, Payment, Proof, TransactionLog, GlobalCounter, GameState

logger = logging.getLogger(__name__)

# -------------------------------------------------
# Database URL setup
# -------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL not set in environment variables")

# ✅ Ensure asyncpg driver is used (Render often defaults to psycopg2)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# -------------------------------------------------
# Engine & Session
# -------------------------------------------------
engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",  # enable SQL logging if needed
    pool_pre_ping=True,     # ✅ checks if connection is alive
    pool_recycle=1800,      # ✅ recycle connections every 30 mins
)

# ✅ async_sessionmaker is the modern way (no deprecation warning)
AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

# -------------------------------------------------
# FastAPI Dependencies
# -------------------------------------------------
async def get_session() -> AsyncSession:
    """
    Dependency for FastAPI routes — yields a DB session.
    Usage:
        async def route(session: AsyncSession = Depends(get_session)):
            ...
    """
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def get_async_session():
    """
    Use in background tasks or outside FastAPI context.
    Example:
        async with get_async_session() as session:
            ...
    """
    async with AsyncSessionLocal() as session:
        yield session

# -------------------------------------------------
# Database Initialization (for dev/local only!)
# -------------------------------------------------
async def init_db():
    """
    Create tables manually — not for production (use Alembic migrations instead).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Database initialized (development use only)")

# -------------------------------------------------
# Game State Initialization
# -------------------------------------------------
async def init_game_state():
    """
    Ensure one GlobalCounter row and one GameState row exist.
    Safe to call on every app startup.
    """
    async with AsyncSessionLocal() as session:
        # ✅ Ensure GlobalCounter row exists
        result = await session.execute(select(GlobalCounter))
        gc = result.scalars().first()
        if not gc:
            logger.info("🪙 Creating default GlobalCounter row")
            session.add(GlobalCounter(paid_tries_total=0))

        # ✅ Ensure GameState row exists
        result = await session.execute(select(GameState))
        gs = result.scalars().first()
        if not gs:
            logger.info("🎯 Creating default GameState row")
            session.add(GameState(current_cycle=1, paid_tries_this_cycle=0))

        await session.commit()
        logger.info("✅ init_game_state: ensured default global counter & game state")

# -------------------------------------------------
# Utility for health check or admin scripts
# -------------------------------------------------
async def test_connection():
    """Quick check if DB is reachable."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda _: None)
        logger.info("✅ Database connection OK")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        raise
