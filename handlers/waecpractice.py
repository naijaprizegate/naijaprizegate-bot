# ====================================================
# handlers/waecpractice.py
# ===================================================
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from helpers import md_escape
from waec_loader import get_waec_subjects


def make_waec_subject_keyboard():
    subjects = get_waec_subjects()
    rows = []
    row = []

    for subject in subjects:
        row.append(
            InlineKeyboardButton(
                subject["name"],
                callback_data=f"wp_subj_{subject['code']}"
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def build_waec_welcome_text() -> str:
    return (
        "📘 *Welcome to WAEC / NECO Practice*\n\n"
        "This section helps you practise for WAEC and NECO just in *two different ways*:\n\n"
        "Please choose a subject below\\."
    )


async def waecpractice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

    context.user_data["wp_subject_code"] = None
    context.user_data["wp_mode"] = None
    context.user_data["wp_topic_id"] = None
    context.user_data["wp_topic_page"] = 1

    await update.effective_message.reply_text(
        build_waec_welcome_text(),
        parse_mode="MarkdownV2",
        reply_markup=make_waec_subject_keyboard(),
    )


def register_handlers(application):
    application.add_handler(CommandHandler("waecpractice", waecpractice_handler))
    application.add_handler(CallbackQueryHandler(waecpractice_handler, pattern=r"^waecneco:practice$"))

