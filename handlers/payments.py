# =============================================================== 
# handlers/payments.py — Skill-Based Rewrite 🚫🎰 → ✔️🧠
# ===============================================================
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from helpers import md_escape, get_or_create_user
from models import Payment
from db import AsyncSessionLocal
from services.flutterwave_client import create_checkout, build_tx_ref
from services.trivia_payments import create_pending_trivia_payment
from sqlalchemy import update, select
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Users purchase *trivia question credits* (skill, not chance)
PACKAGES = [
    (100, 1),
    (500, 7),
    (1000, 15),
]

BOT_USERNAME = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")

# ---------------------------------------------------------------
# SUCCESS MESSAGE (Skill-Based Rewards)
# ---------------------------------------------------------------
def payment_success_text(user, amount, questions_added):
    return (
        f"🎉 Payment Received\\!\n\n"
        f"👏 {md_escape(user.username or user.first_name or 'Friend')}, "
        f"you've unlocked *{questions_added} new trivia questions* 🎯\n"
        f"(Top\\-up: ₦{amount:,})\n\n"
        "🧠 More knowledge, more progress, more leaderboard points!\n"
        "Let’s play and climb the ranks 🚀"
    )

# ---------------------------------------------------------------
# /buy → Select Trivia Package
# ---------------------------------------------------------------
async def buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_async_session() as session:
        user = await get_or_create_user(
            session, update.effective_user.id, update.effective_user.username
        )

    keyboard = [
        [InlineKeyboardButton(
            f"📚 {tries} Trivia Question{'' if tries == 1 else 's'} — ₦{price:,}",
            callback_data=f"buy_{price}"
        )]
        for price, tries in PACKAGES
    ]

    text = (
        f"🛒 *Boost Your Trivia Progress*\n\n"
        f"Choose a package below to unlock more quiz challenges and earn "
        f"more leaderboard points 📊\n\n"
        f"🧠 Correct answers earn *Premium Points*\n\n"
        f"🎯 Premium Points determine leaderboard ranking and jackpot winners\n\n"
        f"📜 Paid participation is governed by our *Terms & Fair Play Rules*"
    )   

    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="MarkdownV2")
    else:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="MarkdownV2")

# ---------------------------------------------------------------
# Handle Package Selection
# ---------------------------------------------------------------
async def handle_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("buy_"):
        return

    valid_packages = {100: 1, 500: 7, 1000: 15}

    try:
        price = int(data.split("_")[1])
    except (IndexError, ValueError):
        return await query.answer("❌ Invalid selection.", show_alert=True)

    if price not in valid_packages:
        return await query.answer("❌ Invalid package.", show_alert=True)

    questions = valid_packages[price]
    tx_ref = build_tx_ref("TRIVIA")

    username = query.from_user.username or f"user_{query.from_user.id}"
    email = f"{username}@naijaprizegate.ng"

    async with AsyncSessionLocal() as session:
        await create_pending_trivia_payment(
            session,
            tx_ref=tx_ref,
            tg_id=query.from_user.id,
            username=username,
            amount=price,
        )
        await session.commit()

    checkout_url = await create_checkout(
        user_id=query.from_user.id,
        amount=price,
        username=username,
        email=email,
        tx_ref=tx_ref,
        meta={
            "tg_id": str(query.from_user.id),
            "username": username,
            "product_type": "TRIVIA",
        },
        product_type="TRIVIA",
    )

    if not checkout_url:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Payment).where(Payment.tx_ref == tx_ref)
            )
            pending = result.scalar_one_or_none()
            if pending and pending.status == "pending":
                pending.status = "expired"
                await session.commit()

        return await query.edit_message_text(
            "⚠️ Payment service unavailable. Please try again shortly.",
            parse_mode="HTML",
        )

    keyboard = [
        [InlineKeyboardButton("💳 Pay Securely", url=checkout_url)],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_payment")]
    ]

    await query.edit_message_text(
        text=(
            f"📚 <b>Package Selected</b>: {questions} Trivia Question{'' if questions == 1 else 's'} "
            f"for ₦{price:,}\n\n"
            "✔ One trivia question per attempt\n"
            "✔ Correct answers earn <b>Premium Points</b>\n"
            "✔ Premium Points affect leaderboard ranking and jackpot winners\n\n"
            "✔ You could become the winner of the grand Prize <b>latest iPhone series</b> and <b>latest Samsung smart phones</b>\n\n"
            "✔ There are other rewards as you play. <b>Airtime</b> <b>Airpods</b> <b>Bluetooth Speakers</b>\n\n"
            "📜 By proceeding, you agree to our <b>Terms & Fair Play Rules</b>.\n\n"
            "👉 Tap to complete payment via Flutterwave Checkout.\n\n"
            "If the button doesn't work, copy the link and open it manually:\n"
            f"<a href='{checkout_url}'>{checkout_url}</a>"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    
# ---------------------------------------------------------------
# Cancel Payment
# ---------------------------------------------------------------
async def handle_cancel_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Payment)
            .where(
                Payment.tg_id == query.from_user.id,
                Payment.status == "pending"
            )
            .order_by(Payment.created_at.desc())
        )
        pending = result.scalars().first()
        
        if pending:
            await session.delete(pending)
            await session.commit()

    keyboard = [
        [InlineKeyboardButton("🧠 Play Trivia Questions (Win iPhone 17 Pro Max)", callback_data="playtrivia")],
        [InlineKeyboardButton("📚 Get More Questions", callback_data="buy")],
        [InlineKeyboardButton("🎁 Earn Free Questions", callback_data="free")],
    ]

    await query.edit_message_text(
        "❌ Payment cancelled\\.\nYou’re back at the menu 👍",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="MarkdownV2"
    )

# ---------------------------------------------------------------
# Expire stale pending payments
# ---------------------------------------------------------------
async def expire_old_payments():
    cutoff = datetime.utcnow() - timedelta(hours=24)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Payment).where(Payment.status=="pending", Payment.created_at < cutoff).values(status="expired")
        )
        await session.commit()

def register_handlers(application):
    application.add_handler(CommandHandler("buy", buy_menu))
    application.add_handler(CallbackQueryHandler(buy_menu, pattern="^buy$"))
    application.add_handler(CallbackQueryHandler(handle_buy_callback, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(handle_cancel_payment, pattern="^cancel_payment$"))
