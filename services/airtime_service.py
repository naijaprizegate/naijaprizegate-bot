# ==========================================================================
# services/airtime_service.py
# Flutterwave Bills API (Airtime - All Nigerian networks)
# ==========================================================================
from __future__ import annotations

import os
import re
import uuid
import json
import httpx
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from telegram import Bot, Update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes
from telegram.ext import MessageHandler, filters

PHONE_REGEX = re.compile(r"^(?:\+?234|0)\d{10}$")

from logger import logger

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# ENV + Constants
# -------------------------------------------------------------------
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")  # Required
FLW_BASE_URL = os.getenv("FLW_BASE_URL", "https://api.flutterwave.com/v3")
AIRTIME_PROVIDER = os.getenv("AIRTIME_PROVIDER", "flutterwave")
WEBHOOK_REDIRECT_URL = os.getenv(
    "WEBHOOK_REDIRECT_URL",
    "https://naijaprizegate-bot-oo2x.onrender.com/flw/redirect"
)

if not FLW_SECRET_KEY:
    raise RuntimeError("❌ FLW_SECRET_KEY not set in environment variables.")


BILLS_ENDPOINT = f"{FLW_BASE_URL.rstrip('/')}/v3/bills"
COUNTRY = "NG"
CURRENCY = "NGN"  # Reserved for future usage


# -------------------------------------------------------------------
# Network prefix detection (fallback to FW if unknown)
# -------------------------------------------------------------------
NETWORK_PREFIXES = {
    "MTN": (
        "234703", "234706", "234803", "234806", "234810",
        "234813", "234814", "234816", "234903", "234906",
        "234913", "234916"
    ),
    "AIRTEL": (
        "234701", "234708", "234802", "234808", "234812",
        "234902", "234907", "234908", "234912"
    ),
    "GLO": (
        "234705", "234805", "234807", "234811", "234815",
        "234905"
    ),
    "9MOBILE": (
        "234809", "234817", "234818", "234909"
    ),
}


def normalize_msisdn(raw: str) -> str:
    """
    Normalize into 234XXXXXXXXXX format.
    """
    number = raw.strip().replace(" ", "").replace("-", "")

    if number.startswith("+"):
        number = number[1:]

    if number.startswith("0") and len(number) == 11:
        number = "234" + number[1:]

    return number

# ---Detect Network-----
def detect_network(msisdn_234: str) -> Optional[str]:
    """
    Prefix-based detection for better logging.
    """
    for net, prefixes in NETWORK_PREFIXES.items():
        if any(msisdn_234.startswith(p) for p in prefixes):
            return net
    return None


# ---Flutterwave Checkout Link----
async def create_flutterwave_checkout_link(
    tx_ref: str,
    amount: int,
    tg_id: int,
    username: str | None = None,
    email: str | None = None,
) -> str | None:
    """
    Create a Flutterwave checkout link for buying trivia attempts.

    - Uses real email if provided
    - Otherwise generates a fake but valid one: user_{tg_id}@naijaprizegate.ng
    """

    if not FLW_SECRET_KEY:
        logger.error("🚫 FLW_SECRET_KEY is not set in environment!")
        return None

    # 1️⃣ Decide customer email (Option C)
    if email and "@" in email:
        customer_email = email.strip()
    else:
        # 🔐 Safe fallback email based on Telegram ID
        customer_email = f"user_{tg_id}@naijaprizegate.ng"

    safe_username = (username or f"User {tg_id}")[:64]

    # 2️⃣ Construct secure payload
    payload = {
        "tx_ref": tx_ref,
        "amount": amount,
        "currency": "NGN",
        "redirect_url": WEBHOOK_REDIRECT_URL,
        "customer": {
            "email": customer_email,
            "name": safe_username,
        },
        "customizations": {
            "title": "NaijaPrizeGate",
            # You can keep or change this logo URL
            "logo": "https://naijaprizegate.ng/static/logo.png",
        },
        "meta": {
            "tg_id": str(tg_id),
            "username": safe_username,
            "generated_at": datetime.utcnow().isoformat(),
        },
    }

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    # 3️⃣ Call Flutterwave
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{FLW_BASE_URL}/payments",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            f"🚫 Flutterwave checkout failed [{e.response.status_code}]: {e.response.text}"
        )
        return None
    except Exception as e:
        logger.exception(
            f"⚠️ Unexpected error during checkout for tg_id={tg_id}: {e}"
        )
        return None

    # 4️⃣ Validate API response
    if data.get("status") != "success" or "data" not in data:
        logger.error(f"🚫 Invalid Flutterwave response structure: {data}")
        return None

    payment_link = data["data"].get("link")
    if not payment_link:
        logger.error(f"🚫 Missing payment link in Flutterwave response: {data}")
        return None

    logger.info(
        f"✅ Checkout created for {customer_email} ({amount:,} NGN) — TX: {tx_ref}"
    )
    return payment_link

