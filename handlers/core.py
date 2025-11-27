# =============================================================== 
# handlers/core.py — Compliance-Safe Version
# ================================================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from helpers import md_escape, get_or_create_user, is_admin
from db import get_async_session
import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# /start (with optional referral)
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    async with get_async_session() as session:
        await get_or_create_user(
            session,
            tg_id=user.id,
            username=user.username,
        )

    text = (
        f"👋 Hello *{md_escape(user.first_name)}*\\!\n\n"
        "🎉 Welcome to *NaijaPrizeGate* — The Nigerian Trivia Challenge 🇳🇬\n\n"
        "🧠 Answer fun questions\n"
        "🎯 Earn reward points\n"
        "🏆 Climb the leaderboard\n"
        "🎁 Unlock weekly reward opportunities\n\n"
        "✨ It’s all about *knowledge and performance* — not luck 🔥\n\n"
        "🔒 100% Free to start\n"
        "📊 Rewards are based on leaderboard ranking\n"
        "📘 See /terms for policy & fair play rules\n\n"
        "Ready to begin?\n"
        "Tap *Play Trivia* below 👇"
    )

    keyboard = [
        [InlineKeyboardButton("🧠 Play Trivia Questions", callback_data="tryluck")],
        [InlineKeyboardButton("💳 Get More Questions", callback_data="buy")],
        [InlineKeyboardButton("🎁 Earn Free Questions", callback_data="free")],
        [InlineKeyboardButton("📊 My Available Questions", callback_data="show_tries")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")]
    ]

    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )

# ---------------------------------------------------------
# Callback: Return to Start (from Cancel)
# ---------------------------------------------------------
async def go_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(f"⚠️ Could not delete message: {e}")

    await start(update, context)

# ---------------------------------------------------------
# /help — Updated for skill-based focus
# ---------------------------------------------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "🆘 *How to Play*\n\n"
        "1️⃣ Select a trivia category\n"
        "2️⃣ Answer questions correctly to earn reward points\n"
        "3️⃣ Score higher to rise on the leaderboard\n"
        "4️⃣ Top performers each week unlock special rewards 🎁\n\n"
        "🎯 Knowledge decides your success — not luck\n"
        "💳 You may get extra trivia questions through the menu\n"
        "🔒 Completely safe and skill-based\n\n"
        "Use the buttons below to continue 👇"
    )

    keyboard = [
        [InlineKeyboardButton("🧠 Play Trivia Questions", callback_data="tryluck")],
        [InlineKeyboardButton("💳 Get More Questions", callback_data="buy")],
        [InlineKeyboardButton("🎁 Earn Free Questions", callback_data="free")],
        [InlineKeyboardButton("📊 My Available Questions", callback_data="show_tries")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        try:
            await query.message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode="MarkdownV2"
            )
        except Exception:
            await query.message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode="MarkdownV2"
            )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode="MarkdownV2"
        )

# ---------------------------------------------------------
# /mytries — now called "My Questions"
# ---------------------------------------------------------
async def mytries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    logger.info(f"🧮 /mytries called by tg_id={tg_user.id}")

    async with get_async_session() as session:
        db_user = await get_or_create_user(session, tg_id=tg_user.id, username=tg_user.username)

        text = (
            f"📊 *Your Question Credits*\n\n"
            f"• Paid: `{db_user.tries_paid or 0}`\n"
            f"• Free: `{db_user.tries_bonus or 0}`\n\n"
            "_Questions = Chances to earn more reward points_ 🎯"
        )

    await update.message.reply_text(md_escape(text), parse_mode="MarkdownV2")

# ---------------------------------------------------------
# Fallback — unchanged but renamed terms
# ---------------------------------------------------------
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤔 Sorry, I didn’t understand that.\n\n"
        "Use /start or tap a menu button ↓"
    )
    safe_text = md_escape(text)

    keyboard = [
        [InlineKeyboardButton("🧠 Play Trivia Questions", callback_data="tryluck")],
        [InlineKeyboardButton("💳 Get More Questions", callback_data="buy")],
        [InlineKeyboardButton("🎁 Earn Free Questions", callback_data="free")],
        [InlineKeyboardButton("📊 My Available Questions", callback_data="show_tries")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")]
    ]

    if update.message:
        await update.message.reply_text(
            safe_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            safe_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )

# ---------------------------------------------------------
# Register handlers
# ---------------------------------------------------------
def register_handlers(application):
    application.add_handler(CommandHandler("start", start))

    greetings = filters.Regex(re.compile(
        r"^(hi|hello|hey|howdy|sup|good\s?(morning|afternoon|evening))",
        re.IGNORECASE
    ))
    application.add_handler(MessageHandler(greetings, start))

    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("mytries", mytries))

    from handlers.leaderboard import register_leaderboard_handlers
    register_leaderboard_handlers(application)

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^[0-9+ ]+$"),
            fallback
        )
    )
