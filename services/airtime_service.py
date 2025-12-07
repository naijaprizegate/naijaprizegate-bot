# ======================================================================
# services/airtime_service.py
# Airtime Rewards via Flutterwave Hosted Checkout (No Bills API)
# ======================================================================
from __future__ import annotations

import os
import uuid
import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import text
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db import AsyncSessionLocal
from utils.security import validate_phone
from config import AIRTIME_MILESTONES

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

    payload = {
        "tx_ref": tx_ref,
        "amount": amount,
        "currency": "NGN",
        "redirect_url": WEBHOOK_REDIRECT_URL,
        "customer": {"email": email, "phonenumber": phone, "name": f"User {tg_id}"},
        "customizations": {
            "title": "Airtime Reward",
            "description": "Your airtime reward is waiting!",
            "logo": "https://naijaprizegate.ng/static/logo.png",
        },
        "meta": {"payout_id": payout_id, "tg_id": str(tg_id), "phone": phone},
    }

    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}", "Content-Type": "application/json"}

    logger.info(f"🌐 Creating Airtime Checkout → payout={payout_id} ₦{amount} ➜ {phone}")

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
    session, update, user_id, tg_id, username: Optional[str], total_premium_spins: int
):
    amount = AIRTIME_MILESTONES.get(total_premium_spins)
    if not amount:
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

    if update.message:
        await update.message.reply_text(message, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.callback_query.message.reply_text(message, reply_markup=keyboard, parse_mode="Markdown")

    logger.info(f"🎯 Airtime reward created | {tg_id=} | payout_id={payout_id}")


# -------------------------------------------------------------------
# Claim Button → Ask for Phone Number
# -------------------------------------------------------------------
async def handle_claim_airtime_button(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    _, payout_id = query.data.split(":", 1)

    context.user_data.clear()
    context.user_data["pending_payout_id"] = payout_id
    context.user_data["awaiting_airtime_phone"] = True
    context.user_data["airtime_expiry"] = (datetime.utcnow() + timedelta(minutes=5)).timestamp()

    await query.message.reply_text(
        "📱 Enter the *11-digit Nigerian phone number* to receive your airtime.\n"
        "Example: `08012345678`",
        parse_mode="Markdown"
    )


# -------------------------------------------------------------------
# User Sends Phone → Create Checkout + UX Buttons
# -------------------------------------------------------------------
async def handle_airtime_claim_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message
    phone = msg.text.strip().replace(" ", "").replace("-", "")

    payout_id = context.user_data.get("pending_payout_id")
    awaiting = context.user_data.get("awaiting_airtime_phone")
    expiry = context.user_data.get("airtime_expiry")

    if not awaiting or not payout_id or datetime.utcnow().timestamp() > (expiry or 0):
        context.user_data.clear()
        return await msg.reply_text("⛔ Claim session expired — open rewards again.")

    if phone.startswith("+234"):
        phone = "0" + phone[4:]

    if not phone.isdigit() or len(phone) != 11 or not validate_phone(phone):
        return await msg.reply_text("❌ Invalid number — must be `08123456789` format")

    context.user_data.pop("awaiting_airtime_phone", None)

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("UPDATE airtime_payouts SET phone_number=:p, status='claim_phone_set' WHERE id=:id"),
            {"p": phone, "id": payout_id}
        )
        await session.commit()

    await msg.reply_text("⏱ Generating your secure airtime link…")

    # Correct lookup — get reward amount from DB!
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT amount FROM airtime_payouts WHERE id=:id"), {"id": payout_id})
        row = result.first()
        amount = row[0] if row else None

    if not amount:
        return await msg.reply_text("⚠️ Something went wrong — retry later.")

    checkout_url = await create_airtime_checkout_link(
        payout_id=payout_id,
        tg_id=update.effective_user.id,
        phone=phone,
        amount=amount
    )

    context.user_data.clear()

    if not checkout_url:
        return await msg.reply_text("⚠️ Could not generate link — try again shortly.")

    await msg.reply_text(
        "🎯 *Almost there!*\n"
        "Tap link below to *claim your airtime instantly* 🔥\n\n"
        f"{checkout_url}",
        parse_mode="Markdown"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Continue Playing", callback_data="playtrivia")],
        [InlineKeyboardButton("🎁 Check Rewards", callback_data="check_rewards")],
    ])

    await msg.reply_text("🚀 You can continue playing while it's processing!", reply_markup=keyboard)
    logger.info(f"📩 Airtime checkout sent | payout_id={payout_id} | phone={phone}")

