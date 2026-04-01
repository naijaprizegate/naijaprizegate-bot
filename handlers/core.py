# ===============================================================
# handlers/core.py — Compliance-Safe Version (Polished)
# ===============================================================
import re
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)

from helpers import md_escape, get_or_create_user
from db import get_async_session
from handlers.challenge import join_challenge

logger = logging.getLogger(__name__)


# ===============================================================
# 📘 /terms COMMAND HANDLER
# ===============================================================
async def terms_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📘 <b>Fair Play & Terms</b>\n\n"
        "NaijaPrizeGate is a <b>skill-influenced trivia competition</b>.\n\n"
        "✔ Rewards are determined by <b>trivia performance and Premium Points</b>\n"
        "✔ Correct answers earn <b>Premium Points</b>\n"
        "✔ Premium Points accumulate across plays and determine leaderboard ranking\n"
        "✔ The <b>highest Premium Points holder</b> at the end of a game cycle wins the jackpot prize\n\n"
        "⚖️ <b>Fair Play Rules</b>\n"
        "✔ Trivia questions are randomly selected from predefined categories\n"
        "✔ Answers are validated server-side\n"
        "✔ Users cannot influence question selection or point calculations\n"
        "✔ Any form of cheating, automation, or abuse leads to disqualification\n\n"
        "💳 <b>Payments & Participation</b>\n"
        "✔ Each trivia attempt requires a paid chance\n"
        "✔ Paid participation supports contest operations\n"
        "✔ Chances are non-refundable once a question is served\n\n"
        "🎁 <b>Rewards & Fulfillment</b>\n"
        "✔ Rewards are <b>not guaranteed</b> on every Trivia attempt\n"
        "✔ Airtime and data rewards are processed after validation\n"
        "✔ Physical prizes require accurate contact and delivery details\n\n"
        "📜 By continuing to use this bot, you agree to the full "
        "<b>Terms & Conditions</b> governing participation.\n\n"
        "🛑 <b>Disclaimer</b>\n"
        "Brand or product names shown as prizes (e.g. iPhone, Samsung Galaxy)\n"
        "are used <b>only to describe rewards available to top performers</b>.\n"
        "Apple Inc. and Samsung Electronics are <b>not sponsors, partners, or affiliated</b>\n"
        "with NaijaPrizeGate in any way."
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Other Menu", callback_data="menu:other")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:main")],
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )


# ===============================================================
# ❓ FAQ HANDLER
# ===============================================================
async def faq_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ <b>FAQs — Quick Answers</b>\n\n"
        "• <b>How do I win?</b>\n"
        "  → Score high on the leaderboard through quiz performance.\n\n"
        "• <b>Is this gambling?</b>\n"
        "  → No. All rewards are based on skill and knowledge.\n\n"
        "• <b>Are there free questions?</b>\n"
        "  → Yes! Earn free questions from the menu. Invite Friends. Follow us on social media platforms.\n\n"
        "• <b>What do I gain from answering questions?</b>\n"
        "  → Quiz points boost your rank and unlock rewards.\n\n"
        "• <b>What if I run out of questions?</b>\n"
        "  → You can earn or buy more through the menu."
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Other Menu", callback_data="menu:other")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:main")],
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )


# ===============================================================
# MAIN MENU / OTHER MENU
# ===============================================================
def build_main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧠 Play Trivia Questions (Win iPhone 17 Pro Max)", callback_data="playtrivia")],
            [InlineKeyboardButton("⚔️ Challenge Friends (Free)", callback_data="challenge:start")],
            [InlineKeyboardButton("🔥 Battle Mode (Free)", callback_data="battle:menu")],
            [InlineKeyboardButton("🎓 JAMB / WAEC / NECO Practice", callback_data="exam:hub")],
            [InlineKeyboardButton("📂 Other Menu", callback_data="menu:other")],
        ]
    )



def build_other_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy")],
            [InlineKeyboardButton("🎁 Earn Free Trivia Attempts", callback_data="free")],
            [InlineKeyboardButton("📊 My Available Trivia Attempts", callback_data="show_tries")],
            [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")],
            [InlineKeyboardButton("📘 Terms & Fair Play", callback_data="terms")],
            [InlineKeyboardButton("❓ FAQs", callback_data="faq")],
            [InlineKeyboardButton("📩 Contact Support / Admin", callback_data="support:start")],
            [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="menu:main")],
        ]
    )


