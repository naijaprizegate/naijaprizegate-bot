# services/airtime_service.py
# Airtime Rewards
# Provider-neutral reward/claim logic.
# Actual airtime delivery is handled by services.airtime_providers.
# Flutterwave Checkout remains for buying trivia attempts.
# ======================================================================

from __future__ import annotations

import os
import json
import uuid
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
    "https://naijaprizegate-bot.fly.dev/flw/redirect",
)

if not FLW_SECRET_KEY:
    raise RuntimeError("❌ FLW_SECRET_KEY is not set in environment variables")


def normalize_ng_phone(raw_phone: str) -> str:
    """Normalize a Nigerian phone number to the local 11-digit format."""
    phone = str(raw_phone or "").strip()

    # Remove common formatting characters.
    phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    if phone.startswith("+234"):
        phone = "0" + phone[4:]
    elif phone.startswith("234"):
        phone = "0" + phone[3:]

    return phone


def _is_safe_missing_phone_recovery(
    status,
    phone_number,
    provider_request_id,
    provider_ref,
    provider_reference,
    provider_response,
) -> bool:
    """
    Allow recovery only for the known false-terminal case where the provider
    recorded "Missing phone_number" and no provider request/reference exists.

    This deliberately does not reopen arbitrary failed payouts: if any request
    identifier/reference exists, the original transaction must be reconciled
    before another purchase is attempted.
    """
    if str(status or "").strip().lower() != "failed_permanent":
        return False
    if phone_number:
        return False
    if str(provider_request_id or "").strip():
        return False
    if str(provider_ref or "").strip() or str(provider_reference or "").strip():
        return False
    if isinstance(provider_response, str):
        try:
            provider_response = json.loads(provider_response)
        except Exception:
            return False
    if not isinstance(provider_response, dict):
        return False

    response_status = str(provider_response.get("status") or "").strip().lower()
    response_message = str(provider_response.get("message") or "").strip().lower()
    return response_status == "error" and response_message == "missing phone_number"


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
# Claim Button → Ask for Network
# -------------------------------------------------------------------
async def handle_claim_airtime_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Handles pressing the "Claim Airtime Reward" button.

    Validates the payout, stores a short-lived claim session,
    and asks the user to select their mobile network.
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
                    SELECT status, tg_id, amount, phone_number,
                           provider_request_id, provider_ref, provider_reference,
                           provider_response
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

        (
            status, payout_tg_id, amount, existing_phone,
            provider_request_id, provider_ref, provider_reference,
            provider_response,
        ) = row

        recoverable_missing_phone = _is_safe_missing_phone_recovery(
            status,
            existing_phone,
            provider_request_id,
            provider_ref,
            provider_reference,
            provider_response,
        )

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

        if status != "pending_claim" and not recoverable_missing_phone:
            await query.message.reply_text(
                "ℹ️ This reward is already being processed or completed. "
                "If you believe this is an error, please contact support.",
                parse_mode="Markdown",
            )
            return ConversationHandler.END

        if recoverable_missing_phone:
            logger.warning(
                "Allowing safe claim recovery after missing-phone validation failure "
                "| payout_id=%s | tg_id=%s",
                payout_id,
                user.id,
            )

        if not amount:
            await query.message.reply_text(
                "⚠️ Reward amount is missing. Please contact support.",
                parse_mode="Markdown",
            )
            return ConversationHandler.END

    except Exception:
        logger.exception(
            "❌ Error validating payout before network prompt | payout_id=%s",
            payout_id,
        )
        await query.message.reply_text(
            "⚠️ Could not verify this reward right now. Please try again shortly.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # ---------------------------------------------------------------
    # Start short-lived airtime claim session
    # ---------------------------------------------------------------
    expires_at = (datetime.utcnow() + timedelta(minutes=5)).timestamp()

    context.user_data["pending_payout_id"] = payout_id
    context.user_data["awaiting_airtime_network"] = True
    context.user_data["airtime_expiry"] = expires_at

    keyboard = [
        [
            InlineKeyboardButton("MTN", callback_data="airtime_network:mtn"),
            InlineKeyboardButton("Airtel", callback_data="airtime_network:airtel"),
        ],
        [
            InlineKeyboardButton("Glo", callback_data="airtime_network:glo"),
            InlineKeyboardButton("9mobile", callback_data="airtime_network:9mobile"),
        ],
    ]

    await query.message.reply_text(
        "📱 *Select your network:*\n\n"
        "Please choose the network that belongs to the phone number "
        "you want to receive your airtime on.\n\n"
        "_This session expires in 5 minutes._",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

    return ConversationHandler.END


# ===============================================================
# Handle Airtime Network Selection
# ===============================================================
async def handle_airtime_network_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Receives the user's selected mobile network for an airtime claim.
    """
    query = update.callback_query
    user = update.effective_user

    if not query or not user or not query.message:
        return ConversationHandler.END

    try:
        await query.answer()
    except Exception:
        pass

    data = (query.data or "").strip()

    if not data.startswith("airtime_network:"):
        return ConversationHandler.END

    network = data.split(":", 1)[1].strip().lower()

    allowed_networks = {
        "mtn": "MTN",
        "airtel": "Airtel",
        "glo": "Glo",
        "9mobile": "9mobile",
    }

    if network not in allowed_networks:
        await query.message.reply_text(
            "⚠️ Invalid network selection. Please try again.",
        )
        return ConversationHandler.END

    payout_id = context.user_data.get("pending_payout_id")
    expires_at = context.user_data.get("airtime_expiry")

    if not payout_id or not context.user_data.get("awaiting_airtime_network"):
        await query.message.reply_text(
            "⚠️ Your airtime claim session has expired. "
            "Please tap *Claim Airtime Reward* again.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    if expires_at and datetime.utcnow().timestamp() > float(expires_at):
        context.user_data.pop("pending_payout_id", None)
        context.user_data.pop("awaiting_airtime_network", None)
        context.user_data.pop("airtime_expiry", None)

        await query.message.reply_text(
            "⌛ Your airtime claim session has expired. "
            "Please tap *Claim Airtime Reward* again.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # Store the selected network for the next step.
    context.user_data["airtime_network"] = network

    # Move the session from network selection to phone entry.
    context.user_data.pop("awaiting_airtime_network", None)
    context.user_data["awaiting_airtime_phone"] = True

    await query.message.reply_text(
        f"✅ Network selected: *{allowed_networks[network]}*\n\n"
        "📱 Now enter your *11-digit Nigerian phone number* "
        "to receive your airtime.\n"
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

    # Do not interfere with support flow
    if context.user_data.get("support_active"):
        return ConversationHandler.END

    tg_id = user.id
    raw_phone = (msg.text or "").strip()

    logger.info("📩 AIRTIME PHONE HANDLER ENTRY | tg_id=%s", tg_id)

    payout_id = context.user_data.get("pending_payout_id")
    awaiting = context.user_data.get("awaiting_airtime_phone")
    expiry_ts = context.user_data.get("airtime_expiry")
    network = context.user_data.get("airtime_network")

    # ----------------------------------
    # Make sure a network was Selected
    # ----------------------------------
    allowed_networks = {
        "mtn",
        "airtel",
        "glo",
        "9mobile",
    }

    if awaiting and network not in allowed_networks:
        await msg.reply_text(
            "⚠️ Please select your network first.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

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
        context.user_data.pop("airtime_network", None)
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
                              AND phone_number IS NULL
                              AND (
                                  status = 'pending_claim'
                                  OR (
                                      status = 'failed_permanent'
                                      AND NULLIF(TRIM(COALESCE(provider_request_id, '')), '') IS NULL
                                      AND NULLIF(TRIM(COALESCE(provider_ref, '')), '') IS NULL
                                      AND NULLIF(TRIM(COALESCE(provider_reference, '')), '') IS NULL
                                      AND LOWER(COALESCE(provider_response ->> 'status', '')) = 'error'
                                      AND LOWER(COALESCE(provider_response ->> 'message', '')) = 'missing phone_number'
                                  )
                              )
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
                        SELECT status, tg_id, amount, phone_number, network,
                               provider_request_id, provider_ref, provider_reference,
                               provider_response
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

                (
                    status, payout_tg_id, amount, existing_phone, existing_network,
                    provider_request_id, provider_ref, provider_reference,
                    provider_response,
                ) = row

                recoverable_missing_phone = _is_safe_missing_phone_recovery(
                    status,
                    existing_phone,
                    provider_request_id,
                    provider_ref,
                    provider_reference,
                    provider_response,
                )

                logger.info(
                    "🔎 AIRTIME CLAIM CHECK | payout_id=%s | tg_id=%s | status=%s | owner=%s | amount=%s | has_phone=%s",
                    payout_id,
                    tg_id,
                    status,
                    payout_tg_id,
                    amount,
                    bool(existing_phone),
                )

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

                if status != "pending_claim" and not recoverable_missing_phone:
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
                update_result = await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET phone_number = :phone,
                            network = :network,
                            status = CASE
                                WHEN status = 'failed_permanent' THEN 'pending_claim'
                                ELSE status
                            END,
                            provider = CASE
                                WHEN status = 'failed_permanent' THEN NULL
                                ELSE provider
                            END,
                            provider_ref = CASE
                                WHEN status = 'failed_permanent' THEN NULL
                                ELSE provider_ref
                            END,
                            provider_reference = CASE
                                WHEN status = 'failed_permanent' THEN NULL
                                ELSE provider_reference
                            END,
                            provider_request_id = CASE
                                WHEN status = 'failed_permanent' THEN NULL
                                ELSE provider_request_id
                            END,
                            provider_response = CASE
                                WHEN status = 'failed_permanent' THEN NULL
                                ELSE provider_response
                            END,
                            provider_payload = CASE
                                WHEN status = 'failed_permanent' THEN NULL
                                ELSE provider_payload
                            END,
                            retry_count = CASE
                                WHEN status = 'failed_permanent' THEN 0
                                ELSE COALESCE(retry_count, 0)
                            END,
                            last_retry_at = NULL
                        WHERE id = :id
                          AND tg_id = :tg_id
                          AND (
                              status = 'pending_claim'
                              OR (
                                  status = 'failed_permanent'
                                  AND phone_number IS NULL
                                  AND NULLIF(TRIM(COALESCE(provider_request_id, '')), '') IS NULL
                                  AND NULLIF(TRIM(COALESCE(provider_ref, '')), '') IS NULL
                                  AND NULLIF(TRIM(COALESCE(provider_reference, '')), '') IS NULL
                                  AND LOWER(COALESCE(provider_response ->> 'status', '')) = 'error'
                                  AND LOWER(COALESCE(provider_response ->> 'message', '')) = 'missing phone_number'
                              )
                          )
                    """),
                    {
                        "phone": phone,
                        "network": network,
                        "id": payout_id,
                        "tg_id": tg_id,
                    },
                )

                if update_result.rowcount != 1:
                    logger.warning(
                        "Airtime claim update affected an unexpected number of rows "
                        "| payout_id=%s | tg_id=%s | rowcount=%s",
                        payout_id,
                        tg_id,
                        update_result.rowcount,
                    )
                    await msg.reply_text(
                        "Your reward could not be updated. Please try claiming it again "
                        "or contact support if the problem continues."
                    )
                    return ConversationHandler.END


        logger.info(
            "☎️ Airtime claim details stored successfully | payout_id=%s | tg_id=%s | network=%s",
            payout_id,
            tg_id,
            network,
        )

    except Exception:
        logger.exception("❌ DB error while saving phone | payout_id=%s", payout_id)
        await msg.reply_text(
            "⚠️ Could not save your phone number right now. Please try again shortly.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # -------------------------------------------------------
    # Clear airtime claim session keys
    # -------------------------------------------------------
    context.user_data.pop("awaiting_airtime_phone", None)
    context.user_data.pop("airtime_network", None)
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

    provider_name = (getattr(airtime_result, "provider", None) or "vtu_ng").lower()
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
