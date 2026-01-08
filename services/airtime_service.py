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
from telegram.ext import ConversationHandler

from utils.conversation_states import AIRTIME_PHONE
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
    """
    Creates a Flutterwave hosted checkout link for TRIVIA purchase payments.

    ✅ Enforces a TRIVIA- prefix on tx_ref to keep webhook routing clean.
    """
    # ✅ Ensure tx_ref is prefixed for trivia purchases
    tx_ref = (tx_ref or "").strip()
    if not tx_ref.startswith("TRIVIA-"):
        tx_ref = f"TRIVIA-{tx_ref}" if tx_ref else f"TRIVIA-{uuid.uuid4()}"

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
        # ✅ optional meta (helps you recover tg_id/username reliably in webhook)
        "meta": {
            "tg_id": str(tg_id),
            "username": username or "",
            "purpose": "trivia_purchase",
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

    # Optional: log tx_ref for debugging (no secrets)
    logger.info(f"✅ Flutterwave checkout created | tx_ref={tx_ref} | amount={amount} | tg_id={tg_id}")

    return data.get("data", {}).get("link")

# -------------------------------------------------------------------
# Clubkonnect/Nellobytes Airtime payout (Automatic)
# -------------------------------------------------------------------
async def send_airtime_via_clubkonnect(
    phone: str,
    amount: int,
    request_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Sends airtime using Clubkonnect/Nellobytes Airtime API (HTTPS GET).
    Returns a JSON dict (or a structured error dict if provider returns non-JSON/HTML).
    """

    # -------------------------------
    # Defensive checks
    # -------------------------------
    if not CK_USER_ID or not CK_API_KEY:
        logger.error("❌ Clubkonnect credentials missing (CK_USER_ID/CK_API_KEY not set)")
        return {"status": "error", "message": "Clubkonnect credentials not configured"}

    if amount < 100:
        return {"status": "error", "message": "Minimum airtime amount is 100"}

    net = guess_network(phone)
    if not net:
        return {"status": "error", "message": "Could not detect network for this number"}

    if net not in NETWORK_CODE:
        return {"status": "error", "message": f"Unsupported network detected: {net}"}

    mobile_network_code = str(NETWORK_CODE[net])
    rid = request_id or f"NP-{uuid.uuid4()}"

    params = {
        "UserID": CK_USER_ID,
        "APIKey": CK_API_KEY,
        "MobileNetwork": mobile_network_code,
        "Amount": str(int(amount)),
        "MobileNumber": phone,
        "RequestID": rid,
        # Optional:
        # "CallBackURL": "https://yourdomain.com/clubkonnect/callback"
    }

    # -------------------------------
    # Endpoints (try V1 first, fallback to legacy)
    # -------------------------------
    endpoints = [
        "/APIAirtimeV1.asp",  # recommended / newer
        "/APIAirtime.asp",    # legacy fallback
    ]

    # IMPORTANT: don't log APIKey or full URL with params
    logger.info(
        f"📤 Clubkonnect airtime request | base={CK_BASE_URL} "
        f"| phone={phone} amount={amount} net={net} MobileNetwork={mobile_network_code} request_id={rid}"
    )

    # -------------------------------
    # Request + parse
    # -------------------------------
    last_error: Optional[Dict[str, Any]] = None

    for ep in endpoints:
        url = f"{CK_BASE_URL}{ep}"

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.get(url, params=params)

            body_snip = (resp.text or "")[:300].replace("\n", " ").replace("\r", " ")
            logger.info(f"🌐 Clubkonnect HTTP | endpoint={ep} status_code={resp.status_code} | body_snip={body_snip}")

            # Try JSON parse
            try:
                data = resp.json()
            except Exception:
                data = {
                    "status": "error",
                    "message": "Non-JSON response from provider",
                    "http_status": resp.status_code,
                    "endpoint": ep,
                    "raw": (resp.text or "")[:500],
                }

            # Always log parsed payload (short)
            logger.info(f"📦 Clubkonnect parsed response | endpoint={ep} request_id={rid} | data={str(data)[:500]}")

            # If we got a dict and it looks like a real CK response, return it
            # (Even if it's failure — your calling code decides success/fail)
            if isinstance(data, dict):
                return data

            # Otherwise, store and try next endpoint
            last_error = {
                "status": "error",
                "message": "Unexpected response type from provider",
                "endpoint": ep,
                "http_status": resp.status_code,
            }

        except Exception as e:
            logger.exception(f"❌ Clubkonnect request failed | endpoint={ep} request_id={rid} | err={e}")
            last_error = {"status": "error", "message": "Clubkonnect request exception", "endpoint": ep}

    # If both endpoints failed
    return last_error or {"status": "error", "message": "Clubkonnect request failed"}


def clubkonnect_is_success(data: Dict[str, Any]) -> bool:
    """
    Determine whether a Clubkonnect/Nellobytes airtime request was accepted or completed.

    Treat as success if EITHER:
    - statuscode is 100 (received) or 200 (completed), OR
    - status is ORDER_RECEIVED / ORDER_COMPLETED

    This avoids false failures due to inconsistent payloads.
    """
    if not isinstance(data, dict):
        return False

    status = str(data.get("status") or "").upper().strip()
    code = str(data.get("statuscode") or "").strip()

    # Common "accepted" or "completed" codes
    if code in ("100", "200"):
        return True

    # Common "accepted" or "completed" statuses
    if status in ("ORDER_RECEIVED", "ORDER_COMPLETED"):
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
    """
    Handles pressing the "Claim Airtime Reward" button.
    Sets user_data state and transitions ConversationHandler to AIRTIME_PHONE.
    """
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    # Small UX feedback on tap
    try:
        await query.answer("Processing...", show_alert=False)
    except Exception:
        pass

    # -------------------------------------------------------
    # Validate callback format
    # -------------------------------------------------------
    data = (query.data or "").strip()
    if not data.startswith("claim_airtime:"):
        await query.message.reply_text(
            "⚠️ Invalid claim request. Please try again.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    _, payout_id = data.split(":", 1)
    payout_id = payout_id.strip()

    if not payout_id:
        await query.message.reply_text(
            "⚠️ Reward reference missing. Please tap *Claim Airtime Reward* again.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # -------------------------------------------------------
    # Save claim session state (do NOT clear user_data)
    # -------------------------------------------------------
    expires_at = (datetime.utcnow() + timedelta(minutes=5)).timestamp()
    context.user_data["pending_payout_id"] = payout_id
    context.user_data["awaiting_airtime_phone"] = True
    context.user_data["airtime_expiry"] = expires_at

    # -------------------------------------------------------
    # Prompt for phone number
    # -------------------------------------------------------
    await query.message.reply_text(
        "📱 Enter your *11-digit Nigerian phone number* to receive your airtime.\n"
        "Example: `08012345678`\n\n"
        "_This session expires in 5 minutes._",
        parse_mode="Markdown",
    )

    # ✅ IMPORTANT: tell ConversationHandler to expect a phone number next
    return AIRTIME_PHONE


# -------------------------------------------------------------------
# User Sends Phone → Validate → Update DB → Send Airtime Automatically
# -------------------------------------------------------------------
async def handle_airtime_claim_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    raw_phone = (msg.text or "").strip()

    # ✅ DEBUG: proves this handler is receiving the phone message
    logger.info(
        f"✅ AIRTIME PHONE HANDLER HIT | tg_id={update.effective_user.id} | raw={raw_phone}"
    )
    print("✅ HIT airtime phone handler", flush=True)

    phone = normalize_ng_phone(raw_phone)

    payout_id = context.user_data.get("pending_payout_id")
    awaiting = context.user_data.get("awaiting_airtime_phone")
    expiry_ts = context.user_data.get("airtime_expiry")

    # -------------------------------------------------------
    # Validate claim session
    # -------------------------------------------------------
    if not awaiting or not payout_id:
        await msg.reply_text(
            "⛔ Claim session not active. Please tap *Claim Airtime Reward* again.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    if expiry_ts and datetime.utcnow().timestamp() > expiry_ts:
        context.user_data.pop("awaiting_airtime_phone", None)
        context.user_data.pop("pending_payout_id", None)
        context.user_data.pop("airtime_expiry", None)

        await msg.reply_text(
            "⛔ Claim session expired. Please start again from rewards.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # -------------------------------------------------------
    # Validate phone number
    # -------------------------------------------------------
    if not phone.isdigit() or len(phone) != 11 or not validate_phone(phone):
        await msg.reply_text(
            "❌ Invalid number — must be like `08123456789`",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # -------------------------------------------------------
    # Lock payout + ownership + status update
    # -------------------------------------------------------
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
                    await msg.reply_text("⚠️ Invalid payout reference.")
                    return ConversationHandler.END

                status, payout_tg_id = row

                if payout_tg_id != update.effective_user.id:
                    logger.warning("🚨 Payout ownership mismatch")
                    await msg.reply_text("⛔ Unauthorized claim attempt.")
                    return ConversationHandler.END

                if status != "pending_claim":
                    await msg.reply_text(
                        "ℹ️ This reward is already being processed or completed."
                    )
                    return ConversationHandler.END

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
        await msg.reply_text(
            "⚠️ Could not save your phone. Please try again shortly."
        )
        return ConversationHandler.END

    # -------------------------------------------------------
    # Fetch reward amount
    # -------------------------------------------------------
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text("SELECT amount FROM airtime_payouts WHERE id = :id"),
            {"id": payout_id},
        )
        row = res.first()
        amount = row[0] if row else None

    if not amount:
        await msg.reply_text("⚠️ Something went wrong. Please retry later.")
        return ConversationHandler.END

    # -------------------------------------------------------
    # Send airtime automatically (Clubkonnect)
    # -------------------------------------------------------
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
        logger.info(f"📦 Clubkonnect raw response | payout_id={payout_id} | data={str(data)[:300]}")
        success = clubkonnect_is_success(data)
        provider_reference = str(data.get("orderid") or "")
    except Exception:
        logger.exception("❌ Clubkonnect call failed")
        success = False

    # -------------------------------------------------------
    # Update payout status
    # -------------------------------------------------------
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
                            "ref": provider_reference or provider_request_id,
                            "resp": json.dumps(data) if data else None,
                        },
                    )
    except Exception:
        logger.exception("❌ Failed to update payout status after provider call")

    # -------------------------------------------------------
    # Cleanup session keys
    # -------------------------------------------------------
    context.user_data.pop("awaiting_airtime_phone", None)
    context.user_data.pop("pending_payout_id", None)
    context.user_data.pop("airtime_expiry", None)

    # -------------------------------------------------------
    # Notify user
    # -------------------------------------------------------
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
        f"✅ Airtime payout processed | payout_id={payout_id} | phone={phone} | amount={amount} | success={success}"
    )

    # ✅ VERY IMPORTANT: end the conversation
    return ConversationHandler.END