def build_exam_hub_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎓 JAMB Practice", callback_data="jambpractice")],
            [InlineKeyboardButton("📝 Mock JAMB / UTME", callback_data="mock:jamb")],
            [InlineKeyboardButton("📘 WAEC / NECO Practice", callback_data="waecneco:practice")],
            [InlineKeyboardButton("🧪 Mock WAEC / NECO Exams", callback_data="mock:waecneco")],
            [InlineKeyboardButton("📚 Tutorials", callback_data="tutorials")],
            [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="menu:main")],
        ]
    )


def build_start_text(user_first_name: str) -> str:
    return (
        f"👋 Hello *{md_escape(user_first_name)}*\\!\n\n"
        "🎉 Welcome to *NaijaPrizeGate* — The Nigerian Trivia Challenge 🇳🇬\n\n"
        "🧠 Answer fun questions \\- Test your knowledge\n"
        "🎯 Earn reward points\n"
        "🏆 Climb the leaderboard\n\n"
        "🎁 Top player this cycle can win:\n\n"
        "📱 *iPhone 17 Pro Max*\n"
        "📱 *Samsung Galaxy S26 Ultra*\n"
        "📱 *Samsung Z Flip 6*\n"
        "🎧 *AirPods*\n"
        "🔊 *Bluetooth Speakers*\n\n"
        "Plus instant rewards like 📞 *Airtime* for premium points milestones\\!\n\n"
        "📘 Tap *Terms & Fair Play* below for policy & transparency\n\n"
        "📜 By using NaijaPrizeGate, you agree to our Terms & Conditions and Fair Play Rules\n\n"
        "Ready to begin?\n"
        "Tap *Play Trivia Questions* below 👇"
    )


def build_exam_hub_text() -> str:
    return (
        "🎓 *Welcome to Exam Practice Hub*\n\n"
        "Prepare for JAMB, WAEC, and NECO with practice questions, mock exams, and tutorials.\n\n"
        "Choose how you want to begin:"
    )



def build_other_menu_text() -> str:
    return (
        "📂 *Other Menu*\n\n"
        "Choose any of the options below:"
    )


# ----------------------------------------
# Exam Hub Handler
# ---------------------------------------
async def exam_hub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    text = build_exam_hub_text()
    markup = build_exam_hub_keyboard()

    try:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )
    except Exception:
        await query.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )


# ===============================================================
# OTHER MENU CALLBACK
# ===============================================================
async def other_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    text = build_other_menu_text()
    markup = build_other_menu_keyboard()

    try:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )
    except Exception:
        await query.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )


