# ===============================================================
# utils/security.py
# ===============================================================
import os
import itsdangerous
from telegram import Update
from sqlalchemy import select
from models import User
from db import get_async_session  # adjust path if needed
import re  # Added for phone validation


# ---------------------------------------------------------------
# 📍 Nigerian Phone Validation (Skill-Based Reward Compliance)
# ---------------------------------------------------------------
# Valid Nigerian mobile prefixes
VALID_PREFIXES = (
    "0701", "0702", "0703", "0704", "0705",
    "0802", "0803", "0804", "0805", "0806",
    "0810", "0811", "0812", "0813", "0814", "0815",
    "0901", "0902", "0903", "0904", "0905",
    "0911", "0912", "0913", "0915", "0916"
)

# Carrier group identification
PROVIDERS = {
    "MTN": {"0703", "0706", "0803", "0806", "0813", "0816", "0903", "0906", "0916"},
    "Airtel": {"0701", "0708", "0802", "0808", "0812", "0901", "0902", "0904"},
    "Glo": {"0705", "0805", "0807", "0811", "0905", "0915"},
    "9mobile": {"0809", "0817", "0818", "0909", "0908"},
}

def validate_phone(phone: str) -> bool:
    """Validate Nigerian phone number formatting."""
    if not phone:
        return False

    phone = phone.strip().replace(" ", "").replace("-", "")

    # Convert +234xxxxxxxxxx → 0xxxxxxxxxx
    if phone.startswith("+234"):
        phone = "0" + phone[4:]

    if not phone.isdigit() or len(phone) != 11:
        return False

    return phone[:4] in VALID_PREFIXES


def detect_provider(phone: str) -> str | None:
    """Return MTN / Airtel / Glo / 9mobile based on prefix."""
    if not phone:
        return None

    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+234"):
        phone = "0" + phone[4:]

    prefix = phone[:4]
    for provider, prefixes in PROVIDERS.items():
        if prefix in prefixes:
            return provider

    return None


# ---------------------------------------------------------------
# 👮 Admin check
# ---------------------------------------------------------------
async def is_admin(update: Update) -> bool:
    user_id = update.effective_user.id

    async with get_async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == user_id))
        user = result.scalar_one_or_none()

    return bool(user and user.is_admin)


# ---------------------------------------------------------------
# 🔐 Secure token handling for Winner Forms
# ---------------------------------------------------------------
SECRET_KEY = os.getenv("FORM_SIGNING_SECRET", "supersecret")
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
