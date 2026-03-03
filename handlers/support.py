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
from db import AsyncSessionLocal


# ==============================================================
# Conversation State
# ==============================================================

SUPPORT_WAITING_MESSAGE = 1


# ==============================================================
# Admin IDs Loader
# ==============================================================

def _get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")
    ids: set[int] = set()

    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))

    return ids


ADMIN_IDS = _get_admin_ids()


# ==============================================================
# ENTRY: /support or text button
# ==============================================================

async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📩 <b>Contact Support</b>\n\n"
        "✍️ Type your message and send it.\n\n"
        "To cancel, send /cancel.",
        parse_mode="HTML",
    )

    return SUPPORT_WAITING_MESSAGE


# ==============================================================
# ENTRY: Inline button callback
# ==============================================================

async def support_start_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    await query.answer()

    try:
        await query.edit_message_text(
            "📩 <b>Contact Support</b>\n\n"
            "✍️ Type your message and send it.\n\n"
            "To cancel, send /cancel.",
            parse_mode="HTML",
        )
    except Exception:
        await query.message.reply_text(
            "📩 <b>Contact Support</b>\n\n"
            "✍️ Type your message and send it.\n\n"
            "To cancel, send /cancel.",
            parse_mode="HTML",
        )

    return SUPPORT_WAITING_MESSAGE


# ==============================================================
# STATE: Receive Support Message
# ==============================================================

async def support_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return SUPPORT_WAITING_MESSAGE

    msg = update.message.text.strip()

    if not msg:
        await update.message.reply_text(
            "⚠️ Message cannot be empty. Please type your message:"
        )
        return SUPPORT_WAITING_MESSAGE

    user = update.effective_user

    # ==========================================================
    # Save to Database
    # ==========================================================
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO support_tickets
                    (tg_id, username, first_name, message, status, created_at)
                    VALUES (:tg_id, :username, :first_name, :message, 'pending', NOW())
                """),
                {
                    "tg_id": int(user.id),
                    "username": user.username,
                    "first_name": user.first_name,
                    "message": msg,
                },
            )
            await session.commit()

    except Exception:
        await update.message.reply_text(
            "❌ Support is temporarily unavailable.\n"
            "Please try again shortly or send /cancel."
        )
        return SUPPORT_WAITING_MESSAGE

    # ==========================================================
    # Notify Admins
    # ==========================================================
    who = user.first_name or "User"
    if user.username:
        who += f" (@{user.username})"

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "📩 <b>New Support Message</b>\n"
                    f"👤 {who}\n"
                    f"🆔 TG_ID: <code>{user.id}</code>\n\n"
                    f"<b>Message:</b>\n{msg}"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    # ==========================================================
    # Confirmation + Properly End Conversation
    # ==========================================================
    await update.message.reply_text(
        "✅ Your message has been sent.\n"
        "Support will reply here shortly.\n\n"
        "Send /start to return to menu."
    )

    return ConversationHandler.END


# ==============================================================
# CANCEL
# ==============================================================

async def support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "✅ Support request cancelled.\nSend /start to return to menu."
    )

    return ConversationHandler.END


# ==============================================================
# Conversation Handler
# ==============================================================

support_conv = ConversationHandler(
    entry_points=[
        CommandHandler("support", support_start),
        MessageHandler(
            filters.Regex(r"^📩 Contact Support / Admin$"),
            support_start,
        ),
        CallbackQueryHandler(
            support_start_from_callback,
            pattern=r"^support:start$",
        ),
    ],
    states={
        SUPPORT_WAITING_MESSAGE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                support_receive_message,
            ),
        ],
    },
    fallbacks=[
        CommandHandler("cancel", support_cancel),
    ],
    allow_reentry=True,
    per_message=False,
    per_chat=True,
    per_user=True,
    block=True,
)
