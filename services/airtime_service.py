# ======================================================================
# services/airtime_service.py
# Airtime Rewards via Flutterwave Hosted Checkout (No Bills API)
# ======================================================================
from __future__ import annotations

import os
import uuid
import httpx
import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import text
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db import AsyncSessionLocal
from utils.security import validate_phone
from services.playtrivia import AIRTIME_MILESTONES

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Environment & Constants
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
# Create Flutterwave Checkout Link (Trivia Purchases)
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
# Airtime Checkout Link (Reward Claim)
# -------------------------------------------------------------------
async def create_airtime_checkout_link(
    payout_id: str, tg_id: int, phone: str, amount: int
) -> Optional[str]:

    tx_ref = f"AIRTIME-{payout_id}"
    email = f"user_{tg_id}@naijaprizegate.ng"

    # Our server webhook for Flutterwave confirmation
    CALLBACK_URL = "https://naijaprizegate-bot-oo2x.onrender.com/flw/webhook"

    payload = {
        "tx_ref": tx_ref,
        "amount": amount,
        "currency": "NGN",
        # 🔥 We use FLW webhook endpoint for both redirect & callback
        "redirect_url": CALLBACK_URL,
        "callback_url": CALLBACK_URL,
        "customer": {
            "email": email,
            "phonenumber": phone,
            "name": f"User {tg_id}",
        },
        "payment_options": "card,ussd,banktransfer",
        "customizations": {
            "title": "Airtime Reward",
            "description": "Your airtime reward is waiting!",
            "logo": "https://naijaprizegate.ng/static/logo.png",
        },
        "meta": {
            "product": "airtime",
            "payout_id": payout_id,
            "tg_id": str(tg_id),
            "phone": phone,
        },
    }

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    logger.info(f"🌐 Creating Airtime Checkout → payout_id={payout_id} amount=₦{amount} phone={phone}")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{FLW_BASE_URL}/v3/payments", json=payload, headers=headers)
        data = resp.json()
    except Exception as e:
        logger.exception(f"Airtime checkout failed: {e}")
        return None

    return data.get("data", {}).get("link")

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
    Logic preserved EXACTLY as original; structure improved for consistency.
    """

    # Determine airtime amount based on milestone count
    amount = AIRTIME_MILESTONES.get(total_premium_spins)
    if not amount:
        logger.warning(f"⚠️ No airtime milestone defined for spins={total_premium_spins}")
        return

    payout_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Insert payout entry with NULL phone_number (same behavior as before)
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
        {
            "id": payout_id,
            "uid": user_id,
            "tg": tg_id,
            "amt": amount,
            "ts": now
        }
    )

    # Commit immediately — required because the next step sends UI
    await session.commit()

    # Build message
    safe_name = username or f"User {tg_id}"

    message = (
        f"🏆 *Milestone Unlocked, {safe_name}!* 🎉\n\n"
        f"🎯 You've reached *{total_premium_spins}* premium attempts.\n"
        f"💸 *₦{amount} Airtime Reward* unlocked!\n\n"
        "Tap the button below to claim 👇"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "⚡ Claim Airtime Reward",
            callback_data=f"claim_airtime:{payout_id}"
        )]
    ])

    # Send UI in both message + callback contexts, same as before
    try:
        if update.message:
            await update.message.reply_text(
                message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        else:
            await update.callback_query.message.reply_text(
                message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"⚠️ Failed to send airtime claim UI: {e}")

    logger.info(
        f"🎯 Airtime reward created | tg_id={tg_id} | payout_id={payout_id} | amount={amount}"
    )

    return payout_id

# -------------------------------------------------------------------
# Claim Button → Ask for Phone Number
# -------------------------------------------------------------------
async def handle_claim_airtime_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles pressing the "Claim Airtime Reward" button.
    Loads payout_id, sets a temporary claim session, and prompts user
    for their 11-digit Nigerian phone number.
    """

    query = update.callback_query
    await query.answer()

    # 🔥 DEBUG LOG HERE — confirms handler is firing
    logger.info("✅ claim_airtime callback received")
    
    # -------------------------------------------------------
    # Validate callback format: "claim_airtime:<payout_id>"
    # -------------------------------------------------------
    data = (query.data or "").strip()
    if not data.startswith("claim_airtime:") or ":" not in data:
        logger.error(f"⚠️ Invalid claim_airtime callback: {data}")
        return await query.message.reply_text(
            "⚠️ Invalid claim request. Please try again.",
            parse_mode="Markdown"
        )

    _, payout_id = data.split(":", 1)
    payout_id = payout_id.strip()

    if not payout_id:
        logger.error("⚠️ Missing payout_id in callback")
        return await query.message.reply_text(
            "⚠️ Reward reference missing. Please try again.",
            parse_mode="Markdown"
        )

    # -------------------------------------------------------
    # Reset temporary context state for this claim session
    # -------------------------------------------------------
    context.user_data.clear()
    expires_at = (datetime.utcnow() + timedelta(minutes=5)).timestamp()

    context.user_data.update({
        "pending_payout_id": payout_id,
        "awaiting_airtime_phone": True,
        "airtime_expiry": expires_at,
    })

    logger.info(
        f"📲 Airtime claim initiated | user={query.from_user.id} | payout_id={payout_id}"
    )

    # -------------------------------------------------------
    # Send the phone number request message
    # -------------------------------------------------------
    try:
        return await query.message.reply_text(
            "📱 Enter your *11-digit Nigerian phone number* to receive your airtime.\n"
            "Example: `08012345678`\n\n"
            "_This session expires in 5 minutes._",
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"⚠️ Failed to send phone collection prompt: {e}")
        return await query.message.reply_text(
            "⚠️ Could not start claim process. Please try again.",
            parse_mode="Markdown"
        )

