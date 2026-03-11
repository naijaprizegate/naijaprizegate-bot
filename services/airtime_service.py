# ======================================================================
# services/airtime_service.py
# Airtime Rewards via Clubkonnect (Nellobytes) Airtime API
# Flutterwave Checkout remains for buying trivia attempts
# ======================================================================
from __future__ import annotations

import os
import uuid
import httpx
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from logger import logger
from utils.conversation_states import AIRTIME_PHONE
from db import AsyncSessionLocal
from utils.security import validate_phone
from services.playtrivia import AIRTIME_MILESTONES

# -------------------------------------------------------------------
# Environment & Constants (Flutterwave still used for buying tries)
# -------------------------------------------------------------------
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
FLW_BASE_URL = os.getenv("FLW_BASE_URL", "https://api.flutterwave.com")
WEBHOOK_REDIRECT_URL = os.getenv(
    "WEBHOOK_REDIRECT_URL",
    "https://naijaprizegate-bot-oo2x.onrender.com/flw/redirect",
)

if not FLW_SECRET_KEY:
    raise RuntimeError("❌ FLW_SECRET_KEY is not set in environment variables")

# -------------------------------------------------------------------
# Clubkonnect / Nellobytes Airtime API env vars
# -------------------------------------------------------------------
CK_USER_ID = os.getenv("CLUBKONNECT_USER_ID")
CK_API_KEY = os.getenv("CLUBKONNECT_API_KEY")
CK_BASE_URL = os.getenv("CLUBKONNECT_BASE_URL", "https://www.nellobytesystems.com")

if not CK_USER_ID or not CK_API_KEY:
    logger.warning(
        "⚠️ CLUBKONNECT_USER_ID / CLUBKONNECT_API_KEY not set. Airtime rewards will fail."
    )

# Network codes from Clubkonnect docs
NETWORK_CODE = {
    "mtn": "01",
    "glo": "02",
    "9mobile": "03",
    "airtel": "04",
}

