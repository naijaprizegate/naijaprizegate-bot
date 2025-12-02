# ===============================================================
# db.py — Central async SQLAlchemy setup
# ===============================================================
import os
import logging
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
)
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

# -------------------------------------------------
# Engine & Async Session Factory
# -------------------------------------------------
engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",  # enable SQL logging if needed
    pool_pre_ping=True,     # checks if connection is alive
    pool_recycle=1800,      # recycle connections every 30 mins
    future=True,
)

# This is the async session factory the whole app should import
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
