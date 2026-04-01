# ====================================================================
# handlers/mockjamb.py
# ====================================================================

import json
import math
import logging

from sqlalchemy import text
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from jamb_loader import get_course_subject_map, get_course_by_code, get_course_subjects, get_subject_by_code
from db import get_async_session
from services.flutterwave_client import create_checkout, build_tx_ref
from services.mockjamb_payments import create_pending_mockjamb_payment, get_mockjamb_payment
from services.mockjamb_session_service import (
    get_or_create_mockjamb_session_from_payment,
    mark_mockjamb_subject_completed,
    get_mockjamb_session_by_payment_reference,
)
from services.mockjamb_exam_service import (
    start_mockjamb_subject,
    answer_mockjamb_question,
    calculate_mockjamb_subject_score,
)

logger = logging.getLogger(__name__)

COURSES_PER_PAGE = 6

MOCKJAMB_SOLO_FEE = 100

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


def make_mockjamb_solo_payment_keyboard(course_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"💳 Pay ₦{MOCKJAMB_SOLO_FEE}", callback_data="mj_pay_solo")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"mj_use_course::{course_code}")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_mockjamb_exam_ready_keyboard(subject_codes: list[str]) -> InlineKeyboardMarkup:
    rows = []

    for code in subject_codes:
        subject = get_course_subjects_for_code(code)
        if subject:
            rows.append([
                InlineKeyboardButton(
                    f"📘 Start with {subject['name']}",
                    callback_data=f"mj_start_subject::{code}"
                )
            ])

    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def get_course_subjects_for_code(subject_code: str):
    from jamb_loader import get_subject_by_code
    return get_subject_by_code(subject_code)


def make_mockjamb_question_answer_keyboard(
    subject_code: str,
    question_order: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("A", callback_data=f"mj_ans::{subject_code}::{question_order}::A"),
                InlineKeyboardButton("B", callback_data=f"mj_ans::{subject_code}::{question_order}::B"),
            ],
            [
                InlineKeyboardButton("C", callback_data=f"mj_ans::{subject_code}::{question_order}::C"),
                InlineKeyboardButton("D", callback_data=f"mj_ans::{subject_code}::{question_order}::D"),
            ],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_mockjamb_next_subject_keyboard(subject_codes: list[str]) -> InlineKeyboardMarkup:
    rows = []

    for code in subject_codes:
        subject = get_subject_by_code(code)
        if subject:
            rows.append([
                InlineKeyboardButton(
                    f"📘 Start {subject['name']}",
                    callback_data=f"mj_start_subject::{code}"
                )
            ])

    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def make_mockjamb_final_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
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


def build_mockjamb_solo_payment_text(course_code: str) -> str:
    course = get_course_by_code(course_code)
    if not course:
        return "⚠️ Course not found."

    subjects = get_course_subjects(course_code)
    subject_lines = "\n".join([f"• {subject['name']}" for subject in subjects])

    return (
        "💳 *Mock JAMB / UTME Solo Access*\n\n"
        f"*Course:* {course['course_name']}\n\n"
        "*Subjects:*\n"
        f"{subject_lines}\n\n"
        f"*Exam Fee:* ₦{MOCKJAMB_SOLO_FEE}\n\n"
        "Tap below to continue to payment."
    )


def build_mockjamb_exam_ready_text(course_code: str, subject_codes: list[str]) -> str:
    course = get_course_by_code(course_code)
    if not course:
        return "⚠️ Course not found."

    subject_lines = []
    for code in subject_codes:
        subject = get_course_subjects_for_code(code)
        if subject:
            subject_lines.append(f"• {subject['name']}")

    joined_subjects = "\n".join(subject_lines)

    return (
        "📝 *Mock JAMB / UTME Exam Ready*\n\n"
        f"*Course:* {course['course_name']}\n\n"
        "*Your subjects:*\n"
        f"{joined_subjects}\n\n"
        "Choose the subject you want to start with first."
    )


def build_mockjamb_live_question_text(
    *,
    subject_code: str,
    question_row: dict,
    question_number: int,
    total_questions: int,
    exam_ends_at=None,
) -> str:
    subject = get_subject_by_code(subject_code)
    subject_name = subject["name"] if subject else subject_code.upper()

    try:
        payload = json.loads(question_row.get("question_json") or "{}")
    except Exception:
        payload = {}

    question_text = (
        payload.get("question")
        or payload.get("text")
        or payload.get("prompt")
        or "Question text unavailable."
    )

    options = payload.get("options") or {}
    if not isinstance(options, dict):
        options = {}

    lines = [
        "📝 *Mock JAMB / UTME*",
        "",
        f"*Subject:* {subject_name}",
        f"*Question:* {question_number} of {total_questions}",
    ]

    if exam_ends_at:
        lines.append("⏱ *Exam timer is running*")

    lines.extend([
        "",
        f"{question_text}",
        "",
        f"A. {options.get('A', '---')}",
        f"B. {options.get('B', '---')}",
        f"C. {options.get('C', '---')}",
        f"D. {options.get('D', '---')}",
        "",
        "Choose your answer below.",
    ])

    return "\n".join(lines)


