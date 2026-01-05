# ======================================================================
# services/airtime_service.py
# Airtime Rewards via Clubkonnect (Nellobytes) Airtime API (Automatic payout)
# + Flutterwave Checkout remains for buying trivia attempts
# ======================================================================
from __future__ import annotations

import os
import uuid
import httpx
import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from sqlalchemy import text
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db import AsyncSessionLocal
from utils.security import validate_phone
from services.playtrivia import AIRTIME_MILESTONES

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Environment & Constants (Flutterwave still used for buying tries)
# -------------------------------------------------------------------
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
FLW_BASE_URL = os.getenv("FLW_BASE_URL", "https://api.flutterwave.com")
WEBHOOK_REDIRECT_URL = os.getenv(
    "WEBHOOK_REDIRECT_URL",
    "https://naijaprizegate-bot-oo2x.onrender.com/flw/redirect"
)

if not FLW_SECRET_KEY:
    raise RuntimeError("❌ FLW_SECRET_KEY is not set in environment variables")

# -------------------------------------------------------------------
# Clubkonnect / Nellobytes Airtime API env vars (used for automatic rewards)
# -------------------------------------------------------------------
CK_USER_ID = os.getenv("CLUBKONNECT_USER_ID")
CK_API_KEY = os.getenv("CLUBKONNECT_API_KEY")
CK_BASE_URL = os.getenv("CLUBKONNECT_BASE_URL", "https://www.nellobytesystems.com")

if not CK_USER_ID or not CK_API_KEY:
    # We don't raise here because the bot may still run without airtime rewards in dev,
    # but we will fail gracefully when attempting payout.
    logger.warning("⚠️ CLUBKONNECT_USER_ID / CLUBKONNECT_API_KEY not set. Airtime rewards will fail.")

# Network codes from Clubkonnect docs
NETWORK_CODE = {
    "mtn": "01",
    "glo": "02",
    "9mobile": "03",
    "airtel": "04",
}

# A simple prefix map (not exhaustive, but solid enough to start)
MTN_PREFIX = ("0703", "0706", "0803", "0806", "0810", "0813", "0814", "0816", "0903", "0906", "0913", "0916")
AIRTEL_PREFIX = ("0701", "0708", "0802", "0808", "0812", "0901", "0902", "0904", "0907", "0912")
GLO_PREFIX = ("0705", "0805", "0807", "0811", "0815", "0905", "0915")
ETISALAT_PREFIX = ("0809", "0817", "0818", "0908", "0909")


def normalize_ng_phone(raw: str) -> str:
    phone = (raw or "").strip().replace(" ", "").replace("-", "")
    if phone.startswith("+234") and len(phone) >= 14:
        phone = "0" + phone[4:]
    return "".join(ch for ch in phone if ch.isdigit())


def guess_network(phone_11: str) -> Optional[str]:
    """
    Guess network based on common prefixes.
    If we can't confidently detect, return None and ask user to choose later.
    """
    if phone_11.startswith(MTN_PREFIX):
        return "mtn"
    if phone_11.startswith(AIRTEL_PREFIX):
        return "airtel"
    if phone_11.startswith(GLO_PREFIX):
        return "glo"
    if phone_11.startswith(ETISALAT_PREFIX):
        return "9mobile"
    return None


