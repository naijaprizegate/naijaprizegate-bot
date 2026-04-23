# ====================================================================
# handlers/mockjamb.py
# ====================================================================

import json
import math
import logging
import os
import urllib.parse

from datetime import datetime, timezone
from sqlalchemy import text
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes
from telegram.error import BadRequest

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

from services.mockjamb_room_service import (
    create_mockjamb_room,
    add_mockjamb_room_player,
    get_mockjamb_room_player,
    list_mockjamb_room_players,
    build_mockjamb_invite_link,
    build_mockjamb_waiting_room_text,
    get_mockjamb_room_by_code,
    update_mockjamb_room_player_setup,
    count_mockjamb_room_paid_players,
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

def make_mockjamb_friends_payment_keyboard(course_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"💳 Pay ₦{MOCKJAMB_SOLO_FEE}", callback_data="mj_pay_friends")],
            [InlineKeyboardButton("⬅️ Back", callback_data="mj_mode_friends")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_mockjamb_invitee_count_keyboard(course_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1 Friend", callback_data="mj_invites_1"),
                InlineKeyboardButton("2 Friends", callback_data="mj_invites_2"),
            ],
            [
                InlineKeyboardButton("3 Friends", callback_data="mj_invites_3"),
                InlineKeyboardButton("4 Friends", callback_data="mj_invites_4"),
            ],
            [
                InlineKeyboardButton("5 Friends", callback_data="mj_invites_5"),
                InlineKeyboardButton("6 Friends", callback_data="mj_invites_6"),
            ],
            [
                InlineKeyboardButton("7 Friends", callback_data="mj_invites_7"),
                InlineKeyboardButton("8 Friends", callback_data="mj_invites_8"),
            ],
            [
                InlineKeyboardButton("9 Friends", callback_data="mj_invites_9"),
                InlineKeyboardButton("10 Friends", callback_data="mj_invites_10"),
            ],
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


def make_mockjamb_room_waiting_keyboard(
    *,
    is_host: bool,
    room_status: str,
    room_code: str | None = None,
    has_course: bool = False,
    has_paid: bool = False,
    is_ready: bool = False,
) -> InlineKeyboardMarkup:
    rows = []

    share_url = None
    safe_room_code = str(room_code or "").strip().upper()

    if safe_room_code:
        bot_username = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")
        invite_link = f"https://t.me/{bot_username}?start=jmroom_{safe_room_code}"

        share_text = (
            "📝 Join my Mock JAMB / UTME room on NaijaPrizeGate!\n\n"
            f"Room Code: {safe_room_code}\n"
            "Tap the link below to join:"
        )

        share_url = (
            "https://t.me/share/url?"
            f"url={urllib.parse.quote(invite_link)}"
            f"&text={urllib.parse.quote(share_text)}"
        )

    if room_status == "waiting":
        if is_host:
            if share_url:
                rows.append([
                    InlineKeyboardButton("📤 Share Room Link / Code", url=share_url)
                ])
            else:
                rows.append([
                    InlineKeyboardButton("📤 Share Room Link / Code", callback_data="mjr_share")
                ])

            rows.append([
                InlineKeyboardButton("🔄 Refresh Room", callback_data="mjr_refresh")
            ])
            rows.append([
                InlineKeyboardButton("▶️ Start Match", callback_data="mjr_start")
            ])
            rows.append([
                InlineKeyboardButton("⬅️ Back to Mode Selection", callback_data="mjr_back_to_mode")
            ])

        else:
            if not has_course:
                rows.append([
                    InlineKeyboardButton("📚 Choose My Course", callback_data="mjr_pick_course")
                ])
            elif not has_paid:
                rows.append([
                    InlineKeyboardButton("💳 Pay Now", callback_data="mjr_pay_friend")
                ])
            elif not is_ready:
                rows.append([
                    InlineKeyboardButton("✅ Ready", callback_data="mjr_ready")
                ])
            else:
                rows.append([
                    InlineKeyboardButton("✅ You Are Ready", callback_data="mjr_ready_done")
                ])

            rows.append([
                InlineKeyboardButton("🔄 Refresh Room", callback_data="mjr_refresh")
            ])

    elif room_status == "locked":
        rows.append([
            InlineKeyboardButton("🔄 Refresh Room", callback_data="mjr_refresh")
        ])

        if is_host:
            if share_url:
                rows.append([
                    InlineKeyboardButton("📤 Share Room Link / Code", url=share_url)
                ])
            else:
                rows.append([
                    InlineKeyboardButton("📤 Share Room Link / Code", callback_data="mjr_share")
                ])

            rows.append([
                InlineKeyboardButton("⬅️ Back to Mode Selection", callback_data="mjr_back_to_mode")
            ])

    elif room_status == "in_progress":
        rows.append([
            InlineKeyboardButton("📝 Resume Exam", callback_data="mock:jamb")
        ])

    rows.append([
        InlineKeyboardButton("🚪 Leave Room", callback_data="mjr_leave")
    ])
    rows.append([
        InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")
    ])

    return InlineKeyboardMarkup(rows)


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



# ------------------------------------------------
# Extract Mock JAMB Room Code From Start Payload
# ------------------------------------------------
def extract_mockjamb_room_code_from_start_payload(payload: str | None) -> str | None:
    raw = str(payload or "").strip()
    prefix = "jmroom_"

    if not raw.startswith(prefix):
        return None

    room_code = raw[len(prefix):].strip().upper()
    if not room_code:
        return None

    return room_code


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

def build_mockjamb_room_share_text(room_code: str, invite_link: str) -> str:
    return (
        "📤 *Share Mock JAMB Room*\n\n"
        f"*Room Code:* `{room_code}`\n\n"
        f"*Invite Link:* {invite_link}\n\n"
        "Friends can join using either the room code or the invite link."
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

def build_mockjamb_friends_payment_text(course_code: str, invitee_count: int | None = None) -> str:
    course = get_course_by_code(course_code)
    if not course:
        return "⚠️ Course not found."

    subjects = get_course_subjects(course_code)
    subject_lines = "\n".join([f"• {subject['name']}" for subject in subjects])

    details_block = ""
    if invitee_count and invitee_count > 0:
        required_player_count = invitee_count + 1
        details_block = (
            f"*Friends to Invite:* {invitee_count}\n"
            f"*Total Players Required:* {required_player_count}\n\n"
        )

    return (
        "👥 *Mock JAMB Multiplayer Host Access*\n\n"
        f"*Course:* {course['course_name']}\n\n"
        "*Your subjects:*\n"
        f"{subject_lines}\n\n"
        f"{details_block}"
        f"*Host Fee:* ₦{MOCKJAMB_SOLO_FEE}\n\n"
        "You need to pay first before your multiplayer room can be created.\n\n"
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

    # ---------------------------------------------------
    # ROOM FLOW: save player's course/subjects into room
    # and return to waiting room instead of mode selection
    # ---------------------------------------------------
    if context.user_data.get("mjr_pick_course_flow"):
        user = update.effective_user
        room_code = str(context.user_data.get("mjr_room_code") or "").strip().upper()

        if not user or not room_code:
            return await query.message.reply_text(
                "⚠️ Room course setup could not be completed."
            )

        subject_codes = [subject["code"] for subject in subjects]

        bot_username = ""
        try:
            me = await context.bot.get_me()
            bot_username = me.username or ""
        except Exception:
            bot_username = ""

        async with get_async_session() as session:
            try:
                room = await get_mockjamb_room_by_code(
                    session,
                    room_code=room_code,
                )
                if not room:
                    return await query.message.reply_text(
                        "⚠️ Room not found."
                    )

                await update_mockjamb_room_player_setup(
                    session,
                    room_code=room_code,
                    user_id=int(user.id),
                    course_code=course_code,
                    subject_codes_json=json.dumps(subject_codes),
                )

                players = await list_mockjamb_room_players(
                    session,
                    room_code=room_code,
                )

                await session.commit()

            except Exception as e:
                await session.rollback()
                logger.exception("Failed to save room player course setup | err=%s", e)
                return await query.message.reply_text(
                    "⚠️ Could not save your room course setup right now."
                )

        host_user_id = int((room or {}).get("host_user_id") or 0)

        context.user_data["mjr_pick_course_flow"] = False
        context.user_data["mj_mode"] = "friends"
        context.user_data["mj_room_code"] = room_code
        context.user_data["mjr_is_host"] = int(user.id) == host_user_id

        invite_link = build_mockjamb_invite_link(bot_username, room_code)

        text = build_mockjamb_waiting_room_text(
            room_code=room_code,
            invite_link=invite_link,
            room_status="waiting",
            players=players,
            host_user_id=host_user_id,
        )

        markup = make_mockjamb_room_waiting_keyboard(
            is_host=bool(context.user_data["mjr_is_host"]),
            room_status="waiting",
            room_code=room_code,
            has_course=True,
            has_paid=False,
            is_ready=False,
        )

        try:
            await query.edit_message_text(
                text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except Exception:
            await query.message.reply_text(
                text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )

        try:
            await refresh_mockjamb_host_waiting_room(context, room_code)
        except Exception:
            logger.exception(
                "Failed to auto-refresh host waiting room after course setup | room_code=%s",
                room_code,
            )

        return

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

    text = (
        "👥 *Mock JAMB Multiplayer Setup*\n\n"
        "First, choose how many friends you want to invite.\n\n"
        "After that, you will continue to payment."
    )
    markup = make_mockjamb_invitee_count_keyboard(course_code)

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


# -----------------------------------------------
# Mock JAMB Invitee Count Handler
# -----------------------------------------------
async def mockjamb_invitee_count_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = str(query.data or "").strip()

    try:
        invitee_count = int(data.replace("mj_invites_", "", 1))
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid invitee selection."
        )

    if invitee_count < 1:
        return await query.message.reply_text(
            "⚠️ Invitee count must be at least 1."
        )

    context.user_data["mj_invitee_count"] = invitee_count
    context.user_data["mj_mode"] = "friends"

    course_code = context.user_data.get("mj_course_code")
    if not course_code:
        return await query.message.reply_text(
            "⚠️ No saved course found. Please choose your course again.",
            reply_markup=make_mockjamb_welcome_keyboard(),
        )

    text = build_mockjamb_friends_payment_text(course_code, invitee_count)
    markup = make_mockjamb_friends_payment_keyboard(course_code)

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

# --------------------------------------------------
# Mock JAMB Room Share Handler
# -------------------------------------------------
async def mockjamb_room_share_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    room_code = str(context.user_data.get("mjr_room_code") or context.user_data.get("mj_room_code") or "").strip().upper()
    if not room_code:
        return await query.message.reply_text(
            "⚠️ No active room was found."
        )

    bot_username = ""
    try:
        me = await context.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""

    invite_link = build_mockjamb_invite_link(bot_username, room_code)
    text = build_mockjamb_room_share_text(room_code, invite_link)

    try:
        await query.message.reply_text(
            text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    except Exception:
        await query.message.reply_text(
            f"Room Code: {room_code}\nInvite Link: {invite_link}",
            disable_web_page_preview=True,
        )


# -------------------------------------------
# Mock JAMB Room Back to Mode Handler
# -------------------------------------------
async def mockjamb_room_back_to_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    course_code = context.user_data.get("mj_course_code")
    if not course_code:
        return await query.message.reply_text(
            "⚠️ No saved course found. Please choose your course again.",
            reply_markup=make_mockjamb_welcome_keyboard(),
        )

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



async def refresh_mockjamb_host_waiting_room(
    context: ContextTypes.DEFAULT_TYPE,
    room_code: str,
) -> None:
    room_code = str(room_code or "").strip().upper()
    if not room_code:
        return

    bot_username = ""
    try:
        me = await context.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""

    async with get_async_session() as session:
        room = await get_mockjamb_room_by_code(
            session,
            room_code=room_code,
        )
        if not room:
            return

        players = await list_mockjamb_room_players(
            session,
            room_code=room_code,
        )

    host_user_id = int(room.get("host_user_id") or 0)
    if not host_user_id:
        return

    invite_link = build_mockjamb_invite_link(bot_username, room_code)
    room_status = str(room.get("status") or "waiting").strip()

    text = build_mockjamb_waiting_room_text(
        room_code=room_code,
        invite_link=invite_link,
        room_status=room_status,
        players=players,
        host_user_id=host_user_id,
    )

    markup = make_mockjamb_room_waiting_keyboard(
        is_host=True,
        room_status=room_status,
    )

    host_waiting_message_id = room.get("host_waiting_message_id")

    if host_waiting_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=host_user_id,
                message_id=int(host_waiting_message_id),
                text=text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            return

        except BadRequest as e:
            error_text = str(e)

            if "Message is not modified" in error_text:
                return

            logger.exception(
                "Failed to edit host waiting room message | room_code=%s | host_user_id=%s | err=%s",
                room_code,
                host_user_id,
                error_text,
            )
            return

        except Exception as e:
            logger.exception(
                "Unexpected host waiting room edit error | room_code=%s | host_user_id=%s | err=%s",
                room_code,
                host_user_id,
                e,
            )
            return

    try:
        sent = await context.bot.send_message(
            chat_id=host_user_id,
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )

        async with get_async_session() as session:
            await session.execute(
                text("""
                    update public.mockjamb_rooms
                    set
                        host_waiting_message_id = :message_id,
                        updated_at = now()
                    where upper(room_code) = :room_code
                """),
                {
                    "room_code": room_code,
                    "message_id": int(sent.message_id),
                },
            )
            await session.commit()

    except Exception as e:
        logger.exception(
            "Failed to send initial host waiting room message | room_code=%s | host_user_id=%s | err=%s",
            room_code,
            host_user_id,
            e,
        )


async def mockjamb_room_refresh_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user:
        return

    room_code = context.user_data.get("mjr_room_code")
    if not room_code:
        return await query.answer("No active room found.", show_alert=True)

    bot_username = ""
    try:
        me = await context.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""

    async with get_async_session() as session:
        room = await get_mockjamb_room_by_code(
            session,
            room_code=room_code,
        )
        if not room:
            return await query.answer("Room not found.", show_alert=True)

        players = await list_mockjamb_room_players(
            session,
            room_code=room_code,
        )

    invite_link = build_mockjamb_invite_link(bot_username, room_code)
    is_host = int(room.get("host_user_id") or 0) == int(user.id)
    room_status = str(room.get("status") or "waiting").strip()

    text = build_mockjamb_waiting_room_text(
        room_code=room_code,
        invite_link=invite_link,
        room_status=room_status,
        players=players,
        host_user_id=int(room.get("host_user_id") or 0),
    )

    current_player = None
    for player in players:
        if int(player.get("user_id") or 0) == int(user.id):
            current_player = player
            break

    has_course = bool(str(current_player.get("course_code") or "").strip())
    has_paid = bool(current_player.get("has_paid"))
    is_ready = bool(current_player.get("is_ready"))

    
    markup = make_mockjamb_room_waiting_keyboard(
        is_host=is_host,
        room_status=room_status,
        room_code=room_code,
        has_course=has_course,
        has_paid=has_paid,
        is_ready=is_ready,
    )

    try:
        await query.edit_message_text(
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        logger.exception(
            "Failed to edit room refresh message | room_code=%s | user_id=%s",
            room_code,
            int(user.id),
        )
    except Exception:
        logger.exception(
            "Unexpected refresh error | room_code=%s | user_id=%s",
            room_code,
            int(user.id),
        )


async def mockjamb_room_join_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user:
        return

    try:
        _, room_code = query.data.split("::", 1)
    except Exception:
        return await query.answer("Invalid room join request.", show_alert=True)

    room_code = str(room_code or "").strip().upper()
    if not room_code:
        return await query.answer("Invalid room code.", show_alert=True)

    bot_username = ""
    try:
        me = await context.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""

    async with get_async_session() as session:
        room = await get_mockjamb_room_by_code(session, room_code=room_code)
        if not room:
            return await query.edit_message_text("⚠️ Room not found.")

        room_status = str(room.get("status") or "").strip().lower()
        if room_status != "waiting":
            return await query.edit_message_text(
                "⚠️ This room is no longer open for joining."
            )

        existing_player = await get_mockjamb_room_player(
            session,
            room_code=room_code,
            user_id=int(user.id),
        )

        if not existing_player:
            await add_mockjamb_room_player(
                session,
                room_code=room_code,
                user_id=int(user.id),
                course_code=None,
                subject_codes_json="[]",
            )

        players = await list_mockjamb_room_players(
            session,
            room_code=room_code,
        )

        await session.commit()

    context.user_data["mjr_room_code"] = room_code
    context.user_data["mjr_is_host"] = int(room.get("host_user_id") or 0) == int(user.id)

    invite_link = build_mockjamb_invite_link(bot_username, room_code)

    text = build_mockjamb_waiting_room_text(
        room_code=room_code,
        invite_link=invite_link,
        room_status="waiting",
        players=players,
        host_user_id=int(room.get("host_user_id") or 0),
    )

    current_player = None
    for player in players:
        if int(player.get("user_id") or 0) == int(user.id):
            current_player = player
            break

    has_course = bool(str((current_player or {}).get("course_code") or "").strip())
    has_paid = bool((current_player or {}).get("has_paid"))
    is_ready = bool((current_player or {}).get("is_ready"))

    markup = make_mockjamb_room_waiting_keyboard(
        is_host=bool(context.user_data["mjr_is_host"]),
        room_status="waiting",
        room_code=room_code,
        has_course=has_course,
        has_paid=has_paid,
        is_ready=is_ready,
    )

    try:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception:
        await query.message.reply_text(
            text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )

    try:
        await refresh_mockjamb_host_waiting_room(context, room_code)
    except Exception:
        logger.exception(
            "Failed to auto-refresh host waiting room after join | room_code=%s",
            room_code,
        )


async def mockjamb_room_ready_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user:
        return

    room_code = str(context.user_data.get("mjr_room_code") or "").strip().upper()
    if not room_code:
        return await query.answer("No active room found.", show_alert=True)

    async with get_async_session() as session:
        room = await get_mockjamb_room_by_code(
            session,
            room_code=room_code,
        )
        if not room:
            return await query.answer("Room not found.", show_alert=True)

        player = await get_mockjamb_room_player(
            session,
            room_code=room_code,
            user_id=int(user.id),
        )
        if not player:
            return await query.answer("You are not in this room.", show_alert=True)

        payment_status = str(player.get("payment_status") or "").strip().lower()
        if payment_status != "successful":
            return await query.answer("Please pay first before getting ready.", show_alert=True)

        course_code = str(player.get("course_code") or "").strip()
        subject_codes_json = str(player.get("subject_codes_json") or "[]")
        has_course = bool(course_code and subject_codes_json and subject_codes_json != "[]")

        if not has_course:
            return await query.answer("Please choose your course first.", show_alert=True)

        already_ready = bool(player.get("is_ready"))
        if not already_ready:
            await session.execute(
                text("""
                    update public.mockjamb_room_players
                    set
                        is_ready = true,
                        ready_at = coalesce(ready_at, now()),
                        updated_at = now()
                    where upper(room_code) = :room_code
                      and user_id = :user_id
                """),
                {
                    "room_code": room_code,
                    "user_id": int(user.id),
                },
            )
            await session.commit()

        room = await get_mockjamb_room_by_code(
            session,
            room_code=room_code,
        )
        players = await list_mockjamb_room_players(
            session,
            room_code=room_code,
        )

    bot_username = ""
    try:
        me = await context.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""

    invite_link = build_mockjamb_invite_link(bot_username, room_code)
    room_status = str((room or {}).get("status") or "waiting").strip()
    host_user_id = int((room or {}).get("host_user_id") or 0)

    text = build_mockjamb_waiting_room_text(
        room_code=room_code,
        invite_link=invite_link,
        room_status=room_status,
        players=players,
        host_user_id=host_user_id,
    )

    markup = make_mockjamb_room_waiting_keyboard(
        is_host=False,
        room_status=room_status,
        room_code=room_code,
        has_course=True,
        has_paid=True,
        is_ready=True,
    )

    try:
        await query.edit_message_text(
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.exception(
                "Failed to update invitee ready state message | room_code=%s | user_id=%s",
                room_code,
                int(user.id),
            )
    except Exception:
        logger.exception(
            "Unexpected ready handler error | room_code=%s | user_id=%s",
            room_code,
            int(user.id),
        )

    try:
        await refresh_mockjamb_host_waiting_room(context, room_code)
    except Exception:
        logger.exception(
            "Failed to auto-refresh host after player ready | room_code=%s",
            room_code,
        )


async def mockjamb_room_pick_course_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    room_code = str(context.user_data.get("mjr_room_code") or "").strip().upper()
    if not room_code:
        return await query.message.reply_text(
            "⚠️ No active room found. Please re-open your room invite."
        )

    context.user_data["mj_mode"] = "friends"
    context.user_data["mj_room_code"] = room_code
    context.user_data["mjr_pick_course_flow"] = True

    page = 1
    courses = get_course_subject_map()
    total_courses = len(courses)
    total_pages = max(1, math.ceil(total_courses / COURSES_PER_PAGE))

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


# -------------------------------------------
# Mock JAMB Room Pay Friend Handler
# -------------------------------------------
async def mockjamb_room_pay_friend_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user:
        return

    room_code = str(context.user_data.get("mjr_room_code") or "").strip().upper()
    if not room_code:
        return await query.message.reply_text(
            "⚠️ No active room found. Please re-open your room invite."
        )

    async with get_async_session() as session:
        room = await get_mockjamb_room_by_code(
            session,
            room_code=room_code,
        )
        if not room:
            return await query.message.reply_text(
                "⚠️ Room not found."
            )

        player = await get_mockjamb_room_player(
            session,
            room_code=room_code,
            user_id=int(user.id),
        )
        if not player:
            return await query.message.reply_text(
                "⚠️ You are not yet in this room."
            )

        course_code = str(player.get("course_code") or "").strip()
        subject_codes_json = str(player.get("subject_codes_json") or "[]")

        try:
            subject_codes = json.loads(subject_codes_json)
        except Exception:
            subject_codes = []

        if not course_code or not subject_codes:
            return await query.message.reply_text(
                "⚠️ Please choose your course first before payment."
            )

        payment_status = str(player.get("payment_status") or "").strip().lower()
        if payment_status == "successful":
            return await query.message.reply_text(
                "✅ You have already paid for this room."
            )

        amount = MOCKJAMB_SOLO_FEE
        tg_id = int(user.id)
        username = user.username or f"user_{tg_id}"
        email = f"{username}@naijaprizegate.ng"

        tx_ref = build_tx_ref("MOCKJAMBROOMFRIEND")

        await create_pending_mockjamb_payment(
            session,
            payment_reference=tx_ref,
            user_id=tg_id,
            amount_paid=amount,
            course_code=course_code,
            subject_codes_json=subject_codes_json,
            exam_mode="room_friend",
            invitee_count=0,
            required_player_count=0,
            room_code=room_code,
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
            "exam_mode": "room_friend",
            "room_code": room_code,
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

    message_text = (
        "💳 *Mock JAMB Room Payment*\n\n"
        f"*Room Code:* {room_code}\n"
        f"*Amount:* ₦{amount}\n\n"
        "Complete your payment to join this room as an active player."
    )

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Pay Securely", url=checkout_url)],
            [InlineKeyboardButton("⬅️ Back to Room", callback_data="mjr_refresh")],
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
# Mock JAMB Pay Friends Handler
# ----------------------------------------------
async def mockjamb_pay_friends_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    course_code = context.user_data.get("mj_course_code")
    subject_codes = context.user_data.get("mj_subject_codes") or []
    invitee_count = int(context.user_data.get("mj_invitee_count") or 0)
    required_player_count = invitee_count + 1

    if not course_code or not subject_codes:
        return await query.message.reply_text(
            "⚠️ Your Mock JAMB setup is incomplete.\n\nPlease choose your course again.",
            reply_markup=make_mockjamb_welcome_keyboard(),
        )

    if invitee_count <= 0:
        return await query.message.reply_text(
            "⚠️ Please choose how many friends you want to invite first."
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

    tx_ref = build_tx_ref("MOCKJAMBROOM")
    subject_codes_json = json.dumps(subject_codes)

    async with get_async_session() as session:
        await create_pending_mockjamb_payment(
            session,
            payment_reference=tx_ref,
            user_id=tg_id,
            amount_paid=amount,
            course_code=course_code,
            subject_codes_json=subject_codes_json,
            exam_mode="friends",
            invitee_count=invitee_count,
            required_player_count=required_player_count,
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
            "exam_mode": "friends",
            "invitee_count": str(invitee_count),
            "required_player_count": str(required_player_count),
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
        "👥 *Mock JAMB Multiplayer Host Payment*\n\n"
        f"*Course:* {course['course_name']}\n\n"
        "*Subjects:*\n"
        f"{subject_names}\n\n"
        f"*Friends to Invite:* {invitee_count}\n"
        f"*Total Players Required:* {required_player_count}\n"
        f"*Amount:* ₦{amount}\n\n"
        "Complete this payment to unlock and create your multiplayer room."
    )

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Pay Securely", url=checkout_url)],
            [InlineKeyboardButton("⬅️ Back", callback_data="mj_mode_friends")],
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
    async def send_response(
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup=None,
        disable_web_page_preview: bool = False,
    ):
        if update.callback_query:
            query = update.callback_query
            try:
                await query.answer()
            except Exception:
                pass

            try:
                await query.edit_message_text(
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_web_page_preview=disable_web_page_preview,
                )
            except Exception:
                await query.message.reply_text(
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_web_page_preview=disable_web_page_preview,
                )
            return

        if update.message:
            await update.message.reply_text(
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )

    if not tx_ref:
        await send_response("⚠️ Payment reference is missing. Please try again.")
        return

    async with get_async_session() as session:
        payment = await get_mockjamb_payment(session, tx_ref)

        if not payment:
            await send_response(
                "⚠️ Mock JAMB payment record not found. Please contact support if payment was deducted."
            )
            return

        payment_status = str(payment.get("payment_status") or "").strip().lower()
        if payment_status != "successful":
            await send_response(
                "⚠️ Your Mock JAMB payment is not yet marked successful. Please wait a moment and try again."
            )
            return

        course_code = str(payment.get("course_code") or "").strip()
        subject_codes_json = payment.get("subject_codes_json") or "[]"
        exam_mode = str(payment.get("exam_mode") or "solo").strip().lower()
        invitee_count = int(payment.get("invitee_count") or 0)
        required_player_count = int(payment.get("required_player_count") or 0)
        payer_user_id = int(payment.get("user_id") or 0)
        payment_room_code = str(payment.get("room_code") or "").strip().upper()

        try:
            subject_codes = json.loads(subject_codes_json)
        except Exception:
            subject_codes = []

        if not isinstance(subject_codes, list):
            subject_codes = []

        if not course_code or not subject_codes or payer_user_id <= 0:
            await send_response(
                "⚠️ Your saved Mock JAMB exam data is incomplete. Please contact support."
            )
            return

        # ============================================================
        # FRIENDS / MULTIPLAYER FLOW
        # ============================================================
        if exam_mode == "friends":
            room_code = ""
            players = []
            total_required_players = (
                required_player_count
                if required_player_count > 0
                else max(2, invitee_count + 1)
            )

            try:
                room = await create_mockjamb_room(
                    session,
                    host_user_id=payer_user_id,
                    duration_minutes=120,
                    required_player_count=total_required_players,
                )

                room_code = str((room or {}).get("room_code") or "").strip().upper()
                if not room_code:
                    raise ValueError("Room code was not created.")

                existing_host_player = await get_mockjamb_room_player(
                    session,
                    room_code=room_code,
                    user_id=payer_user_id,
                )

                if existing_host_player:
                    await update_mockjamb_room_player_setup(
                        session,
                        room_code=room_code,
                        user_id=payer_user_id,
                        course_code=course_code,
                        subject_codes_json=subject_codes_json,
                        is_host=True,
                        has_paid=True,
                    )
                else:
                    await add_mockjamb_room_player(
                        session,
                        room_code=room_code,
                        user_id=payer_user_id,
                        course_code=course_code,
                        subject_codes_json=subject_codes_json,
                        is_host=True,
                        has_paid=True,
                    )

                players = await list_mockjamb_room_players(
                    session,
                    room_code=room_code,
                )

                await session.commit()

            except Exception as e:
                await session.rollback()
                logger.exception(
                    "Failed to create paid multiplayer room | tx_ref=%s | exam_mode=%s | user_id=%s | err=%s",
                    tx_ref,
                    exam_mode,
                    payer_user_id,
                    e,
                )
                await send_response(
                    "⚠️ Payment succeeded, but room creation failed. Please try /start again. If it still fails, contact support."
                )
                return

            context.user_data["mj_course_code"] = course_code
            context.user_data["mj_subject_codes"] = subject_codes
            context.user_data["mj_mode"] = "friends"
            context.user_data["mj_room_code"] = room_code
            context.user_data["mjr_room_code"] = room_code
            context.user_data["mjr_is_host"] = True
            context.user_data["mj_payment_reference"] = tx_ref
            context.user_data["mj_session_id"] = None
            context.user_data["mj_invitee_count"] = invitee_count
            context.user_data["mj_required_player_count"] = total_required_players

            bot_username = ""
            try:
                me = await context.bot.get_me()
                bot_username = me.username or ""
            except Exception:
                bot_username = ""

            invite_link = build_mockjamb_invite_link(bot_username, room_code)

            message_text = build_mockjamb_waiting_room_text(
                room_code=room_code,
                invite_link=invite_link,
                room_status="waiting",
                players=players,
                host_user_id=payer_user_id,
            )

            markup = make_mockjamb_room_waiting_keyboard(
                is_host=True,
                room_status="waiting",
                room_code=room_code,
                has_course=True,
                has_paid=True,
                is_ready=False,
            )

            sent_message = None

            if update.callback_query:
                query = update.callback_query
                try:
                    await query.answer()
                except Exception:
                    pass

                try:
                    sent_message = await query.edit_message_text(
                        text=message_text,
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    sent_message = await query.message.reply_text(
                        text=message_text,
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )

            elif update.message:
                sent_message = await update.message.reply_text(
                    text=message_text,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )

            if sent_message:
                async with get_async_session() as save_session:
                    await save_session.execute(
                        text("""
                            update public.mockjamb_rooms
                            set
                                host_waiting_message_id = :message_id,
                                updated_at = now()
                            where room_code = :room_code
                        """),
                        {
                            "message_id": int(sent_message.message_id),
                            "room_code": room_code,
                        },
                    )
                    await save_session.commit()

            return

        # ============================================================
        # ROOM INVITEE PAYMENT FLOW
        # ============================================================
        if exam_mode == "room_friend":
            room_code = payment_room_code

            if not room_code:
                await send_response(
                    "⚠️ Payment succeeded, but your room code is missing from the payment record. Please contact support."
                )
                return

            try:
                room = await get_mockjamb_room_by_code(
                    session,
                    room_code=room_code,
                )
                if not room:
                    await send_response(
                        "⚠️ Payment succeeded, but the room was not found."
                    )
                    return

                existing_player = await get_mockjamb_room_player(
                    session,
                    room_code=room_code,
                    user_id=payer_user_id,
                )

                if existing_player:
                    await update_mockjamb_room_player_setup(
                        session,
                        room_code=room_code,
                        user_id=payer_user_id,
                        course_code=course_code,
                        subject_codes_json=subject_codes_json,
                        has_paid=True,
                    )
                else:
                    await add_mockjamb_room_player(
                        session,
                        room_code=room_code,
                        user_id=payer_user_id,
                        course_code=course_code,
                        subject_codes_json=subject_codes_json,
                        is_host=False,
                        has_paid=True,
                    )

                players = await list_mockjamb_room_players(
                    session,
                    room_code=room_code,
                )

                await session.commit()

            except Exception as e:
                await session.rollback()
                logger.exception(
                    "Failed to complete room friend payment flow | tx_ref=%s | room_code=%s | user_id=%s | err=%s",
                    tx_ref,
                    room_code,
                    payer_user_id,
                    e,
                )
                await send_response(
                    "⚠️ Payment succeeded, but your room access could not be updated right now. Please tap Refresh in the room."
                )
                return

            context.user_data["mj_course_code"] = course_code
            context.user_data["mj_subject_codes"] = subject_codes
            context.user_data["mj_mode"] = "friends"
            context.user_data["mj_room_code"] = room_code
            context.user_data["mjr_room_code"] = room_code
            context.user_data["mjr_is_host"] = False
            context.user_data["mj_payment_reference"] = tx_ref
            context.user_data["mj_session_id"] = None

            bot_username = ""
            try:
                me = await context.bot.get_me()
                bot_username = me.username or ""
            except Exception:
                bot_username = ""

            invite_link = build_mockjamb_invite_link(bot_username, room_code)

            message_text = build_mockjamb_waiting_room_text(
                room_code=room_code,
                invite_link=invite_link,
                room_status="waiting",
                players=players,
                host_user_id=int((room or {}).get("host_user_id") or 0),
            )

            markup = make_mockjamb_room_waiting_keyboard(
                is_host=False,
                room_status="waiting",
                room_code=room_code,
                has_course=True,
                has_paid=True,
                is_ready=False,
            )

            await send_response(
                message_text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )

            try:
                await refresh_mockjamb_host_waiting_room(context, room_code)
            except Exception:
                logger.exception(
                    "Failed to auto-refresh host after invitee payment | room_code=%s",
                    room_code,
                )

            return
        
        # ============================================================
        # SOLO FLOW
        # ============================================================
        try:
            mj_session = await get_or_create_mockjamb_session_from_payment(
                session,
                payment_reference=tx_ref,
                user_id=payer_user_id,
                course_code=course_code,
                subject_codes_json=subject_codes_json,
            )
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.exception(
                "Failed to create Mock JAMB solo session | tx_ref=%s | err=%s",
                tx_ref,
                e,
            )
            await send_response(
                "⚠️ Payment succeeded, but your exam session could not be created right now. Please try again."
            )
            return

    context.user_data["mj_course_code"] = course_code
    context.user_data["mj_subject_codes"] = subject_codes
    context.user_data["mj_mode"] = exam_mode
    context.user_data["mj_room_code"] = None
    context.user_data["mjr_room_code"] = None
    context.user_data["mjr_is_host"] = False
    context.user_data["mj_payment_reference"] = tx_ref
    context.user_data["mj_session_id"] = mj_session["id"]

    message_text = build_mockjamb_exam_ready_text(course_code, subject_codes)
    markup = make_mockjamb_exam_ready_keyboard(subject_codes)

    await send_response(
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
    application.add_handler(CallbackQueryHandler(mockjamb_room_pick_course_handler, pattern=r"^mjr_pick_course$"))
    application.add_handler(CallbackQueryHandler(mockjamb_room_pay_friend_handler, pattern=r"^mjr_pay_friend$"))
    application.add_handler(CallbackQueryHandler(mockjamb_mode_friends_handler, pattern=r"^mj_mode_friends$"))
    application.add_handler(CallbackQueryHandler(mockjamb_invitee_count_handler, pattern=r"^mj_invites_"))
    application.add_handler(CallbackQueryHandler(mockjamb_room_refresh_handler, pattern=r"^mjr_refresh$"))
    application.add_handler(CallbackQueryHandler(mockjamb_room_share_handler, pattern=r"^mjr_share$"))
    application.add_handler(CallbackQueryHandler(mockjamb_room_ready_handler, pattern=r"^mjr_ready(?:_done)?$"))
    application.add_handler(CallbackQueryHandler(mockjamb_room_back_to_mode_handler, pattern=r"^mjr_back_to_mode$"))
    application.add_handler(CallbackQueryHandler(mockjamb_room_join_handler, pattern=r"^mjr_join::"))
    application.add_handler(CallbackQueryHandler(mockjamb_pay_solo_handler, pattern=r"^mj_pay_solo$"))
    application.add_handler(CallbackQueryHandler(mockjamb_pay_friends_handler, pattern=r"^mj_pay_friends$"))
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


