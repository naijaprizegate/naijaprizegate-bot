# ===============================================================
# handlers/free.py  (HTML VERSION)
# ===============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from helpers import get_or_create_user
from models import Proof
from db import get_async_session
from sqlalchemy import insert
import os
import random
import html

BOT_USERNAME = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")


# --- FREE MENU HANDLER ---
async def free_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_async_session() as session:
        db_user = await get_or_create_user(
            session,
            update.effective_user.id,
            update.effective_user.username
        )

    tg_user = update.effective_user
    display_name = html.escape(tg_user.first_name or tg_user.username or "Friend")

    text = (
        f"🎁 <b>Hey {display_name}!</b>\n\n"
        "Ready to boost your performance and get ahead on the leaderboard? 😎\n\n"
        "💡 Every correct answer earns you points.\n"
        "🏆 Once the performance target is reached, the top scorer wins the prize.\n\n"
        "<b>How to earn FREE Trivia Questions</b> 👇\n\n"
        "1️⃣ <b>Invite friends</b> — Each friend who joins through your link = <b>+1 Free Question</b>\n\n"
        "2️⃣ <b>Follow us on social media</b> — Upload a screenshot proof = "
        "<b>+1 Free Question</b> after approval\n\n"
        "⚡ The more questions you answer, the higher you climb.\n\n"
        "Be the player others try to catch — not the one trying to catch up! 🚀\n\n"
        "👉 Choose an option below to increase your quiz access:\n\n"
        "To go back to the main menu, click /start"
    )

    ref_link = f"https://t.me/{BOT_USERNAME}?start={db_user.id}"

    share_variants = [
        (
            f"🎰 Hey, it’s <b>{display_name}</b> here!\n\n"
            f"NaijaPrizeGate is the game to play right now 🔥\n\n"
            f"Answer questions on <b>Football</b>, <b>Entertainment</b>, and <b>History</b> "
            f"and win amazing rewards 🚀\n\n"
            f"<b>Top scorer wins:</b> 🎯\n\n"
            f"📱 <b>iPhone 16 Pro Max</b>\n"
            f"📱 <b>iPhone 17 Pro Max</b>\n"
            f"📱 <b>Samsung Galaxy Z Flip 7</b>\n"
            f"📱 <b>Samsung Galaxy S25 Ultra</b>\n\n"
            f"Join me now 👇\n"
            f"{ref_link}"
        ),

        (
            f"🔥 Hey, it’s <b>{display_name}</b>!\n\n"
            f"I'm already playing on <b>NaijaPrizeGate</b> 🎯\n\n"
            f"Test your knowledge in <b>Football</b>, <b>Entertainment</b>, and <b>History</b> "
            f"and climb the leaderboard fast 🚀\n\n"
            f"<b>Prizes up for grabs:</b>\n\n"
            f"🏆 <b>iPhone 16 Pro Max</b>\n"
            f"🏆 <b>iPhone 17 Pro Max</b>\n"
            f"🏆 <b>Samsung Galaxy Z Flip 7</b>\n"
            f"🏆 <b>Samsung Galaxy S25 Ultra</b>\n\n"
            f"Don’t miss out 👇\n"
            f"{ref_link}"
        ),
    ]

    share_message = random.choice(share_variants)

    keyboard = [
        [
            InlineKeyboardButton("🚀 Invite & Earn", callback_data="get_referral_link"),
            InlineKeyboardButton("👥 Share Referral", switch_inline_query=share_message),
        ],
        [
            InlineKeyboardButton("📘 Facebook", url="https://web.facebook.com/Naijaprizegate"),
            InlineKeyboardButton("📸 Instagram", url="https://www.instagram.com/naijaprizegate/"),
        ],
        [
            InlineKeyboardButton("🎶 TikTok", url="https://www.tiktok.com/@naijaprizegate"),
            InlineKeyboardButton("🎥 YouTube", url="https://www.youtube.com/@Naijaprizegate"),
        ],
        [InlineKeyboardButton("📸 Upload Proof & Claim", callback_data="upload_proof")],
    ]

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )


# --- REFERRAL LINK HANDLER ---
async def send_referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    async with get_async_session() as session:
        db_user = await get_or_create_user(session, tg_user.id, tg_user.username)

    ref_link = f"https://t.me/{BOT_USERNAME}?start={db_user.id}"
    display_name = html.escape(tg_user.first_name or tg_user.username or "Friend")

    text = (
        f"🚀 <b>Boom, {display_name}!</b>\n\n"
        f"🔗 <b>Your personal referral link:</b>\n{ref_link}\n\n"
        "👥 For <b>every friend</b> who joins through your link you get "
        "<b>+1 FREE Trivia Question!</b> 🎉\n\n"
        "🧠 More questions = More chances to score higher\n"
        "💪 Higher score = Better chance to finish #1\n\n"
        "Share your link everywhere — let’s see how far your knowledge can take you! 🏆"
    )

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(text, parse_mode="HTML")


# --- PROOF UPLOAD HANDLER ---
async def ask_proof_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📤 Please send a <b>photo screenshot</b> showing that you followed us.\n\n"
        "Once approved, you’ll receive <b>+1 FREE Trivia Question</b> 🎉\n\n"
        "📌 Remember: Rewards are skill-based. Higher performance wins!",
        parse_mode="HTML"
    )
    context.user_data["awaiting_proof"] = True


async def handle_proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_proof"):
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    async with get_async_session() as session:
        db_user = await get_or_create_user(
            session,
            update.effective_user.id,
            update.effective_user.username
        )
        stmt = insert(Proof).values(
            user_id=db_user.id,
            file_id=file_id,
            status="pending"
        )
        await session.execute(stmt)
        await session.commit()

    await update.message.reply_text(
        "✅ <b>Proof received!</b>\n\n"
        "Our team will review it shortly.\n\n"
        "You’ll be notified once approved 🎉 and your free question is credited.\n\n"
        "📍 Tip: The more questions you answer correctly, the higher you rank.\n\n"
        "Click /start to return to the main menu.",
        parse_mode="HTML"
    )

    context.user_data["awaiting_proof"] = False


# --- NOTIFICATION FOR APPROVAL ---
def proof_approved_text(db_user, bonus_tries: int):
    display_name = html.escape(db_user.username or "Friend")

    return (
        f"🎉 <b>Congrats {display_name}!</b>\n\n"
        "✅ Your proof has been approved.\n"
        f"💎 You earned <b>{bonus_tries} FREE Trivia Question(s)!</b>\n\n"
        "🧠 Keep climbing the leaderboard!\n\n"
        "👉 Head back to <b>Play Trivia Questions</b> and aim for the top 🏆"
    )


# --- REGISTRATION ---
def register_handlers(application):
    application.add_handler(CommandHandler("free", free_menu))
    application.add_handler(CallbackQueryHandler(free_menu, pattern="^free$"))
    application.add_handler(CallbackQueryHandler(send_referral_link, pattern="^get_referral_link$"))
    application.add_handler(CallbackQueryHandler(ask_proof_upload, pattern="^upload_proof$"))
    application.add_handler(MessageHandler(filters.PHOTO, handle_proof_photo))