# ===============================================================
# /start (with optional referral / deep links)
# ===============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    joined = await join_challenge(update, context)
    if joined:
        return

    user = update.effective_user

    async with get_async_session() as session:
        await get_or_create_user(
            session,
            tg_id=user.id,
            username=user.username,
        )

    # ===========================================================
    # DEEP LINK HANDLERS
    # ===========================================================
    if context.args:
        arg = context.args[0].strip()

        # -------------------------------------------------------
        # BATTLE LINK HANDLER
        # Example: /start battle_A7K92Q
        # -------------------------------------------------------
        if arg.startswith("battle_"):
            room_code = arg.replace("battle_", "", 1).strip()
            from handlers.battle import battle_join_from_payload
            await battle_join_from_payload(update, context, room_code)
            return

        # -------------------------------------------------------
        # PAYMENT SUCCESS DEEP LINKS
        # -------------------------------------------------------
        if arg.startswith("payok_trivia_"):
            if update.message:
                await update.message.reply_text(
                    "✅ *Payment confirmed!*\n\nOpening *Play Trivia* now...",
                    parse_mode="Markdown",
                )

            from handlers.playtrivia import playtrivia_handler
            await playtrivia_handler(update, context)
            return

        if arg.startswith("payok_jamb_"):
            if update.message:
                await update.message.reply_text(
                    "✅ *Payment confirmed!*\n\nOpening *JAMB Practice* now...",
                    parse_mode="Markdown",
                )

            from handlers.jambpractice import jambpractice_handler
            await jambpractice_handler(update, context)
            return

        if arg.startswith("payok_mockjamb_"):
            if update.message:
                await update.message.reply_text(
                    "✅ *Payment confirmed!*\n\nOpening *Mock JAMB / UTME* now...",
                    parse_mode="Markdown",
                )

            from handlers.mockjamb import mockjamb_start_handler
            await mockjamb_start_handler(update, context)
            return
        
        # -------------------------------------------------------
        # PAYMENT FAILED DEEP LINKS
        # -------------------------------------------------------
        if arg.startswith("payfail_trivia_"):
            if update.message:
                await update.message.reply_text(
                    "❌ *Trivia payment was not completed.*\n\nPlease try again.",
                    parse_mode="Markdown",
                )

            from handlers.payments import buy_menu
            await buy_menu(update, context)
            return

        if arg.startswith("payfail_jamb_"):
            if update.message:
                await update.message.reply_text(
                    "❌ *JAMB payment was not completed.*\n\nPlease try again.",
                    parse_mode="Markdown",
                )

            from handlers.jambpractice import jambpractice_handler
            await jambpractice_handler(update, context)
            return

        if arg.startswith("payfail_mockjamb_"):
            if update.message:
                await update.message.reply_text(
                    "❌ *Mock JAMB / UTME payment was not completed.*\n\nPlease try again.",
                    parse_mode="Markdown",
                )

            from handlers.mockjamb import mockjamb_start_handler
            await mockjamb_start_handler(update, context)
            return

        # -------------------------------------------------------
        # CHALLENGE LINK HANDLER
        # -------------------------------------------------------
        if arg.startswith("challenge_"):
            challenge_id = arg.split("_", 1)[1]

            if update.message:
                await update.message.reply_text(
                    "⚔️ *Friend Challenge Invitation*\n\n"
                    "You were invited to compete in a trivia challenge!\n\n"
                    f"Challenge ID: `{challenge_id}`\n\n"
                    "Press *Challenge Friends* or *Play Trivia Questions* to continue.",
                    parse_mode="Markdown",
                )
            return

    # ===========================================================
    # SUPPORT FLOW SAFETY GUARD
    # ===========================================================
    if (context.user_data or {}).get("in_support_flow"):
        msg_text = ""
        if update.message and update.message.text:
            msg_text = update.message.text.strip()

        if not msg_text.startswith("/start"):
            return

        context.user_data.pop("in_support_flow", None)

    text = build_start_text(user.first_name or "there")
    markup = build_main_menu_keyboard()

    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=markup,
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
                text,
                reply_markup=markup,
                parse_mode="MarkdownV2",
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text,
                reply_markup=markup,
                parse_mode="MarkdownV2",
            )
        return


# ===============================================================
# GO BACK TO MAIN MENU
# ===============================================================
async def go_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    await start(update, context)


# ===============================================================
# /help
# ===============================================================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🆘 *How to Play*\n\n"
        "1️⃣ Select *Play Trivia Questions*\n"
        "2️⃣ Choose a category\n"
        "3️⃣ Answer correctly to earn reward points\n"
        "4️⃣ Climb the leaderboard and compete for prizes\n\n"
        "🎁 Top player this cycle can win:\n\n"
        "📱 *iPhone 17 Pro Max*\n"
        "📱 *Samsung Galaxy S26 Ultra*\n"
        "📱 *Samsung Z Flip 6*\n"
        "🎧 *AirPods*\n"
        "🔊 *Bluetooth Speakers*\n\n"
        "Plus instant rewards like 📞 *Airtime* for premium points milestones\\!\n\n"
        "Use the buttons below to continue 👇"
    )

    await update.message.reply_text(
        text,
        reply_markup=build_main_menu_keyboard(),
        parse_mode="MarkdownV2",
    )


