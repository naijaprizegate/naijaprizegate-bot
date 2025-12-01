# ===============================================================
# handlers/free.py
# ===============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from helpers import md_escape, get_or_create_user
from models import Proof
from db import get_async_session
from sqlalchemy import insert
import os
import random

BOT_USERNAME = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")

# --- FREE MENU HANDLER ---
async def free_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_async_session() as session:
        db_user = await get_or_create_user(session, update.effective_user.id, update.effective_user.username)

    tg_user = update.effective_user
    display_name = md_escape(tg_user.first_name or tg_user.username or "Friend")

    text = (
        f"🎁 *Hey {display_name}*\\! \n\n"
        "Ready to boost your performance and get ahead on the leaderboard\\? 😎\n\n"
        "💡 Every correct answer earns you points\\.\n"
        "🏆 Once the performance target is reached, the top scorer wins the prize\\.\n\n"
        "*How to earn FREE Trivia Questions* 👇\n"
        "1️⃣ *Invite friends* — Each friend who joins through your link = *\\+1 Free Question*\n\n"
        "2️⃣ *Follow us on social media* — Upload a screenshot proof = *\\+1 Free Question* after approval\n\n"
        "⚡ The more questions you answer, the higher you climb\\.\n\n"
        "Be the player others try to catch — not the one trying to catch up\\! 🚀\n\n"
        "👉 Choose an option below to increase your quiz access\\:"
    )


    ref_link = f"https://t.me/{BOT_USERNAME}?start={db_user.id}"
    ref_link_md = md_escape(ref_link)  # ✅ escape before using in Markdown

    share_variants = [
        (
            f"🎰 Yo, it’s *{display_name}* here\\!\n\n"
            f"NaijaPrizeGate is lit right now 🔥\n\n"
            f"🧠 *I'm upgrading my knowledge and climbing the leaderboard on NaijaPrizeGate!* 🚀\n\n"
            f"Top scorer wins the prize 🎯\n\n"
            f"Up for grabs this cycle:\n\n"
            f"📱 iPhone 16 Pro Max\n\n"
            f"📱 iPhone 17 Pro Max\n\n"
            f"📱 Samsung Galaxy Z Flip 7\n\n"
            f"📱 Samsung Galaxy S25 Ultra\n\n"
            f"Join me by answering fun questions and show what you know 👇\n"
            f"Don’t snooze — tap my link before it’s too late ⏳👇\n"
            f"👉 {ref_link}"
        ),
        (
            f"🚀 *{display_name}* just unlocked *free Trivia Questions* on *NaijaPrizeGate* 🎉\n\n"
            f"I used to scroll endlessly on my phone\\.\n"
            f"Now I’m using it to actually challenge my brain 🧠🔥\n\n"
            f"NaijaPrizeGate is rewarding top scorers with amazing prizes:\n"
            f"📱 iPhone 16 Pro Max\n\n"
            f"📱 iPhone 17 Pro Max\n\n"
            f"📱 Samsung Galaxy Z Flip 7\n\n"
            f"📱 Samsung Galaxy S25 Ultra\n\n"
            f"Join me — let’s level up and win smart 👇\n"
            f"👉 {ref_link}"
        ),
        (
            f"🔥 *{display_name}* is already playing\\! \n\n"
            f"NaijaPrizeGate’s dropping Top-Tier Campaign Rewards like crazy 🎰💸\n\n"
            f"Up for grabs:\n\n"
            f"🏆 *iPhone 16 Pro Max*\n\n"
            f"🏆 *iPhone 17 Pro Max*\n\n"
            f"🏆 *Samsung Galaxy Z Flip 7*\n\n"
            f"🏆 *Samsung Galaxy S25 Ultra*\n\n"
            f"Click my link — don’t miss the wave 👇\n"
            f"👉 {ref_link}"
        ),
    ]


    share_message = random.choice(share_variants)

    keyboard = [
        [
            InlineKeyboardButton("🚀 Invite & Earn", callback_data="get_referral_link"),
            InlineKeyboardButton("👥 Share Referral", switch_inline_query=share_message),
        ],
        [
            InlineKeyboardButton("🔥 Follow on Facebook", url="https://web.facebook.com/Naijaprizegate"),
            InlineKeyboardButton("✨ Follow on Instagram", url="https://www.instagram.com/naijaprizegate/"),
        ],
        [
            InlineKeyboardButton("🎶 Follow on TikTok", url="https://www.tiktok.com/@naijaprizegate"),
            InlineKeyboardButton("🎥 Subscribe on YouTube", url="https://www.youtube.com/@Naijaprizegate"),
        ],
        [InlineKeyboardButton("📸 Upload Proof & Claim", callback_data="upload_proof")],
    ]

    if update.callback_query:  # triggered by button
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )
    else:  # triggered by /free command
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )

