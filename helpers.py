#===============================================================
# helpers.py
#===============================================================
import html
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from models import User, GlobalCounter, Play


# Escape MarkdownV2 for Telegram messages
def md_escape(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in escape_chars else c for c in text)


# Create or get an existing user
async def get_or_create_user(session: AsyncSession, tg_id: int, username: str = None) -> User:
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    user = result.scalar_one_or_none()

    if not user:
        user = User(tg_id=tg_id, username=username)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


# Add tries (paid or bonus)
async def add_tries(session: AsyncSession, user: User, count: int, paid: bool = True):
    if paid:
        user.tries_paid += count
    else:
        user.tries_bonus += count
    await session.commit()


# Consume one try (bonus first, then paid)
async def consume_try(session: AsyncSession, user: User) -> str:
    if user.tries_bonus > 0:
        user.tries_bonus -= 1
        await session.commit()
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
        return new_count  # Return counter value for win-check

    return None

# Get user by UUID (database id)
async def get_user_by_id(session: AsyncSession, user_id) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()

# Record a play
async def record_play(session: AsyncSession, user: User, result: str):
    play = Play(user_id=user.id, result=result)
    session.add(play)
    await session.commit()
    return play

# Check if a user is admin
def is_admin(user: User) -> bool:
    """
    Return True if the user is marked as admin.
    Assumes the User model has an `is_admin` boolean column.
    """
    return getattr(user, "is_admin", False)

