# ===============================================================
# handlers/free.py
# ===============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from helpers import md_escape, get_or_create_user
from models import Proof
from db import AsyncSessionLocal, get_async_session
from sqlalchemy import insert
import os
import random

BOT_USERNAME = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")

# --- FREE MENU HANDLER ---
async def free_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_async_session() as session:

        user = await get_or_create_user(session, update.effective_user.id, update.effective_user.username)

    text = (
        f"🎁 *Hey {md_escape(user.username or user.first_name)}!* \n\n"
        "Wanna grab some *FREE spins*? ⬇️\n\n"
        "🤩 Don’t sleep on this — it’s your golden chance to stack up extra tries and chase the jackpot! 💎🔥\n\n"
        "1️⃣ *Invite a friend*: Drop your referral link. Every signup through YOU = +1 free try ⚡ (the more friends, the more spins!)\n\n"
        "2️⃣ *Follow us everywhere*: Facebook, Instagram, TikTok, YouTube 📲. Snap a proof pic 📸 → once approved, BOOM, another +1 free try lands in your account 🚀\n\n"
        "⚠️ Don’t wait — others are already stacking free spins while you’re still reading this 👀. Be the one who wins, not the one who watches! 🏆\n\n"
        "👉 Pick your move below and start racking up those FREE shots at glory:"
    )

    # Personalize with first_name → fallback to username → fallback to "Friend"
    display_name = md_escape(user.first_name or user.username or "Friend")
    ref_link = f"https://t.me/NaijaPrizeGateBot?start={user.id}"

    share_variants = [
        (
            f"🎰 Yo, it’s *{display_name}* here!\n\n"
            f"Don’t sleep on this 👇\n\n"
            f"I’m spinning on *NaijaPrizeGate* and cashing out free tries and a chance to win an iPhone 16 Pro Max 🔥\n"
            f"Jump in with my link before you miss it ⏳\n\n"
            f"👉 {ref_link}"
        ),
        (
            f"🚀 *{display_name}* just scored free spins on *NaijaPrizeGate* 🎉\n\n"
            f"Ready to try your luck? Use my referral link now & claim yours fast 👇\n\n"
            f"👉 {ref_link}"
        ),
        (
            f"🔥 Hey, *{display_name}* is already playing! \n\n"
            f"NaijaPrizeGate is giving out free spins for a chance to win an iPhone 16 Pro Max 🎰💸\n"
            f"Click my link & don’t get left behind 👇\n\n"
            f"👉 {ref_link}"
        ),
    ]

    # Pick a random hype + personalized message
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

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="MarkdownV2"
    )



# --- REFERRAL LINK HANDLER ---
async def send_referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the user their personal referral link."""
    user = update.effective_user
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user.id}"

    text = (
        f"🚀 *Boom, {md_escape(user.first_name)}!* Your golden referral link is ready:\n\n"
        f"🔗 {md_escape(ref_link)}\n\n"
        "👥 Every friend who joins through *your* link = you unlock *+1 FREE try!* 🎉\n\n"
        "📢 Share this link with friends. "
        "🔥 The more you share, the more spins you stack. Imagine hitting the jackpot while others are still watching 👀\n\n"
        "Don’t sit back — blast your link in your groups, drop it in DMs, post it everywhere. "
        "*First movers win BIG!* 💰💎"
    )

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(text, parse_mode="MarkdownV2")


# --- PROOF UPLOAD HANDLER ---
async def ask_proof_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to upload their proof (photo)."""
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📤 Please send a *photo screenshot* showing that you followed us "
        "on social media.\n\n"
        "Once an admin approves it, you’ll receive +1 free try 🎉",
        parse_mode="MarkdownV2"
    )
    context.user_data["awaiting_proof"] = True


async def handle_proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user photo submission and save proof row in DB."""
    if not context.user_data.get("awaiting_proof"):
        return

    photo = update.message.photo[-1]  # get best resolution
    file_id = photo.file_id
    

    # Save proof to DB
    async with get_async_session() as session:
        user = await get_or_create_user(session, update.effective_user.id, update.effective_user.username)
        stmt = insert(Proof).values(user_id=user.id, file_id=file_id, status="pending")
        await session.execute(stmt)
        await session.commit()

    await update.message.reply_text(
        "✅ Proof received! \n\n"
        "An admin will review it shortly. "
        "You’ll be notified once approved 🎉"
    )

    context.user_data["awaiting_proof"] = False


# --- NOTIFICATION FOR APPROVAL ---
def proof_approved_text(user, bonus_tries: int):
    """Text sent when admin approves proof and user is credited."""
    return (
        f"🎉 *Congrats {md_escape(user.first_name)}!* \n\n"
        f"✅ Your proof has been approved by our team. \n"
        f"💎 You just earned *{bonus_tries} FREE spin(s)*!\n\n"
            "🔥 That’s one more shot at grabbing the jackpot. Remember… every extra spin takes you closer to the *BIG WIN!* 💎💰\n\n"
        "📢 Don’t stop here — keep stacking free tries by inviting friends and smashing those social follows. "
        "*The ones who keep pushing are the ones who win BIG!* 🚀"
        "👉 Head back to *Try Luck* and put it to work 🔥"
    )

# --- REGISTRATION ---
def register_handlers(application):
    application.add_handler(CommandHandler("free", free_menu))
    application.add_handler(CallbackQueryHandler(send_referral_link, pattern="^get_referral$"))
    application.add_handler(CallbackQueryHandler(ask_proof_upload, pattern="^upload_proof$"))
    application.add_handler(MessageHandler(filters.PHOTO, handle_proof_photo))

