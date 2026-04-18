# ====================================================================
# handlers/mockjamb.py
# ====================================================================

import json
import math
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from jamb_loader import get_course_subject_map, get_course_by_code, get_course_subjects, get_subject_by_code
from db import get_async_session
from helpers import md_escape
from services.flutterwave_client import create_checkout, build_tx_ref
from services.mockjamb_payments import create_pending_mockjamb_payment, get_mockjamb_payment
from services.mockjamb_session_service import (
    get_or_create_mockjamb_session_from_payment,
    mark_mockjamb_subject_completed,
    get_mockjamb_session_by_payment_reference,
    get_latest_active_mockjamb_session_for_user,
)
from services.mockjamb_exam_service import (
    start_mockjamb_subject,
    answer_mockjamb_question,
    calculate_mockjamb_subject_score,
    get_mockjamb_review_rows,
    get_mockjamb_subject_question_by_order,
    get_mockjamb_subject_question_count,
    get_mockjamb_subject_result_stats,
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
            [InlineKeyboardButton("✅ Submit Exam Now", callback_data="mj_submit_exam_confirm")],
            [InlineKeyboardButton("🛑 End Exam", callback_data="mj_end_exam")],
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
            [InlineKeyboardButton("📄 Preview Result", callback_data="mj_review_all")],
            [InlineKeyboardButton("❌ Wrong Answers Review", callback_data="mj_review_wrong")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_mockjamb_submit_exam_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Yes, Submit Now", callback_data="mj_submit_exam_yes")],
            [InlineKeyboardButton("❌ No, Continue Exam", callback_data="mj_submit_exam_no")],
        ]
    )


def build_mockjamb_submit_exam_confirm_text() -> str:
    return (
        "⚠️ *Submit Mock JAMB / UTME Now?*\n\n"
        "If you submit now:\n"
        "• your exam will end immediately\n"
        "• unanswered questions will count as zero\n"
        "• your current scores will be calculated and shown\n\n"
        "Are you sure you want to submit?"
    )


def make_mockjamb_stale_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶ Resume Exam", callback_data="mj_resume_exam")],
            [InlineKeyboardButton("🛑 End Exam", callback_data="mj_end_exam")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )

def make_mockjamb_time_up_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Submit Exam Now", callback_data="mj_submit_exam_confirm")],
            [InlineKeyboardButton("🛑 End Exam", callback_data="mj_end_exam")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )

