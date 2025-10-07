# =============================================================== 
# handlers/core.py
# ================================================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from helpers import md_escape, get_or_create_user, is_admin
from db import get_async_session
import re
import logging

logger = logging.getLogger(__name__)

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
# /help handler
# ---------------------------------------------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🆘 *Need a quick tour?*\n\n"
        "*NaijaPrizeGate* 🎰 is your gateway to *daily wins* 💸\n\n"
        "Here’s your control panel:\n\n"
        "• `/start` → Begin or refresh menu\n"
        "• ✨ *Try Luck* → Spin the wheel, feel the thrill\n"
        "• 💳 *Buy* → Load up paid spins & chase the jackpot\n"
        "• 🎁 *Free* → Earn bonus spins \\(invite friends \\= more chances\\)\n"
        "• 📊 `/mytries` → Track your spin balance\n"
        "• 🏆 *Jackpot* → Every spin moves us closer to the big win 🔥\n\n"
        "👉 Don’t just stand at the gate… *spin your way through* 🚀\n"
        "Hit it and be the next winner 🎉"
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2")

# ---------------------------------------------------------
# /mytries handler
# ---------------------------------------------------------
async def mytries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"🔔 /mytries called by tg_id={user.id}, username={user.username}")

    async with get_async_session() as session:
        db_user = await get_or_create_user(session, user.id, user.username)

        # Log what we actually fetched from DB
        logger.info(
            f"📊 User {db_user.id} (tg_id={db_user.tg_id}) has "
            f"paid={db_user.tries_paid}, bonus={db_user.tries_bonus}"
        )

        text = (
            f"🧮 *Your Tries*\n\n"
            f"• Paid: `{db_user.tries_paid or 0}`\n"
            f"• Free: `{db_user.tries_bonus or 0}`"
        )

    # Escape just in case user fields cause Markdown issues
    await update.message.reply_text(md_escape(text), parse_mode="MarkdownV2")

# ---------------------------------------------------------
# Fallback text handler
# ---------------------------------------------------------
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤔 Sorry, I didn’t understand that\n"
        "Use the menu buttons or try /help"
    )
    safe_text = md_escape(text)

    keyboard = [
        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
        [InlineKeyboardButton("💳 Buy Tries", callback_data="buy")],
        [InlineKeyboardButton("🎁 Free Tries", callback_data="free")],
    ]

    if update.message:  # User typed something
        await update.message.reply_text(
            safe_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2",
        )
    elif update.callback_query:  # User pressed an inline button
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            safe_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2",
        )

# ---------------------------------------------------------
# Register handlers (unified)
# ---------------------------------------------------------
def register_handlers(application):
    # /start command
    application.add_handler(CommandHandler("start", start))

    # 👋 greetings like hi, hello, hey
    greetings = filters.Regex(re.compile(
        r"^(hi|hello|hey|howdy|sup|good\s?(morning|afternoon|evening))",
        re.IGNORECASE
    ))
    application.add_handler(MessageHandler(greetings, start))

    # core commands
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("mytries", mytries))

    # fallback for unrecognized text
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))

