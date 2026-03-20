# ====================================================================
# handlers/jambpractice.py
# ===================================================================

import math
import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from sqlalchemy import text

from db import get_async_session
from jamb_loader import get_jamb_subjects, get_subject_topics, get_subject_by_code

logger = logging.getLogger(__name__)

TOPICS_PER_PAGE = 6


# =============================
# DB helpers
# =============================
async def ensure_jamb_user_access(user_id: int):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    insert into jamb_user_access (user_id)
                    values (:user_id)
                    on conflict (user_id) do nothing
                """),
                {"user_id": user_id},
            )


async def get_jamb_user_access(user_id: int) -> Optional[dict]:
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                select
                    free_questions_remaining,
                    paid_question_credits,
                    total_questions_used
                from jamb_user_access
                where user_id = :user_id
            """),
            {"user_id": user_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


# =============================
# Keyboards
# =============================
def make_subject_keyboard():
    subjects = get_jamb_subjects()
    rows = []
    row = []

    for subject in subjects:
        row.append(
            InlineKeyboardButton(
                subject["name"],
                callback_data=f"jp_subj_{subject['code']}"
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def make_mode_keyboard(subject_code: str):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📚 By Topics", callback_data=f"jp_mode_topics_{subject_code}")],
            [InlineKeyboardButton("📝 Mock UTME", callback_data=f"jp_mode_mock_{subject_code}")],
            [InlineKeyboardButton("⬅️ Back to Subjects", callback_data="jambpractice")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_topics_keyboard(subject_code: str, page: int = 1):
    topics = get_subject_topics(subject_code)
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
                callback_data=f"jp_topic::{subject_code}::{topic['id']}"
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton("◀ Prev", callback_data=f"jp_topicpage_{subject_code}_{page-1}")
        )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton("Next ▶", callback_data=f"jp_topicpage_{subject_code}_{page+1}")
        )
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("⬅️ Back to Mode", callback_data=f"jp_back_mode_{subject_code}")])
    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])

    return InlineKeyboardMarkup(rows), page, total_pages


# =============================
# Message builders
# =============================
def build_welcome_text(free_remaining: int, paid_credits: int) -> str:
    return (
        "🎓 *Welcome to JAMB Practice*\n\n"
        "Practice original UTME-style questions by subject and topic.\n"
        "You can study with detailed answer explanations after each question.\n\n"
        "*How it works:*\n"
        "• First-time users get *5 free questions*\n"
        "• After that, it costs *₦100 per 50 questions*\n"
        "• Questions are served topic by topic\n"
        "• Repeats are avoided until you exhaust the topic bank\n\n"
        "*Disclaimer:*\n"
        "This is an independent study tool and not an official JAMB platform.\n\n"
        f"🎁 Free questions left: *{free_remaining}*\n"
        f"💳 Paid question credits: *{paid_credits}*\n\n"
        "Please choose a subject below."
    )


# =============================
# Entry point
# =============================
async def jambpractice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    if not tg:
        return

    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

    await ensure_jamb_user_access(tg.id)
    access = await get_jamb_user_access(tg.id)

    free_remaining = int((access or {}).get("free_questions_remaining", 5))
    paid_credits = int((access or {}).get("paid_question_credits", 0))

    context.user_data["jp_subject_code"] = None
    context.user_data["jp_mode"] = None
    context.user_data["jp_topic_id"] = None
    context.user_data["jp_topic_page"] = 1

    text_msg = build_welcome_text(free_remaining, paid_credits)

    await update.effective_message.reply_text(
        text_msg,
        parse_mode="Markdown",
        reply_markup=make_subject_keyboard(),
    )


# =============================
# Subject selected
# =============================
async def jamb_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, _, subject_code = query.data.split("_", 2)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid subject selection.")

    subject = get_subject_by_code(subject_code)
    if not subject:
        return await query.message.reply_text("⚠️ Subject not found or inactive.")

    context.user_data["jp_subject_code"] = subject_code
    context.user_data["jp_mode"] = None
    context.user_data["jp_topic_id"] = None
    context.user_data["jp_topic_page"] = 1

    await query.message.reply_text(
        f"📘 *You selected:* {subject['name']}\n\n"
        "How would you like to practice?",
        parse_mode="Markdown",
        reply_markup=make_mode_keyboard(subject_code),
    )