# Common Nigerian prefixes
MTN_PREFIX = (
    "0703", "0704", "0706", "0803", "0806", "0810", "0813",
    "0814", "0816", "0903", "0906", "0913", "0916",
)
AIRTEL_PREFIX = (
    "0701", "0708", "0802", "0808", "0812", "0901",
    "0902", "0904", "0907", "0912",
)
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
    Returns None if not confidently detected.
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
# Clubkonnect/Nellobytes Airtime payout
# -------------------------------------------------------------------
async def send_airtime_via_clubkonnect(
    phone: str,
    amount: int,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends airtime using Clubkonnect/Nellobytes Airtime API (HTTPS GET).
    Returns a JSON dict or a structured error dict.
    """
    if not CK_USER_ID or not CK_API_KEY:
        logger.error("❌ Clubkonnect credentials missing")
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
    }

    endpoints = [
        "/APIAirtimeV1.asp",
        "/APIAirtime.asp",
    ]

    logger.info(
        "📤 Clubkonnect airtime request | base=%s | phone=%s | amount=%s | net=%s | MobileNetwork=%s | request_id=%s",
        CK_BASE_URL,
        phone,
        amount,
        net,
        mobile_network_code,
        rid,
    )

    last_error: Optional[Dict[str, Any]] = None

    for ep in endpoints:
        url = f"{CK_BASE_URL}{ep}"

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.get(url, params=params)

            body_snip = (resp.text or "")[:300].replace("\n", " ").replace("\r", " ")
            logger.info(
                "🌐 Clubkonnect HTTP | endpoint=%s | status_code=%s | body_snip=%s",
                ep,
                resp.status_code,
                body_snip,
            )

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

            logger.info(
                "📦 Clubkonnect parsed response | endpoint=%s | request_id=%s | data=%s",
                ep,
                rid,
                str(data)[:500],
            )

            if isinstance(data, dict):
                return data

            last_error = {
                "status": "error",
                "message": "Unexpected response type from provider",
                "endpoint": ep,
                "http_status": resp.status_code,
            }

        except Exception as e:
            logger.exception(
                "❌ Clubkonnect request failed | endpoint=%s | request_id=%s | err=%s",
                ep,
                rid,
                e,
            )
            last_error = {
                "status": "error",
                "message": "Clubkonnect request exception",
                "endpoint": ep,
            }

    return last_error or {"status": "error", "message": "Clubkonnect request failed"}


def clubkonnect_is_success(data: Dict[str, Any]) -> bool:
    """
    Determine whether a Clubkonnect/Nellobytes airtime request
    was accepted or completed.
    """
    if not isinstance(data, dict):
        return False

    status = str(data.get("status") or "").upper().strip()
    code = str(data.get("statuscode") or "").strip()

    if code in ("100", "200"):
        return True

    if status in ("ORDER_RECEIVED", "ORDER_COMPLETED"):
        return True

    return False


# -----------------------------------------------------
# Create Airtime Payout Record + Prompt Claim Button
# -----------------------------------------------------
async def create_pending_airtime_payout(
    session: AsyncSession,
    user_id: str,
    tg_id: int,
    total_premium_spins: int,
    cycle_id: int,
) -> Optional[Dict[str, int | str]]:
    """
    Creates or reuses a pending airtime payout if a milestone is reached.
    """

    spins = int(total_premium_spins or 0)
    amount = AIRTIME_MILESTONES.get(spins)

    if not amount:
        logger.info(
            "ℹ️ No airtime milestone | tg_id=%s | cycle_id=%s | spins=%s",
            tg_id,
            cycle_id,
            spins,
        )
        return None

    amt = int(amount)
    uid = str(user_id)
    tg = int(tg_id)
    c = int(cycle_id)

    REUSABLE_STATUSES = (
        "pending_claim",
        "claim_phone_set",
        "queued",
        "retrying",
        "failed_retryable",
        "failed_needs_funding",
        "failed",
    )

    existing = await session.execute(
        text("""
            SELECT id::text
            FROM airtime_payouts
            WHERE user_id = CAST(:uid AS uuid)
              AND cycle_id = :c
              AND amount = :amt
              AND status = ANY(:statuses)
            ORDER BY created_at DESC
            LIMIT 1
            FOR UPDATE
        """),
        {"uid": uid, "c": c, "amt": amt, "statuses": list(REUSABLE_STATUSES)},
    )
    existing_id = existing.scalar_one_or_none()

    if existing_id:
        logger.info(
            "✅ Reusing existing airtime payout | tg_id=%s | cycle_id=%s | payout_id=%s | amount=%s | spins=%s",
            tg,
            c,
            existing_id,
            amt,
            spins,
        )
        return {
            "payout_id": existing_id,
            "amount": amt,
            "spins": spins,
            "cycle_id": c,
        }

    payout_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    await session.execute(
        text("""
            INSERT INTO airtime_payouts (
                id,
                user_id,
                tg_id,
                phone_number,
                amount,
                status,
                cycle_id,
                created_at
            )
            VALUES (
                :id,
                CAST(:uid AS uuid),
                :tg,
                NULL,
                :amt,
                'pending_claim',
                :c,
                :ts
            )
        """),
        {
            "id": payout_id,
            "uid": uid,
            "tg": tg,
            "amt": amt,
            "c": c,
            "ts": now,
        },
    )

    logger.info(
        "🎯 Airtime payout created | tg_id=%s | cycle_id=%s | payout_id=%s | amount=%s | spins=%s",
        tg,
        c,
        payout_id,
        amt,
        spins,
    )

    return {
        "payout_id": payout_id,
        "amount": amt,
        "spins": spins,
        "cycle_id": c,
    }


# -------------------------------------------------------------------
# Claim Button → Ask for Phone Number
# -------------------------------------------------------------------
async def handle_claim_airtime_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Handles pressing the "Claim Airtime Reward" button.
    Stores a short-lived session in user_data and moves ConversationHandler
    to AIRTIME_PHONE.
    """
    query = update.callback_query
    user = update.effective_user

    if not query or not user or not query.message:
        return ConversationHandler.END

    try:
        await query.answer("Processing...", show_alert=False)
    except Exception:
        pass

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

    try:
        payout_id = str(uuid.UUID(payout_id))
    except Exception:
        await query.message.reply_text(
            "⚠️ Invalid reward reference. Please tap *Claim Airtime Reward* again.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    logger.info(
        "🧾 claim_airtime callback received | payout_id=%s | tg_id=%s",
        payout_id,
        user.id,
    )

    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text("""
                    SELECT status, tg_id, amount
                    FROM airtime_payouts
                    WHERE id = :id
                """),
                {"id": payout_id},
            )
            row = res.first()

        if not row:
            await query.message.reply_text(
                "⚠️ Reward not found. Please try again.",
                parse_mode="Markdown",
            )
            return ConversationHandler.END

        status, payout_tg_id, amount = row

        if payout_tg_id != user.id:
            logger.warning(
                "🚨 Unauthorized claim button press | payout_id=%s | tg_id=%s | owner=%s",
                payout_id,
                user.id,
                payout_tg_id,
            )
            await query.message.reply_text(
                "⛔ This reward does not belong to you.",
                parse_mode="Markdown",
            )
            return ConversationHandler.END

        if status != "pending_claim":
            await query.message.reply_text(
                "ℹ️ This reward is already being processed or completed.",
                parse_mode="Markdown",
            )
            return ConversationHandler.END

        if not amount:
            await query.message.reply_text(
                "⚠️ Reward amount is missing. Please contact support.",
                parse_mode="Markdown",
            )
            return ConversationHandler.END

    except Exception:
        logger.exception(
            "❌ Error validating payout before phone prompt | payout_id=%s",
            payout_id,
        )
        await query.message.reply_text(
            "⚠️ Could not verify this reward right now. Please try again shortly.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

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

    return ConversationHandler.END 


# ===============================================================
# Handle Airtime Claim Phone
# ===============================================================
async def handle_airtime_claim_phone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Receives the user's phone number for an airtime claim.

    IMPORTANT:
    - Validates and normalizes the phone number
    - Tries to resolve the pending payout from user_data first
    - Falls back to DB lookup if user_data session is missing
    - Saves only the phone number to DB
    - DOES NOT send airtime here
    - DOES NOT mark payout as 'processing' here
      (leave that for the notifier/worker)
    """
    msg = update.message
    user = update.effective_user

    if not msg or not user:
        return ConversationHandler.END

    tg_id = user.id
    raw_phone = (msg.text or "").strip()

    logger.info("📩 AIRTIME PHONE HANDLER ENTRY | tg_id=%s | raw=%s", tg_id, raw_phone)

    payout_id = context.user_data.get("pending_payout_id")
    awaiting = context.user_data.get("awaiting_airtime_phone")
    expiry_ts = context.user_data.get("airtime_expiry")

    # -------------------------------------------------------
    # Normalize + validate phone first
    # -------------------------------------------------------
    try:
        phone = normalize_ng_phone(raw_phone)
    except Exception:
        phone = raw_phone

    if not phone or not phone.isdigit() or len(phone) != 11 or not validate_phone(phone):
        # Quietly ignore unrelated text if we are not in a claim session
        if not awaiting and not payout_id:
            logger.info("ℹ️ Ignoring non-claim text in airtime phone handler | tg_id=%s", tg_id)
            return ConversationHandler.END

        await msg.reply_text(
            "❌ Invalid number — must be like `08123456789`",
            parse_mode="Markdown",
        )
        return AIRTIME_PHONE

    # -------------------------------------------------------
    # Check session expiry if session exists
    # -------------------------------------------------------
    if awaiting and expiry_ts and datetime.utcnow().timestamp() > expiry_ts:
        context.user_data.pop("awaiting_airtime_phone", None)
        context.user_data.pop("pending_payout_id", None)
        context.user_data.pop("airtime_expiry", None)

        await msg.reply_text(
            "⛔ Claim session expired. Please start again from rewards.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # -------------------------------------------------------
    # Resolve payout_id
    # First try user_data
    # If missing, fall back to latest pending_claim in DB
    # -------------------------------------------------------
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Fallback lookup if user_data session is missing
                if not payout_id:
                    logger.info(
                        "🔎 No pending_payout_id in user_data, falling back to DB lookup | tg_id=%s",
                        tg_id,
                    )

                    fallback_res = await session.execute(
                        text("""
                            SELECT id
                            FROM airtime_payouts
                            WHERE tg_id = :tg_id
                              AND status = 'pending_claim'
                              AND phone_number IS NULL
                            ORDER BY created_at DESC
                            LIMIT 1
                            FOR UPDATE
                        """),
                        {"tg_id": tg_id},
                    )
                    payout_id = fallback_res.scalar_one_or_none()

                    if payout_id:
                        payout_id = str(payout_id)
                        logger.info(
                            "✅ Fallback payout found | tg_id=%s | payout_id=%s",
                            tg_id,
                            payout_id,
                        )
                    else:
                        logger.info(
                            "ℹ️ No pending airtime claim found for fallback lookup | tg_id=%s",
                            tg_id,
                        )
                        return ConversationHandler.END

                # Lock the payout row
                res = await session.execute(
                    text("""
                        SELECT status, tg_id, amount, phone_number
                        FROM airtime_payouts
                        WHERE id = :id
                        FOR UPDATE
                    """),
                    {"id": payout_id},
                )
                row = res.first()

                if not row:
                    await msg.reply_text(
                        "⚠️ Invalid payout reference.",
                        parse_mode="Markdown",
                    )
                    return ConversationHandler.END

                status, payout_tg_id, amount, existing_phone = row

                if payout_tg_id != tg_id:
                    logger.warning(
                        "🚨 Payout ownership mismatch | payout_id=%s | tg_id=%s | owner=%s",
                        payout_id,
                        tg_id,
                        payout_tg_id,
                    )
                    await msg.reply_text(
                        "⛔ Unauthorized claim attempt.",
                        parse_mode="Markdown",
                    )
                    return ConversationHandler.END

                if status != "pending_claim":
                    await msg.reply_text(
                        "ℹ️ This reward is already being processed or completed.",
                        parse_mode="Markdown",
                    )
                    return ConversationHandler.END

                if not amount:
                    await msg.reply_text(
                        "⚠️ Missing reward amount. Please contact support.",
                        parse_mode="Markdown",
                    )
                    return ConversationHandler.END

                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET phone_number = :phone,
                            retry_count = COALESCE(retry_count, 0),
                            last_retry_at = NULL
                        WHERE id = :id
                    """),
                    {
                        "phone": phone,
                        "id": payout_id,
                    },
                )

        logger.info(
            "☎️ Phone stored successfully | payout_id=%s | tg_id=%s | phone=%s",
            payout_id,
            tg_id,
            phone,
        )

    except Exception:
        logger.exception("❌ DB error while saving phone | payout_id=%s", payout_id)
        await msg.reply_text(
            "⚠️ Could not save your phone number right now. Please try again shortly.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # -------------------------------------------------------
    # Clear phone-entry session keys
    # -------------------------------------------------------
    context.user_data.pop("awaiting_airtime_phone", None)
    context.user_data.pop("airtime_expiry", None)
    context.user_data.pop("pending_payout_id", None)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Continue Playing", callback_data="playtrivia")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
    ])

    await msg.reply_text(
        "✅ Phone number received.\n\n"
        "⏳ Your airtime reward is now queued for processing. "
        "You will get a confirmation shortly.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

    return ConversationHandler.END

# ===================================================
# Finalize Airtime Payout
# ===================================================
async def _finalize_airtime_payout(
    bot,
    chat_id: int,
    tg_id: int,
    payout_id,
    phone: str,
    amount: int,
    airtime_result,
):
    """
    Finalizes airtime payout in DB and notifies the user.

    Intended for use from a background worker / notifier.
    Does NOT depend on ConversationHandler update/context state.
    """

    provider_name = (getattr(airtime_result, "provider", None) or "clubkonnect").lower()
    provider_ref = str(getattr(airtime_result, "reference", "") or "").strip()
    raw_payload = getattr(airtime_result, "raw", None) or {}

    statuscode = ""
    status_text = ""

    try:
        if isinstance(raw_payload, dict):
            statuscode = str(
                raw_payload.get("statuscode")
                or raw_payload.get("statusCode")
                or ""
            ).strip()
            status_text = str(raw_payload.get("status") or "").strip().upper()
    except Exception:
        pass

    if statuscode == "200" or status_text in ("ORDER_COMPLETED", "COMPLETED", "SUCCESS"):
        new_status = "completed"
    elif statuscode in ("100", "300") or status_text in ("ORDER_RECEIVED", "ORDER_PROCESSED"):
        new_status = "sent"
    elif getattr(airtime_result, "success", False):
        new_status = "sent"
    else:
        new_status = "failed_permanent"

    provider_response_json = json.dumps(raw_payload or {}, ensure_ascii=False)
    provider_payload_json = provider_response_json[:5000]

    updated = False

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                res = await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status = :status,
                            provider = :provider,
                            provider_ref = :provider_ref,
                            provider_reference = :provider_reference,
                            provider_response = :provider_response,
                            provider_payload = :provider_payload,
                            sent_at = CASE
                                WHEN :status IN ('sent', 'completed') THEN COALESCE(sent_at, NOW())
                                ELSE sent_at
                            END,
                            completed_at = CASE
                                WHEN :status = 'completed' THEN COALESCE(completed_at, NOW())
                                ELSE completed_at
                            END
                        WHERE id = :id
                          AND tg_id = :tg_id
                          AND status NOT IN (
                              'completed',
                              'failed_permanent',
                              'failed_needs_funding'
                          )
                        RETURNING status
                    """),
                    {
                        "id": payout_id,
                        "tg_id": tg_id,
                        "status": new_status,
                        "provider": provider_name,
                        "provider_ref": provider_ref,
                        "provider_reference": provider_ref,
                        "provider_response": provider_response_json,
                        "provider_payload": provider_payload_json,
                    },
                )
                row = res.first()
                updated = bool(row)

    except Exception:
        logger.exception("❌ Failed to update payout status in DB | payout_id=%s", payout_id)

    if not updated:
        logger.info(
            "ℹ️ Finalize skipped (idempotent) | payout_id=%s | attempted_status=%s",
            payout_id,
            new_status,
        )
        return

    try:
        if new_status == "completed":
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🎉 *Airtime Delivered!*\n\n"
                    f"₦{amount} has been credited to *{phone}* ✅\n"
                    f"Ref: `{provider_ref or 'N/A'}`"
                ),
                parse_mode="Markdown",
            )

        elif new_status == "sent":
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ *Airtime Request Accepted*\n\n"
                    f"Your airtime of ₦{amount} to *{phone}* has been accepted by the provider and is being delivered now.\n"
                    f"Ref: `{provider_ref or 'N/A'}`\n\n"
                    f"If it doesn’t reflect within a few minutes, we’ll retry/reconcile automatically."
                ),
                parse_mode="Markdown",
            )

        else:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "⚠️ *Airtime Not Sent Yet*\n\n"
                    "We couldn’t complete your airtime reward right now.\n"
                    "Please contact support via /support if it persists."
                ),
                parse_mode="Markdown",
            )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧠 Continue Playing", callback_data="playtrivia")],
            [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
        ])

        await bot.send_message(
            chat_id=chat_id,
            text="What would you like to do next?",
            reply_markup=keyboard,
        )

    except Exception:
        logger.exception(
            "❌ Failed to notify user after payout finalization | payout_id=%s",
            payout_id,
        )

    logger.info(
        "✅ Airtime payout finalized | payout_id=%s | phone=%s | amount=%s | status=%s | ref=%s",
        payout_id,
        phone,
        amount,
        new_status,
        provider_ref,
    )
