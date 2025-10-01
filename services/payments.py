#================================================================
# services/payments.py
#================================================================
import os
import httpx
import hmac
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime

from db import get_async_session
from models import Payment, TransactionLog, GlobalCounter

# ==== Config ====
FLW_BASE_URL = "https://api.flutterwave.com/v3"
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")  # used to validate webhook requests

# Guarantee win after this many paid tries
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "14600"))


# ------------------------------------------------------
# 1. Create Checkout (generate payment link for a user)
# ------------------------------------------------------
async def create_checkout(user_id: str, amount: int, tx_ref: str) -> str:
    """
    Creates a Flutterwave payment checkout link.
    """
    url = f"{FLW_BASE_URL}/payments"
    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}"}
    payload = {
        "tx_ref": tx_ref,
        "amount": amount,
        "currency": "NGN",
        "redirect_url": "https://yourapp.com/payment/callback",  # optional if webhook is used
        "customer": {"email": "test@example.com"},  # replace with real user email
        "customizations": {"title": "NaijaPrizeGate"},
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()

    return data["data"]["link"]  # return payment link to redirect user


# ------------------------------------------------------
# 2. Verify Payment (update DB + global counter)
# ------------------------------------------------------
async def verify_payment(tx_ref: str, session: AsyncSession) -> bool:
    """
    Verifies payment status from Flutterwave by transaction reference.
    Updates the Payment row in DB if successful.
    Also increments the global counter and resets if WIN_THRESHOLD is reached.
    """
    url = f"{FLW_BASE_URL}/transactions/verify_by_reference?tx_ref={tx_ref}"
    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}"}

    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()

    # Only mark as successful if Flutterwave confirms
    if data["status"] == "success" and data["data"]["status"] == "successful":
        stmt = select(Payment).where(Payment.tx_ref == tx_ref)
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()

        if payment and payment.status != "successful":
            payment.status = "successful"
            payment.amount = data["data"]["amount"]
            payment.updated_at = datetime.utcnow()

            # --- Update global counter ---
            counter_stmt = select(GlobalCounter).limit(1)
            counter_result = await session.execute(counter_stmt)
            counter = counter_result.scalar_one_or_none()

            if not counter:
                # If counter row doesn’t exist, create it
                counter = GlobalCounter(paid_tries_total=0)
                session.add(counter)
                await session.flush()

            counter.paid_tries_total += 1

            # Reset if threshold is reached
            if counter.paid_tries_total >= WIN_THRESHOLD:
                counter.paid_tries_total = 0
                print(f"🎉 WIN threshold {WIN_THRESHOLD} reached → counter reset!")

            await session.commit()

        return True
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
