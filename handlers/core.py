# ==============================================================
# handlers/core.py — Compliance-Safe Version (Updated)
# ===============================================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from helpers import md_escape, get_or_create_user
from db import get_async_session
from utils.security import validate_phone, detect_provider
import re
import logging

logger = logging.getLogger(__name__)

# ===============================================================
# 📘 /terms COMMAND HANDLER — ADDED
# ===============================================================
async def terms_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📘 <b>Fair Play & Terms</b>\n\n"
        "✔ NaijaPrizeGate is a <b>knowledge-based trivia competition</b>\n"
        "✔ Performance on the <b>leaderboard</b> determines rewards\n"
        "✔ <b>100% Skill-Based</b> — no randomness in determining winners — outcomes are not based on chance\n"
        "✔ Players earn quiz points by <b>answering questions</b>\n"
        "✔ Paid questions help support the contest operations\n"
        "✔ A prize unlocks when the cycle’s participation milestone is reached\n"
        "✔ Winners must provide accurate delivery details\n"
        "✔ Fraud or cheating will result in disqualification\n\n"
        "📌 By continuing to use this bot, you agree to the rules above.\n\n"
        "➡️ Use /start to return to the main menu.\n\n"
        "🛑 Disclaimer\n"
        "Brand names or product names shown as prizes (e.g., iPhone, Samsung Galaxy)\n"
        "are used *only to describe rewards available to top performers*.\n"
        "Apple Inc. and Samsung Electronics are **not sponsors, partners or affiliated\n"
        "with this competition in any way."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")


# ===============================================================
# ❓ FAQ HANDLER — ADDED
# ===============================================================
async def faq_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ <b>FAQs — Quick Answers</b>\n\n"
        "• <b>How do I win?</b>\n"
        "  → Score high on the leaderboard through quiz performance.\n\n"
        "• <b>Is this gambling?</b>\n"
        "  → No. All rewards are based on skill and knowledge.\n\n"
        "• <b>Are there free questions?</b>\n"
        "  → Yes! Earn free questions from the menu.\n\n"
        "• <b>What do I gain from answering questions?</b>\n"
        "  → Quiz points boost your rank and unlock rewards.\n\n"
        "• <b>What if I run out of questions?</b>\n"
        "  → You can earn or buy more through the menu.\n\n"
        "➡️ Use /start to return to the main menu"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")


# ===============================================================
# 📱 PHONE CAPTURE FOR AIRTIME REWARDS
# ===============================================================
async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Ask the user for their Nigerian phone number when an airtime
    reward is available but no phone is on file.
    """
    target = update.message or update.callback_query

    if isinstance(target, type(update.callback_query)):
        # If called from a callback query, reply in chat
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            "📱 To receive your airtime reward, please send your *11-digit Nigerian phone number*.\n"
            "Example: 08123456789",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "📱 To receive your airtime reward, please send your *11-digit Nigerian phone number*.\n"
            "Example: 08123456789",
            parse_mode="Markdown"
        )

    context.user_data["awaiting_phone"] = True


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle phone number input when the user is being asked
    to provide a line for airtime credit.
    """
    # Only act if we are expecting a phone number
    if not context.user_data.get("awaiting_phone"):
        return

    phone = (update.message.text or "").strip()

    if not validate_phone(phone):
        await update.message.reply_text(
            "⚠️ Invalid number format.\n"
            "Please enter a valid Nigerian phone number e.g.\n*08123456789*",
            parse_mode="Markdown"
        )
        return

    provider = detect_provider(phone)
    provider_txt = provider or "Your Network"

    # Save phone number to DB
    async with get_async_session() as session:
        tg_user = update.effective_user
        db_user = await get_or_create_user(
            session,
            tg_id=tg_user.id,
            username=tg_user.username,
        )
        db_user.phone_number = phone
        await session.commit()

    context.user_data["awaiting_phone"] = False

    await update.message.reply_text(
        f"🎉 Great! {provider_txt} line saved successfully!\n"
        "🔁 Reprocessing your airtime reward now…",
        parse_mode="Markdown"
    )

    # Trigger reward logic retry (lazy import to avoid circular dependency)
    try:
        from handlers.playtrivia import retry_last_reward
        await retry_last_reward(update, context)
    except Exception as e:
        logger.error(f"❌ Failed to retry reward after phone capture: {e}")
        await update.message.reply_text(
            "⚠️ Something went wrong while reprocessing your reward.\n"
            "But your phone number has been saved. Please try again.",
            parse_mode="Markdown"
        )


