# ===============================================================
# handlers/payments.py
# ===============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from helpers import md_escape, get_or_create_user, add_tries
from models import Payment
from db import AsyncSessionLocal
from services.payments import create_checkout, verify_payment
from sqlalchemy import insert, update, select
import uuid
import os

# --- HARDCODED PACKAGES ---
PACKAGES = [
    (500, 1),
    (2000, 5),
    (5000, 15),
]

BOT_USERNAME = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")

# --- SUCCESS MESSAGE ---
def payment_success_text(user, amount, tries_added):
    return (
        f"💸 *Boom!* Payment received!\n\n"
        f"🎉 *{md_escape(user.first_name)}*, you just unlocked *{tries_added} new spins* 🚀\n"
        f"(Top-up: ₦{amount:,})\n\n"
        "Your arsenal is loaded, your chances just went way up ⚡\n\n"
        "👉 Don’t keep luck waiting — hit *Try Luck* now and chase that jackpot! 🏆🔥"
    )

# --- /buy entrypoint ---
async def buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show package options."""
    user = await get_or_create_user(update.effective_user)

    keyboard = [
        [InlineKeyboardButton(f"💳 Buy {tries} Try{'s' if tries>1 else ''} — ₦{price}", callback_data=f"buy_{price}")]
        for price, tries in PACKAGES
    ]

    await update.message.reply_text(
        f"🛒 *Choose your top-up package, {md_escape(user.first_name)}:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="MarkdownV2"
    )

# --- Handle package selection ---
async def handle_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("buy_"):
        return

    price = int(data.split("_")[1])
    tries = dict(PACKAGES)[price]

    # Generate tx_ref (unique per user)
    tx_ref = str(uuid.uuid4())

    # Create pending Payment row
    async with AsyncSessionLocal() as session:
        stmt = insert(Payment).values(
            user_id=query.from_user.id,
            amount=price,
            tries=tries,
            tx_ref=tx_ref,
            status="pending"
        )
        await session.execute(stmt)
        await session.commit()

    # Create checkout link via Flutterwave
    checkout_url = await create_checkout(amount=price, tx_ref=tx_ref, user_id=query.from_user.id)

    # Show Confirm & Cancel
    keyboard = [
        [InlineKeyboardButton("✅ Confirm & Pay", url=checkout_url)],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_payment")]
    ]

    await query.edit_message_text(
        f"💳 *Package selected:* {tries} Try{'s' if tries>1 else ''} for ₦{price}\n\n"
        "👉 Click below to confirm payment, or cancel to go back.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="MarkdownV2"
    )

# --- Cancel payment ---
async def handle_cancel_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    async with AsyncSessionLocal() as session:
        # Find their most recent pending payment
        result = await session.execute(
            select(Payment)
            .where(Payment.user_id == query.from_user.id, Payment.status == "pending")
            .order_by(Payment.created_at.desc())
        )
        pending_payment = result.scalars().first()

        # Delete if exists
        if pending_payment:
            await session.delete(pending_payment)
            await session.commit()

    # Back to menu
    keyboard = [
        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
        [InlineKeyboardButton("💳 Buy Tries", callback_data="buy_menu")],
        [InlineKeyboardButton("🎁 Free Tries", callback_data="free_menu")],
    ]

    await query.edit_message_text(
        "❌ Payment cancelled. Pending transaction cleared.\n\n"
        "You’re back at the main menu!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# --- Webhook success handler (called from app.py webhook endpoint) ---
async def handle_payment_success(tx_ref: str, amount: int, user_id: int, tries: int, bot):
    """Called after webhook verifies payment with Flutterwave."""
    async with AsyncSessionLocal() as session:
        # Mark row as successful
        stmt = (
            update(Payment)
            .where(Payment.tx_ref == tx_ref)
            .values(status="successful")
        )
        await session.execute(stmt)
        await session.commit()

    # Add tries to user
    await add_tries(user_id, tries, paid=True)

    # Notify user
    await bot.send_message(
        chat_id=user_id,
        text=payment_success_text(
            user=await get_or_create_user({"id": user_id}),  # minimal fallback
            amount=amount,
            tries_added=tries,
        ),
        parse_mode="MarkdownV2"
    )

# ----Expire Old Payments ----
from datetime import datetime, timedelta
from sqlalchemy import update

async def expire_old_payments():
    cutoff = datetime.utcnow() - timedelta(hours=24)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Payment)
            .where(Payment.status == "pending", Payment.created_at < cutoff)
            .values(status="expired")
        )
        await session.commit()

# --- Register handlers ---
def register_handlers(application):
    application.add_handler(CommandHandler("buy", buy_menu))
    application.add_handler(CallbackQueryHandler(handle_buy_callback, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(handle_cancel_payment, pattern="^cancel_payment$"))

