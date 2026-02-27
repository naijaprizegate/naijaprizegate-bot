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

from db import AsyncSessionLocal  # ✅ adjust import if needed

SUPPORT_WAITING_MESSAGE = 1


# ✅ Put admin IDs in env: ADMIN_IDS="123,456"
def _get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


ADMIN_IDS = _get_admin_ids()


# ----------------------------
# Internal helper (single UI)
# ----------------------------
async def _enter_support_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ✅ mark that user is in support flow so fallback must not interrupt
    context.user_data["in_support_flow"] = True

    msg = (
        "📩 <b>Contact Support</b>\n\n"
        "Type your message here and send it.\n\n"
        "To cancel and return to menu, send /cancel (or /start)."
    )

    if update.callback_query:
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            pass
        await query.message.reply_text(msg, parse_mode="HTML")
    else:
        await update.effective_message.reply_text(msg, parse_mode="HTML")

    return SUPPORT_WAITING_MESSAGE


# ----------------------------
# Entry points
# ----------------------------
async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _enter_support_prompt(update, context)


async def support_start_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _enter_support_prompt(update, context)


# ----------------------------
# Receive the support message
# ----------------------------
async def support_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Must be a normal text message
    if not update.message or not update.message.text:
        return SUPPORT_WAITING_MESSAGE

    msg = (update.message.text or "").strip()
    if not msg:
        await update.message.reply_text("⚠️ Please type your message (it cannot be empty).")
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

    # ✅ clear support-flow flag so fallback can work normally again
    context.user_data.pop("in_support_flow", None)

    await update.message.reply_text(
        "✅ Your message has been sent to support.\n"
        "You’ll get a reply here as soon as possible.\n\n"
        "Send /start to return to the main menu."
    )
    return ConversationHandler.END


# ----------------------------
# Cancel support
# ----------------------------
async def support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("in_support_flow", None)
    await update.effective_message.reply_text("✅ Cancelled. Send /start to return to the menu.")
    return ConversationHandler.END


# ----------------------------
# Support ConversationHandler
# ----------------------------
support_conv = ConversationHandler(
    entry_points=[
        CommandHandler("support", support_start),

        # ✅ If you have a ReplyKeyboard button that sends this exact text
        MessageHandler(filters.Regex(r"^📩 Contact Support / Admin$"), support_start),

        # ✅ InlineKeyboard callback button
        CallbackQueryHandler(support_start_from_callback, pattern=r"^support:start$"),
    ],
    states={
        SUPPORT_WAITING_MESSAGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive_message),
        ],
    },
    fallbacks=[
        CommandHandler("cancel", support_cancel),
        CommandHandler("start", support_cancel),  # ✅ IMPORTANT because you told them “use /start to go back”
    ],
    allow_reentry=True,
    per_message=False,
    per_chat=True,
    per_user=True,
)
