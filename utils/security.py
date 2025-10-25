# ===============================================================
# utils/security.py
# ===============================================================
from telegram import Update
from sqlalchemy import select
from models import User
from database import get_async_session  # adjust path if needed


# ✅ Checks if the user is an admin (via DB or known ID)
async def is_admin(update: Update) -> bool:
    user_id = update.effective_user.id

    async with get_async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == user_id))
        user = result.scalar_one_or_none()

    return bool(user and user.is_admin)