# ===============================================================
# /start (with optional referral)
# ===============================================================
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
        "📘 Tap *Fair Play Rules* below for policy & transparency\n\n"
        "Ready to begin?\n"
        "Tap *Play Trivia* below 👇"
    )

    keyboard = [
        [InlineKeyboardButton("🧠 Play Trivia Questions", callback_data="playtrivia")],
        [InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy")],
        [InlineKeyboardButton("🎁 Earn Free Trivia Attempts", callback_data="free")],
        [InlineKeyboardButton("📊 My Available Trivia Attempts", callback_data="show_tries")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")],
        [InlineKeyboardButton("📘 Fair Play Rules", callback_data="terms")],  # NEW
        [InlineKeyboardButton("❓ FAQs", callback_data="faq")]                # NEW
    ]

    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )


# ===============================================================
# GO BACK (from cancel or menu)
# ===============================================================
async def go_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await start(update, context)


# ===============================================================
# /help — Skill-based focus (unchanged)
# ===============================================================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🆘 *How to Play*\n\n"
        "1️⃣ Select a trivia category\n"
        "2️⃣ Answer questions correctly to earn reward points\n"
        "3️⃣ Score higher to rise on the leaderboard\n"
        "4️⃣ Top performers unlock special rewards 🎁\n\n"
        "🎯 Knowledge decides your success — not luck\n"
        "🔒 Completely safe and skill-based\n\n"
        "Use the buttons below to continue 👇"
    )

    keyboard = [
        [InlineKeyboardButton("🧠 Play Trivia Questions", callback_data="playtrivia")],
        [InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy")],
        [InlineKeyboardButton("🎁 Earn Free Trivia Attempts", callback_data="free")],
        [InlineKeyboardButton("📊 My Available Trivia Attempts", callback_data="show_tries")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")],
        [InlineKeyboardButton("📘 Fair Play Rules", callback_data="terms")],  # NEW
        [InlineKeyboardButton("❓ FAQs", callback_data="faq")]                # NEW
    ]

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="MarkdownV2"
    )


# ===============================================================
# /mytries — unchanged
# ===============================================================
async def mytries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    async with get_async_session() as session:
        db_user = await get_or_create_user(session, tg_id=tg_user.id, username=tg_user.username)

        text = (
            f"📊 *Your Question Credits*\n\n"
            f"• Paid: `{db_user.tries_paid or 0}`\n"
            f"• Free: `{db_user.tries_bonus or 0}`\n\n"
            "_Questions = Chances to earn more reward points_ 🎯"
        )

    await update.message.reply_text(md_escape(text), parse_mode="MarkdownV2")


# ===============================================================
# Fallback — unchanged (still skips numeric-only messages)
# ===============================================================
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    safe_text = md_escape(
        "🤔 Sorry, I didn’t understand that.\n\n"
        "Use /start or tap a menu button ↓"
    )
    keyboard = [
        [InlineKeyboardButton("🧠 Play Trivia Questions", callback_data="playtrivia")],
        [InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy")],
        [InlineKeyboardButton("🎁 Earn Free Questions", callback_data="free")],
        [InlineKeyboardButton("📊 My Available Trivia Attempts", callback_data="show_tries")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")],
        [InlineKeyboardButton("📘 Fair Play Rules", callback_data="terms")],  # NEW
        [InlineKeyboardButton("❓ FAQs", callback_data="faq")]                # NEW
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


# ===============================================================
# Register Handlers
# ===============================================================
def register_handlers(application):

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("mytries", mytries))
    application.add_handler(CommandHandler("terms", terms_handler))  # NEW
    application.add_handler(CommandHandler("faq", faq_handler))      # NEW

    # Phone capture (numeric-ish text, e.g. 08123456789 or +234...)
    application.add_handler(
        MessageHandler(
            filters.Regex(r"^[0-9+ ]+$"),
            handle_phone
        )
    )

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone))

    # Callback menu buttons
    application.add_handler(CallbackQueryHandler(terms_handler, pattern="^terms$"))  # NEW
    application.add_handler(CallbackQueryHandler(faq_handler, pattern="^faq$"))      # NEW

    # Friendly greeting triggers
    greetings = filters.Regex(re.compile(
        r"^(hi|hello|hey|howdy|sup|good\s?(morning|afternoon|evening))",
        re.IGNORECASE
    ))
    application.add_handler(MessageHandler(greetings, start))

    # Leaderboard routing
    from handlers.leaderboard import register_leaderboard_handlers
    register_leaderboard_handlers(application)

    # Fallback (non-command, non-numeric text)
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^[0-9+ ]+$"),
            fallback
        )
    )
