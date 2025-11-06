# ===============================================================
# utils/security.py
# ===============================================================
import os
import itsdangerous
from telegram import Update
from sqlalchemy import select
from models import User
from db import get_async_session  # adjust path if needed


# ✅ Admin check
async def is_admin(update: Update) -> bool:
    user_id = update.effective_user.id

    async with get_async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == user_id))
        user = result.scalar_one_or_none()

    return bool(user and user.is_admin)


# -------------------------------------------
# 🔐 Secure token generation for winner forms
# -------------------------------------------
SECRET_KEY = os.getenv("FORM_SIGNING_SECRET", "supersecret")  # override in .env or Render
serializer = itsdangerous.URLSafeTimedSerializer(SECRET_KEY)


def generate_signed_link(tgid, choice):
    """Generate a signed token that expires after 1 hour."""
    return serializer.dumps({"tgid": tgid, "choice": choice})


def verify_signed_link(token, max_age=3600):
    """Validate signed token and return decoded data if valid."""
    try:
        return serializer.loads(token, max_age=max_age)
    except itsdangerous.BadSignature:
        return None