# -------------------------------------------------------------------
# User Sends Phone → Validate → Update DB → Generate Checkout Link
# -------------------------------------------------------------------
async def handle_airtime_claim_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles user submission of phone number for claiming airtime rewards."""

    msg = update.message
    raw_phone = (msg.text or "").strip()

    # ---------------------------------
    # Normalize phone format
    # ---------------------------------
    phone = raw_phone.replace(" ", "").replace("-", "")

    # Convert +234XXXXXXXXXX → 0XXXXXXXXXX
    if phone.startswith("+234") and len(phone) >= 14:
        phone = "0" + phone[4:]

    payout_id = context.user_data.get("pending_payout_id")
    awaiting = context.user_data.get("awaiting_airtime_phone")
    expiry_ts = context.user_data.get("airtime_expiry")

    # ---------------------------------
    # Validate claim session state
    # ---------------------------------
    if not awaiting or not payout_id:
        context.user_data.clear()
        return await msg.reply_text("⛔ Claim session not active — please try again.")

    if expiry_ts and datetime.utcnow().timestamp() > expiry_ts:
        context.user_data.clear()
        return await msg.reply_text("⛔ Claim session expired — start again from rewards.")

    # ---------------------------------
    # Validate phone number
    # ---------------------------------
    if (
        not phone.isdigit() or
        len(phone) != 11 or
        not validate_phone(phone)
    ):
        return await msg.reply_text(
            "❌ Invalid number — must be like `08123456789`",
            parse_mode="Markdown"
        )

    # Once valid, ensure no repeated prompts
    context.user_data.pop("awaiting_airtime_phone", None)

    # ---------------------------------
    # Update payout record safely
    # ---------------------------------
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET phone_number = :p, status = 'claim_phone_set'
                        WHERE id = :id
                    """),
                    {"p": phone, "id": payout_id},
                )

        logger.info(f"☎️ Phone stored | payout_id={payout_id} | phone={phone}")

    except Exception as e:
        logger.exception(f"DB error updating payout phone: {e}")
        return await msg.reply_text("⚠️ Could not save your phone — try again shortly.")

    # ---------------------------------
    # UX: Generate link message
    # ---------------------------------
    await msg.reply_text("⏱ Generating your secure airtime link…")

    # ---------------------------------
    # Fetch reward amount
    # ---------------------------------
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text("SELECT amount FROM airtime_payouts WHERE id = :id"),
            {"id": payout_id},
        )
        row = res.first()
        amount = row[0] if row else None

    if not amount:
        logger.error(f"⚠️ No amount found for payout_id={payout_id}")
        return await msg.reply_text("⚠️ Something went wrong — please retry later.")

    # ---------------------------------
    # Create the final checkout link
    # ---------------------------------
    checkout_url = await create_airtime_checkout_link(
        payout_id=payout_id,
        tg_id=update.effective_user.id,
        phone=phone,
        amount=amount,
    )

    # Clear local session state after success
    context.user_data.clear()

    if not checkout_url:
        logger.error(f"⚠️ Failed to generate checkout link for payout_id={payout_id}")
        return await msg.reply_text("⚠️ Could not generate link — please retry shortly.")

    # ---------------------------------
    # Deliver the claim link
    # ---------------------------------
    await msg.reply_text(
        "🎯 *Almost there!*\n"
        "Tap the link below to *claim your airtime instantly* 🔥\n\n"
        f"{checkout_url}",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

    # ---------------------------------
    # Helpful UX buttons so user can continue
    # ---------------------------------
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Continue Playing", callback_data="playtrivia")],
        [InlineKeyboardButton("🎁 Check Rewards", callback_data="check_rewards")],
    ])

    await msg.reply_text(
        "🚀 You can continue playing while it's processing!",
        reply_markup=keyboard
    )

    logger.info(
        f"📩 Airtime checkout link sent | payout_id={payout_id} | phone={phone} | amount={amount}"
    )
