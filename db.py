#===============================================================
# db.py
#===============================================================
import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from models import GlobalCounter, GameState

logger = logging.getLogger(__name__)

# Import your models so metadata is available
from models import Base, User, Play, Payment, Proof, TransactionLog

# Get DATABASE_URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL not set in environment variables")

# Ensure asyncpg format (Render often gives psycopg2 style)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    future=True,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true"  # set to true for debug logging
)

# Session factory (each request/handler will use this)
AsyncSessionLocal = sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession
)

from contextlib import asynccontextmanager

# For routes (dependency injection)
async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
        
# Dependency: get an async session
async def get_async_session():
    async with AsyncSessionLocal() as session:
        yield session

# ⚠️ For local development only
# In production, don’t run this — rely on migrations
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database initialized (dev only)")

# ----------------
# init_game_state
# ----------------
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

