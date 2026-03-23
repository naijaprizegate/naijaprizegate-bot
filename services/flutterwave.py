# ================================================================
# services/flutterwave.py
# ================================================================
import uuid
import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    FLUTTERWAVE_SECRET_KEY,
    FLUTTERWAVE_REDIRECT_URL,
    APP_LOGO_URL,
)

logger = logging.getLogger(__name__)


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
    Create a Flutterwave hosted checkout link.

    Notes:
    - This function does NOT create DB payment rows.
    - The caller must create any pending payment record first.
    - If tx_ref is provided, it will be used exactly as given.
    - Supports both TRIVIA and JAMB purchases.
    """

    product_type = (product_type or "TRIVIA").upper().strip()

    if not tx_ref:
        prefix = "TRIVIA" if product_type == "TRIVIA" else "GEN"
        tx_ref = f"{prefix}-{uuid.uuid4().hex[:12].upper()}"

    customer_email = (
        email.strip()
        if email and "@" in email
        else f"user_{tg_id}@naijaprizegate.ng"
    )
    safe_username = (username or f"User {tg_id}").strip()[:64]

    description_map = {
        "TRIVIA": "Trivia attempts purchase",
        "JAMB": "JAMB question credits purchase",
    }

    fallback_meta = {
        "tg_id": tg_id,
        "username": safe_username,
        "purpose": product_type,
    }

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
            "description": description_map.get(product_type, "Payment"),
            "logo": APP_LOGO_URL,
        },
        "meta": meta or fallback_meta,
    }

    logger.info(
        "🟡 Creating Flutterwave checkout | product_type=%s | tx_ref=%s | tg_id=%s | amount=%s",
        product_type,
        tx_ref,
        tg_id,
        amount,
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
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
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = e.response.text
        except Exception:
            body = "<unavailable>"
        logger.error(
            "❌ Flutterwave checkout HTTP error | tx_ref=%s | status=%s | body=%s",
            tx_ref,
            e.response.status_code if e.response else "unknown",
            body,
        )
        raise
    except Exception as e:
        logger.error(
            "❌ Flutterwave checkout request failed | tx_ref=%s | error=%s",
            tx_ref,
            e,
            exc_info=True,
        )
        raise

    payment_link = (data.get("data") or {}).get("link")
    if not payment_link:
        logger.error(
            "❌ Flutterwave response missing payment link | tx_ref=%s | response=%s",
            tx_ref,
            data,
        )
        raise ValueError("Flutterwave response missing payment link")

    logger.info(
        "🟢 Flutterwave checkout created | product_type=%s | tx_ref=%s",
        product_type,
        tx_ref,
    )

    return payment_link
