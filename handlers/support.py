# ==============================================================
# handlers/support.py
# ==============================================================
import os
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from sqlalchemy import text

from db import AsyncSessionLocal  # ✅ adjust import if your session lives elsewhere

SUPPORT_WAITING_MESSAGE = 1

# ✅ Put admin IDs in env
def _get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids

ADMIN_IDS = _get_admin_ids()


async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📩 <b>Contact Support</b>\n\n"
        "Type your message here and send it.\n\n"
        "To cancel, type or click on /start to go back to the main menu",
        parse_mode="HTML",
    )
    return SUPPORT_WAITING_MESSAGE


async def support_start_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "📩 <b>Contact Support</b>\n\n"
        "Type your message here and send it.\n\n"
        "To cancel, type or click on /start to go back to the main menu",
        parse_mode="HTML",
    )
    return SUPPORT_WAITING_MESSAGE


async def support_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (update.message.text or "").strip()
    if not msg:
        await update.message.reply_text("Please type a message.")
        return SUPPORT_WAITING_MESSAGE

    user = update.effective_user

    # ✅ Save to DB
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO support_tickets (tg_id, username, first_name, message, status)
                VALUES (:tg_id, :username, :first_name, :message, 'pending')
            """),
            {
                "tg_id": int(user.id),
                "username": user.username,
                "first_name": user.first_name,
                "message": msg,
            },
        )
        await session.commit()

    # ✅ Optional: notify admins
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "📩 <b>New Support Message</b>\n"
                    f"From: {user.first_name or 'User'}"
                    f"{' (@' + user.username + ')' if user.username else ''}\n"
                    f"TG_ID: <code>{user.id}</code>\n\n"
                    f"<b>Message:</b>\n{msg}\n\n"
                    "Use /admin to view tickets."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await update.message.reply_text(
        "✅ Your message has been sent to support.\n"
        "We’ll reply here as soon as possible."
    )
    return ConversationHandler.END


async def support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Cancelled. Back to menu.")
    return ConversationHandler.END


support_conv = ConversationHandler(
    entry_points=[
        CommandHandler("support", support_start),

        # ✅ This catches your menu button text (ReplyKeyboard)
        MessageHandler(filters.Regex(r"^📩 Contact Support / Admin$"), support_start),

        # ✅ If you used an InlineKeyboard callback button
        CallbackQueryHandler(support_start_from_callback, pattern=r"^support:start$"),
    ],
    states={
        SUPPORT_WAITING_MESSAGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive_message),
        ],
    },
    fallbacks=[CommandHandler("cancel", support_cancel)],
)

