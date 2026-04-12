# ====================================================
# handlers/waecpractice.py
# ===================================================
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CallbackQueryHandler

from helpers import md_escape
from waec_loader import (
    get_waec_subjects,
    get_waec_subject_by_code,
)

TOPICS_PER_PAGE = 7


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


def make_waec_mode_keyboard(subject_code: str):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📚 By Topics", callback_data=f"wp_mode_topics_{subject_code}")],
            [InlineKeyboardButton("📝 Mock WAEC / NECO", callback_data=f"wp_mode_mock_{subject_code}")],
            [InlineKeyboardButton("⬅️ Back to Subjects", callback_data="waecneco:practice")],
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

    context.user_data["wp_subject_code"] = None
    context.user_data["wp_mode"] = None
    context.user_data["wp_topic_id"] = None
    context.user_data["wp_topic_page"] = 1

    await update.effective_message.reply_text(
        build_waec_welcome_text(),
        parse_mode="MarkdownV2",
        reply_markup=make_waec_subject_keyboard(),
    )


async def waec_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, _, subject_code = query.data.split("_", 2)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid subject selection.")

    subject = get_waec_subject_by_code(subject_code)
    if not subject:
        return await query.message.reply_text("⚠️ Subject not found or inactive.")

    context.user_data["wp_subject_code"] = subject_code
    context.user_data["wp_mode"] = None
    context.user_data["wp_topic_id"] = None
    context.user_data["wp_topic_page"] = 1

    safe_subject_name = md_escape(str(subject["name"]))

    await query.message.reply_text(
        f"📘 *You selected:* {safe_subject_name}\n\n"
        "How would you like to practice\\?",
        parse_mode="MarkdownV2",
        reply_markup=make_waec_mode_keyboard(subject_code),
    )


async def waec_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data

    if data.startswith("wp_mode_topics_"):
        subject_code = data.replace("wp_mode_topics_", "", 1)
        context.user_data["wp_subject_code"] = subject_code
        context.user_data["wp_mode"] = "topic_practice"

        subject = get_waec_subject_by_code(subject_code)
        safe_subject_name = md_escape(str(subject["name"])) if subject else md_escape(subject_code)

        return await query.message.reply_text(
            f"📚 *{safe_subject_name} Topics*\n\n"
            "Topic page will be the next step\\.",
            parse_mode="MarkdownV2",
        )

    if data.startswith("wp_mode_mock_"):
        subject_code = data.replace("wp_mode_mock_", "", 1)
        subject = get_waec_subject_by_code(subject_code)
        safe_subject_name = md_escape(str(subject["name"])) if subject else md_escape(subject_code)

        return await query.message.reply_text(
            f"📝 *Mock WAEC / NECO*\n\n"
            f"Subject: *{safe_subject_name}*\n\n"
            "This part will be connected after normal topic practice is finished\\.",
            parse_mode="MarkdownV2",
        )

def register_handlers(application):
    application.add_handler(CommandHandler("waecpractice", waecpractice_handler))
    application.add_handler(CallbackQueryHandler(waecpractice_handler, pattern=r"^waecneco:practice$"))
