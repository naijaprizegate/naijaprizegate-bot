# ====================================================
# handlers/waecpractice.py
# ===================================================
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from helpers import md_escape


def make_waec_subject_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Biology", callback_data="wp_subj_bio")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def build_waec_welcome_text() -> str:
    return (
        "📘 *Welcome to WAEC / NECO Practice*\n\n"
        "This section helps you practise for WAEC and NECO just like JAMB Practice\\.\n\n"
        "Please choose a subject below\\."
    )


async def waecpractice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

    await update.effective_message.reply_text(
        build_waec_welcome_text(),
        parse_mode="MarkdownV2",
        reply_markup=make_waec_subject_keyboard(),
    )


def register_handlers(application):
    application.add_handler(CommandHandler("waecpractice", waecpractice_handler))
    application.add_handler(CallbackQueryHandler(waecpractice_handler, pattern=r"^waecneco:practice$"))
