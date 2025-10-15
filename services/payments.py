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
from helpers import add_tries  # ✅ FIX: Missing import


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
        "customizations": {
            "title": "NaijaPrizeGate",
        },
        # ✅ Add meta info so webhook knows who paid
        "meta": {
            "tg_id": str(user_id),
            "username": username or "Anonymous",
        },
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
    # normalize tx_ref
    tx_ref = str(tx_ref)

    url = f"{FLW_BASE_URL}/transactions/verify_by_reference?tx_ref={tx_ref}"
    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.exception(f"❌ Verification failed for {tx_ref}: {e}")
        return False

    logger.info(f"🔎 Flutterwave verification for {tx_ref}: {data}")

    # sanity checks
    if not data.get("status") == "success" or not data.get("data"):
        logger.warning(f"⚠️ Invalid or empty response for tx_ref={tx_ref}")
        return False

    tx_data = data["data"]
    tx_status = tx_data.get("status")
    amount = tx_data.get("amount")

    # accept both shapes flutterwave might send
    meta = tx_data.get("meta") or tx_data.get("meta_data") or {}
    logger.info(f"🔍 verify_payment meta for {tx_ref}: {meta}")

    # normalize tg_id (we store User.tg_id as BigInteger)
    tg_id_raw = meta.get("tg_id") or meta.get("tgId") or meta.get("customer_id")
    tg_id = None
    if tg_id_raw is not None:
        try:
            tg_id = int(tg_id_raw)
        except Exception:
            # if it's not an int-like value, keep None (defensive)
            logger.warning(f"⚠️ Could not parse tg_id from meta for {tx_ref}: {tg_id_raw}")
            tg_id = None

    username = meta.get("username") or meta.get("name") or None

    # find payment row if exists
    result = await session.execute(select(Payment).where(Payment.tx_ref == tx_ref))
    payment = result.scalar_one_or_none()

    # create placeholder payment if missing
    if not payment:
        logger.warning(f"⚠️ No Payment record found for {tx_ref} → creating a new one.")
        from models import User  # local import to avoid circulars
        user = None
        if tg_id is not None:
            result = await session.execute(select(User).where(User.tg_id == tg_id))
            user = result.scalar_one_or_none()
            if not user:
                # optional safety-net: create a user row so we can link payment
                user = User(tg_id=tg_id, username=username)
                session.add(user)
                await session.flush()  # give user.id

        payment = Payment(
            tx_ref=str(tx_ref),
            amount=amount,
            status=tx_status or "pending",
            user_id=user.id if user else None,
            credited_tries=0,
            # helpful debug/link columns
            tg_id=tg_id if tg_id is not None else None,
            username=username
        )

        # flw id may be numeric — always cast to str if present
        if tx_data.get("id") is not None:
            payment.flw_tx_id = str(tx_data.get("id"))

        session.add(payment)
        await session.commit()
        logger.info(f"🆕 Payment placeholder created for tx_ref={tx_ref} (linked_user_tg={tg_id})")

    # update fields from verification
    payment.amount = amount
    if tx_data.get("id") is not None:
        # ensure stored as string
        payment.flw_tx_id = str(tx_data.get("id"))
    payment.updated_at = datetime.utcnow()

    # backfill missing user_id from meta (if available)
    if not payment.user_id and tg_id is not None:
        from models import User
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if user:
            payment.user_id = user.id
            logger.info(f"🔗 Linked payment {tx_ref} to user {user.tg_id} from meta data")

    # handle outcomes
    if tx_status == "successful":
        if payment.status == "successful":
            logger.info(f"ℹ️ Payment {tx_ref} already marked successful → skipping re-credit")
            # Still update flw_tx_id/amount if needed and commit
            await session.commit()
            return True

        if credit:
            try:
                logger.info(f"💳 Crediting user {payment.user_id} for tx_ref={tx_ref} ...")
                user, tries = await credit_user_tries(session, payment)

                payment.status = "successful"
                await session.commit()
                logger.info(f"✅ User {getattr(user,'tg_id', None)} credited with {tries} tries for tx_ref={tx_ref}")

                # notify user via Telegram if bot provided
                if bot and user and getattr(user, "tg_id", None):
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

            # update global counter (best-effort)
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

    elif tx_status in ["failed", "expired"]:
        payment.status = tx_status
        await session.commit()
        logger.info(f"❌ Payment {tx_ref} marked as {tx_status}")
        return False

    # pending / unknown
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
    """Convert amount (₦) to number of tries."""
    if amount in PRICE_TO_TRIES:
        return PRICE_TO_TRIES[amount]
    # fallback rule: 1 try per ₦500
    return max(1, amount // 500)


async def credit_user_tries(session, payment: Payment):
    """
    Safely credit user tries based on a verified payment.
    - Ensures payment.user_id exists and is valid.
    - Prevents double-crediting.
    - Logs each step for debugging.
    """

    # ✅ Step 1: Sanity check for user_id
    if not payment.user_id:
        logger.error(f"❌ credit_user_tries: payment {payment.tx_ref} has no user_id linked.")
        return None, 0

    # ✅ Step 2: Fetch user by UUID (linked via payment.user_id)
    try:
        user = await session.get(User, payment.user_id)
    except Exception as e:
        logger.exception(f"❌ Failed to fetch user {payment.user_id} for tx_ref={payment.tx_ref}: {e}")
        return None, 0

    if not user:
        logger.error(f"❌ No user found for payment {payment.tx_ref} (user_id={payment.user_id})")
        return None, 0

    # ✅ Step 3: Skip if already credited
    if payment.credited_tries and payment.credited_tries > 0:
        logger.info(f"ℹ️ Payment {payment.tx_ref} already credited → skipping re-credit.")
        return user, payment.credited_tries

    # ✅ Step 4: Calculate tries from amount
    try:
        tries = calculate_tries(int(payment.amount))
    except Exception as e:
        logger.warning(f"⚠️ Could not calculate tries for amount {payment.amount}: {e}")
        return user, 0

    if tries <= 0:
        logger.warning(f"⚠️ No valid tries mapping for amount {payment.amount}")
        return user, 0

    # ✅ Step 5: Credit user with tries
    user = await add_tries(session, user, tries, paid=True)
    payment.credited_tries = tries
    await session.flush()

    # ✅ Step 6: Log success
    logger.info(f"🎉 Credited {tries} tries to user {user.tg_id} ({user.username}) — tx_ref={payment.tx_ref}")

    return user, tries

# ------------------------------------------------------ 
# 6. Resolve Payment Status (helper for redirect/status)
# ------------------------------------------------------ 
async def resolve_payment_status(tx_ref: str, session: AsyncSession) -> Payment | None:
    """
    Centralized resolver for payment status:
    - Checks DB
    - Calls Flutterwave verify if needed
    - Updates DB & credits user (once only)
    Returns latest Payment or None.
    """
    # 1) look for an existing payment row
    stmt = select(Payment).where(Payment.tx_ref == tx_ref)
    result = await session.execute(stmt)
    payment = result.scalar_one_or_none()

    # If already terminal, return
    if payment and payment.status in ["successful", "failed", "expired"]:
        return payment

    # 2) Ask Flutterwave for the canonical status
    verify_url = f"{FLW_BASE_URL}/transactions/verify_by_reference?tx_ref={str(tx_ref)}"
    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(verify_url, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", {}) or {}
    except Exception as e:
        logger.warning(f"⚠️ Could not verify payment {tx_ref}: {e}")
        return payment

    if not data:
        return payment

    # 3) Extract useful values (defensively)
    flw_status = data.get("status")
    amount = data.get("amount")
    flw_id = data.get("id")
    meta = data.get("meta") or data.get("meta_data") or {}
    logger.info(f"🔍 resolve_payment_status meta for {tx_ref}: {meta}")

    # Normalize tg_id if present
    tg_id_meta = meta.get("tg_id") or meta.get("tgId") or meta.get("customer_id")
    tg_id_int = None
    if tg_id_meta is not None:
        try:
            # prefer int for tg_id lookups (Telegram IDs are numeric)
            tg_id_int = int(tg_id_meta)
        except Exception:
            tg_id_int = tg_id_meta

    username_meta = meta.get("username") or meta.get("user") or None

    # 4) If payment row doesn't exist, create one and link to a User if possible
    if not payment:
        # Attempt to find an existing user by tg_id
        linked_user_id = None
        if tg_id_int is not None:
            result = await session.execute(select(User).where(User.tg_id == tg_id_int))
            user_obj = result.scalar_one_or_none()
            if user_obj:
                linked_user_id = user_obj.id
            else:
                # Create a minimal user record so we can credit tries reliably
                # (This is optional but prevents "no user" errors)
                user_obj = User(tg_id=tg_id_int, username=username_meta)
                session.add(user_obj)
                await session.flush()  # get user_obj.id
                linked_user_id = user_obj.id
                logger.info(f"🆕 Created lightweight User for tg_id={tg_id_int} -> id={linked_user_id}")

        payment = Payment(
            tx_ref=str(tx_ref),
            amount=amount,
            status=flw_status or "pending",
            flw_tx_id=str(flw_id) if flw_id is not None else None,
            user_id=linked_user_id,
            tg_id = tg_id_int if tg_id_int is not None else None,
            username = username_meta,
            credited_tries=0,
        )
        session.add(payment)
        # don't commit yet — we'll commit after we credit if needed
        await session.flush()
        logger.info(f"🆕 Payment placeholder created for tx_ref={tx_ref} (linked_user_id={linked_user_id})")

    # 5) Update payment fields defensively
    payment.amount = amount
    if flw_id is not None:
        payment.flw_tx_id = str(flw_id)
    # update debug fields if present
    if tg_id_int is not None and payment.tg_id is None:
        payment.tg_id = tg_id_int
    if username_meta and not payment.username:
        payment.username = username_meta
    payment.updated_at = datetime.utcnow()
    await session.flush()

    # 6) If the transaction is successful, ensure we have a user_id and credit tries
    if flw_status == "successful":
        # Ensure payment.user_id references a real User; if not, try to link/create
        if not payment.user_id and tg_id_int is not None:
            result = await session.execute(select(User).where(User.tg_id == tg_id_int))
            user_obj = result.scalar_one_or_none()
            if user_obj:
                payment.user_id = user_obj.id
                logger.info(f"🔗 Backfilled payment.user_id from tg_id {tg_id_int} -> {user_obj.id}")
            else:
                # Create a minimal user record (again defensive)
                user_obj = User(tg_id=tg_id_int, username=username_meta)
                session.add(user_obj)
                await session.flush()
                payment.user_id = user_obj.id
                logger.info(f"🆕 Created User and linked to payment for tg_id={tg_id_int} -> id={user_obj.id}")

        # Now attempt to credit (credit_user_tries expects payment.user_id to be a User PK)
        try:
            user, tries = await credit_user_tries(session, payment)
            payment.status = "successful"
            await session.commit()
            logger.info(f"✅ Payment {tx_ref} resolved & user credited with {tries} tries")
        except Exception as e:
            # rollback to avoid partial state; caller/logs will show the problem
            logger.exception(f"❌ Failed to credit tries for payment {tx_ref}: {e}")
            await session.rollback()
            return payment

    elif flw_status in ["failed", "expired"]:
        payment.status = flw_status
        await session.commit()
        logger.info(f"❌ Payment {tx_ref} resolved as {flw_status}")
    else:
        # still pending / unknown
        await session.commit()
        logger.info(f"⏳ Payment {tx_ref} still pending (status={flw_status})")

    # 7) Return the freshest Payment row
    stmt = select(Payment).where(Payment.tx_ref == tx_ref)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
