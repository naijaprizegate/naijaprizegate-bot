# =============================================================== 
# handlers/payments.py
# ===============================================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from helpers import md_escape, get_or_create_user, add_tries
from models import Payment, User
from db import AsyncSessionLocal, get_async_session
from services.payments import create_checkout
from sqlalchemy import insert, update, select
from datetime import datetime, timedelta
import uuid
import os
import logging
from logging_config import setup_logger
from helpers import mask_sensitive

logger = setup_logger("payments")

...

logger.info(
    f"✅ Payment verified: user={mask_sensitive(str(user_id))}, "
    f"tx_ref={mask_sensitive(tx_ref)}, amount={amount}"
)

logger = logging.getLogger(__name__)

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
        f"💸 *Boom\\!* Payment received\\!\n\n"
        f"🎉 *{md_escape(user.username or user.first_name or 'Friend')}*, you just unlocked *{tries_added} new spins* 🚀\n"
        f"(Top\\-up: ₦{amount:,})\n\n"
        "Your arsenal is loaded, your chances just went way up ⚡\n\n"
        "👉 Don’t keep luck waiting\\. Hit *Try Luck* now and chase that jackpot\\! 🏆🔥"
    )

# --- /buy entrypoint ---
async def buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_async_session() as session:
        user = await get_or_create_user(
            session, update.effective_user.id, update.effective_user.username
        )

    keyboard = [
        [InlineKeyboardButton(f"💳 Buy {tries} Try{'s' if tries>1 else ''} — ₦{price}", callback_data=f"buy_{price}")]
        for price, tries in PACKAGES
    ]

    if update.message:
        await update.message.reply_text(
            f"🛒 *Choose your top\\-up package, {md_escape(user.username or 'Friend')}:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            f"🛒 *Choose your top\\-up package, {md_escape(user.username or 'Friend')}:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )

