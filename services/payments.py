# ================================================================
# services/payments.py
# ================================================================
import os
import httpx
import hmac
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from db import get_async_session
from models import Payment, TransactionLog, GlobalCounter, User

# ==== Config ====
FLW_BASE_URL = "https://api.flutterwave.com/v3"
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")  # used to validate webhook requests
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "14600"))

# ==== Logger Setup ====
logger = logging.getLogger("payments")
logger.setLevel(logging.INFO)


# ------------------------------------------------------
# 1. Create Checkout (generate payment link for a user)
# ------------------------------------------------------
async def create_checkout(user_id: str, amount: int, tx_ref: str, username: str = None, email: str = None) -> str:
    """
    Creates a Flutterwave payment checkout link.
    """
    url = f"{FLW_BASE_URL}/payments"
    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}"}

    # Build a safe customer email
    customer_email = email or (f"{username}@naijaprizegate.ng" if username else f"user{user_id}@naijaprizegate.ng")

    payload = {
        "tx_ref": tx_ref,
        "amount": amount,
        "currency": "NGN",
        "redirect_url": "https://naijaprizegate-bot-oo2x.onrender.com/flw/redirect",
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

    logger.info(f"✅ Flutterwave checkout created for {customer_email}: {data}")
    return data["data"]["link"]


# ------------------------------------------------------
# 2. Verify Payment (via tx_ref)
# ------------------------------------------------------
async def verify_payment(tx_ref: str, session: AsyncSession, bot=None, credit: bool = True) -> bool:
    """
    Verifies payment status with Flutterwave.
    - If credit=True: credit the user and notify via Telegram.
    - If credit=False: only updates DB.
    """

    url = f"{FLW_BASE_URL}/transactions/verify_by_reference?tx_ref={tx_ref}"
    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}"}

    # --- Step 1: Call Flutterwave API ---
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.exception(f"❌ Verification failed for {tx_ref}: {e}")
        return False

    logger.info(f"🔎 Flutterwave verification for {tx_ref}: {data}")

    if not data.get("status") == "success" or not data.get("data"):
        logger.warning(f"⚠️ Invalid response for tx_ref={tx_ref}")
        return False

    tx_data = data["data"]
    tx_status = tx_data.get("status")
    amount = tx_data.get("amount")

    # --- Step 2: Get payment record from DB ---
    result = await session.execute(select(Payment).where(Payment.tx_ref == tx_ref))
    payment = result.scalar_one_or_none()

    if not payment:
        logger.warning(f"⚠️ No Payment record found for {tx_ref}")
        return False

    payment.amount = amount
    payment.updated_at = datetime.utcnow()

    # ----------------- SUCCESS CASE -----------------
    if tx_status == "successful":
        if payment.status == "successful":
            logger.info(f"ℹ️ Payment {tx_ref} already marked successful → skipping re-credit")
            return True

        if credit:
            try:
                logger.info(f"💳 Crediting user {payment.user_id} for tx_ref={tx_ref} ...")
                user, tries = await credit_user_tries(session, payment)

                # ✅ Mark successful only AFTER tries credited
                payment.status = "successful"
                await session.commit()
                logger.info(f"✅ User {user.tg_id} credited with {tries} tries for tx_ref={tx_ref}")

                # --- Notify user on Telegram ---
                if bot and user and user.tg_id:
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
                logger.exception(f"❌ Failed to credit user {payment.user_id} for tx_ref={tx_ref}: {e}")
                await session.rollback()
                return False

            # --- Update Global Counter ---
            try:
                counter_result = await session.execute(select(GlobalCounter).limit(1))
                counter = counter_result.scalar_one_or_none()
                if not counter:
                    counter = GlobalCounter(paid_tries_total=0)
                    session.add(counter)
                    await session.flush()

                counter.paid_tries_total += 1
                if counter.paid_tries_total >= WIN_THRESHOLD:
                    counter.paid_tries_total = 0
                    logger.info(f"🎉 WIN threshold {WIN_THRESHOLD} reached → counter reset!")
                await session.commit()
            except Exception as e:
                logger.warning(f"⚠️ Could not update GlobalCounter for tx_ref={tx_ref}: {e}")

        return True

    # ----------------- FAILED / EXPIRED -----------------
    elif tx_status in ["failed", "expired"]:
        payment.status = tx_status
        await session.commit()
        logger.info(f"❌ Payment {tx_ref} marked as {tx_status}")
        return False

    # ----------------- STILL PENDING -----------------
    await session.commit()
    logger.info(f"⏳ Payment {tx_ref} still pending (status={tx_status})")
    return False


# ------------------------------------------------------
# 3. Validate Webhook Signature
# ------------------------------------------------------
def validate_webhook(request_headers, body: str) -> bool:
    signature = request_headers.get("verif-hash")
    if not FLW_SECRET_HASH or not signature:
        return False
    return hmac.compare_digest(signature, FLW_SECRET_HASH)


# ------------------------------------------------------
# 4. Log Raw Transaction Payload
# ------------------------------------------------------
async def log_transaction(session: AsyncSession, provider: str, payload: str):
    log = TransactionLog(provider=provider, payload=payload)
    session.add(log)
    await session.commit()


# ------------------------------------------------------ 
# 5. Credit User Tries
# ------------------------------------------------------ 
PRICE_TO_TRIES = {
    500: 1,
    2000: 5,
    5000: 15,
}

def calculate_tries(amount: int) -> int:
    """
    Returns number of tries for given amount.
    - Exact matches use PRICE_TO_TRIES
    - Otherwise fallback: 1 try per ₦500
    """
    if amount in PRICE_TO_TRIES:
        return PRICE_TO_TRIES[amount]
    return max(1, amount // 500)  # never return 0 for valid payments


async def credit_user_tries(session, payment: Payment):
    """
    Credits a user's account with tries based on the payment amount.
    Ensures we don't double-credit the same payment.
    NOTE: Commit is controlled by caller (verify_payment).
    """
    user = await session.get(User, payment.user_id)
    if not user:
        logger.error(f"❌ No user found for payment {payment.tx_ref}")
        return None, 0

    # Prevent double-credit
    if payment.credited_tries and payment.credited_tries > 0:
        logger.info(f"ℹ️ Payment {payment.tx_ref} already credited → skipping")
        return user, payment.credited_tries

    tries = calculate_tries(int(payment.amount))
    if tries <= 0:
        logger.warning(f"⚠️ No tries mapping for amount {payment.amount}")
        return user, 0

    # ✅ Update user balance
    user.tries_paid = (user.tries_paid or 0) + tries
    payment.credited_tries = tries

    await session.flush()  # only stage changes, do not commit here
    logger.info(f"🎉 Credited {tries} tries to user {user.tg_id} ({user.username}) — tx_ref={payment.tx_ref}")

    return user, tries