# ----------------------------------------------------
# Get Airtime Amount
# ----------------------------------------------------
async def get_airtime_amount(payout_id: str) -> int:
    async with async_session() as session:
        res = await session.execute(
            text("SELECT amount FROM airtime_payouts WHERE id = :pid"),
            {"pid": payout_id}
        )
        row = res.first()
        return row[0] if row else 0

# -------------------------------------------------------------------
# 🎯 Create Airtime Payout Record (pending_claim) + Claim Button
# -------------------------------------------------------------------
async def create_pending_airtime_payout_and_prompt(
    session,
    update,
    user_id,
    tg_id: int,
    username: str | None,
    total_premium_spins: int,
):
    """
    Called when a user hits an airtime milestone.

    - Looks up the airtime amount from AIRTIME_MILESTONES
    - Inserts a row into airtime_payouts with status = 'pending_claim'
    - Sends a Telegram message with a ⚡ Claim Airtime Reward button
    """

    # 1️⃣ Check if this spin count has a configured reward
    amount = AIRTIME_MILESTONES.get(total_premium_spins)
    if not amount:
        return  # no airtime at this milestone

    payout_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # 2️⃣ Insert pending_claim payout into DB
    await session.execute(
        text("""
            INSERT INTO airtime_payouts (
                id,
                user_id,
                tg_id,
                phone_number,
                amount,
                status,
                flutterwave_tx_ref,
                provider_response,
                retries,
                last_retry_at,
                created_at,
                completed_at
            )
            VALUES (
                :id,
                :user_id,
                :tg_id,
                NULL,
                :amount,
                'pending_claim',
                NULL,
                NULL,
                0,
                NULL,
                :created_at,
                NULL
            )
        """),
        {
            "id": payout_id,
            "user_id": user_id,
            "tg_id": tg_id,
            "amount": amount,
            "created_at": now,
        },
    )
    await session.commit()

    logger.info(
        f"🎯 Airtime reward unlocked | user_id={user_id} tg_id={tg_id} "
        f"spins={total_premium_spins} amount={amount} payout_id={payout_id}"
    )

    # 3️⃣ Send Claim Airtime message with button
    safe_username = username or f"User {tg_id}"

    text_msg = (
        f"🏆 *Milestone Unlocked, {safe_username}!* 🎉\n\n"
        f"🎯 You just reached *{total_premium_spins}* premium attempts.\n"
        f"💸 Airtime reward unlocked: *₦{amount}* 🔥\n\n"
        "📱 To receive your reward, tap the button below and enter the "
        "*11-digit Nigerian phone number* you want the airtime sent to.\n\n"
        "_You can even send it to a friend!_ 💚"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⚡ Claim Airtime Reward",
                callback_data=f"claim_airtime:{payout_id}",
            )
        ]
    ])

    # Use the same pattern you use elsewhere for replying
    if update.message:
        await update.message.reply_text(
            text_msg,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    elif update.callback_query:
        await update.callback_query.message.reply_text(
            text_msg,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

# -----------------------------------------------------
# Handle Claim Airtime Button
# -----------------------------------------------------
async def handle_claim_airtime_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles tap on: ⚡ Claim Airtime Reward
    callback_data looks like: 'claim_airtime:<payout_id>'
    """
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    _, payout_id = data.split(":", 1)  # 'claim_airtime', '<uuid>'

    # Store which payout we are collecting phone for
    context.user_data["awaiting_airtime_phone_for"] = payout_id

    await query.message.reply_text(
        "📱 Please enter the *11-digit Nigerian phone number* you want the airtime sent to.\n"
        "Example: *08123456789*",
        parse_mode="Markdown",
    )


# ---------------------------------------------------
# Handle Airtime Claim Phone (FINAL MERGED VERSION)
# ---------------------------------------------------
async def handle_airtime_claim_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process phone entry for airtime claim with expiry & UX safety."""
    
    tg_id = update.effective_user.id
    message = update.message
    phone = message.text.strip().replace(" ", "").replace("-", "")

    # Check whether phone is expected now
    payout_id = context.user_data.get("pending_payout_id")
    awaiting = context.user_data.get("awaiting_airtime_phone")
    expiry = context.user_data.get("claim_expiry")

    if not awaiting or not payout_id:
        # User typed random text — ignore or notify
        await message.reply_text(
            "⛔ This airtime claim session has expired.\n"
            "Return to the rewards menu and try again 🎯"
        )
        return

    # Timeout protection ⏳
    if not expiry or datetime.utcnow().timestamp() > expiry:
        context.user_data.clear()
        await message.reply_text(
            "⌛ Your airtime claim session expired.\n"
            "Try again from the rewards page 🔁"
        )
        return

    # Convert +234 format
    if phone.startswith("+234"):
        phone = "0" + phone[4:]

    # Validate full Nigerian line format
    if not phone.isdigit() or len(phone) != 11 or not validate_phone(phone):
        await message.reply_text(
            "❌ Invalid phone number!\n"
            "Send a *valid 11-digit Nigerian mobile number* 📱\n"
            "Example: `08123456789`",
            parse_mode="Markdown",
        )
        return

    # Phone valid → Remove state lock
    del context.user_data["awaiting_airtime_phone"]

    # Save phone immediately into DB
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    UPDATE airtime_payouts
                    SET phone_number = :phone,
                        status = 'claim_phone_set',
                        updated_at = NOW()
                    WHERE id = :pid
                """),
                {"pid": payout_id, "phone": phone},
            )

    await message.reply_text("⏱ Generating a secure Flutterwave checkout…")

    # Generate FW checkout URL
    amount = context.user_data.get("pending_airtime_amount", 0)
    checkout_url = await create_airtime_checkout_link(
        payout_id=payout_id,
        tg_id=tg_id,
        phone=phone,
        amount=amount
    )

    if not checkout_url:
        await message.reply_text(
            "⚠️ Something went wrong generating your airtime link.\n"
            "Try again shortly!"
        )
        return

    # Send Airtime Payment Link
    await message.reply_text(
        "🎯 You’re almost there!\n\n"
        "Tap the secure link below to *claim your airtime instantly* 🔥\n\n"
        f"{checkout_url}"
    )

    # Encourage continued engagement
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Continue Playing", callback_data="playtrivia")],
        [InlineKeyboardButton("🎁 Check Rewards", callback_data="check_rewards")],
    ])

    await message.reply_text(
        "🔄 You can continue playing while your airtime processes! 🚀✨",
        reply_markup=keyboard,
    )

    # Cleanup session state
    context.user_data.clear()
    logger.info(f"📩 Airtime claim link sent | payout_id={payout_id} phone={phone}")


# ===================================================================
# Single airtime payout processor — Flutterwave Bills API (Patched)
# ===================================================================

MAX_RETRY = 5   # prevents infinite retry loops

async def process_single_airtime_payout(
    session: AsyncSession,
    payout_id: str,
    bot: Optional[Bot],
    admin_id: int,
) -> None:

    if AIRTIME_PROVIDER.lower() != "flutterwave":
        logger.warning(f"⚠️ Unsupported airtime provider configured: {AIRTIME_PROVIDER}")
        return

    # Create bot instance if missing
    if bot is None:
        try:
            bot = Bot(token=BOT_TOKEN)
        except Exception as e:
            logger.error(f"❌ Failed to init Bot instance: {e}")
            return

    async with session.begin():
        res = await session.execute(
            text("""
                SELECT id, user_id, tg_id, phone_number, amount, retry_count
                FROM airtime_payouts
                WHERE id = :pid AND status = 'pending'
                FOR UPDATE
            """),
            {"pid": payout_id},
        )
        row = res.first()

        if not row:
            logger.info(f"ℹ️ No pending payout for {payout_id}")
            return

        row_map = row._mapping
        phone = row_map["phone_number"]
        amount = row_map["amount"]
        tg_id = row_map["tg_id"]
        retry_count = row_map["retry_count"] or 0

        if not phone:
            logger.warning(f"📵 No phone number for payout {payout_id}")
            await session.execute(
                text("""
                    UPDATE airtime_payouts
                    SET status='pending_phone',
                        last_retry_at=NULL
                    WHERE id=:pid
                """),
                {"pid": payout_id}
            )
            return

        if retry_count >= MAX_RETRY:
            logger.error(f"🚫 Retry limit reached for {payout_id}")
            await session.execute(
                text("""
                    UPDATE airtime_payouts
                    SET status='failed',
                        last_retry_at=NOW()
                    WHERE id=:pid
                """),
                {"pid": payout_id}
            )
            return

        logger.info(f"🚀 Processing payout {payout_id}: ₦{amount} → {phone}")

        try:
            fw_data = await call_flutterwave_airtime(phone, amount)
            status = str(fw_data.get("status", "")).lower()
            data = fw_data.get("data") or {}
            fw_ref = data.get("reference") or data.get("tx_ref")
            provider_json = json.dumps(fw_data)

            # Save JSONB safely
            def update_status(new_status):
                return session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status=:sts,
                            provider_response = CAST(:resp AS JSONB),
                            retry_count = retry_count + 1,
                            last_retry_at = NOW()
                        WHERE id=:pid
                    """),
                    {"pid": payout_id, "resp": provider_json, "sts": new_status},
                )

            # SUCCESS
            if status == "success":
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status='completed',
                            flutterwave_tx_ref=:tx,
                            provider_response = CAST(:resp AS JSONB),
                            completed_at=NOW()
                        WHERE id=:pid
                    """),
                    {"pid": payout_id, "tx": fw_ref, "resp": provider_json},
                )

                masked = phone[:-4].rjust(len(phone), "•")
                try:
                    await bot.send_message(
                        tg_id,
                        (
                            "🎉 *Airtime Reward Sent!*\n\n"
                            f"📱 `{masked}`\n"
                            f"💸 *₦{amount}*\n\n"
                            "Keep playing & winning! 🔥"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

                await bot.send_message(
                    admin_id,
                    f"📲 Airtime sent\nPhone: `{phone}`\nAmount: ₦{amount}\nRef: `{fw_ref}`",
                    parse_mode="Markdown"
                )

                logger.info(f"💚 Completed Airtime | ID {payout_id}")
                return

            # FAILURE FROM API
            await update_status("failed")
            logger.warning(f"⚠️ FW returned failure for {payout_id}: {status}")

            await bot.send_message(
                admin_id,
                f"⚠️ Airtime FAILED\nPhone: `{phone}`\nAmount: ₦{amount}\nFW Status: `{status}`",
                parse_mode="Markdown"
            )
            return

        except Exception as e:
            err = str(e).lower()
            logger.error(f"❌ Exception in payout {payout_id}: {e}")

            if "whitelist" in err:
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status='ip_blocked',
                            last_retry_at=NOW()
                        WHERE id=:pid
                    """),
                    {"pid": payout_id},
                )
                logger.critical("🚫 Flutterwave blocking API — IP whitelisting required")
            else:
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status='failed',
                            last_retry_at=NOW()
                        WHERE id=:pid
                    """),
                    {"pid": payout_id},
                )

            try:
                await bot.send_message(
                    admin_id,
                    (
                        "🚨 *Airtime payout EXCEPTION*\n"
                        f"ID: `{payout_id}`\n"
                        f"Phone: `{phone}`\n"
                        f"Amount: ₦{amount}\n"
                        f"Error: `{e}`"
                    ),
                    parse_mode="Markdown",
                )
            except:
                pass


application.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_airtime_claim_phone
    )
)
