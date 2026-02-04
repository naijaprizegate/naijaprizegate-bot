# ===============================================================
# helpers.py (SAFE ASYNC HELPERS — NO COMMITS INSIDE)
# ===============================================================
import logging
import time
from datetime import datetime
from typing import Optional, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User, GameState, GlobalCounter, Play

logger = logging.getLogger(__name__)

# -------------------------------------------------
# Escape MarkdownV2 for Telegram messages
# -------------------------------------------------
def md_escape(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!`"
    return "".join(f"\\{c}" if c in escape_chars else c for c in str(text))


# -------------------------------------------------
# Create or get an existing user (NO COMMIT HERE)
# -------------------------------------------------
async def get_or_create_user(
    session: AsyncSession,
    tg_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> User:
    """
    Fetch a User by Telegram ID, or create one if not exists.

    IMPORTANT:
    - No commit here. Caller controls transactions (session.begin()).
    - Uses session.flush() only.
    """

    res = await session.execute(select(User).where(User.tg_id == tg_id))
    user = res.scalar_one_or_none()

    if user:
        changed = False
        if username is not None and user.username != username:
            user.username = username
            changed = True
        if full_name is not None and getattr(user, "full_name", None) != full_name:
            user.full_name = full_name
            changed = True
        if changed:
            await session.flush()
        return user

    # New user: always initialize numeric fields to avoid None issues
    user = User(
        tg_id=tg_id,
        username=username,
        full_name=full_name,
        tries_paid=0,
        tries_bonus=0,
        premium_spins=0,
        total_premium_spins=0,
        created_at=datetime.utcnow(),
    )
    session.add(user)
    await session.flush()
    return user


# -------------------------------------------------
# Ensure GameState exists (cycle system)
# -------------------------------------------------
async def ensure_game_state(session: AsyncSession) -> GameState:
    gs = await session.get(GameState, 1)
    if not gs:
        gs = GameState(
            id=1,
            current_cycle=1,
            paid_tries_this_cycle=0,
            lifetime_paid_tries=0,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(gs)
        await session.flush()

    # Normalize nulls (defensive)
    if gs.current_cycle is None:
        gs.current_cycle = 1
    if gs.paid_tries_this_cycle is None:
        gs.paid_tries_this_cycle = 0
    if gs.lifetime_paid_tries is None:
        gs.lifetime_paid_tries = 0

    return gs


# -------------------------------------------------
# (Optional) Ensure GlobalCounter exists
# Keep this only if other parts still reference global_counter.
# -------------------------------------------------
async def ensure_global_counter(session: AsyncSession) -> GlobalCounter:
    gc = await session.get(GlobalCounter, 1)
    if not gc:
        gc = GlobalCounter(id=1, paid_tries_total=0)
        session.add(gc)
        await session.flush()

    if gc.paid_tries_total is None:
        gc.paid_tries_total = 0
        await session.flush()

    return gc


# -------------------------------------------------
# Add tries (paid or bonus) — NO COMMIT
# -------------------------------------------------
async def add_tries(
    session: AsyncSession,
    user_or_id: Union[User, str],
    count: int,
    paid: bool = True,
) -> Optional[User]:
    """
    Increment user's tries (paid or bonus) inside an active session.

    - No commit here.
    - If paid, updates GameState counters (cycle + lifetime).
    - Optionally updates GlobalCounter if you still use it elsewhere.

    Returns updated User or None if user not found.
    """
    if count <= 0:
        return None

    user_id = user_or_id.id if hasattr(user_or_id, "id") else user_or_id

    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        logger.warning("⚠️ add_tries: user not found | user_id=%s", user_id)
        return None

    if paid:
        user.tries_paid = int(user.tries_paid or 0) + int(count)

        # cycle system counters
        gs = await ensure_game_state(session)
        gs.paid_tries_this_cycle = int(gs.paid_tries_this_cycle or 0) + int(count)
        gs.lifetime_paid_tries = int(gs.lifetime_paid_tries or 0) + int(count)
        gs.updated_at = datetime.utcnow()

        # optional global counter (safe)
        try:
            gc = await ensure_global_counter(session)
            gc.paid_tries_total = int(gc.paid_tries_total or 0) + int(count)
        except Exception:
            pass

        logger.info(
            "✅ add_tries paid | tg_id=%s | +%s | paid=%s | cycle_paid=%s | lifetime=%s",
            user.tg_id,
            count,
            user.tries_paid,
            gs.paid_tries_this_cycle,
            gs.lifetime_paid_tries,
        )

    else:
        user.tries_bonus = int(user.tries_bonus or 0) + int(count)
        logger.info(
            "✅ add_tries bonus | tg_id=%s | +%s | bonus=%s",
            user.tg_id,
            count,
            user.tries_bonus,
        )

    session.add(user)
    await session.flush()
    return user


# -------------------------------------------------
# Consume one try (bonus first, then paid) — NO COMMIT
# -------------------------------------------------
async def consume_try(session: AsyncSession, user: User):
    """
    Deduct one try. Uses bonus first, then paid.
    Returns: "bonus" | "paid" | None
    """
    paid = int(user.tries_paid or 0)
    bonus = int(user.tries_bonus or 0)

    logger.info(
        "🎲 consume_try | tg_id=%s | paid=%s bonus=%s",
        user.tg_id, paid, bonus
    )

    if bonus > 0:
        user.tries_bonus = bonus - 1
        await session.flush()
        logger.info("➖ used bonus try | tg_id=%s | bonus_left=%s", user.tg_id, user.tries_bonus)
        return "bonus"

    if paid > 0:
        user.tries_paid = paid - 1
        await session.flush()
        logger.info("➖ used paid try | tg_id=%s | paid_left=%s", user.tg_id, user.tries_paid)
        return "paid"

    logger.warning("⚠️ no tries left | tg_id=%s", user.tg_id)
    return None


# -------------------------------------------------
# Get user by DB ID — NO COMMIT
# -------------------------------------------------
async def get_user_by_id(session: AsyncSession, user_id) -> Optional[User]:
    res = await session.execute(select(User).where(User.id == user_id))
    return res.scalar_one_or_none()


# -------------------------------------------------
# Record a play — NO COMMIT
# -------------------------------------------------
async def record_play(session: AsyncSession, user: User, result: str) -> Optional[Play]:
    """
    Adds a row into plays table. No commit here.
    Caller controls transaction.
    """
    try:
        play = Play(user_id=user.id, result=result)
        session.add(play)
        await session.flush()
        logger.info("📝 record_play | play_id=%s tg_id=%s result=%s", getattr(play, "id", None), user.tg_id, result)
        return play
    except Exception:
        logger.exception("❌ record_play failed | tg_id=%s", user.tg_id)
        return None


# -------------------------------------------------
# Check if a user is admin
# -------------------------------------------------
def is_admin(user: User) -> bool:
    return bool(getattr(user, "is_admin", False))


# ----------------------------
# 🧩 Mask Sensitive Helper
# ----------------------------
def mask_sensitive(data: str, visible: int = 4) -> str:
    if not data:
        return ""
    data = str(data)
    if len(data) <= visible:
        return data
    return f"{'*' * (len(data) - visible)}{data[-visible:]}"


# -------------------------------
# 🚦 Rate Limiting Helper
# --------------------------------
_LAST_WEBHOOK_CALL = {}
_RATE_LIMIT_SECONDS = 10

def is_rate_limited(tx_ref: str) -> bool:
    now = time.time()
    last_call = _LAST_WEBHOOK_CALL.get(tx_ref, 0)
    if now - last_call < _RATE_LIMIT_SECONDS:
        return True
    _LAST_WEBHOOK_CALL[tx_ref] = now
    return False
