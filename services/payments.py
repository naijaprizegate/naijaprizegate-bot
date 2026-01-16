# ================================================================
# services/payments.py
# ================================================================
import os
import httpx
import hmac
import aiohttp
import hashlib
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from db import get_async_session
from models import Payment, TransactionLog, GlobalCounter, User
from helpers import add_tries  # ✅ FIX: Missing import
from helpers import mask_sensitive


# ==== Config ====
FLW_BASE_URL = "https://api.flutterwave.com/v3"
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")  # used to validate webhook requests
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "50000"))
WEBHOOK_REDIRECT_URL = os.getenv("WEBHOOK_REDIRECT_URL", "https://naijaprizegate-bot-oo2x.onrender.com/flw/redirect")

# ✅ Define your approved packages (anti-tampering)
ALLOWED_PACKAGES = {200, 500, 1000}

# ==== Logger Setup ====
logger = logging.getLogger("payments")
logger.setLevel(logging.INFO)



PRICE_TO_TRIES = {
    200: 1,
    500: 3,
    1000: 7,
}

def calculate_tries(amount: int) -> int:
    """
    Convert payment amount (₦) into trivia tries.
    """
    if not isinstance(amount, int) or amount <= 0:
        return 0

    if amount in PRICE_TO_TRIES:
        return PRICE_TO_TRIES[amount]

    # fallback rule: 1 try per ₦200
    return max(1, amount // 200)


def validate_flutterwave_webhook(headers: dict, raw_body: str) -> bool:
    """
    Validates Flutterwave webhook using verif-hash header.
    """
    signature = headers.get("verif-hash")
    if not signature or not FLW_SECRET_HASH:
        return False

    return hmac.compare_digest(signature, FLW_SECRET_HASH)


# ------------------------------------------------------
# 1. Create Checkout
# ------------------------------------------------------

async def create_checkout(
    user_id: str,
    amount: int,
    tx_ref: str,
    username: str = None,
    email: str = None
) -> str:
    """
    Wrapper for trivia purchases.
    Performs validation ONLY.
    Delegates Flutterwave logic to airtime_service.
    """

    # ✅ 1. Environment validation
    if not FLW_SECRET_KEY:
        logger.error("❌ Missing FLW_SECRET_KEY in environment!")
        return None

    if not WEBHOOK_REDIRECT_URL.startswith("https://"):
        logger.error(f"❌ Insecure redirect URL detected: {WEBHOOK_REDIRECT_URL}")
        return None

    # ✅ 2. Validate payment amount
    if not isinstance(amount, int) or amount <= 0:
        logger.warning(f"⚠️ Invalid payment amount by user {user_id}: {amount}")
        return None

    if amount not in ALLOWED_PACKAGES:
        logger.warning(f"🚫 Unauthorized payment amount {amount} by user {user_id}")
        return None

    # ✅ 3. Delegate to the SAFE Flutterwave function
    from services.airtime_service import create_flutterwave_checkout_link

    return await create_flutterwave_checkout_link(
        tx_ref=tx_ref,
        amount=amount,
        tg_id=update.effective_user.id,
        username=update.effective_user.username,
        email=email,
    )

# ------------------------------------------------------
# 2. Verify Payment (via tx_ref + transaction_id)
# ------------------------------------------------------
# ------------------------------------------------------
# Verify Payment (REDIRECT / MANUAL CHECK ONLY)
# ------------------------------------------------------
async def verify_payment(tx_ref: str, session: AsyncSession) -> dict:
    """
    Verifies payment status with Flutterwave.
    ⚠️ MUST NOT be used inside webhook.
    Returns status info only — no crediting.
    """
    tx_ref = str(tx_ref)
    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}"}

    try:
        # Lookup by reference
        lookup_url = f"{FLW_BASE_URL}/transactions?tx_ref={tx_ref}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            lookup_resp = await client.get(lookup_url, headers=headers)
            lookup_resp.raise_for_status()
            lookup_data = lookup_resp.json()

        data_list = lookup_data.get("data") or []
        if not data_list:
            logger.warning(f"⚠️ verify_payment: no tx found for {tx_ref}")
            return {"status": "not_found"}

        tx_id = data_list[0].get("id")
        if not tx_id:
            return {"status": "invalid"}

        # Verify by transaction ID
        verify_url = f"{FLW_BASE_URL}/transactions/{tx_id}/verify"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(verify_url, headers=headers)
            resp.raise_for_status()
            fw_resp = resp.json()

    except Exception as e:
        logger.exception(f"❌ verify_payment error for {tx_ref}: {e}")
        return {"status": "error", "error": str(e)}

    tx_data = fw_resp.get("data") or {}
    status = (tx_data.get("status") or "").lower()

    return {
        "status": status,
        "amount": tx_data.get("amount"),
        "tx_ref": tx_ref,
        "flw_tx_id": tx_data.get("id"),
        "meta": tx_data.get("meta") or {},
    }


# ------------------------------------------------------
# Log Raw Transaction Payload
# ------------------------------------------------------
async def log_transaction(session: AsyncSession, provider: str, payload: str):
    log = TransactionLog(provider=provider, payload=payload)
    session.add(log)
    await session.commit()



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


async def verify_transaction(transaction_id: str, amount: int) -> bool:
    """
    Verifies a Flutterwave transaction directly with Flutterwave's API.
    Returns True if the transaction is valid and successful.
    """
    url = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify"

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
                logger.info(f"🔍 Verify response for tx_id={transaction_id}: {data}")

                if data.get("status") == "success":
                    tx_data = data.get("data", {})
                    if (
                        tx_data.get("status") == "successful" and
                        int(tx_data.get("amount", 0)) == int(amount)
                    ):
                        return True
        return False
    except Exception as e:
        logger.error(f"❌ verify_transaction() failed for tx_id={transaction_id}: {e}", exc_info=True)
        return False
