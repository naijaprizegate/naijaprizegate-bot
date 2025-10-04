#================================================================
# services/payments.py
# ================================================================
import os
import httpx
import hmac
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from db import get_async_session
from models import Payment, TransactionLog, GlobalCounter
from sqlalchemy.future import select
from models import Payment, GlobalCounter, User
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


# ==== Config ====
FLW_BASE_URL = "https://api.flutterwave.com/v3"
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")  # used to validate webhook requests

# Guarantee win after this many paid tries
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "14600"))

# Setup logger
logger = logging.getLogger("payments")
logger.setLevel(logging.INFO)


# ------------------------------------------------------
# 1. Create Checkout (generate payment link for a user)
# ------------------------------------------------------
async def create_checkout(user_id: str, amount: int, tx_ref: str, username: str = None, email: str = None) -> str:
    """
    Creates a Flutterwave payment checkout link.
    - Uses Telegram email if available, otherwise falls back to username or user_id-based dummy email.
    """
    url = f"{FLW_BASE_URL}/payments"
    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}"}

    # Build a safe customer email
    if email:
        customer_email = email
    elif username:
        customer_email = f"{username}@naijaprizegate.ng"
    else:
        customer_email = f"user{user_id}@naijaprizegate.ng"

    payload = {
        "tx_ref": tx_ref,
        "amount": amount,
        "currency": "NGN",
        "redirect_url": "https://naijaprizegate-bot-oo2x.onrender.com/flw/redirect",  # optional
        "customer": {
            "email": customer_email,
            "name": username or f"User {user_id}"
        },
        "customizations": {"title": "NaijaPrizeGate"},
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()

    # 🔥 Log the full Flutterwave response for debugging
    logger.info(f"✅ Flutterwave checkout created for {customer_email}: {data}")

    return data["data"]["link"]


# ----------------------------------------------------
# Verify Payment (with Telegram deep link)
# ----------------------------------------------------
async def verify_payment(
    tx_ref: str,
    session: AsyncSession,
    bot=None,
    credit: bool = True
) -> bool:
    """
    Verifies payment status from Flutterwave.
    - If `credit=True` (webhook), credit user if not already credited.
    - If `credit=False` (redirect), only update DB + return status (no crediting).
    """

    url = f"{FLW_BASE_URL}/transactions?tx_ref={tx_ref}"
    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.exception(f"❌ Failed to verify payment {tx_ref}: {e}")
            return False

    logger.info(f"🔎 Flutterwave verification for {tx_ref}: {data}")

    if data.get("status") != "success" or not data.get("data"):
        logger.warning(f"⚠️ No transaction found for {tx_ref}")
        return False

    tx_data = data["data"][0]
    tx_status = tx_data.get("status")
    amount = tx_data.get("amount")

    stmt = select(Payment).where(Payment.tx_ref == tx_ref)
    result = await session.execute(stmt)
    payment = result.scalar_one_or_none()

    if not payment:
        logger.warning(f"⚠️ No Payment record found for {tx_ref}")
        return False

    # Always update DB
    payment.amount = amount
    payment.updated_at = datetime.utcnow()

    # ----------------- SUCCESS -----------------
    if tx_status == "successful" and payment.status != "successful":
        payment.status = "successful"

        if credit:
            try:
                # --- Credit user tries ---
                user, tries = await credit_user_tries(session, payment)
                logger.info(f"🎉 Credited {tries} tries to user {user.id} (tg_id={user.tg_id})")

                await session.commit()  # Commit DB changes before sending message

                # --- Telegram notify with deep link ---
                if bot and user.tg_id:
                    deep_link = f"https://t.me/NaijaPrizeGateBot?start=payment_success_{tx_ref}"
                    keyboard = [
                        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
                        [InlineKeyboardButton("🎟️ My Tries", callback_data="mytries")],
                        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await bot.send_message(
                        chat_id=user.tg_id,
                        text=(
                            f"✅ Payment of ₦{amount} verified!\n\n"
                            f"You’ve been credited with <b>{tries}</b> tries 🎉\n\n"
                            f"Ref: <code>{tx_ref}</code>\n\n"
                            f"👉 [Return to Bot]({deep_link})"
                        ),
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                        disable_web_page_preview=True
                    )

            except Exception as e:
                logger.exception(f"❌ Failed to credit or notify user {payment.user_id}: {e}")
                await session.rollback()
                return False

            # --- Global counter update ---
            try:
                counter_stmt = select(GlobalCounter).limit(1)
                counter_result = await session.execute(counter_stmt)
                counter = counter_result.scalar_one_or_none()

                if not counter:
                    counter = GlobalCounter(paid_tries_total=0)
                    session.add(counter)
                    await session.flush()

                counter.paid_tries_total += 1
                if counter.paid_tries_total >= WIN_THRESHOLD:
                    counter.paid_tries_total = 0
                    logger.info(f"🎉 WIN threshold {WIN_THRESHOLD} reached → counter reset!")

            except Exception as e:
                logger.warning(f"⚠️ Could not update GlobalCounter: {e}")

        await session.commit()
        return True

    # ----------------- FAILED / EXPIRED -----------------
    elif tx_status in ["failed", "expired"]:
        payment.status = tx_status
        await session.commit()
        return False

    # ----------------- PENDING -----------------
    await session.commit()
    return False

# ------------------------------------------------------
# 3. Validate Webhook Signature
# ------------------------------------------------------
def validate_webhook(request_headers, body: str) -> bool:
    """
    Validates Flutterwave webhook using the verif-hash header.
    """
    signature = request_headers.get("verif-hash")
    if not FLW_SECRET_HASH or not signature:
        return False
    return hmac.compare_digest(signature, FLW_SECRET_HASH)


# ------------------------------------------------------
# 4. Log Raw Transaction Payload
# ------------------------------------------------------
async def log_transaction(session: AsyncSession, provider: str, payload: str):
    """
    Logs raw webhook payload into transaction_logs for debugging.
    """
    log = TransactionLog(provider=provider, payload=payload)
    session.add(log)
    await session.commit()

# ----------------------------------------------------
# 5. Credit User Tries (Final)
# ----------------------------------------------------
from models import User, Payment
from logger import logger

# Conversion table (amount → number of tries)
PRICE_TO_TRIES = {
    500: 1,
    2000: 5,
    5000: 15,
}

async def credit_user_tries(session, payment: Payment):
    """
    Safely credits a user with tries based on Payment.amount.
    - Idempotent: will not credit again if already credited.
    - Updates both User.tries_paid and Payment.credited_tries.
    - Returns (success: bool, tries: int).
    """

    # 1️⃣ Load user
    user = await session.get(User, payment.user_id)
    if not user:
        logger.error(f"❌ No user found for payment {payment.tx_ref}")
        return False, 0

    # 2️⃣ Prevent double-credit
    if payment.credited_tries and payment.credited_tries > 0:
        logger.info(f"ℹ️ Payment {payment.tx_ref} already credited with {payment.credited_tries} tries → skipping")
        return True, payment.credited_tries

    # 3️⃣ Calculate tries from amount
    tries = PRICE_TO_TRIES.get(int(payment.amount), 0)
    if tries <= 0:
        logger.warning(f"⚠️ No tries mapping for amount {payment.amount} (Payment {payment.tx_ref})")
        return False, 0

    # 4️⃣ Apply credit
    user.tries_paid = (user.tries_paid or 0) + tries
    payment.credited_tries = tries  # ✅ record credited amount in Payment

    # Persist changes
    await session.commit()

    logger.info(f"🎉 Credited {tries} tries to user {user.tg_id} ({user.username}), payment {payment.tx_ref}")

    return True, tries
