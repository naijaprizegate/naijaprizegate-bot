# ===============================================================
# db.py
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

from models import Base, User, Play, Payment, Proof, TransactionLog, GlobalCounter, GameState

logger = logging.getLogger(__name__)

# -------------------------------------------------
# Database URL setup
# -------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL not set in environment variables")

# Ensure asyncpg driver is used (Render often gives psycopg2 style)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# -------------------------------------------------
# Engine & Session
# -------------------------------------------------
engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",  # SQL logs if enabled
    pool_pre_ping=True,     # ✅ checks if connection is alive
    pool_recycle=1800,      # ✅ recycle connections every 30 mins
)

# ✅ async_sessionmaker is the modern factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

# -------------------------------------------------
# FastAPI Dependencies
# -------------------------------------------------
async def get_session() -> AsyncSession:
    """Use inside FastAPI routes via Depends()."""
    async with AsyncSessionLocal() as session:
        yield session

@asynccontextmanager
async def get_async_session():
    """Use in background tasks (not request-bound)."""
    async with AsyncSessionLocal() as session:
        yield session

# -------------------------------------------------
# Database Init (dev only!)
# -------------------------------------------------
async def init_db():
    """Create tables locally if no migrations (⚠️ not for prod)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database initialized (dev only)")

# -------------------------------------------------
# Ensure Global State Rows
# -------------------------------------------------
async def init_game_state():
    """
    Ensure the DB has exactly one GlobalCounter row and one GameState row.
    Safe to call on every startup (idempotent).
    """
    async with AsyncSessionLocal() as session:
        # Ensure GlobalCounter row exists
        result = await session.execute(select(GlobalCounter))
        gc = result.scalars().first()
        if not gc:
            logger.info("Creating default GlobalCounter row")
            session.add(GlobalCounter(paid_tries_total=0))

        # Ensure GameState row exists
        result = await session.execute(select(GameState))
        gs = result.scalars().first()
        if not gs:
            logger.info("Creating default GameState row")
            session.add(GameState(current_cycle=1, paid_tries_this_cycle=0))

        await session.commit()
        logger.info("init_game_state: done (global counter & game state ensured)")
