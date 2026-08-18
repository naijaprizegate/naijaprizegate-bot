# ===============================================================
# handlers/free.py  (HTML VERSION - CLEANED)
# ===============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from helpers import get_or_create_user
from models import Proof
from db import get_async_session
from sqlalchemy import insert
import html


def _safe_name(user) -> str:
    """HTML-safe display name for bot messages."""
    return html.escape(user.first_name or user.username or "Friend")



# --- FREE MENU HANDLER ---
async def free_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    if tg_user is None:
        return

    display_name_html = _safe_name(tg_user)

    text = (
        f"🎁 <b>Hey {display_name_html}!</b>\n\n"
        "Want to earn <b>10 FREE Trivia Questions</b>? 🎯\n\n"
        "📱 <b>Follow us on social media</b>\n\n"
        "Follow NaijaPrizeGate on our social media platforms "
        "and upload a screenshot proof showing that you followed us.\n\n"
        "🎁 Once your proof is reviewed and approved, "
        "you will receive <b>10 FREE Trivia Questions</b>.\n\n"
        "⚡ More questions = More chances to score higher.\n\n"
        "🏆 Keep answering, keep improving, and aim for the top!\n\n"
        "👉 Choose a platform below, follow us, then upload your proof."
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "📘 Facebook",
                url="https://web.facebook.com/Naijaprizegate",
            ),
            InlineKeyboardButton(
                "📸 Instagram",
                url="https://www.instagram.com/naijaprizegate/",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎶 TikTok",
                url="https://www.tiktok.com/@naijaprizegate",
            ),
            InlineKeyboardButton(
                "🎥 YouTube",
                url="https://www.youtube.com/@Naijaprizegate",
            ),
        ],
        [
            InlineKeyboardButton(
                "📸 Upload Proof & Claim 10 Questions",
                callback_data="upload_proof",
            )
        ],
    ]

    markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


# --- PROOF UPLOAD HANDLER ---
async def ask_proof_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if query:
        await query.answer()

    msg = (
        "📤 <b>Submit Your Proof</b>\n\n"
        "Please send a <b>photo screenshot</b> showing "
        "that you followed us.\n\n"
        "🎁 Once your proof is reviewed and approved, "
        "you’ll receive <b>10 FREE Trivia Questions</b>.\n\n"
        "📌 Remember: Rewards are skill-based. "
        "Higher performance wins!"
    )

    if query:
        await query.edit_message_text(
            msg,
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            msg,
            parse_mode="HTML",
        )

    context.user_data["awaiting_proof"] = True


async def handle_proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_proof"):
        return

    tg_user = update.effective_user
    if tg_user is None:
        return

    if not update.message or not update.message.photo:
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    async with get_async_session() as session:
        db_user = await get_or_create_user(session, tg_user.id, tg_user.username)
        stmt = insert(Proof).values(user_id=db_user.id, file_id=file_id, status="pending")
        await session.execute(stmt)
        await session.commit()

    await update.message.reply_text(
        "✅ <b>Proof received!</b>\n\n"
        "Our team will review it shortly.\n\n"
        "You’ll be notified once approved 🎉 and your "
        "<b>10 FREE Trivia Questions</b> will be credited.\n\n"
        "📍 Tip: The more questions you answer correctly, "
        "the higher you rank.\n\n"
        "Click /start to return to the main menu.",
        parse_mode="HTML",
    )

    context.user_data["awaiting_proof"] = False


# --- NOTIFICATION FOR APPROVAL ---
def proof_approved_text(db_user, bonus_tries: int):
    display_name = html.escape(db_user.username or "Friend")
    return (
        f"🎉 <b>Congrats {display_name}!</b>\n\n"
        "✅ Your proof has been approved.\n"
        f"💎 You earned <b>{int(bonus_tries)} FREE Trivia Question(s)!</b>\n\n"
        "🧠 Keep climbing the leaderboard!\n\n"
        "👉 Head back to <b>Play Trivia Questions</b> and aim for the top 🏆"
    )


# --- REGISTRATION ---
def register_handlers(application):
    application.add_handler(CommandHandler("free", free_menu))
    application.add_handler(CallbackQueryHandler(free_menu, pattern=r"^free$"))
    application.add_handler(CallbackQueryHandler(ask_proof_upload, pattern=r"^upload_proof$"))
    application.add_handler(MessageHandler(filters.PHOTO, handle_proof_photo))

