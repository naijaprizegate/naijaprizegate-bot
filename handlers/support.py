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

from db import AsyncSessionLocal  # adjust if needed


# -----------------------------
# Conversation State
# -----------------------------
SUPPORT_WAITING_MESSAGE = 1


# -----------------------------
# Admin IDs from env
# -----------------------------
def _get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


ADMIN_IDS = _get_admin_ids()


# -----------------------------
# Helpers
# -----------------------------
async def _send_support_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sends the "Type your message" prompt and activates support state.
    Works for both command/message entry and callback entry.
    """
    # Flag used to prevent your global "unknown" handler from replying during support flow
    context.user_data["in_support_flow"] = True

    text_prompt = (
        "📩 <b>Contact Support</b>\n\n"
        "Type your message here and send it.\n\n"
        "To cancel, send /cancel (or /start to return to the menu)."
    )

    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await q.message.reply_text(text_prompt, parse_mode="HTML")
    else:
        await update.effective_message.reply_text(text_prompt, parse_mode="HTML")

    return SUPPORT_WAITING_MESSAGE


# -----------------------------
# Entry points
# -----------------------------
async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _send_support_prompt(update, context)


async def support_start_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _send_support_prompt(update, context)


# -----------------------------
# State handler
# -----------------------------
async def support_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Must be a normal text message
    if not update.message or not update.message.text:
        return SUPPORT_WAITING_MESSAGE

    msg = (update.message.text or "").strip()
    if not msg:
        await update.message.reply_text("⚠️ Please type a message.")
        return SUPPORT_WAITING_MESSAGE

    user = update.effective_user

    # ✅ Save ticket to DB
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

    # ✅ Notify admins (optional)
    for admin_id in ADMIN_IDS:
        try:
            who = (user.first_name or "User").strip()
            if user.username:
                who += f" (@{user.username})"

            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "📩 <b>New Support Message</b>\n"
                    f"From: {who}\n"
                    f"TG_ID: <code>{user.id}</code>\n\n"
                    f"<b>Message:</b>\n{msg}\n\n"
                    "Use /admin to view tickets."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Clear support-flow flag
    context.user_data.pop("in_support_flow", None)

    await update.message.reply_text(
        "✅ Your message has been sent to support.\n"
        "You’ll get a reply here as soon as possible.\n\n"
        "Send /start to go back to the main menu."
    )

    return ConversationHandler.END


# -----------------------------
# Fallbacks / Cancel
# -----------------------------
async def support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("in_support_flow", None)
    await update.effective_message.reply_text("✅ Cancelled. Send /start to return to the menu.")
    return ConversationHandler.END


# -----------------------------
# ConversationHandler
# -----------------------------
support_conv = ConversationHandler(
    entry_points=[
        # /support command
        CommandHandler("support", support_start),

        # ✅ ReplyKeyboard button text (make this flexible)
        MessageHandler(filters.Regex(r"^📩\s*Contact Support\b"), support_start),

        # ✅ InlineKeyboard callback (accept multiple possible patterns)
        CallbackQueryHandler(support_start_from_callback, pattern=r"^(support:start|support|contact_support)$"),
    ],
    states={
        SUPPORT_WAITING_MESSAGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive_message),
        ],
    },
    fallbacks=[
        CommandHandler("cancel", support_cancel),
        CommandHandler("start", support_cancel),  # since you told users "/start" cancels
    ],
    allow_reentry=True,
    per_message=False,
    per_chat=True,
    per_user=True,
)
