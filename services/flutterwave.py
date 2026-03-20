# ================================================================
# services/flutterwave.py
# ===============================================================
import uuid
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    FLUTTERWAVE_SECRET_KEY,
    FLUTTERWAVE_REDIRECT_URL,
    APP_LOGO_URL,
)


async def create_flutterwave_checkout_link(
    *,
    session: AsyncSession | None = None,
    tg_id: int,
    amount: int,
    username: str | None = None,
    email: str | None = None,
    tx_ref: str | None = None,
    meta: dict | None = None,
    product_type: str = "TRIVIA",
) -> str:
    """
    Creates a Flutterwave hosted checkout link.

    IMPORTANT:
    - This function does NOT create DB payment rows.
    - The caller is responsible for creating pending payment records.
    """

    if not tx_ref:
        prefix = "TRIVIA" if product_type.upper() == "TRIVIA" else "GEN"
        tx_ref = f"{prefix}-{uuid.uuid4().hex[:12].upper()}"

    customer_email = (
        email if email and "@" in email else f"user_{tg_id}@naijaprizegate.ng"
    )
    safe_username = (username or f"User {tg_id}")[:64]

    payload = {
        "tx_ref": tx_ref,
        "amount": amount,
        "currency": "NGN",
        "redirect_url": FLUTTERWAVE_REDIRECT_URL,
        "customer": {
            "email": customer_email,
            "name": safe_username,
        },
        "customizations": {
            "title": "NaijaPrizeGate",
            "description": (
                "Trivia attempts purchase"
                if product_type.upper() == "TRIVIA"
                else "Payment"
            ),
            "logo": APP_LOGO_URL,
        },
        "meta": meta or {
            "tg_id": tg_id,
            "username": safe_username,
            "purpose": product_type.upper(),
        },
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.flutterwave.com/v3/payments",
            json=payload,
            headers={
                "Authorization": f"Bearer {FLUTTERWAVE_SECRET_KEY}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return data["data"]["link"]
