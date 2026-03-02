# ==============================================================
# handlers/support.py
# ==============================================================
import os
import re
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

# ---- Conversation state ----
SUPPORT_WAITING_MESSAGE = 1

# ---- Admin IDs helper ----
def _get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")  # e.g. "12345,67890"
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids

ADMIN_IDS = _get_admin_ids()


# -----------------------------
# Safe support flow flag helpers
# -----------------------------
def _support_on(context: ContextTypes.DEFAULT_TYPE) -> None:
    # ✅ NEVER do: context.user_data = {}
    context.user_data["in_support_flow"] = True


def _support_off(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("in_support_flow", None)


# =========================================
# Support Start (via /support or text menu)
# =========================================
async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _support_on(context)

    await update.effective_message.reply_text(
        "📩 <b>Contact Support</b>\n\n"
        "✍️ Type your message here and send it.\n\n"
        "To cancel, send /cancel (or /start to return to the menu).",
        parse_mode="HTML",
    )
    return SUPPORT_WAITING_MESSAGE


# =========================================
# Support Start (via inline button callback)
# =========================================
async def support_start_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    try:
        await query.answer()
    except Exception:
        pass

    _support_on(context)

    # Use reply_text to avoid "message is not modified" / edit conflicts
    await query.message.reply_text(
        "📩 <b>Contact Support</b>\n\n"
        "✍️ Type your message here and send it.\n\n"
        "To cancel, send /cancel (or /start to return to the menu).",
        parse_mode="HTML",
    )
    return SUPPORT_WAITING_MESSAGE


# ----------------------------------------
# Support Receive Message (the typed text)
# ----------------------------------------
async def support_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return SUPPORT_WAITING_MESSAGE

    msg = (update.message.text or "").strip()

    # ✅ If they typed /start inside support, treat as exit
    if msg.startswith("/start"):
        context.user_data["_handled_by_support"] = True
        _support_off(context)
        return ConversationHandler.END

    if not msg:
        await update.message.reply_text(
            "⚠️ Message cannot be empty. Please type your message:"
        )
        return SUPPORT_WAITING_MESSAGE

    if len(msg) < 2:
        await update.message.reply_text(
            "⚠️ Please type a clearer message:"
        )
        return SUPPORT_WAITING_MESSAGE

    user = update.effective_user

    # ✅ Mark this update as handled by support
    # This prevents fallback from replying after conversation ends
    context.user_data["_handled_by_support"] = True

    # ✅ Turn off support mode BEFORE ending conversation
    _support_off(context)

    # ✅ Save to DB
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO support_tickets (tg_id, username, first_name, message, status, created_at)
                    VALUES (:tg_id, :username, :first_name, :message, 'pending', NOW())
                """),
                {
                    "tg_id": int(user.id),
                    "username": (user.username or None),
                    "first_name": (user.first_name or None),
                    "message": msg,
                },
            )
            await session.commit()

    except Exception:
        # If DB fails, restore support mode so user can retry
        _support_on(context)

        # Remove handled flag so fallback can work again
        context.user_data.pop("_handled_by_support", None)

        await update.message.reply_text(
            "❌ Sorry—support could not receive your message right now.\n"
            "Please try again in a minute, or send /cancel."
        )
        return SUPPORT_WAITING_MESSAGE

    # ✅ Notify admins (best effort)
    who = (user.first_name or "User")
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
                    f"<b>Message:</b>\n{msg}\n\n"
                    "Open /admin → Support Inbox to reply."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await update.message.reply_text(
        "✅ Your message has been sent to support.\n"
        "You’ll get a reply here as soon as possible.\n\n"
        "Send /start to return to the main menu."
    )

    return ConversationHandler.END

# ----------------------------------------
# Support Cancel
# ----------------------------------------
async def support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _support_off(context)
    await update.effective_message.reply_text("✅ Cancelled. Send /start to return to menu.")
    return ConversationHandler.END


# ✅ Real ConversationHandler
support_conv = ConversationHandler(
    entry_points=[
        CommandHandler("support", support_start),

        # ReplyKeyboard text button (if you have it)
        MessageHandler(filters.Regex(r"^📩 Contact Support / Admin$"), support_start),

        # InlineKeyboard callback button
        CallbackQueryHandler(support_start_from_callback, pattern=r"^support:start$"),
    ],
    states={
        SUPPORT_WAITING_MESSAGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive_message),
        ],
    },
    fallbacks=[
        CommandHandler("cancel", support_cancel),
        CommandHandler("start", support_cancel),  # ⭐ makes /start cleanly exit support mode
    ],
    allow_reentry=True,
    per_message=False,
    per_chat=True,
    per_user=True,
    block=True,
)
