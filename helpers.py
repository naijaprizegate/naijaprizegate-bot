# ===============================================================
# helpers.py
# ===============================================================
import html
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from models import User, GlobalCounter, Play


# Escape MarkdownV2 for Telegram messages
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
        # update username if changed
        if username and user.username != username:
            user.username = username
            await session.commit()
        return user

    # Create new user
    user = User(tg_id=tg_id, username=username)
    session.add(user)
    await session.commit()
    await session.refresh(user)  # ensures ID and defaults are loaded
    return user


# -------------------------------------------------
# Add tries (paid or bonus)
# -------------------------------------------------
async def add_tries(session: AsyncSession, user: User, count: int, paid: bool = True) -> User:
    """
    Increment user's tries (paid or bonus) inside an active session.
    NOTE: This function does not commit — caller must handle commit.
    """
    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"🌀 Adding {count} {'paid' if paid else 'bonus'} tries → user_id={user.id}")

    if paid:
        user.tries_paid = (user.tries_paid or 0) + count
    else:
        user.tries_bonus = (user.tries_bonus or 0) + count

    session.add(user)  # ensure user is tracked
    await session.flush()      # stage changes
    await session.refresh(user)  # refresh values from DB

    logger.info(
        f"✅ User {user.id} now has paid={user.tries_paid}, bonus={user.tries_bonus} after adding {count}"
    )
    return user


# -------------------------------------------------
# Consume one try (bonus first, then paid)
# -------------------------------------------------
async def consume_try(session: AsyncSession, user: User):
    """
    Deduct one try. Uses bonus first, then paid.
    Returns:
      - "bonus" if bonus used
      - int new_global_count if paid used
      - None if no tries left
    """
    import logging
    logger = logging.getLogger(__name__)


    logger.info(
        f"🎲 Attempting to consume try for user_id={user.id} "
        f"(paid={user.tries_paid}, bonus={user.tries_bonus})"
    )

    if user.tries_bonus > 0:
        user.tries_bonus -= 1
        await session.commit()
        logger.info(
            f"➖ Consumed 1 bonus try → user_id={user.id}, remaining bonus={user.tries_bonus}"
        )
        return "bonus"

    if user.tries_paid > 0:
        user.tries_paid -= 1

        # Atomically increment global counter
        result = await session.execute(
            update(GlobalCounter)
            .where(GlobalCounter.id == 1)
            .values(paid_tries_total=GlobalCounter.paid_tries_total + 1)
            .returning(GlobalCounter.paid_tries_total)
        )
        new_count = result.scalar_one()
        await session.commit()

        logger.info(
            f"➖ Consumed 1 paid try → user_id={user.id}, remaining paid={user.tries_paid}, "
            f"new_global_count={new_count}"
        )
        return new_count

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
    import logging
    logger = logging.getLogger(__name__)


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
    """
    Return True if the user is marked as admin.
    Assumes the User model has an `is_admin` boolean column.
    """
    return getattr(user, "is_admin", False)
