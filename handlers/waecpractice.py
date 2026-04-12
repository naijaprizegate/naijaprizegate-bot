# ====================================================
# handlers/waecpractice.py
# ===================================================
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from helpers import md_escape
from waec_loader import (
    get_waec_subjects,
    get_waec_subject_by_code,
    get_waec_subject_topics,
)

import math

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


def make_waec_topics_keyboard(subject_code: str, page: int = 1):
    topics = get_waec_subject_topics(subject_code)
    total_topics = len(topics)
    total_pages = max(1, math.ceil(total_topics / TOPICS_PER_PAGE))

    page = max(1, min(page, total_pages))

    start = (page - 1) * TOPICS_PER_PAGE
    end = start + TOPICS_PER_PAGE
    page_topics = topics[start:end]

    rows = []

    for topic in page_topics:
        rows.append([
            InlineKeyboardButton(
                f"{topic['number']}. {topic['title']}",
                callback_data=f"wp_topic::{subject_code}::{topic['id']}"
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton("◀ Prev", callback_data=f"wp_topicpage_{subject_code}_{page-1}")
        )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton("Next ▶", callback_data=f"wp_topicpage_{subject_code}_{page+1}")
        )
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("⬅️ Back to Mode", callback_data=f"wp_back_mode_{subject_code}")])
    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])

    return InlineKeyboardMarkup(rows), page, total_pages


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
        context.user_data["wp_topic_page"] = 1

        subject = get_waec_subject_by_code(subject_code)
        kb, page, total_pages = make_waec_topics_keyboard(subject_code, 1)

        safe_subject_name = md_escape(str(subject["name"]))
        safe_page = md_escape(str(page))
        safe_total_pages = md_escape(str(total_pages))

        return await query.message.reply_text(
            f"📚 *{safe_subject_name} Topics*\n\n"
            f"Choose a topic below\\.\n"
            f"_Page {safe_page} of {safe_total_pages}_",
            parse_mode="MarkdownV2",
            reply_markup=kb,
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


async def waec_topic_page_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, _, subject_code, page_str = query.data.split("_", 3)
        page = int(page_str)
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid topic page\\.",
            parse_mode="MarkdownV2",
        )

    context.user_data["wp_subject_code"] = subject_code
    context.user_data["wp_topic_page"] = page

    subject = get_waec_subject_by_code(subject_code)
    kb, page, total_pages = make_waec_topics_keyboard(subject_code, page)

    safe_subject_name = md_escape(str(subject["name"]))
    safe_page = md_escape(str(page))
    safe_total_pages = md_escape(str(total_pages))

    await query.message.reply_text(
        f"📚 *{safe_subject_name} Topics*\n\n"
        f"Choose a topic below\\.\n"
        f"_Page {safe_page} of {safe_total_pages}_",
        parse_mode="MarkdownV2",
        reply_markup=kb,
    )


async def waec_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, subject_code, topic_id = query.data.split("::")
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid topic selection\\.",
            parse_mode="MarkdownV2",
        )

    topics = get_waec_subject_topics(subject_code)
    selected_topic = next((t for t in topics if t["id"] == topic_id), None)

    if not selected_topic:
        return await query.message.reply_text(
            "⚠️ Topic not found\\.",
            parse_mode="MarkdownV2",
        )

    context.user_data["wp_subject_code"] = subject_code
    context.user_data["wp_topic_id"] = topic_id

    safe_topic_title = md_escape(str(selected_topic["title"]))

    await query.message.reply_text(
        f"✅ *Topic selected:* {safe_topic_title}\n\n"
        "Next step: topic access screen will be connected next\\.",
        parse_mode="MarkdownV2",
    )


async def waec_back_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    subject_code = query.data.replace("wp_back_mode_", "", 1)
    subject = get_waec_subject_by_code(subject_code)

    context.user_data["wp_subject_code"] = subject_code
    context.user_data["wp_mode"] = None
    context.user_data["wp_topic_id"] = None

    safe_subject_name = md_escape(str(subject["name"]))

    await query.message.reply_text(
        f"📘 *You selected:* {safe_subject_name}\n\n"
        "How would you like to practice\\?",
        parse_mode="MarkdownV2",
        reply_markup=make_waec_mode_keyboard(subject_code),
    )
    
def register_handlers(application):
    application.add_handler(CommandHandler("waecpractice", waecpractice_handler))
    application.add_handler(CallbackQueryHandler(waecpractice_handler, pattern=r"^waecneco:practice$"))
