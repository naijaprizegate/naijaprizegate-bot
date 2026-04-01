# ====================================================================
# handlers/mockjamb.py
# ====================================================================

import math
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from jamb_loader import get_course_subject_map, get_course_by_code, get_course_subjects


logger = logging.getLogger(__name__)

COURSES_PER_PAGE = 6


# ====================================================================
# Keyboards
# ====================================================================
def make_mockjamb_welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎯 Choose Course", callback_data="mj_course_page_1")],
            [InlineKeyboardButton("⬅️ Back to Exam Hub", callback_data="exam:hub")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_course_page_keyboard(page: int = 1) -> InlineKeyboardMarkup:
    courses = get_course_subject_map()
    total_courses = len(courses)
    total_pages = max(1, math.ceil(total_courses / COURSES_PER_PAGE))

    page = max(1, min(page, total_pages))

    start = (page - 1) * COURSES_PER_PAGE
    end = start + COURSES_PER_PAGE
    page_courses = courses[start:end]

    rows = []

    for course in page_courses:
        rows.append([
            InlineKeyboardButton(
                course["course_name"],
                callback_data=f"mj_course_select::{course['course_code']}"
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton("◀ Prev", callback_data=f"mj_course_page_{page - 1}")
        )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton("Next ▶", callback_data=f"mj_course_page_{page + 1}")
        )
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("⬅️ Back to Mock JAMB", callback_data="mock:jamb")])
    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])

    return InlineKeyboardMarkup(rows)


def make_course_recommendation_keyboard(course_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Use This Combination", callback_data=f"mj_use_course::{course_code}")],
            [InlineKeyboardButton("🔁 Change Course", callback_data="mj_course_page_1")],
            [InlineKeyboardButton("⬅️ Back to Mock JAMB", callback_data="mock:jamb")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_mockjamb_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧍 Write Alone", callback_data="mj_mode_solo")],
            [InlineKeyboardButton("👥 Invite Friends", callback_data="mj_mode_friends")],
            [InlineKeyboardButton("⬅️ Change Course", callback_data="mj_course_page_1")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


# ====================================================================
# Message Builders
# ====================================================================
def build_mockjamb_welcome_text() -> str:
    return (
        "📝 *Welcome to Mock JAMB / UTME*\n\n"
        "This mock exam is designed to simulate the real UTME experience.\n\n"
        "You will write *4 subjects*:\n"
        "• *Use of English* (compulsory)\n"
        "• *3 other subjects* based on your intended course\n\n"
        "To begin, choose your intended course and we will recommend a likely JAMB subject combination for you."
    )


def build_course_page_text(page: int, total_pages: int) -> str:
    return (
        "🎯 *Choose Your Intended Course*\n\n"
        "Select your course below and we will recommend a likely JAMB subject combination for your mock exam.\n\n"
        f"_Page {page} of {total_pages}_"
    )


def build_course_recommendation_text(course_code: str) -> str:
    course = get_course_by_code(course_code)
    if not course:
        return "⚠️ Course not found."

    subjects = get_course_subjects(course_code)

    lines = [
        "🎯 *Recommended Subject Combination*",
        "",
        f"*Course:* {course['course_name']}",
        "",
        "*Recommended Subjects:*",
    ]

    for subject in subjects:
        lines.append(f"• {subject['name']}")

    notes = (course.get("notes") or "").strip()
    if notes:
        lines.extend(["", f"_Note: {notes}_"])

    lines.extend([
        "",
        "Do you want to continue with this combination?"
    ])

    return "\n".join(lines)


def build_mockjamb_mode_text(course_code: str) -> str:
    course = get_course_by_code(course_code)
    if not course:
        return "⚠️ Course not found."

    subjects = get_course_subjects(course_code)
    subject_lines = "\n".join([f"• {subject['name']}" for subject in subjects])

    return (
        "✅ *Subject Combination Saved*\n\n"
        f"*Course:* {course['course_name']}\n\n"
        "*Your Mock JAMB / UTME subjects are:*\n"
        f"{subject_lines}\n\n"
        "How would you like to take this mock exam?"
    )


# ====================================================================
# Entry Handler
# ====================================================================
async def mockjamb_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = build_mockjamb_welcome_text()
    markup = make_mockjamb_welcome_keyboard()

    context.user_data["mj_course_code"] = None
    context.user_data["mj_subject_codes"] = []
    context.user_data["mj_mode"] = None
    context.user_data["mj_room_code"] = None

    if update.callback_query:
        query = update.callback_query
        await query.answer()

        try:
            await query.edit_message_text(
                text,
                parse_mode="Markdown",
                reply_markup=markup,
            )
        except Exception:
            await query.message.reply_text(
                text,
                parse_mode="Markdown",
                reply_markup=markup,
            )
        return

    if update.message:
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )


# ====================================================================
# Course Pagination Handler
# ====================================================================
async def mockjamb_course_page_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        page = int(query.data.replace("mj_course_page_", "", 1))
    except Exception:
        return await query.message.reply_text("⚠️ Invalid course page.")

    courses = get_course_subject_map()
    total_courses = len(courses)
    total_pages = max(1, math.ceil(total_courses / COURSES_PER_PAGE))
    page = max(1, min(page, total_pages))

    text = build_course_page_text(page, total_pages)
    markup = make_course_page_keyboard(page)

    try:
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )


# ====================================================================
# Course Selected Handler
# ====================================================================
async def mockjamb_course_select_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, course_code = query.data.split("::", 1)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid course selection.")

    course = get_course_by_code(course_code)
    if not course:
        return await query.message.reply_text("⚠️ Course not found.")

    subjects = get_course_subjects(course_code)

    context.user_data["mj_course_code"] = course_code
    context.user_data["mj_subject_codes"] = [subject["code"] for subject in subjects]

    text = build_course_recommendation_text(course_code)
    markup = make_course_recommendation_keyboard(course_code)

    try:
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )


