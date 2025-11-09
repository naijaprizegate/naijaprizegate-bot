# ===============================================================
# helpers.py
# ===============================================================
import html
import logging
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from models import User, GameState, GlobalCounter, Play  # ✅ Ensure these exist and are imported
from datetime import datetime

logger = logging.getLogger(__name__)

# -------------------------------------------------
# Escape MarkdownV2 for Telegram messages
# -------------------------------------------------
def md_escape(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in escape_chars else c for c in text)


# -------------------------------------------------
# Create or get an existing user
# -------------------------------------------------
async def get_or_create_user(
    session: AsyncSession,
    tg_id: int,
    username: str | None = None
) -> User:
    """
    Fetch a User by Telegram ID, or create one if not exists.
    Uses the provided AsyncSession (don't close it here).
    """
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    user = result.scalar_one_or_none()

    if user:
        if username and user.username != username:
            user.username = username
            await session.commit()
        return user

    # New user
    user = User(tg_id=tg_id, username=username)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


# -------------------------------------------------
# Add tries (paid or bonus)
# -------------------------------------------------
async def add_tries(session: AsyncSession, user_or_id, count: int, paid: bool = True) -> User:
    """
    Increment user's tries (paid or bonus) inside an active session.
    Also updates GameState and GlobalCounter for paid tries.

    Supports both a full User object or a user_id (int).
    NOTE: This function does not commit — caller must handle commit.
    """

    # ✅ Accept either User object or user_id
    user_id = user_or_id.id if hasattr(user_or_id, "id") else user_or_id

    # ✅ Fetch user
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning(f"⚠️ Tried to add tries to non-existent user_id={user_id}")
        return None

    logger.info(f"🌀 Adding {count} {'paid' if paid else 'bonus'} tries → user_id={user.id}")

    if paid:
        # 🪙 Paid tries increment
        user.tries_paid = (user.tries_paid or 0) + count

        # ✅ Ensure GlobalCounter exists
        gc = await session.get(GlobalCounter, 1)
        if not gc:
            gc = GlobalCounter(id=1, paid_tries_total=0)
            session.add(gc)
            await session.flush()

        # ✅ Ensure GameState exists
        gs = await session.get(GameState, 1)
        if not gs:
            gs = GameState(
                id=1,
                current_cycle=1,
                paid_tries_this_cycle=0,
                lifetime_paid_tries=0,
                created_at=datetime.utcnow(),
            )
            session.add(gs)
            await session.flush()

        # ✅ Update counters
        gc.paid_tries_total = (gc.paid_tries_total or 0) + count
        gs.paid_tries_this_cycle = (gs.paid_tries_this_cycle or 0) + count
        gs.lifetime_paid_tries = (gs.lifetime_paid_tries or 0) + count

        session.add_all([gc, gs])
        logger.info(
            f"📊 Updated GameState → lifetime={gs.lifetime_paid_tries}, cycle={gs.paid_tries_this_cycle}"
        )

    else:
        # 🎁 Bonus tries increment (for admin-approved proofs, etc.)
        user.tries_bonus = (user.tries_bonus or 0) + count
        logger.info(f"🎁 Added {count} bonus tries → user_id={user.id}")

    # ✅ Update user record
    session.add(user)
    await session.flush()
    await session.refresh(user)

    logger.info(
        f"✅ User {user.id} now has paid={user.tries_paid}, bonus={user.tries_bonus}"
    )

    return user

# -------------------------------------------------
# Consume one try (bonus first, then paid)
# -------------------------------------------------
async def consume_try(session: AsyncSession, user: User):
    """
    Deduct one try. Uses bonus first, then paid.
    Returns:
      - "bonus" if a bonus try was used
      - "paid" if a paid try was used
      - None if no tries left
    NOTE: This function does not commit — caller must handle commit.
    """
    logger.info(
        f"🎲 Attempting to consume try for user_id={user.id} "
        f"(paid={user.tries_paid}, bonus={user.tries_bonus})"
    )

    if user.tries_bonus and user.tries_bonus > 0:
        user.tries_bonus -= 1
        await session.flush()
        logger.info(f"➖ Consumed 1 bonus try → user_id={user.id}, remaining bonus={user.tries_bonus}")
        return "bonus"

    if user.tries_paid and user.tries_paid > 0:
        user.tries_paid -= 1
        await session.flush()
        logger.info(f"➖ Consumed 1 paid try → user_id={user.id}, remaining paid={user.tries_paid}")
        return "paid"

    logger.warning(f"⚠️ No tries left to consume for user_id={user.id}")
    return None


# -------------------------------------------------
# Get user by DB ID
# -------------------------------------------------
async def get_user_by_id(session: AsyncSession, user_id) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


# -------------------------------------------------
# Record a play
# -------------------------------------------------
async def record_play(session: AsyncSession, user: User, result: str):
    play = Play(user_id=user.id, result=result)
    session.add(play)
    await session.commit()
    await session.refresh(play)

    logger.info(
        f"📝 Recorded play → play_id={play.id}, user_id={user.id}, result='{result}'"
    )

    return play


# -------------------------------------------------
# Check if a user is admin
# -------------------------------------------------
def is_admin(user: User) -> bool:
    """Return True if the user is marked as admin."""
    return getattr(user, "is_admin", False)


# ----------------------------
# 🧩 Mask Sensitive Helper
# ----------------------------
def mask_sensitive(data: str, visible: int = 4) -> str:
    """Mask all but last few visible characters of sensitive data."""
    if not data:
        return ""
    data = str(data)
    if len(data) <= visible:
        return data
    return f"{'*' * (len(data) - visible)}{data[-visible:]}"

# -------------------------------
# 🚦 Rate Limiting Helper
# --------------------------------
import time

_LAST_WEBHOOK_CALL = {}
_RATE_LIMIT_SECONDS = 10

def is_rate_limited(tx_ref: str) -> bool:
    """Prevents flooding by blocking the same tx_ref within RATE_LIMIT_SECONDS."""
    now = time.time()
    last_call = _LAST_WEBHOOK_CALL.get(tx_ref, 0)
    if now - last_call < _RATE_LIMIT_SECONDS:
        return True
    _LAST_WEBHOOK_CALL[tx_ref] = now
    return False

