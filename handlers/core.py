# ==============================================================
# handlers/core.py — Compliance-Safe Version (Updated)
# ===============================================================
import re
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from sqlalchemy import text

from helpers import md_escape, get_or_create_user
from db import get_async_session
from utils.security import validate_phone, detect_provider

logger = logging.getLogger(__name__)

# ===============================================================
# 📘 /terms COMMAND HANDLER — ADDED
# ===============================================================
async def terms_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📘 <b>Fair Play & Terms</b>\n\n"

        "NaijaPrizeGate is a <b>skill-influenced trivia competition</b>.\n\n"

        "✔ Rewards are determined by <b>trivia performance and Premium Points</b>\n"
        "✔ Correct answers earn <b>Premium Spins</b> and Premium Points\n"
        "✔ Incorrect answers earn <b>Standard Spins</b>\n"
        "✔ Premium Points accumulate across plays and determine leaderboard ranking\n"
        "✔ The <b>highest Premium Points holder</b> at the end of a game cycle wins the jackpot prize\n\n"

        "⚖️ <b>Fair Play Rules</b>\n"
        "✔ Trivia questions are randomly selected from predefined categories\n"
        "✔ Answers are validated server-side\n"
        "✔ Users cannot influence question selection, spins, or point calculations\n"
        "✔ Any form of cheating, automation, or abuse leads to disqualification\n\n"

        "💳 <b>Payments & Participation</b>\n"
        "✔ Each trivia attempt requires a paid chance\n"
        "✔ Paid participation supports contest operations\n"
        "✔ Chances are non-refundable once a question is served\n\n"

        "🎁 <b>Rewards & Fulfillment</b>\n"
        "✔ Rewards are <b>not guaranteed</b> on every spin\n"
        "✔ Airtime and data rewards are processed after validation\n"
        "✔ Physical prizes require accurate contact and delivery details\n\n"

        "📜 By continuing to use this bot, you agree to the full "
        "<b>Terms & Conditions</b> governing participation.\n\n"

        "➡️ Use /start to return to the main menu.\n\n"

        "🛑 <b>Disclaimer</b>\n"
        "Brand or product names shown as prizes (e.g. iPhone, Samsung Galaxy)\n"
        "are used <b>only to describe rewards available to top performers</b>.\n"
        "Apple Inc. and Samsung Electronics are <b>not sponsors, partners, or affiliated</b>\n"
        "with NaijaPrizeGate in any way."
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
        "📘 Tap *Terms & Fair Play* below for policy & transparency\n\n"
        "📜 By using NaijaPrizeGate, you agree to our Terms & Conditions and Fair Play Rules\n\n"
        "Ready to begin?\n"
        "Tap *Play Trivia* below 👇"
    )

    keyboard = [
        [InlineKeyboardButton("🧠 Play Trivia Questions", callback_data="playtrivia")],
        [InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy")],
        [InlineKeyboardButton("🎁 Earn Free Trivia Attempts", callback_data="free")],
        [InlineKeyboardButton("📊 My Available Trivia Attempts", callback_data="show_tries")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")],
        [InlineKeyboardButton("📘 Terms & Fair Play", callback_data="terms")],  # NEW
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
        [InlineKeyboardButton("📘 Terms & Fair Play", callback_data="terms")],  # NEW
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
    # ✅ Don't interrupt airtime claim flow
    if context.user_data.get("awaiting_airtime_phone"):
        return

    safe_text = md_escape(
        "🤔 Sorry, I didn’t understand that.\n\n"
        "Use /start or tap a menu button ↓"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Play Trivia Questions", callback_data="playtrivia")],
        [InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy")],
        [InlineKeyboardButton("🎁 Earn Free Questions", callback_data="free")],
        [InlineKeyboardButton("📊 My Available Trivia Attempts", callback_data="show_tries")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")],
        [InlineKeyboardButton("📘 Terms & Fair Play", callback_data="terms")],
        [InlineKeyboardButton("❓ FAQs", callback_data="faq")],
    ])

    # ✅ Reply safely based on update type
    if update.message:
        await update.message.reply_text(
            safe_text,
            reply_markup=keyboard,
            parse_mode="MarkdownV2",
        )
        return

    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

        try:
            await update.callback_query.edit_message_text(
                safe_text,
                reply_markup=keyboard,
                parse_mode="MarkdownV2",
            )
        except Exception:
            # If edit fails (e.g., message not editable), fall back to sending a new message
            await update.callback_query.message.reply_text(
                safe_text,
                reply_markup=keyboard,
                parse_mode="MarkdownV2",
            )
        return

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
