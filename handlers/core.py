# =============================================================== 
# handlers/core.py
# ================================================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from helpers import md_escape, get_or_create_user, is_admin
from db import get_async_session

# ---------------------------------------------------------
# /start handler (with optional referral arg)
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    async with get_async_session() as session:
        await get_or_create_user(
            session,
            tg_id=user.id,
            username=user.username,
        )
        # TODO: handle referral (your get_or_create_user doesn’t take referred_by currently)

    text = (
        f"👋 Hey *{md_escape(user.first_name)}*\\!\n\n"
        "Welcome to *NaijaPrizeGate* 🎰\n\n"
        "Your golden ticket to daily wins 💸🔥\n\n"
        "You can become the *winner* of an *iPhone 16 Pro Max*\\!\n\n"
        "Here’s how you unlock the gate:\n"
        "✨ `Try Luck` → Spin now & feel the thrill\n"
        "💳 `Buy` → Load up more spins \\(paid tries\\)\n"
        "🎁 `Free` → Earn bonus spins \\(invite & win\\)\n"
        "📊 `/mytries` → See your balance of chances\n\n"
        "⚡ Every spin counts towards the *Jackpot*\n"
        "…and someone *will* take it home 👑\n\n"
        "Ready\\? 🎯 Tap *Try Luck* and let’s roll\\!"
    )

    keyboard = [
        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
        [InlineKeyboardButton("💳 Buy Tries", callback_data="buy")],
        [InlineKeyboardButton("🎁 Free Tries", callback_data="free")]
    ]

    # Handles both /start and greeting triggers (update.message always exists here)
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="MarkdownV2"
    )

# ---------------------------------------------------------
# Register handlers
# ---------------------------------------------------------
from telegram.ext import MessageHandler, filters

def register_handlers(application):
    # /start command
    application.add_handler(CommandHandler("start", start))

    # 👋 greetings like hi, hello, hey
    greetings = filters.Regex(
        r'(?i)^(hi|hello|hey|howdy|sup|good\s?(morning|afternoon|evening))'
    )
    application.add_handler(MessageHandler(greetings, start))
