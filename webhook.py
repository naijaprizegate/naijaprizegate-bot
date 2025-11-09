# =====================================================================
# webhook.py
# =====================================================================
from fastapi import APIRouter, Request
from loguru import logger
from handlers.payments import handle_payment_success
from helpers import is_rate_limited

# Import your helpers
from app import bot  # Import your bot object (if defined elsewhere)

router = APIRouter()

@router.post("/webhook")
async def webhook_listener(request: Request):
    """
    Receives payment confirmations from Flutterwave or your payment service.
    Applies rate-limit protection and then processes the payment.
    """
    data = await request.json()

    # Extract payment data
    tx_ref = data.get("tx_ref")
    amount = data.get("amount")
    user_id = data.get("user_id")
    tries = data.get("tries")

    # 🛡️ Step 1: Rate-limit protection
    if is_rate_limited(tx_ref):
        logger.warning(f"🚫 Duplicate webhook within 10s for tx_ref={tx_ref}")
        return {"status": "ignored", "reason": "too frequent"}

    # ✅ Step 2: Process the successful payment
    await handle_payment_success(tx_ref, amount, user_id, tries, bot)
    return {"status": "ok"}