# --------------------------------------
# Mock Time Remaining
# -------------------------------------
def format_mockjamb_time_remaining(exam_ends_at) -> str:
    """
    Returns a friendly remaining-time string like:
    - 1h 42m
    - 18m
    - Time up
    """
    if not exam_ends_at:
        return "Unknown"

    if isinstance(exam_ends_at, str):
        try:
            exam_ends_at = datetime.fromisoformat(exam_ends_at.replace("Z", "+00:00"))
        except Exception:
            return "Unknown"

    if exam_ends_at.tzinfo is None:
        exam_ends_at = exam_ends_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    delta = exam_ends_at - now
    total_seconds = int(delta.total_seconds())

    if total_seconds <= 0:
        return "Time up"

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


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

    raw_question_text = (
        payload.get("question")
        or payload.get("text")
        or payload.get("prompt")
        or "Question text unavailable."
    )

    options = payload.get("options") or {}
    if not isinstance(options, dict):
        options = {}

    question_text = md_escape(str(raw_question_text))
    option_a = md_escape(str(options.get("A", "---")))
    option_b = md_escape(str(options.get("B", "---")))
    option_c = md_escape(str(options.get("C", "---")))
    option_d = md_escape(str(options.get("D", "---")))
    safe_subject_name = md_escape(str(subject_name))

    lines = [
        "📝 *Mock JAMB / UTME*",
        "",
        f"*Subject:* {safe_subject_name}",
        f"*Question:* {question_number} of {total_questions}",
    ]

    if exam_ends_at:
        lines.append("⏱ *Exam timer is running*")

    lines.extend([
        "",
        question_text,
        "",
        f"A\\. {option_a}",
        f"B\\. {option_b}",
        f"C\\. {option_c}",
        f"D\\. {option_d}",
        "",
        "Choose your answer below\\.",
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
    answered_counts: dict | None = None,
    correct_counts: dict | None = None,
) -> str:
    course = get_course_by_code(course_code)
    course_name = course["course_name"] if course else course_code

    answered_counts = answered_counts or {}
    correct_counts = correct_counts or {}

    aggregate = 0
    lines = [
        "📊 *Mock JAMB / UTME Result*",
        "",
        f"Course: *{course_name}*",
        "",
    ]

    for code in subject_codes:
        subject = get_subject_by_code(code)
        subject_name = subject["name"] if subject else code.upper()
        score = int(scores.get(code) or 0)
        aggregate += score
        lines.append(f"*{subject_name}*: *{score}*")

    lines.extend([
        "",
        f"*Aggregate:* *{aggregate} / 400*",
        "",
        "━━━━━━━━━━━━━━━━━━",
        "📦 *Detailed Breakdown*",
        "",
    ])

    for code in subject_codes:
        subject = get_subject_by_code(code)
        subject_name = subject["name"] if subject else code.upper()
        answered = int(answered_counts.get(code) or 0)
        correct = int(correct_counts.get(code) or 0)
        score = int(scores.get(code) or 0)

        lines.extend([
            f"*{subject_name}*",
            f"Answered: {answered}",
            f"Correct: {correct}",
            f"Score: {score}/100",
            "",
        ])

    return "\n".join(lines)


# ----Question Has Passage-----------
def question_has_passage(question_row: dict) -> bool:
    try:
        payload = json.loads(question_row.get("question_json") or "{}")
    except Exception:
        payload = {}

    raw_passage_text = str(payload.get("passage") or "").strip()
    question_type = str(payload.get("question_type") or "").strip().lower()

    return bool(raw_passage_text) or question_type == "comprehension_mcq"


def build_mockjamb_passage_text(
    *,
    subject_code: str,
    question_row: dict,
    question_start: int,
    question_end: int,
    total_questions: int,
    exam_ends_at=None,
) -> str:
    subject = get_subject_by_code(subject_code)
    subject_name = subject["name"] if subject else subject_code.upper()

    payload = get_question_payload(question_row)

    raw_passage_title = payload.get("passage_title") or ""
    raw_passage_text = payload.get("passage") or ""

    safe_subject_name = md_escape(str(subject_name))
    safe_passage_title = md_escape(str(raw_passage_title)) if raw_passage_title else ""
    safe_passage_text = md_escape(str(raw_passage_text)) if raw_passage_text else ""

    lines = [
        "📝 *Mock JAMB / UTME*",
        "",
        f"*Subject:* {safe_subject_name}",
        (
            f"*Questions:* {question_start} \\- {question_end} of {total_questions}"
            if question_start != question_end
            else f"*Question:* {question_start} of {total_questions}"
        ),
    ]

    if exam_ends_at:
        remaining = format_mockjamb_time_remaining(exam_ends_at)
        lines.append(f"⏱ *Time remaining:* {md_escape(remaining)}")

    if safe_passage_title:
        lines.extend([
            "",
            f"*Passage Title:* {safe_passage_title}",
        ])

    if safe_passage_text:
        lines.extend([
            "",
            "*Passage:*",
            safe_passage_text,
        ])

    return "\n".join(lines)


def build_mockjamb_question_only_text(
    *,
    subject_code: str,
    question_row: dict,
    question_number: int,
    total_questions: int,
    exam_ends_at=None,
) -> str:
    subject = get_subject_by_code(subject_code)
    subject_name = subject["name"] if subject else subject_code.upper()

    payload = get_question_payload(question_row)

    raw_question_text = (
        payload.get("question")
        or payload.get("text")
        or payload.get("prompt")
        or "Question text unavailable."
    )

    options = payload.get("options") or {}
    if not isinstance(options, dict):
        options = {}

    question_text = md_escape(str(raw_question_text))
    option_a = md_escape(str(options.get("A", "---")))
    option_b = md_escape(str(options.get("B", "---")))
    option_c = md_escape(str(options.get("C", "---")))
    option_d = md_escape(str(options.get("D", "---")))
    safe_subject_name = md_escape(str(subject_name))

    lines = [
        "📝 *Mock JAMB / UTME*",
        "",
        f"*Subject:* {safe_subject_name}",
        f"*Question:* {question_number} of {total_questions}",
    ]

    if exam_ends_at:
        remaining = format_mockjamb_time_remaining(exam_ends_at)
        lines.append(f"⏱ *Time remaining:* {md_escape(remaining)}")

    lines.extend([
        "",
        "*Question:*",
        question_text,
        "",
        f"A\\. {option_a}",
        f"B\\. {option_b}",
        f"C\\. {option_c}",
        f"D\\. {option_d}",
        "",
        "Choose your answer below\\.",
    ])

    return "\n".join(lines)



def sort_review_rows_by_subject_order(rows: list[dict], subject_codes: list[str]) -> list[dict]:
    subject_order = {code: idx for idx, code in enumerate(subject_codes)}

    return sorted(
        rows,
        key=lambda row: (
            subject_order.get(str(row.get("subject_code") or ""), 999),
            int(row.get("question_order") or 0),
        ),
    )


def build_mockjamb_review_text(
    *,
    review_row: dict,
    review_index: int,
    total_reviews: int,
) -> str:
    subject_code = str(review_row.get("subject_code") or "").strip()
    subject = get_subject_by_code(subject_code)
    subject_name = subject["name"] if subject else subject_code.upper()

    try:
        payload = json.loads(review_row.get("question_json") or "{}")
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

    passage_title = payload.get("passage_title") or ""
    passage_text = payload.get("passage") or ""

    explanation = payload.get("explanation") or {}
    if not isinstance(explanation, dict):
        explanation = {}

    principle = str(explanation.get("principle") or "No principle available.").strip()
    steps = explanation.get("steps") or []
    if not isinstance(steps, list):
        steps = []

    simple_explanation = str(
        explanation.get("simple_explanation")
        or explanation.get("final_answer")
        or "No simple explanation available."
    ).strip()

    selected_option = str(review_row.get("selected_option") or "-").upper()
    correct_option = str(review_row.get("correct_option") or "-").upper()
    is_correct = bool(review_row.get("is_correct"))

    selected_option_text = str(options.get(selected_option, "---"))
    correct_option_text = str(options.get(correct_option, "---"))

    lines = [
        "📄 *Mock JAMB / UTME Review*",
        "",
        f"*Review Item:* {review_index} of {total_reviews}",
        f"*Subject:* {md_escape(subject_name)}",
        f"*Question No:* {int(review_row.get('question_order') or 0)}",
        "",
    ]

    if passage_text:
        if passage_title:
            lines.append(f"*Passage Title:* {md_escape(str(passage_title))}")
            lines.append("")
        lines.append("*Passage:*")
        lines.append(md_escape(str(passage_text)))
        lines.append("")

    lines.extend([
        "*Question:*",
        md_escape(str(question_text)),
        "",
        f"A\\. {md_escape(str(options.get('A', '---')))}",
        f"B\\. {md_escape(str(options.get('B', '---')))}",
        f"C\\. {md_escape(str(options.get('C', '---')))}",
        f"D\\. {md_escape(str(options.get('D', '---')))}",
        "",
        f"*Your Answer:* {md_escape(selected_option)} \\- {md_escape(selected_option_text)}",
        f"*Correct Answer:* {md_escape(correct_option)} \\- {md_escape(correct_option_text)}",
        f"*Result:* {'✅ Correct' if is_correct else '❌ Wrong'}",
        "",
        "*Explanation:*",
        "",
        f"*Principle:* {md_escape(principle)}",
        "",
        "*Steps:*",
    ])

    if steps:
        for idx, step in enumerate(steps, start=1):
            lines.append(f"{idx}\\. {md_escape(str(step))}")
    else:
        lines.append("No steps available\\.")

    lines.extend([
        "",
        "*Simple Explanation:*",
        md_escape(simple_explanation),
    ])

    return "\n".join(lines)


def make_mockjamb_review_nav_keyboard(
    *,
    mode: str,
    current_index: int,
    total_reviews: int,
) -> InlineKeyboardMarkup:
    nav_row = []

    if current_index > 0:
        nav_row.append(
            InlineKeyboardButton("⬅ Prev", callback_data=f"mj_review_nav::{mode}::{current_index - 1}")
        )

    if current_index < total_reviews - 1:
        nav_row.append(
            InlineKeyboardButton("Next ➡", callback_data=f"mj_review_nav::{mode}::{current_index + 1}")
        )

    rows = []
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("⬅ Back to Result", callback_data="mj_back_to_result")])
    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])

    return InlineKeyboardMarkup(rows)