# -------------------------------------------------------------------
# Flutterwave Checkout Link (Trivia Purchases) - KEEP THIS
# -------------------------------------------------------------------
async def create_flutterwave_checkout_link(
    tx_ref: str,
    amount: int,
    tg_id: int,
    username: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[str]:
    customer_email = email if email and "@" in email else f"user_{tg_id}@naijaprizegate.ng"
    safe_name = (username or f"User {tg_id}")[:64]

    payload = {
        "tx_ref": tx_ref,
        "amount": amount,
        "currency": "NGN",
        "redirect_url": WEBHOOK_REDIRECT_URL,
        "customer": {"email": customer_email, "name": safe_name},
        "customizations": {
            "title": "NaijaPrizeGate",
            "logo": "https://naijaprizegate.ng/static/logo.png",
        },
    }

    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{FLW_BASE_URL}/v3/payments", json=payload, headers=headers)
        data = resp.json()
    except Exception as e:
        logger.exception(f"Checkout creation failed: {e}")
        return None

    return data.get("data", {}).get("link")


# -------------------------------------------------------------------
# Clubkonnect/Nellobytes Airtime payout (Automatic)
# -------------------------------------------------------------------
async def send_airtime_via_clubkonnect(phone: str, amount: int, request_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Sends airtime using Nellobytes/Clubkonnect Airtime API.
    Uses HTTPS GET with query params and returns JSON dict.
    """
    if not CK_USER_ID or not CK_API_KEY:
        return {"status": "error", "message": "Clubkonnect credentials not configured"}

    if amount < 50:
        return {"status": "error", "message": "Minimum airtime amount is 50"}

    net = guess_network(phone)
    if not net:
        return {"status": "error", "message": "Could not detect network for this number"}

    mobile_network_code = NETWORK_CODE[net]
    rid = request_id or f"NP-{uuid.uuid4()}"

    params = {
        "UserID": CK_USER_ID,
        "APIKey": CK_API_KEY,
        "MobileNetwork": mobile_network_code,
        "Amount": str(amount),
        "MobileNumber": phone,
        "RequestID": rid,
        # Optional: CallBackURL can be used later if you want provider to ping your server
        # "CallBackURL": "https://yourdomain.com/clubkonnect/callback"
    }

    url = f"{CK_BASE_URL}/APIAirtimeV1.asp"

    # IMPORTANT: Don't log APIKey or full URL
    logger.info(f"📤 Clubkonnect airtime request | phone={phone} amount={amount} network={net} request_id={rid}")

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, params=params)

    try:
        data = resp.json()
    except Exception:
        # Provider sometimes returns non-JSON on error; keep small snippet
        data = {"status": "error", "message": "Non-JSON response from provider", "raw": resp.text[:200]}

    # Log minimal status only
    logger.info(
        f"📥 Clubkonnect airtime response | request_id={rid} status={data.get('status')} statuscode={data.get('statuscode')} orderid={data.get('orderid')}"
    )
    return data


def clubkonnect_is_success(data: Dict[str, Any]) -> bool:
    """
    Determine whether a Clubkonnect airtime request was accepted or completed.

    Success cases:
    - status = ORDER_RECEIVED (statuscode 100)
    - status = ORDER_COMPLETED (statuscode 200)

    Anything else is treated as failure.
    """

    if not isinstance(data, dict):
        return False

    status = str(data.get("status", "")).upper().strip()
    statuscode = str(data.get("statuscode", "")).strip()

    # Explicit success cases only
    if status == "ORDER_COMPLETED" and statuscode == "200":
        return True

    if status == "ORDER_RECEIVED" and statuscode == "100":
        return True

    return False


# -------------------------------------------------------------------
# Create Airtime Payout Record + Prompt Claim Button
# -------------------------------------------------------------------
async def create_pending_airtime_payout_and_prompt(
    session,
    update,
    user_id: str,
    tg_id: int,
    username: Optional[str],
    total_premium_spins: int
):
    """
    Creates a pending airtime payout entry and sends a claim button message.
    """

    amount = AIRTIME_MILESTONES.get(total_premium_spins)
    if not amount:
        logger.warning(f"⚠️ No airtime milestone defined for spins={total_premium_spins}")
        return

    payout_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    await session.execute(
        text("""
            INSERT INTO airtime_payouts (
                id, user_id, tg_id, phone_number,
                amount, status, created_at
            )
            VALUES (
                :id, :uid, :tg, NULL,
                :amt, 'pending_claim', :ts
            )
        """),
        {"id": payout_id, "uid": user_id, "tg": tg_id, "amt": amount, "ts": now},
    )
    await session.commit()

    safe_name = username or f"User {tg_id}"
    message = (
        f"🏆 *Milestone Unlocked, {safe_name}!* 🎉\n\n"
        f"🎯 You've reached *{total_premium_spins}* premium attempts.\n"
        f"💸 *₦{amount} Airtime Reward* unlocked!\n\n"
        "Tap the button below to claim 👇"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Claim Airtime Reward", callback_data=f"claim_airtime:{payout_id}")]
    ])

    try:
        if update.message:
            await update.message.reply_text(message, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.callback_query.message.reply_text(message, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"⚠️ Failed to send airtime claim UI: {e}")

    logger.info(f"🎯 Airtime reward created | tg_id={tg_id} | payout_id={payout_id} | amount={amount}")
    return payout_id


# -------------------------------------------------------------------
# Claim Button → Ask for Phone Number
# -------------------------------------------------------------------
async def handle_claim_airtime_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = (query.data or "").strip()
    if not data.startswith("claim_airtime:"):
        return await query.message.reply_text("⚠️ Invalid claim request. Please try again.", parse_mode="Markdown")

    _, payout_id = data.split(":", 1)
    payout_id = payout_id.strip()
    if not payout_id:
        return await query.message.reply_text("⚠️ Reward reference missing. Please try again.", parse_mode="Markdown")

    expires_at = (datetime.utcnow() + timedelta(minutes=5)).timestamp()
    context.user_data["pending_payout_id"] = payout_id
    context.user_data["awaiting_airtime_phone"] = True
    context.user_data["airtime_expiry"] = expires_at

    await query.message.reply_text(
        "📱 Enter your *11-digit Nigerian phone number* to receive your airtime.\n"
        "Example: `08012345678`\n\n"
        "_This session expires in 5 minutes._",
        parse_mode="Markdown",
    )


# -------------------------------------------------------------------
# User Sends Phone → Validate → Update DB → Send Airtime Automatically
# -------------------------------------------------------------------
async def handle_airtime_claim_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    raw_phone = (msg.text or "").strip()
    phone = normalize_ng_phone(raw_phone)

    payout_id = context.user_data.get("pending_payout_id")
    awaiting = context.user_data.get("awaiting_airtime_phone")
    expiry_ts = context.user_data.get("airtime_expiry")

    # Validate claim session
    if not awaiting or not payout_id:
        return await msg.reply_text(
            "⛔ Claim session not active. Please tap *Claim Airtime Reward* again.",
            parse_mode="Markdown",
        )

    if expiry_ts and datetime.utcnow().timestamp() > expiry_ts:
        context.user_data.pop("awaiting_airtime_phone", None)
        context.user_data.pop("pending_payout_id", None)
        context.user_data.pop("airtime_expiry", None)
        return await msg.reply_text(
            "⛔ Claim session expired. Please start again from rewards.",
            parse_mode="Markdown",
        )

    # Validate phone
    if not phone.isdigit() or len(phone) != 11 or not validate_phone(phone):
        return await msg.reply_text("❌ Invalid number — must be like `08123456789`", parse_mode="Markdown")

    # Lock payout + ownership + status update
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                res = await session.execute(
                    text("""
                        SELECT status, tg_id
                        FROM airtime_payouts
                        WHERE id = :id
                        FOR UPDATE
                    """),
                    {"id": payout_id},
                )
                row = res.first()

                if not row:
                    return await msg.reply_text("⚠️ Invalid payout reference.")

                status, payout_tg_id = row

                if payout_tg_id != update.effective_user.id:
                    logger.warning("🚨 Payout ownership mismatch")
                    return await msg.reply_text("⛔ Unauthorized claim attempt.")

                if status != "pending_claim":
                    return await msg.reply_text("ℹ️ This reward is already being processed or completed.")

                # Save phone & move to processing
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET phone_number = :p,
                            status = 'processing',
                            retry_count = 0,
                            last_retry_at = NULL
                        WHERE id = :id
                    """),
                    {"p": phone, "id": payout_id},
                )

        logger.info(f"☎️ Phone stored | payout_id={payout_id} | phone={phone}")

    except Exception:
        logger.exception("❌ DB error updating payout phone")
        return await msg.reply_text("⚠️ Could not save your phone. Please try again shortly.")

    # Fetch reward amount
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text("SELECT amount FROM airtime_payouts WHERE id = :id"),
            {"id": payout_id},
        )
        row = res.first()
        amount = row[0] if row else None

    if not amount:
        return await msg.reply_text("⚠️ Something went wrong. Please retry later.")

    # Send airtime automatically (NO checkout link)
    provider_request_id = f"AIRTIME-{payout_id}"
    provider_name = "clubkonnect"

    data = {}
    provider_reference = ""
    success = False

    try:
        data = await send_airtime_via_clubkonnect(
            phone=phone,
            amount=int(amount),
            request_id=provider_request_id
        )
        success = clubkonnect_is_success(data)
        provider_reference = str(data.get("orderid") or "")  # orderid is what they return
    except Exception:
        logger.exception("❌ Clubkonnect call failed")
        success = False

    # Update payout status based on provider response
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                if success:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status = 'completed',
                                provider = :provider,
                                provider_reference = :ref,
                                provider_response = :resp,
                                sent_at = NOW(),
                                completed_at = NOW()
                            WHERE id = :id
                        """),
                        {
                            "id": payout_id,
                            "provider": provider_name,
                            "ref": provider_reference or provider_request_id,
                            "resp": json.dumps(data) if data else None,
                        },
                    )
                else:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status = 'failed',
                                provider = :provider,
                                provider_reference = :ref,
                                provider_response = :resp,
                                sent_at = NOW()
                            WHERE id = :id
                        """),
                        {
                            "id": payout_id,
                            "provider": provider_name,
                            # keep a ref for debugging even on fail
                            "ref": provider_reference or provider_request_id,
                            "resp": json.dumps(data) if data else None,
                        },
                    )
    except Exception:
        logger.exception("❌ Failed to update payout status after provider call")

    # Cleanup only OUR keys
    context.user_data.pop("awaiting_airtime_phone", None)
    context.user_data.pop("pending_payout_id", None)
    context.user_data.pop("airtime_expiry", None)

    # Notify user
    if success:
        await msg.reply_text(
            f"🎉 *Airtime Sent!*\n\n"
            f"₦{amount} has been credited to *{phone}* ✅\n"
            f"Ref: `{provider_reference or provider_request_id}`",
            parse_mode="Markdown",
        )
    else:
        await msg.reply_text(
            "⚠️ *Airtime Not Sent Yet*\n\n"
            "We couldn’t complete your airtime reward right now.\n"
            "Please try again later or contact support.",
            parse_mode="Markdown",
        )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Continue Playing", callback_data="playtrivia")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
    ])

    await msg.reply_text("What would you like to do next?", reply_markup=keyboard)

    logger.info(
        f"✅ Airtime payout processed | payout_id={payout_id} | phone={phone} | amount={amount} | success={success} | provider_orderid={provider_reference}"
    )