# --- REFERRAL LINK HANDLER ---
async def send_referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the user their personal referral link."""
    tg_user = update.effective_user

    # ✅ Use DB user.id for referral links
    async with get_async_session() as session:
        db_user = await get_or_create_user(session, tg_user.id, tg_user.username)

    ref_link = f"https://t.me/{BOT_USERNAME}?start={db_user.id}"
    ref_link_md = md_escape(ref_link)  # ✅ escape before sending

    display_name = md_escape(tg_user.first_name or tg_user.username or "Friend")

    text = (
        f"🚀 *Boom, {display_name}*\\! Your personal referral link is ready:\n\n"
        f"🔗 {ref_link_md}\n\n"
        "👥 Every friend who joins through *your* link \\= *\\+1 FREE Trivia Questions\\!* 🎉\n\n"
        "🧠 More questions = More chances to score higher\n\n" 
        "💪 Higher score = Better chance to finish #1\n\n"
        "Share your link everywhere — let’s see how far your knowledge can take you\\! 🏆"
        "*Be the first to get to the top\\!* 💰💎"
    )

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(text, parse_mode="MarkdownV2")

# --- PROOF UPLOAD HANDLER ---
async def ask_proof_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to upload their proof (photo)."""
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📤 Please send a *photo screenshot* showing that you followed us "
        "on social media\\.\n\n"
        "Once approved by our team, you’ll receive *\\+1 free Trivia Question 🎉\n\n" \
        "📌 Remember: Rewards are skill-based — higher performance wins\\!",
        parse_mode="MarkdownV2"
    )
    context.user_data["awaiting_proof"] = True


async def handle_proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user photo submission and save proof row in DB."""
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
        stmt = insert(Proof).values(user_id=db_user.id, file_id=file_id, status="pending")
        await session.execute(stmt)
        await session.commit()

    await update.message.reply_text(
        "✅ Proof received\\! \n\n"
        "Our team will review it shortly\\.\n\n "
        "You’ll be notified once approved 🎉 and your free question is credited 💡\n\n"
        "📍 Tip: The more questions you answer correctly, the higher you rank\\.\n\n"
        "Type or click /Start to go back to the main menu\\.",
        parse_mode="MarkdownV2"
    )

    context.user_data["awaiting_proof"] = False


# --- NOTIFICATION FOR APPROVAL ---
def proof_approved_text(db_user, bonus_tries: int):
    """Text sent when admin approves proof and user is credited."""
    display_name = md_escape(db_user.username or "Friend")

    return (
        f"🎉 *Congrats {display_name}*\\! \n\n"
        f"✅ Your proof has been approved by our team\\. \n"
        f"💎 You just earned *{bonus_tries} FREE Trivia Question(s)*\\!\n\n"
        "🧠 Ready to boost your score even more?\n\n"
        "📢 Don’t stop here — keep getting free Trivia Questions by inviting friends\\. "
        "*Leaderboard ranking is based entirely on correct answers\\.\n\n"
        "👉 Head back to *Play trivia Questions* — every point gets you closer to the top spot 🏆"
    )


# --- REGISTRATION ---
def register_handlers(application):
    application.add_handler(CommandHandler("free", free_menu))
    # ✅ Make "🎁 Free Tries" button from /start work
    application.add_handler(CallbackQueryHandler(free_menu, pattern="^free$"))
    application.add_handler(CallbackQueryHandler(send_referral_link, pattern="^get_referral_link$"))
    application.add_handler(CallbackQueryHandler(ask_proof_upload, pattern="^upload_proof$"))
    application.add_handler(MessageHandler(filters.PHOTO, handle_proof_photo))