def build_mockjamb_resume_prompt_text(
    *,
    course_code: str,
    subject_codes: list[str],
    completed_subjects: list[str],
    current_subject_code: str | None,
    current_question_index: int,
    exam_ends_at=None,
) -> str:
    course = get_course_by_code(course_code)
    course_name = course["course_name"] if course else course_code

    remaining_subject_codes = [
        code for code in subject_codes if code not in completed_subjects
    ]

    lines = [
        "📝 *Resume Mock JAMB / UTME*",
        "",
        f"*Course:* {course_name}",
        "",
    ]

    time_up = False

    if exam_ends_at:
        remaining = format_mockjamb_time_remaining(exam_ends_at)
        lines.append(f"*Time Remaining:* {remaining}")
        lines.append("")

        if str(remaining).strip().lower() == "time up":
            time_up = True

    if current_subject_code:
        current_subject = get_subject_by_code(current_subject_code)
        current_subject_name = current_subject["name"] if current_subject else current_subject_code.upper()
        next_question_no = max(1, int(current_question_index or 0) + 1)

        lines.extend([
            f"*Current Subject:* {current_subject_name}",
            f"*Resume From:* Question {next_question_no}",
            "",
        ])
    else:
        lines.extend([
            "You have an active Mock JAMB exam in progress.",
            "",
        ])

    if completed_subjects:
        lines.append("*Completed Subjects:*")
        for code in completed_subjects:
            subject = get_subject_by_code(code)
            if subject:
                lines.append(f"• {subject['name']}")
        lines.append("")

    if remaining_subject_codes:
        lines.append("*Remaining Subjects:*")
        for code in remaining_subject_codes:
            subject = get_subject_by_code(code)
            if subject:
                lines.append(f"• {subject['name']}")
    else:
        lines.append("All subjects have been completed.")

    if time_up:
        lines.extend([
            "",
            "Your exam time has ended. You can submit or end the exam below.",
        ])
    else:
        lines.extend([
            "",
            "Tap below to continue.",
        ])

    return "\n".join(lines)