# =============================
# Mode selected
# =============================
async def jamb_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data

    if data.startswith("jp_mode_topics_"):
        subject_code = data.replace("jp_mode_topics_", "", 1)
        context.user_data["jp_subject_code"] = subject_code
        context.user_data["jp_mode"] = "topic_practice"
        context.user_data["jp_topic_page"] = 1

        subject = get_subject_by_code(subject_code)
        kb, page, total_pages = make_topics_keyboard(subject_code, 1)

        return await query.message.reply_text(
            f"📚 *{subject['name']} Topics*\n\n"
            f"Choose a topic below.\n"
            f"_Page {page} of {total_pages}_",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    if data.startswith("jp_mode_mock_"):
        subject_code = data.replace("jp_mode_mock_", "", 1)
        context.user_data["jp_subject_code"] = subject_code
        context.user_data["jp_mode"] = "mock_utme"

        subject = get_subject_by_code(subject_code)

        return await query.message.reply_text(
            f"📝 *Mock UTME for {subject['name']}*\n\n"
            "Mock UTME mode is coming next.\n"
            "For now, please use *By Topics* while we complete the full flow.",
            parse_mode="Markdown",
            reply_markup=make_mode_keyboard(subject_code),
        )


# =============================
# Topic pagination
# =============================
async def jamb_topic_page_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, _, subject_code, page_str = query.data.split("_", 3)
        page = int(page_str)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid topic page.")

    context.user_data["jp_subject_code"] = subject_code
    context.user_data["jp_topic_page"] = page

    subject = get_subject_by_code(subject_code)
    kb, page, total_pages = make_topics_keyboard(subject_code, page)

    await query.message.reply_text(
        f"📚 *{subject['name']} Topics*\n\n"
        f"Choose a topic below.\n"
        f"_Page {page} of {total_pages}_",
        parse_mode="Markdown",
        reply_markup=kb,
    )


# =============================
# Topic selected
# =============================
async def jamb_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, subject_code, topic_id = query.data.split("::")
    except Exception:
        return await query.message.reply_text("⚠️ Invalid topic selection.")

    topics = get_subject_topics(subject_code)
    selected_topic = next((t for t in topics if t["id"] == topic_id), None)

    if not selected_topic:
        return await query.message.reply_text("⚠️ Topic not found.")

    context.user_data["jp_subject_code"] = subject_code
    context.user_data["jp_topic_id"] = topic_id

    await query.message.reply_text(
        f"✅ *Topic selected:* {selected_topic['title']}\n\n"
        "Next step: we will now ask the user how many questions they want.\n"
        "That is the next task.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⬅️ Back to Topics", callback_data=f"jp_topicpage_{subject_code}_1")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )


# =============================
# Back to mode
# =============================
async def jamb_back_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    subject_code = query.data.replace("jp_back_mode_", "", 1)
    subject = get_subject_by_code(subject_code)

    context.user_data["jp_subject_code"] = subject_code
    context.user_data["jp_mode"] = None
    context.user_data["jp_topic_id"] = None

    await query.message.reply_text(
        f"📘 *You selected:* {subject['name']}\n\n"
        "How would you like to practice?",
        parse_mode="Markdown",
        reply_markup=make_mode_keyboard(subject_code),
    )


# =============================
# Register handlers
# =============================
def register_handlers(application):
    application.add_handler(CommandHandler("jambpractice", jambpractice_handler))
    application.add_handler(CallbackQueryHandler(jambpractice_handler, pattern=r"^jambpractice$"))
    application.add_handler(CallbackQueryHandler(jamb_subject_handler, pattern=r"^jp_subj_"))
    application.add_handler(CallbackQueryHandler(jamb_mode_handler, pattern=r"^jp_mode_"))
    application.add_handler(CallbackQueryHandler(jamb_topic_page_handler, pattern=r"^jp_topicpage_"))
    application.add_handler(CallbackQueryHandler(jamb_topic_handler, pattern=r"^jp_topic::"))
    application.add_handler(CallbackQueryHandler(jamb_back_mode_handler, pattern=r"^jp_back_mode_"))