# --- Handle package selection (SECURE) ---
async def handle_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("buy_"):
        return

    # ✅ Validate package choice from trusted config (no user tampering)
    valid_packages = {500: 1, 2000: 5, 5000: 15}
    try:
        price = int(data.split("_")[1])
    except (IndexError, ValueError):
        return await query.answer("❌ Invalid selection.", show_alert=True)

    if price not in valid_packages:
        return await query.answer("❌ Invalid package.", show_alert=True)

    tries = valid_packages[price]
    tx_ref = str(uuid.uuid4())

    # ✅ Safely register pending payment in DB
    async with AsyncSessionLocal() as session:
        db_user = await get_or_create_user(session, query.from_user.id, query.from_user.username)

        # Defensive: delete any old "pending" payment for same user
        await session.execute(
            Payment.__table__.delete().where(
                Payment.user_id == db_user.id,
                Payment.status == "pending"
            )
        )

        stmt = insert(Payment).values(
            user_id=db_user.id,
            amount=price,
            credited_tries=tries,
            tx_ref=tx_ref,
            status="pending",
            created_at=datetime.utcnow()
        )
        await session.execute(stmt)
        await session.commit()

    # ✅ Prepare checkout session
    username = query.from_user.username or f"user_{query.from_user.id}"
    email = f"{username}@naijaprizegate.ng"

    checkout_url = await create_checkout(
        amount=price,
        tx_ref=tx_ref,
        user_id=query.from_user.id,
        username=username,
        email=email
    )

    # ✅ Safety net: ensure URL was generated properly
    if not checkout_url:
        return await query.edit_message_text(
            "⚠️ Payment service unavailable. Please try again shortly.",
            parse_mode="HTML"
        )

    # ✅ Clean UI
    keyboard = [
        [InlineKeyboardButton("✅ Confirm & Pay", url=checkout_url)],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_payment")]
    ]

    await query.edit_message_text(
        text=(
            f"💳 <b>Package selected:</b> {tries} Try{'s' if tries > 1 else ''} for ₦{price:,}\n\n"
            "👉 Click below to confirm your payment securely.\n\n"
            f"If the button doesn’t work, copy this link and open it in Chrome or Safari:\n"
            f"<a href='{checkout_url}'>{checkout_url}</a>\n\n"
            "💡 <b>Tip:</b> If Telegram blocks it, disable in-app browser in settings.\n"
            "Then tap again — smooth sailing. 😎"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# --- Cancel payment ---
async def handle_cancel_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    print("❌ Cancel button pressed by", query.from_user.id)

    async with AsyncSessionLocal() as session:
        db_user = await get_or_create_user(session, query.from_user.id, query.from_user.username)

        result = await session.execute(
            select(Payment)
            .where(Payment.user_id == db_user.id, Payment.status == "pending")
            .order_by(Payment.created_at.desc())
        )
        pending = result.scalars().first()
        if pending:
            await session.delete(pending)
            await session.commit()
            print(f"Deleted pending payment {pending.tx_ref} for {db_user.id}")

    keyboard = [
        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
        [InlineKeyboardButton("💳 Buy Tries", callback_data="buy")],
        [InlineKeyboardButton("🎁 Free Tries", callback_data="free")],
    ]

    await query.edit_message_text(
        "❌ Payment cancelled\\. Pending transaction cleared\\.\n\n"
        "You’re back at the main menu\\!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="MarkdownV2"
    )

# --- Webhook success handler ---
async def handle_payment_success(tx_ref: str, amount: int, user_id: int, tries: int, bot):
    try:
        # 🔐 Step 1: Confirm transaction is truly successful
        is_valid = await verify_transaction(tx_ref, amount)
        if not is_valid:
            logger.warning(f"⚠️ Webhook verification failed for tx_ref={tx_ref}")
            return

        async with get_async_session() as session:
            # 🔍 Fetch the current Payment row state
            result = await session.execute(
                select(Payment).where(Payment.tx_ref == tx_ref)
            )
            payment_row = result.scalar_one_or_none()

            if not payment_row:
                logger.error(f"❌ No Payment row found for tx_ref={tx_ref}")
                return

            logger.info(
                f"🔎 Incoming webhook for tx_ref={tx_ref} → "
                f"DB status={payment_row.status}, "
                f"credited_tries={payment_row.credited_tries}, "
                f"completed_at={payment_row.completed_at}"
            )

            # 🚦 Idempotency check (skip ONLY if both status=successful AND tries already credited)
            if payment_row.status == "successful" and payment_row.credited_tries > 0:
                logger.info(
                    f"ℹ️ Payment {tx_ref} already credited "
                    f"({payment_row.credited_tries} tries) → skipping re-credit"
                )
                return

            # 1. Fetch & credit user
            db_user = await get_or_create_user(session, user_id)
            db_user.tries_paid += tries

            # 2. Mark payment successful (in same transaction)
            payment_row.status = "successful"
            payment_row.credited_tries = tries
            payment_row.completed_at = datetime.utcnow()

            # 📝 Diagnostic log BEFORE commit
            logger.info(
                f"📝 Pre-commit state → "
                f"user_id={db_user.id}, tg_id={db_user.tg_id}, "
                f"paid={db_user.tries_paid}, bonus={db_user.tries_bonus}; "
                f"payment.status={payment_row.status}, "
                f"credited_tries={payment_row.credited_tries}"
            )

            # 3. Commit both changes atomically
            await session.commit()

            # 4. Refresh user so we have latest values
            await session.refresh(db_user)

            # ✅ Success logs
            logger.info(
                f"🎉 Credited {tries} tries for user {user_id} "
                f"(tx_ref={tx_ref}, amount={amount})"
            )
            logger.info(
                f"📊 After credit: db_user.id={db_user.id}, tg_id={db_user.tg_id}, "
                f"paid={db_user.tries_paid}, bonus={db_user.tries_bonus}"
            )

    except Exception as e:
        logger.error(
            f"❌ Failed to credit tries for user_id={user_id}, "
            f"tx_ref={tx_ref}, amount={amount}, tries={tries} → {e}",
            exc_info=True
        )
        return

    # 5. Notify user only if DB commit succeeded
    await bot.send_message(
        chat_id=user_id,
        text=payment_success_text(db_user, amount, tries),
        parse_mode="MarkdownV2"
    )
    
# ---- Expire Old Payments ----
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
    application.add_handler(CallbackQueryHandler(buy_menu, pattern="^buy$"))
    application.add_handler(CallbackQueryHandler(handle_buy_callback, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(handle_cancel_payment, pattern="^cancel_payment$"))

