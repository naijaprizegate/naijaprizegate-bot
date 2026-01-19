import uuid
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from models import Payment
from config import (
    FLUTTERWAVE_SECRET_KEY,
    FLUTTERWAVE_REDIRECT_URL,
    APP_LOGO_URL,
)

async def create_flutterwave_checkout_link(
    *,
    session: AsyncSession,
    tg_id: int,
    amount: int,
    username: str | None = None,
    email: str | None = None,
) -> str:
    """
    Creates a Flutterwave hosted checkout link for TRIVIA purchases.
    This is the SINGLE source of tx_ref.
    """

    tx_ref = f"TRIVIA-{uuid.uuid4()}"

    customer_email = (
        email if email and "@" in email else f"user_{tg_id}@naijaprizegate.ng"
    )
    safe_username = (username or f"User {tg_id}")[:64]

    payment = Payment(
        tx_ref=tx_ref,
        tg_id=tg_id,
        username=safe_username,
        amount=amount,
        status="pending",
        credited_tries=0,
    )
    session.add(payment)
    await session.commit()

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
            "title": "NaijaPrizeGate Trivia",
            "description": "Trivia attempts purchase",
            "logo": APP_LOGO_URL,
        },
        "meta": {
            "tg_id": tg_id,
            "username": safe_username,
            "purpose": "TRIVIA",
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