# ====================================================================
# Use Recommended Course Combination
# ====================================================================
async def mockjamb_use_course_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, course_code = query.data.split("::", 1)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid course confirmation.")

    course = get_course_by_code(course_code)
    if not course:
        return await query.message.reply_text("⚠️ Course not found.")

    subjects = get_course_subjects(course_code)
    context.user_data["mj_course_code"] = course_code
    context.user_data["mj_subject_codes"] = [subject["code"] for subject in subjects]

    text = build_mockjamb_mode_text(course_code)
    markup = make_mockjamb_mode_keyboard()

    try:
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )


# ====================================================================
# Temporary Solo Mode Handler
# ====================================================================
async def mockjamb_mode_solo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    context.user_data["mj_mode"] = "solo"

    course_code = context.user_data.get("mj_course_code")
    if not course_code:
        return await query.message.reply_text(
            "⚠️ No saved course found. Please choose your course again.",
            reply_markup=make_mockjamb_welcome_keyboard(),
        )

    course = get_course_by_code(course_code)
    subjects = get_course_subjects(course_code)
    subject_lines = "\n".join([f"• {subject['name']}" for subject in subjects])

    text = (
        "🧍 *Write Alone Selected*\n\n"
        f"*Course:* {course['course_name']}\n\n"
        "*Subjects:*\n"
        f"{subject_lines}\n\n"
        "Next step: we will build the payment screen for solo mock exam access."
    )

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Back", callback_data=f"mj_use_course::{course_code}")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )

    try:
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )


# ====================================================================
# Temporary Friends Mode Handler
# ====================================================================
async def mockjamb_mode_friends_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    context.user_data["mj_mode"] = "friends"

    course_code = context.user_data.get("mj_course_code")
    if not course_code:
        return await query.message.reply_text(
            "⚠️ No saved course found. Please choose your course again.",
            reply_markup=make_mockjamb_welcome_keyboard(),
        )

    course = get_course_by_code(course_code)
    subjects = get_course_subjects(course_code)
    subject_lines = "\n".join([f"• {subject['name']}" for subject in subjects])

    text = (
        "👥 *Invite Friends Selected*\n\n"
        f"*Course:* {course['course_name']}\n\n"
        "*Subjects for this room:*\n"
        f"{subject_lines}\n\n"
        "All players in the same room will use this same subject combination.\n\n"
        "Next step: we will build the multiplayer room and invite link flow."
    )

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Back", callback_data=f"mj_use_course::{course_code}")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )

    try:
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )


# ====================================================================
# Register Handlers
# ====================================================================
def register_handlers(application):
    application.add_handler(CommandHandler("mockjamb", mockjamb_start_handler))
    application.add_handler(CallbackQueryHandler(mockjamb_start_handler, pattern=r"^mock:jamb$"))
    application.add_handler(CallbackQueryHandler(mockjamb_course_page_handler, pattern=r"^mj_course_page_"))
    application.add_handler(CallbackQueryHandler(mockjamb_course_select_handler, pattern=r"^mj_course_select::"))
    application.add_handler(CallbackQueryHandler(mockjamb_use_course_handler, pattern=r"^mj_use_course::"))
    application.add_handler(CallbackQueryHandler(mockjamb_mode_solo_handler, pattern=r"^mj_mode_solo$"))
    application.add_handler(CallbackQueryHandler(mockjamb_mode_friends_handler, pattern=r"^mj_mode_friends$"))
