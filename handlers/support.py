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
# ENTRY: Start Support
# ==============================================================

async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    # mark user as inside conversation
    context.user_data["_in_conversation"] = True

    text_msg = (
        "📩 <b>Contact Support</b>\n\n"
        "✍️ Type your message and send it.\n\n"
        "Send /cancel to stop."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            text_msg,
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            text_msg,
            parse_mode="HTML",
        )

    return SUPPORT_WAITING_MESSAGE


# ==============================================================
# ENTRY: Inline Button
# ==============================================================

async def support_start_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    context.user_data["_in_conversation"] = True

    try:
        await query.edit_message_text(
            "📩 <b>Contact Support</b>\n\n"
            "✍️ Type your message and send it.\n\n"
            "Send /cancel to stop.",
            parse_mode="HTML",
        )
    except Exception:
        await query.message.reply_text(
            "📩 <b>Contact Support</b>\n\n"
            "✍️ Type your message and send it.\n\n"
            "Send /cancel to stop.",
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

        # stay inside conversation
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
                    "📩 <b>New Support Message</b>\n\n"
                    f"👤 {who}\n"
                    f"🆔 TG_ID: <code>{user.id}</code>\n\n"
                    f"<b>Message:</b>\n{msg}\n\n"
                    f"<b>Reply:</b>\n"
                    f"/reply {user.id} your message"
                ),
                parse_mode="HTML",
            )

        except Exception:
            pass


    # ==========================================================
    # Confirmation to User
    # ==========================================================

    await update.message.reply_text(
        "✅ Your message has been sent.\n"
        "Support will reply here shortly.\n\n"
        "Send /start to return to menu."
    )

    # ==========================================================
    # Exit Conversation Properly
    # ==========================================================

    context.user_data.pop("_in_conversation", None)

    return ConversationHandler.END

# ==============================================================
# ADMIN REPLY COMMAND
# ==============================================================

async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if user.id not in ADMIN_IDS:
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage:\n/reply user_id message"
        )
        return

    try:

        target_user = int(context.args[0])
        message = " ".join(context.args[1:])

        await context.bot.send_message(
            chat_id=target_user,
            text=f"🧑‍💻 <b>Support Reply</b>\n\n{message}",
            parse_mode="HTML",
        )

        await update.message.reply_text("✅ Reply sent.")

    except Exception:

        await update.message.reply_text(
            "❌ Failed to send reply."
        )


# ==============================================================
# CANCEL
# ==============================================================

async def support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["_in_conversation"] = False

    await update.effective_message.reply_text(
        "❌ Support request cancelled.\nSend /start to return to menu."
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
    per_user=True,
    per_chat=True,
)