def build_mockjamb_subject_completed_text(
    *,
    course_code: str,
    completed_subject_code: str,
    score_100: int,
    remaining_subject_codes: list[str],
) -> str:
    course = get_course_by_code(course_code)
    completed_subject = get_subject_by_code(completed_subject_code)

    course_name = course["course_name"] if course else course_code
    subject_name = completed_subject["name"] if completed_subject else completed_subject_code.upper()

    lines = [
        "✅ *Subject Completed*",
        "",
        f"*Course:* {course_name}",
        f"*Completed Subject:* {subject_name}",
        f"*Score:* {score_100}",
        "",
    ]

    if remaining_subject_codes:
        lines.append("*Choose your next subject:*")
        for code in remaining_subject_codes:
            subject = get_subject_by_code(code)
            if subject:
                lines.append(f"• {subject['name']}")
    else:
        lines.append("All subjects completed.")

    return "\n".join(lines)


def build_mockjamb_final_result_text(
    *,
    course_code: str,
    subject_codes: list[str],
    scores: dict,
) -> str:
    course = get_course_by_code(course_code)
    course_name = course["course_name"] if course else course_code

    aggregate = 0
    lines = [
        "📊 *Mock JAMB / UTME Result*",
        "",
        f"*Course:* {course_name}",
        "",
    ]

    for code in subject_codes:
        subject = get_subject_by_code(code)
        subject_name = subject["name"] if subject else code.upper()
        score = int(scores.get(code) or 0)
        aggregate += score
        lines.append(f"{subject_name} {score}")

    lines.extend([
        "",
        f"*Aggregate:* {aggregate}",
    ])

    return "\n".join(lines)


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
# Solo Mode Handler
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

    text = build_mockjamb_solo_payment_text(course_code)
    markup = make_mockjamb_solo_payment_keyboard(course_code)

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
# Friends Mode Handler
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



# -------------------------------------------
# Mock JAMB Pay Solo Handler
# -------------------------------------------
async def mockjamb_pay_solo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    course_code = context.user_data.get("mj_course_code")
    subject_codes = context.user_data.get("mj_subject_codes") or []

    if not course_code or not subject_codes:
        return await query.message.reply_text(
            "⚠️ Your Mock JAMB setup is incomplete.\n\nPlease choose your course again.",
            reply_markup=make_mockjamb_welcome_keyboard(),
        )

    course = get_course_by_code(course_code)
    if not course:
        return await query.message.reply_text(
            "⚠️ Course not found. Please choose your course again."
        )

    amount = MOCKJAMB_SOLO_FEE
    user = query.from_user
    tg_id = user.id
    username = user.username or f"user_{tg_id}"
    email = f"{username}@naijaprizegate.ng"

    tx_ref = build_tx_ref("MOCKJAMB")
    subject_codes_json = json.dumps(subject_codes)

    async with get_async_session() as session:
        await create_pending_mockjamb_payment(
            session,
            payment_reference=tx_ref,
            user_id=tg_id,
            amount_paid=amount,
            course_code=course_code,
            subject_codes_json=subject_codes_json,
            exam_mode="solo",
        )
        await session.commit()

    checkout_url = await create_checkout(
        user_id=tg_id,
        amount=amount,
        username=username,
        email=email,
        tx_ref=tx_ref,
        meta={
            "tg_id": str(tg_id),
            "username": username,
            "product_type": "MOCKJAMB",
            "course_code": course_code,
            "exam_mode": "solo",
        },
        product_type="MOCKJAMB",
    )

    if not checkout_url:
        async with get_async_session() as session:
            await session.execute(
                text("""
                    update public.mockjamb_payments
                    set
                        payment_status = 'expired',
                        updated_at = now()
                    where payment_reference = :payment_reference
                      and lower(coalesce(payment_status, '')) = 'pending'
                """),
                {"payment_reference": tx_ref},
            )
            await session.commit()

        return await query.message.reply_text(
            "⚠️ Payment service is unavailable right now. Please try again shortly."
        )

    subject_names = "\n".join(
        [f"• {subject['name']}" for subject in get_course_subjects(course_code)]
    )

    message_text = (
        "💳 *Mock JAMB / UTME Payment*\n\n"
        f"*Course:* {course['course_name']}\n\n"
        "*Subjects:*\n"
        f"{subject_names}\n\n"
        f"*Amount:* ₦{amount}\n\n"
        "Tap below to complete your payment securely."
    )

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Pay Securely", url=checkout_url)],
            [InlineKeyboardButton("⬅️ Back", callback_data="mj_mode_solo")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )

    try:
        await query.edit_message_text(
            message_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            message_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )



# --------------------------------------------
# Mockjamb Payment Success Handler
# ---------------------------------------------
async def mockjamb_payment_success_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tx_ref: str,
):
    if not tx_ref:
        if update.message:
            await update.message.reply_text(
                "⚠️ Payment reference is missing. Please try again."
            )
        return

    async with get_async_session() as session:
        payment = await get_mockjamb_payment(session, tx_ref)

        if not payment:
            if update.message:
                await update.message.reply_text(
                    "⚠️ Mock JAMB payment record not found. Please contact support if payment was deducted."
                )
            return

        if str(payment.get("payment_status", "")).lower().strip() != "successful":
            if update.message:
                await update.message.reply_text(
                    "⚠️ Your Mock JAMB payment is not yet marked successful. Please wait a moment and try again."
                )
            return

        course_code = str(payment.get("course_code") or "").strip()
        subject_codes_json = payment.get("subject_codes_json") or "[]"
        exam_mode = str(payment.get("exam_mode") or "solo").strip()

        try:
            subject_codes = json.loads(subject_codes_json)
        except Exception:
            subject_codes = []

        if not course_code or not subject_codes:
            if update.message:
                await update.message.reply_text(
                    "⚠️ Your saved Mock JAMB exam data is incomplete. Please contact support."
                )
            return

        mj_session = await get_or_create_mockjamb_session_from_payment(
            session,
            payment_reference=tx_ref,
            user_id=int(payment["user_id"]),
            course_code=course_code,
            subject_codes_json=subject_codes_json,
        )
        await session.commit()

    context.user_data["mj_course_code"] = course_code
    context.user_data["mj_subject_codes"] = subject_codes
    context.user_data["mj_mode"] = exam_mode
    context.user_data["mj_room_code"] = None
    context.user_data["mj_payment_reference"] = tx_ref
    context.user_data["mj_session_id"] = mj_session["id"]

    message_text = build_mockjamb_exam_ready_text(course_code, subject_codes)
    markup = make_mockjamb_exam_ready_keyboard(subject_codes)

    if update.message:
        await update.message.reply_text(
            message_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
        return

    if update.callback_query:
        query = update.callback_query
        await query.answer()

        try:
            await query.edit_message_text(
                message_text,
                parse_mode="Markdown",
                reply_markup=markup,
            )
        except Exception:
            await query.message.reply_text(
                message_text,
                parse_mode="Markdown",
                reply_markup=markup,
            )


# ------------------------------------------------
# Mock JAMB Start Subject Handler
# -----------------------------------------------
async def mockjamb_start_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, subject_code = query.data.split("::", 1)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid subject selection.")

    payment_reference = context.user_data.get("mj_payment_reference")
    mj_subject_codes = context.user_data.get("mj_subject_codes") or []

    if not payment_reference:
        return await query.message.reply_text(
            "⚠️ Mock JAMB payment reference not found. Please restart from your paid exam link."
        )

    if subject_code not in mj_subject_codes:
        return await query.message.reply_text(
            "⚠️ That subject is not part of your current Mock JAMB subject combination."
        )

    user_id = query.from_user.id

    async with get_async_session() as session:
        try:
            result = await start_mockjamb_subject(
                session,
                payment_reference=payment_reference,
                user_id=int(user_id),
                subject_code=subject_code,
            )
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.exception(
                "Failed to start Mock JAMB subject | tx_ref=%s | subject=%s | err=%s",
                payment_reference,
                subject_code,
                e,
            )
            return await query.message.reply_text(
                "⚠️ Could not start this subject right now. Please try again."
            )

    session_row = result["session"]
    current_question = result["current_question"]

    if not current_question:
        return await query.message.reply_text(
            "⚠️ No question could be loaded for this subject."
        )

    context.user_data["mj_current_subject_code"] = subject_code
    context.user_data["mj_current_question_order"] = 1

    message_text = build_mockjamb_live_question_text(
        subject_code=subject_code,
        question_row=current_question,
        question_number=1,
        total_questions=50,
        exam_ends_at=session_row.get("exam_ends_at"),
    )

    markup = make_mockjamb_question_answer_keyboard(
        subject_code=subject_code,
        question_order=1,
    )

    try:
        await query.edit_message_text(
            message_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            message_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )        


# -----------------------------------------------
# Mock JAMB Answer Handler
# -----------------------------------------------
async def mockjamb_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, subject_code, question_order_raw, selected_option = query.data.split("::", 3)
        question_order = int(question_order_raw)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid answer submission.")

    payment_reference = context.user_data.get("mj_payment_reference")
    course_code = context.user_data.get("mj_course_code")
    subject_codes = context.user_data.get("mj_subject_codes") or []

    if not payment_reference or not course_code:
        return await query.message.reply_text(
            "⚠️ Mock JAMB exam session not found."
        )

    async with get_async_session() as session:
        try:
            answer_result = await answer_mockjamb_question(
                session,
                payment_reference=payment_reference,
                subject_code=subject_code,
                question_order=question_order,
                selected_option=selected_option,
            )

            if answer_result.get("status") == "next_question":
                await session.commit()

                next_question = answer_result["next_question"]
                next_question_order = int(answer_result["next_question_order"])
                total_questions = int(answer_result["total_questions"])

                session_row = await get_mockjamb_session_by_payment_reference(
                    session,
                    payment_reference,
                )

                context.user_data["mj_current_subject_code"] = subject_code
                context.user_data["mj_current_question_order"] = next_question_order

                message_text = build_mockjamb_live_question_text(
                    subject_code=subject_code,
                    question_row=next_question,
                    question_number=next_question_order,
                    total_questions=total_questions,
                    exam_ends_at=(session_row or {}).get("exam_ends_at"),
                )

                markup = make_mockjamb_question_answer_keyboard(
                    subject_code=subject_code,
                    question_order=next_question_order,
                )

                try:
                    await query.edit_message_text(
                        message_text,
                        parse_mode="Markdown",
                        reply_markup=markup,
                    )
                except Exception:
                    await query.message.reply_text(
                        message_text,
                        parse_mode="Markdown",
                        reply_markup=markup,
                    )
                return

            if answer_result.get("status") == "completed_subject":
                score_info = await calculate_mockjamb_subject_score(
                    session,
                    payment_reference=payment_reference,
                    subject_code=subject_code,
                )

                session_row = await mark_mockjamb_subject_completed(
                    session,
                    payment_reference=payment_reference,
                    subject_code=subject_code,
                    score=int(score_info["score_100"]),
                )
                await session.commit()

                if not session_row:
                    return await query.message.reply_text(
                        "⚠️ Could not finalize this subject."
                    )

                try:
                    completed_subjects = json.loads(session_row.get("completed_subjects_json") or "[]")
                except Exception:
                    completed_subjects = []

                try:
                    scores = json.loads(session_row.get("scores_json") or "{}")
                except Exception:
                    scores = {}

                remaining_subject_codes = [
                    code for code in subject_codes if code not in completed_subjects
                ]

                if remaining_subject_codes:
                    message_text = build_mockjamb_subject_completed_text(
                        course_code=course_code,
                        completed_subject_code=subject_code,
                        score_100=int(score_info["score_100"]),
                        remaining_subject_codes=remaining_subject_codes,
                    )
                    markup = make_mockjamb_next_subject_keyboard(remaining_subject_codes)
                else:
                    message_text = build_mockjamb_final_result_text(
                        course_code=course_code,
                        subject_codes=subject_codes,
                        scores=scores,
                    )
                    markup = make_mockjamb_final_result_keyboard()

                try:
                    await query.edit_message_text(
                        message_text,
                        parse_mode="Markdown",
                        reply_markup=markup,
                    )
                except Exception:
                    await query.message.reply_text(
                        message_text,
                        parse_mode="Markdown",
                        reply_markup=markup,
                    )
                return

            await session.rollback()
            return await query.message.reply_text(
                "⚠️ Could not process that answer."
            )

        except Exception as e:
            await session.rollback()
            logger.exception(
                "Failed to process Mock JAMB answer | tx_ref=%s | subject=%s | q=%s | err=%s",
                payment_reference,
                subject_code,
                question_order,
                e,
            )
            return await query.message.reply_text(
                "⚠️ Could not process your answer right now. Please try again."
            )

# -------------------------------------------------
# Mock JAMB Return to Exam Ready Handler
# --------------------------------------------------
async def mockjamb_return_to_exam_ready_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    course_code = context.user_data.get("mj_course_code")
    subject_codes = context.user_data.get("mj_subject_codes") or []

    if not course_code or not subject_codes:
        return await query.message.reply_text(
            "⚠️ Mock JAMB exam state not found."
        )

    message_text = build_mockjamb_exam_ready_text(course_code, subject_codes)
    markup = make_mockjamb_exam_ready_keyboard(subject_codes)

    try:
        await query.edit_message_text(
            message_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            message_text,
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
    application.add_handler(CallbackQueryHandler(mockjamb_pay_solo_handler, pattern=r"^mj_pay_solo$"))
    application.add_handler(CallbackQueryHandler(mockjamb_start_subject_handler, pattern=r"^mj_start_subject::"))
    application.add_handler(CallbackQueryHandler(mockjamb_answer_handler, pattern=r"^mj_ans::"))
    application.add_handler(CallbackQueryHandler(mockjamb_return_to_exam_ready_handler, pattern=r"^payok_mockjamb_return$"))