def make_mockjamb_resume_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶ Resume Exam", callback_data="mj_resume_exam")],
            [InlineKeyboardButton("🛑 End Exam", callback_data="mj_end_exam")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def build_mockjamb_continue_subject_choice_text(
    *,
    course_code: str,
    remaining_subject_codes: list[str],
) -> str:
    course = get_course_by_code(course_code)
    course_name = course["course_name"] if course else course_code

    lines = [
        "📝 *Mock JAMB / UTME In Progress*",
        "",
        f"*Course:* {course_name}",
        "",
        "*Choose the next subject to continue:*",
    ]

    for code in remaining_subject_codes:
        subject = get_subject_by_code(code)
        if subject:
            lines.append(f"• {subject['name']}")

    return "\n".join(lines)


def get_question_payload(question_row: dict) -> dict:
    try:
        return json.loads(question_row.get("question_json") or "{}")
    except Exception:
        return {}


def get_question_passage_id(question_row: dict) -> str:
    payload = get_question_payload(question_row)
    return str(payload.get("passage_id") or "").strip()


def get_passage_question_range(
    *,
    paper_rows: list[dict],
    current_question_row: dict,
) -> tuple[int, int]:
    current_passage_id = get_question_passage_id(current_question_row)
    current_order = int(current_question_row.get("question_order") or 0)

    if not current_passage_id:
        return current_order, current_order

    matching_orders = []

    for row in paper_rows:
        row_passage_id = get_question_passage_id(row)
        if row_passage_id == current_passage_id:
            row_order = int(row.get("question_order") or 0)
            if row_order > 0:
                matching_orders.append(row_order)

    if not matching_orders:
        return current_order, current_order

    matching_orders = sorted(set(matching_orders))
    return matching_orders[0], matching_orders[-1]


def question_has_passage(question_row: dict) -> bool:
    payload = get_question_payload(question_row)
    raw_passage_text = str(payload.get("passage") or "").strip()
    question_type = str(payload.get("question_type") or "").strip().lower()
    return bool(raw_passage_text) or question_type == "comprehension_mcq"


def should_show_passage_for_question(
    *,
    question_row: dict,
    context: ContextTypes.DEFAULT_TYPE,
    force_show: bool = False,
) -> bool:
    if not question_has_passage(question_row):
        return False

    current_passage_id = get_question_passage_id(question_row)

    if force_show:
        return True

    last_passage_id_shown = str(context.user_data.get("mj_last_passage_id_shown") or "").strip()

    # If there is no passage_id, fall back to showing passage
    if not current_passage_id:
        return True

    return current_passage_id != last_passage_id_shown


def mark_passage_as_shown(question_row: dict, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["mj_last_passage_id_shown"] = get_question_passage_id(question_row)


async def clear_mockjamb_passage_message(
    *,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    passage_message_id = context.user_data.get("mj_active_passage_message_id")

    if not passage_message_id:
        return

    try:
        await context.bot.delete_message(
            chat_id=chat_id,
            message_id=int(passage_message_id),
        )
    except Exception:
        # Ignore delete errors
        pass

    context.user_data["mj_active_passage_message_id"] = None
    context.user_data["mj_last_passage_id_shown"] = ""


def store_mockjamb_passage_message_id(
    *,
    message_id: int | None,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    context.user_data["mj_active_passage_message_id"] = message_id

# --------------------------------------------
# Mock JAMB Time Expire
# ------------------------------------------
def is_mockjamb_time_expired(exam_ends_at) -> bool:
    if not exam_ends_at:
        return False

    if isinstance(exam_ends_at, str):
        try:
            exam_ends_at = datetime.fromisoformat(exam_ends_at.replace("Z", "+00:00"))
        except Exception:
            return False

    if exam_ends_at.tzinfo is None:
        exam_ends_at = exam_ends_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    return now >= exam_ends_at


async def build_mockjamb_result_from_session(
    *,
    payment_reference: str,
    course_code: str,
    subject_codes: list[str],
) -> tuple[str, InlineKeyboardMarkup] | None:
    async with get_async_session() as session:
        session_row = await get_mockjamb_session_by_payment_reference(
            session,
            payment_reference,
        )

        if not session_row:
            return None

        try:
            scores = json.loads(session_row.get("scores_json") or "{}")
        except Exception:
            scores = {}

        answered_counts = {}
        correct_counts = {}

        for subject_code in subject_codes:
            stats = await get_mockjamb_subject_result_stats(
                session,
                payment_reference=payment_reference,
                subject_code=subject_code,
            )
            answered_counts[subject_code] = int(stats.get("answered_count") or 0)
            correct_counts[subject_code] = int(stats.get("correct_count") or 0)

    message_text = build_mockjamb_final_result_text(
        course_code=course_code,
        subject_codes=subject_codes,
        scores=scores,
        answered_counts=answered_counts,
        correct_counts=correct_counts,
    )
    markup = make_mockjamb_final_result_keyboard()
    return message_text, markup


# ------------------------------------
# Finalize Mockjamb Exam Now
# ------------------------------------
async def finalize_mockjamb_exam_now(
    *,
    payment_reference: str,
    course_code: str,
    subject_codes: list[str],
) -> tuple[str, InlineKeyboardMarkup]:
    scores = {}
    answered_counts = {}
    correct_counts = {}

    async with get_async_session() as session:
        for subject_code in subject_codes:
            score_info = await calculate_mockjamb_subject_score(
                session,
                payment_reference=payment_reference,
                subject_code=subject_code,
            )
            scores[subject_code] = int(score_info.get("score_100") or 0)

            stats = await get_mockjamb_subject_result_stats(
                session,
                payment_reference=payment_reference,
                subject_code=subject_code,
            )
            answered_counts[subject_code] = int(stats.get("answered_count") or 0)
            correct_counts[subject_code] = int(stats.get("correct_count") or 0)

        await session.execute(
            text("""
                update public.mockjamb_sessions
                set
                    completed_subjects_json = :completed_subjects_json,
                    scores_json = :scores_json,
                    current_subject_code = null,
                    current_question_index = 0,
                    status = 'completed',
                    updated_at = now()
                where payment_reference = :payment_reference
            """),
            {
                "payment_reference": payment_reference,
                "completed_subjects_json": json.dumps(subject_codes),
                "scores_json": json.dumps(scores),
            },
        )
        await session.commit()

    message_text = build_mockjamb_final_result_text(
        course_code=course_code,
        subject_codes=subject_codes,
        scores=scores,
        answered_counts=answered_counts,
        correct_counts=correct_counts,
    )
    markup = make_mockjamb_final_result_keyboard()
    return message_text, markup

# ====================================================================
# Entry Handler
# ====================================================================
async def mockjamb_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    async with get_async_session() as session:
        active_session = await get_latest_active_mockjamb_session_for_user(
            session,
            user_id=int(user.id),
        )

    if active_session:
        course_code = str(active_session.get("course_code") or "").strip()

        try:
            subject_codes = json.loads(active_session.get("subject_codes_json") or "[]")
        except Exception:
            subject_codes = []

        try:
            completed_subjects = json.loads(active_session.get("completed_subjects_json") or "[]")
        except Exception:
            completed_subjects = []

        current_subject_code = str(active_session.get("current_subject_code") or "").strip() or None
        current_question_index = int(active_session.get("current_question_index") or 0)
        payment_reference = str(active_session.get("payment_reference") or "").strip()

        if course_code and subject_codes and payment_reference:
            context.user_data["mj_course_code"] = course_code
            context.user_data["mj_subject_codes"] = subject_codes
            context.user_data["mj_mode"] = "solo"
            context.user_data["mj_room_code"] = None
            context.user_data["mj_payment_reference"] = payment_reference
            context.user_data["mj_session_id"] = active_session["id"]

            text = build_mockjamb_resume_prompt_text(
                course_code=course_code,
                subject_codes=subject_codes,
                completed_subjects=completed_subjects,
                current_subject_code=current_subject_code,
                current_question_index=current_question_index,
                exam_ends_at=active_session.get("exam_ends_at"),
            )

            if is_mockjamb_time_expired(active_session.get("exam_ends_at")):
                markup = make_mockjamb_time_up_keyboard()
            else:
                markup = make_mockjamb_resume_keyboard()

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
                return

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
            active_session = await get_mockjamb_session_by_payment_reference(
                session,
                payment_reference,
            )

            if not active_session:
                return await query.message.reply_text(
                    "⚠️ Mock JAMB session not found."
                )

            if is_mockjamb_time_expired(active_session.get("exam_ends_at")):
                await session.execute(
                    text("""
                        update public.mockjamb_sessions
                        set
                            status = 'completed',
                            updated_at = now()
                        where payment_reference = :payment_reference
                    """),
                    {"payment_reference": payment_reference},
                )
                await session.commit()

                result_payload = await build_mockjamb_result_from_session(
                    payment_reference=payment_reference,
                    course_code=context.user_data.get("mj_course_code"),
                    subject_codes=mj_subject_codes,
                )

                if not result_payload:
                    return await query.message.reply_text(
                        "⏰ Mock JAMB time is up.\n\nYour exam has ended."
                    )

                message_text, markup = result_payload
                timeout_text = (
                    "⏰ *Mock JAMB time is up.*\n\n"
                    "Your exam has ended. Here is your result:\n\n"
                    f"{message_text}"
                )

                try:
                    await query.edit_message_text(
                        timeout_text,
                        parse_mode="Markdown",
                        reply_markup=markup,
                    )
                except Exception:
                    await query.message.reply_text(
                        timeout_text,
                        parse_mode="Markdown",
                        reply_markup=markup,
                    )
                return

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
    context.user_data["mj_last_passage_id_shown"] = ""
    context.user_data["mj_active_passage_message_id"] = None

    total_questions = get_mockjamb_subject_question_count(subject_code)

    question_text = build_mockjamb_question_only_text(
        subject_code=subject_code,
        question_row=current_question,
        question_number=1,
        total_questions=total_questions,
        exam_ends_at=session_row.get("exam_ends_at"),
    )

    markup = make_mockjamb_question_answer_keyboard(
        subject_code=subject_code,
        question_order=1,
    )

    if should_show_passage_for_question(
        question_row=current_question,
        context=context,
        force_show=False,
    ):
        paper_rows = result["paper_info"]["paper_rows"]
        passage_start, passage_end = get_passage_question_range(
            paper_rows=paper_rows,
            current_question_row=current_question,
        )

        passage_text = build_mockjamb_passage_text(
            subject_code=subject_code,
            question_row=current_question,
            question_start=passage_start,
            question_end=passage_end,
            total_questions=total_questions,
            exam_ends_at=session_row.get("exam_ends_at"),
        )
        mark_passage_as_shown(current_question, context)

        try:
            await query.edit_message_text(
                passage_text,
                parse_mode="MarkdownV2",
            )

            store_mockjamb_passage_message_id(
                message_id=query.message.message_id,
                context=context,
            )

            await query.message.reply_text(
                question_text,
                parse_mode="MarkdownV2",
                reply_markup=markup,
            )
        except Exception:
            sent_passage = await query.message.reply_text(
                passage_text,
                parse_mode="MarkdownV2",
            )

            store_mockjamb_passage_message_id(
                message_id=sent_passage.message_id,
                context=context,
            )

            await query.message.reply_text(
                question_text,
                parse_mode="MarkdownV2",
                reply_markup=markup,
            )
        return

    await clear_mockjamb_passage_message(
        chat_id=query.message.chat_id,
        context=context,
    )

    try:
        await query.edit_message_text(
            question_text,
            parse_mode="MarkdownV2",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            question_text,
            parse_mode="MarkdownV2",
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
            active_session = await get_mockjamb_session_by_payment_reference(
                session,
                payment_reference,
            )

            if not active_session:
                return await query.message.reply_text(
                    "⚠️ Mock JAMB session not found."
                )

            # ---------------------------------------------------
            # STALE BUTTON / ENDED EXAM GUARD
            # ---------------------------------------------------
            current_subject_code = str(active_session.get("current_subject_code") or "").strip()
            current_question_index = int(active_session.get("current_question_index") or 0)
            expected_question_order = max(1, current_question_index + 1)
            session_status = str(active_session.get("status") or "").strip().lower()

            if session_status == "completed":
                result_payload = await build_mockjamb_result_from_session(
                    payment_reference=payment_reference,
                    course_code=course_code,
                    subject_codes=subject_codes,
                )

                if not result_payload:
                    return await query.message.reply_text(
                        "⚠️ This Mock JAMB exam has already ended."
                    )

                message_text, markup = result_payload
                ended_text = (
                    "⚠️ *This Mock JAMB exam has already ended.*\n\n"
                    f"{message_text}"
                )

                try:
                    await query.edit_message_text(
                        ended_text,
                        parse_mode="Markdown",
                        reply_markup=markup,
                    )
                except Exception:
                    await query.message.reply_text(
                        ended_text,
                        parse_mode="Markdown",
                        reply_markup=markup,
                    )
                return

            if subject_code != current_subject_code or int(question_order) != int(expected_question_order):
                return await query.message.reply_text(
                    "⚠️ That question is no longer active.\n\nTap below to continue from your current live question.",
                    parse_mode="Markdown",
                    reply_markup=make_mockjamb_stale_action_keyboard(),
                )

            # ---------------------------------------------------
            # TIME EXPIRY GUARD
            # ---------------------------------------------------
            if is_mockjamb_time_expired(active_session.get("exam_ends_at")):
                message_text = (
                    "⏰ *Mock JAMB time is up.*\n\n"
                    "You can submit your exam now or end it below."
                )
                markup = make_mockjamb_time_up_keyboard()

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

                question_text = build_mockjamb_question_only_text(
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

                # ---------------------------------------------------
                # CASE 1: NEXT QUESTION STILL BELONGS TO A PASSAGE BLOCK
                # ---------------------------------------------------
                if question_has_passage(next_question):
                    # Only show the passage message again if it is a NEW passage
                    if should_show_passage_for_question(
                        question_row=next_question,
                        context=context,
                        force_show=False,
                    ):
                        paper_info = await session.execute(
                            text("""
                                select
                                    question_order,
                                    question_json
                                from public.mockjamb_subject_questions
                                where payment_reference = :payment_reference
                                and subject_code = :subject_code
                                order by question_order asc
                            """),
                            {
                                "payment_reference": payment_reference,
                                "subject_code": subject_code,
                            },
                        )
                        paper_rows = [dict(row) for row in paper_info.mappings().all()]

                        passage_start, passage_end = get_passage_question_range(
                            paper_rows=paper_rows,
                            current_question_row=next_question,
                        )

                        passage_text = build_mockjamb_passage_text(
                            subject_code=subject_code,
                            question_row=next_question,
                            question_start=passage_start,
                            question_end=passage_end,
                            total_questions=total_questions,
                            exam_ends_at=(session_row or {}).get("exam_ends_at"),
                        )
                        mark_passage_as_shown(next_question, context)

                        try:
                            await query.edit_message_text(
                                passage_text,
                                parse_mode="MarkdownV2",
                            )

                            store_mockjamb_passage_message_id(
                                message_id=query.message.message_id,
                                context=context,
                            )

                            await query.message.reply_text(
                                question_text,
                                parse_mode="MarkdownV2",
                                reply_markup=markup,
                            )
                        except Exception:
                            sent_passage = await query.message.reply_text(
                                passage_text,
                                parse_mode="MarkdownV2",
                            )

                            store_mockjamb_passage_message_id(
                                message_id=sent_passage.message_id,
                                context=context,
                            )

                            await query.message.reply_text(
                                question_text,
                                parse_mode="MarkdownV2",
                                reply_markup=markup,
                            )
                        return

                    # Same passage block continues (for example question 2, 3, 4, 5)
                    # Do NOT delete the passage. Just update the question message.
                    try:
                        await query.edit_message_text(
                            question_text,
                            parse_mode="MarkdownV2",
                            reply_markup=markup,
                        )
                    except Exception:
                        await query.message.reply_text(
                            question_text,
                            parse_mode="MarkdownV2",
                            reply_markup=markup,
                        )
                    return

                # ---------------------------------------------------
                # CASE 2: NEXT QUESTION HAS NO PASSAGE
                # ---------------------------------------------------
                await clear_mockjamb_passage_message(
                    chat_id=query.message.chat_id,
                    context=context,
                )

                try:
                    await query.edit_message_text(
                        question_text,
                        parse_mode="MarkdownV2",
                        reply_markup=markup,
                    )
                except Exception:
                    await query.message.reply_text(
                        question_text,
                        parse_mode="MarkdownV2",
                        reply_markup=markup,
                    )
                return

            if answer_result.get("status") == "completed_subject":
                await clear_mockjamb_passage_message(
                    chat_id=query.message.chat_id,
                    context=context,
                )
                
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
                context.user_data["mj_last_passage_id_shown"] = ""

                if remaining_subject_codes:
                    message_text = build_mockjamb_subject_completed_text(
                        course_code=course_code,
                        completed_subject_code=subject_code,
                        score_100=int(score_info["score_100"]),
                        remaining_subject_codes=remaining_subject_codes,
                    )
                    markup = make_mockjamb_next_subject_keyboard(remaining_subject_codes)
                else:
                    answered_counts = {}
                    correct_counts = {}

                    for code in subject_codes:
                        stats = await get_mockjamb_subject_result_stats(
                            session,
                            payment_reference=payment_reference,
                            subject_code=code,
                        )
                        answered_counts[code] = int(stats.get("answered_count") or 0)
                        correct_counts[code] = int(stats.get("correct_count") or 0)

                    message_text = build_mockjamb_final_result_text(
                        course_code=course_code,
                        subject_codes=subject_codes,
                        scores=scores,
                        answered_counts=answered_counts,
                        correct_counts=correct_counts,
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



async def mockjamb_review_open_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    mode = "all" if query.data == "mj_review_all" else "wrong"
    payment_reference = context.user_data.get("mj_payment_reference")
    subject_codes = context.user_data.get("mj_subject_codes") or []

    if not payment_reference:
        return await query.message.reply_text(
            "⚠️ Mock JAMB result session not found."
        )

    async with get_async_session() as session:
        review_rows = await get_mockjamb_review_rows(
            session,
            payment_reference=payment_reference,
            wrong_only=(mode == "wrong"),
        )

    review_rows = sort_review_rows_by_subject_order(review_rows, subject_codes)

    if not review_rows:
        if mode == "wrong":
            return await query.message.reply_text(
                "✅ No wrong answers found in this Mock JAMB result."
            )
        return await query.message.reply_text(
            "⚠️ No review items found for this Mock JAMB result."
        )

    context.user_data["mj_review_mode"] = mode
    context.user_data["mj_review_rows"] = review_rows
    context.user_data["mj_review_index"] = 0

    message_text = build_mockjamb_review_text(
        review_row=review_rows[0],
        review_index=1,
        total_reviews=len(review_rows),
    )
    markup = make_mockjamb_review_nav_keyboard(
        mode=mode,
        current_index=0,
        total_reviews=len(review_rows),
    )

    try:
        await query.edit_message_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=markup,
        )


async def mockjamb_review_nav_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, mode, index_raw = query.data.split("::", 2)
        index = int(index_raw)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid review navigation.")

    review_rows = context.user_data.get("mj_review_rows") or []
    if not review_rows:
        return await query.message.reply_text("⚠️ No review session found.")

    if index < 0 or index >= len(review_rows):
        return await query.message.reply_text("⚠️ Review item out of range.")

    context.user_data["mj_review_mode"] = mode
    context.user_data["mj_review_index"] = index

    message_text = build_mockjamb_review_text(
        review_row=review_rows[index],
        review_index=index + 1,
        total_reviews=len(review_rows),
    )
    markup = make_mockjamb_review_nav_keyboard(
        mode=mode,
        current_index=index,
        total_reviews=len(review_rows),
    )

    try:
        await query.edit_message_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=markup,
        )


async def mockjamb_back_to_result_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    payment_reference = context.user_data.get("mj_payment_reference")
    course_code = context.user_data.get("mj_course_code")
    subject_codes = context.user_data.get("mj_subject_codes") or []

    if not payment_reference or not course_code:
        return await query.message.reply_text(
            "⚠️ Mock JAMB result session not found."
        )

    async with get_async_session() as session:
        session_row = await get_mockjamb_session_by_payment_reference(
            session,
            payment_reference,
        )

        if not session_row:
            return await query.message.reply_text(
                "⚠️ Could not reload your Mock JAMB result."
            )

        try:
            scores = json.loads(session_row.get("scores_json") or "{}")
        except Exception:
            scores = {}

        answered_counts = {}
        correct_counts = {}

        for code in subject_codes:
            stats = await get_mockjamb_subject_result_stats(
                session,
                payment_reference=payment_reference,
                subject_code=code,
            )
            answered_counts[code] = int(stats.get("answered_count") or 0)
            correct_counts[code] = int(stats.get("correct_count") or 0)

    message_text = build_mockjamb_final_result_text(
        course_code=course_code,
        subject_codes=subject_codes,
        scores=scores,
        answered_counts=answered_counts,
        correct_counts=correct_counts,
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


# ---------------------------------------
# Mock JAMB Resume Exam Handler
# ---------------------------------------
async def mockjamb_resume_exam_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user_id = query.from_user.id

    async with get_async_session() as session:
        active_session = await get_latest_active_mockjamb_session_for_user(
            session,
            user_id=int(user_id),
        )

    if not active_session:
        return await query.message.reply_text(
            "⚠️ No active Mock JAMB exam was found."
        )

    payment_reference = str(active_session.get("payment_reference") or "").strip()
    course_code = str(active_session.get("course_code") or "").strip()
    current_subject_code = str(active_session.get("current_subject_code") or "").strip() or None
    current_question_index = int(active_session.get("current_question_index") or 0)
    status = str(active_session.get("status") or "").strip().lower()

    try:
        subject_codes = json.loads(active_session.get("subject_codes_json") or "[]")
    except Exception:
        subject_codes = []

    try:
        completed_subjects = json.loads(active_session.get("completed_subjects_json") or "[]")
    except Exception:
        completed_subjects = []

    try:
        scores = json.loads(active_session.get("scores_json") or "{}")
    except Exception:
        scores = {}

    if not payment_reference or not course_code or not subject_codes:
        return await query.message.reply_text(
            "⚠️ This Mock JAMB session is incomplete and cannot be resumed."
        )

    context.user_data["mj_course_code"] = course_code
    context.user_data["mj_subject_codes"] = subject_codes
    context.user_data["mj_mode"] = "solo"
    context.user_data["mj_room_code"] = None
    context.user_data["mj_payment_reference"] = payment_reference
    context.user_data["mj_session_id"] = active_session["id"]

    if is_mockjamb_time_expired(active_session.get("exam_ends_at")):
        message_text = build_mockjamb_resume_prompt_text(
            course_code=course_code,
            subject_codes=subject_codes,
            completed_subjects=completed_subjects,
            current_subject_code=current_subject_code,
            current_question_index=current_question_index,
            exam_ends_at=active_session.get("exam_ends_at"),
        )
        markup = make_mockjamb_time_up_keyboard()

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
    
    if status == "completed":
        answered_counts = {}
        correct_counts = {}

        async with get_async_session() as session:
            for code in subject_codes:
                stats = await get_mockjamb_subject_result_stats(
                    session,
                    payment_reference=payment_reference,
                    subject_code=code,
                )
                answered_counts[code] = int(stats.get("answered_count") or 0)
                correct_counts[code] = int(stats.get("correct_count") or 0)

        message_text = build_mockjamb_final_result_text(
            course_code=course_code,
            subject_codes=subject_codes,
            scores=scores,
            answered_counts=answered_counts,
            correct_counts=correct_counts,
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

    remaining_subject_codes = [
        code for code in subject_codes if code not in completed_subjects
    ]

    if current_subject_code:
        next_question_order = max(1, current_question_index + 1)

        async with get_async_session() as session:
            question_row = await get_mockjamb_subject_question_by_order(
                session,
                payment_reference=payment_reference,
                subject_code=current_subject_code,
                question_order=next_question_order,
            )

        if question_row:
            context.user_data["mj_current_subject_code"] = current_subject_code
            context.user_data["mj_current_question_order"] = next_question_order

            total_questions = get_mockjamb_subject_question_count(current_subject_code)

            question_text = build_mockjamb_question_only_text(
                subject_code=current_subject_code,
                question_row=question_row,
                question_number=next_question_order,
                total_questions=total_questions,
                exam_ends_at=active_session.get("exam_ends_at"),
            )

            markup = make_mockjamb_question_answer_keyboard(
                subject_code=current_subject_code,
                question_order=next_question_order,
            )

            if should_show_passage_for_question(
                question_row=question_row,
                context=context,
                force_show=True,
            ):
                async with get_async_session() as range_session:
                    result = await range_session.execute(
                        text("""
                            select
                                question_order,
                                question_json
                            from public.mockjamb_subject_questions
                            where payment_reference = :payment_reference
                            and subject_code = :subject_code
                            order by question_order asc
                        """),
                        {
                            "payment_reference": payment_reference,
                            "subject_code": current_subject_code,
                        },
                    )
                    paper_rows = [dict(row) for row in result.mappings().all()]

                passage_start, passage_end = get_passage_question_range(
                    paper_rows=paper_rows,
                    current_question_row=question_row,
                )
                
                passage_text = build_mockjamb_passage_text(
                    subject_code=current_subject_code,
                    question_row=question_row,
                    question_start=passage_start,
                    question_end=passage_end,
                    total_questions=total_questions,
                    exam_ends_at=active_session.get("exam_ends_at"),
                )
                mark_passage_as_shown(question_row, context)

                try:
                    await query.edit_message_text(
                        passage_text,
                        parse_mode="MarkdownV2",
                    )
                    await query.message.reply_text(
                        question_text,
                        parse_mode="MarkdownV2",
                        reply_markup=markup,
                    )
                except Exception:
                    await query.message.reply_text(
                        passage_text,
                        parse_mode="MarkdownV2",
                    )
                    await query.message.reply_text(
                        question_text,
                        parse_mode="MarkdownV2",
                        reply_markup=markup,
                    )
                return

            try:
                await query.edit_message_text(
                    question_text,
                    parse_mode="MarkdownV2",
                    reply_markup=markup,
                )
            except Exception:
                await query.message.reply_text(
                    question_text,
                    parse_mode="MarkdownV2",
                    reply_markup=markup,
                )
            return

    if remaining_subject_codes:
        message_text = build_mockjamb_continue_subject_choice_text(
            course_code=course_code,
            remaining_subject_codes=remaining_subject_codes,
        )
        markup = make_mockjamb_next_subject_keyboard(remaining_subject_codes)

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

    answered_counts = {}
    correct_counts = {}

    async with get_async_session() as session:
        for code in subject_codes:
            stats = await get_mockjamb_subject_result_stats(
                session,
                payment_reference=payment_reference,
                subject_code=code,
            )
            answered_counts[code] = int(stats.get("answered_count") or 0)
            correct_counts[code] = int(stats.get("correct_count") or 0)

    message_text = build_mockjamb_final_result_text(
        course_code=course_code,
        subject_codes=subject_codes,
        scores=scores,
        answered_counts=answered_counts,
        correct_counts=correct_counts,
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


# -------------------------------------------
# MockJAMB Submit Exam Confirmation
# -------------------------------------------
async def mockjamb_submit_exam_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    text = build_mockjamb_submit_exam_confirm_text()
    markup = make_mockjamb_submit_exam_confirm_keyboard()

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


async def mockjamb_submit_exam_no_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    payment_reference = context.user_data.get("mj_payment_reference")
    if not payment_reference:
        return await query.message.reply_text(
            "⚠️ No active Mock JAMB exam session found."
        )

    await mockjamb_resume_exam_handler(update, context)


async def mockjamb_submit_exam_yes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    payment_reference = context.user_data.get("mj_payment_reference")
    course_code = context.user_data.get("mj_course_code")
    subject_codes = context.user_data.get("mj_subject_codes") or []

    if not payment_reference or not course_code or not subject_codes:
        return await query.message.reply_text(
            "⚠️ No active Mock JAMB exam session found."
        )

    try:
        message_text, markup = await finalize_mockjamb_exam_now(
            payment_reference=payment_reference,
            course_code=course_code,
            subject_codes=subject_codes,
        )
    except Exception as e:
        logger.exception(
            "Failed to submit Mock JAMB exam early | tx_ref=%s | err=%s",
            payment_reference,
            e,
        )
        return await query.message.reply_text(
            "⚠️ Could not submit your exam right now. Please try again."
        )

    submit_text = (
        "✅ *Mock JAMB / UTME submitted successfully.*\n\n"
        f"{message_text}"
    )

    try:
        await query.edit_message_text(
            submit_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            submit_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )


async def mockjamb_end_exam_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    payment_reference = context.user_data.get("mj_payment_reference")
    course_code = context.user_data.get("mj_course_code")
    subject_codes = context.user_data.get("mj_subject_codes") or []

    if not payment_reference or not course_code or not subject_codes:
        return await query.message.reply_text(
            "⚠️ No active Mock JAMB exam session found."
        )

    try:
        message_text, markup = await finalize_mockjamb_exam_now(
            payment_reference=payment_reference,
            course_code=course_code,
            subject_codes=subject_codes,
        )
    except Exception as e:
        logger.exception(
            "Failed to end Mock JAMB exam | tx_ref=%s | err=%s",
            payment_reference,
            e,
        )
        return await query.message.reply_text(
            "⚠️ Could not end your exam right now. Please try again."
        )

    end_text = (
        "🛑 *Mock JAMB exam ended.*\n\n"
        f"{message_text}"
    )

    try:
        await query.edit_message_text(
            end_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            end_text,
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
    application.add_handler(CallbackQueryHandler(mockjamb_submit_exam_confirm_handler, pattern=r"^mj_submit_exam_confirm$"))
    application.add_handler(CallbackQueryHandler(mockjamb_submit_exam_yes_handler, pattern=r"^mj_submit_exam_yes$"))
    application.add_handler(CallbackQueryHandler(mockjamb_submit_exam_no_handler, pattern=r"^mj_submit_exam_no$"))
    application.add_handler(CallbackQueryHandler(mockjamb_end_exam_handler, pattern=r"^mj_end_exam$"))
    application.add_handler(CallbackQueryHandler(mockjamb_answer_handler, pattern=r"^mj_ans::"))
    application.add_handler(CallbackQueryHandler(mockjamb_return_to_exam_ready_handler, pattern=r"^payok_mockjamb_return$"))
    application.add_handler(CallbackQueryHandler(mockjamb_resume_exam_handler, pattern=r"^mj_resume_exam$"))
    application.add_handler(CallbackQueryHandler(mockjamb_review_open_handler, pattern=r"^mj_review_(all|wrong)$"))
    application.add_handler(CallbackQueryHandler(mockjamb_review_nav_handler, pattern=r"^mj_review_nav::"))
    application.add_handler(CallbackQueryHandler(mockjamb_back_to_result_handler, pattern=r"^mj_back_to_result$"))