# ===============================================================
# /mytries
# ===============================================================
async def mytries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    async with get_async_session() as session:
        db_user = await get_or_create_user(
            session,
            tg_id=tg_user.id,
            username=tg_user.username,
        )

        text = (
            f"📊 *Your Question Credits*\n\n"
            f"• Paid: `{db_user.tries_paid or 0}`\n"
            f"• Free: `{db_user.tries_bonus or 0}`\n\n"
            "_Questions = Chances to earn more reward points_ 🎯"
        )

    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ===============================================================
# Smart Fallback (LAST handler)
# ===============================================================
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("_in_conversation"):
        return

    if not update.message or not update.message.text:
        return

    text_msg = update.message.text.strip()

    if text_msg.startswith("/"):
        return

    if re.fullmatch(r"^[0-9+ ]+$", text_msg):
        return

    intent = detect_user_intent(text_msg)

    if intent:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➡️ Continue", callback_data=intent)]
        ])

        await update.message.reply_text(
            md_escape(
                "🤖 I think I understand what you mean.\n\n"
                "Tap below to continue."
            ),
            reply_markup=keyboard,
            parse_mode="MarkdownV2",
        )


# ===============================================================
# Intent Detector
# ===============================================================
def detect_user_intent(text: str):
    text = text.lower()

    challenge_words = [
        "challenge", "challenge friend", "challenge friends", "friend challenge"
    ]

    battle_words = [
        "battle", "battle mode", "multiplayer"
    ]

    support_words = [
        "support", "contact", "admin", "help desk"
    ]

    trivia_words = [
        "play", "trivia", "question", "questions",
        "quiz", "game", "answer"
    ]

    payment_words = [
        "subscribe", "attempt", "attempts", "buy"
    ]

    faq_words = [
        "faq", "rule", "rules", "terms",
        "guide", "instruction"
    ]

    leaderboard_words = [
        "leaderboard", "rank", "ranking",
        "score", "top", "winner"
    ]

    free_words = [
        "free", "bonus", "earn"
    ]

    menu_words = [
        "menu", "other menu"
    ]

    if any(word in text for word in challenge_words):
        return "challenge:start"

    if any(word in text for word in battle_words):
        return "battle:menu"

    if any(word in text for word in support_words):
        return "support:start"

    if any(word in text for word in trivia_words):
        return "playtrivia"

    if any(word in text for word in payment_words):
        return "buy"

    if any(word in text for word in faq_words):
        return "faq"

    if any(word in text for word in leaderboard_words):
        return "leaderboard:show"

    if any(word in text for word in free_words):
        return "free"

    if any(word in text for word in menu_words):
        return "menu:other"

    return None


# ===============================================================
# Register Handlers
# ===============================================================
def register_handlers(application):
    # ---------------------------------------------------
    # Commands (BLOCK propagation)
    # ---------------------------------------------------
    application.add_handler(CommandHandler("start", start, block=True))
    application.add_handler(CommandHandler("help", help_cmd, block=True))
    application.add_handler(CommandHandler("mytries", mytries, block=True))
    application.add_handler(CommandHandler("terms", terms_handler, block=True))
    application.add_handler(CommandHandler("faq", faq_handler, block=True))

    # ---------------------------------------------------
    # Callback buttons
    # ---------------------------------------------------
    application.add_handler(CallbackQueryHandler(exam_hub_handler, pattern=r"^exam:hub$"))
    application.add_handler(CallbackQueryHandler(other_menu_handler, pattern=r"^menu:other$"))
    application.add_handler(CallbackQueryHandler(go_start_callback, pattern=r"^menu:main$"))
    application.add_handler(CallbackQueryHandler(go_start_callback, pattern=r"^go_start$"))
    application.add_handler(CallbackQueryHandler(terms_handler, pattern=r"^terms$"))
    application.add_handler(CallbackQueryHandler(faq_handler, pattern=r"^faq$"))

    # ---------------------------------------------------
    # Leaderboard
    # ---------------------------------------------------
    from handlers.leaderboard import register_leaderboard_handlers
    register_leaderboard_handlers(application)

    # ---------------------------------------------------
    # Fallback
    # ---------------------------------------------------
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & ~filters.Regex(r"^[0-9+ ]+$")
            & ~filters.UpdateType.EDITED_MESSAGE,
            fallback,
        ),
        group=20,
    )

