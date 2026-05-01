# ====================================================================
# handlers/mockwaec.py
# ====================================================================

import json
import math
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from waec_loader import get_waec_subjects, get_subject_by_code
from db import get_async_session
from helpers import md_escape
from services.flutterwave_client import create_checkout, build_tx_ref
from services.mockwaec_payments import create_pending_mockwaec_payment, get_mockwaec_payment
from services.mockwaec_session_service import (
    get_or_create_mockwaec_session_from_payment,
    mark_mockwaec_subject_completed,
    get_mockwaec_session_by_payment_reference,
    get_latest_active_mockwaec_session_for_user,
    get_mockwaec_exam_duration_minutes,
)
from services.mockwaec_exam_service import (
    start_mockwaec_subject,
    answer_mockwaec_question,
    calculate_mockwaec_subject_score,
    get_mockwaec_review_rows,
    get_mockwaec_subject_question_by_order,
    get_mockwaec_subject_question_count,
    get_mockwaec_subject_result_stats,
    get_mockwaec_grade_from_score,
)

from services.mockwaec_room_service import (
    create_mockwaec_room,
    add_mockwaec_room_player,
    get_mockwaec_room_player,
    get_mockwaec_room_by_code,
    list_mockwaec_room_players,
    build_mockwaec_invite_link,
)

logger = logging.getLogger(__name__)

COURSES_PER_PAGE = 6

MOCKWAEC_SOLO_FEE = 100

# ====================================================================
# Keyboards
# ====================================================================
def make_mockwaec_welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📚 Choose Subjects", callback_data="mw_subjects_open")],
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
                callback_data=f"mw_course_select::{course['course_code']}"
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton("◀ Prev", callback_data=f"mw_course_page_{page - 1}")
        )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton("Next ▶", callback_data=f"mw_course_page_{page + 1}")
        )
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("⬅️ Back to Mock WAEC/NECO", callback_data="mock:waec")])
    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])

    return InlineKeyboardMarkup(rows)


def make_course_recommendation_keyboard(course_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Use This Combination", callback_data=f"mw_use_course::{course_code}")],
            [InlineKeyboardButton("🔁 Change Course", callback_data="mw_course_page_1")],
            [InlineKeyboardButton("⬅️ Back to Mock WAEC/NECO", callback_data="mock:waec")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_mockwaec_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧍 Write Alone", callback_data="mw_mode_solo")],
            [InlineKeyboardButton("👥 Invite Friends", callback_data="mw_mode_friends")],
            [InlineKeyboardButton("⬅️ Change Course", callback_data="mw_course_page_1")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_mockwaec_solo_payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"💳 Pay ₦{MOCKWAEC_SOLO_FEE}", callback_data="mw_pay_solo")],
            [InlineKeyboardButton("⬅️ Back", callback_data="mw_subjects_open")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_mockwaec_subject_selection_keyboard(selected_codes: list[str]) -> InlineKeyboardMarkup:
    subjects = get_waec_subjects()
    rows = []
    current_row = []

    for subject in subjects:
        code = subject["code"]
        name = subject["name"]
        prefix = "✅ " if code in selected_codes else ""

        current_row.append(
            InlineKeyboardButton(
                f"{prefix}{name}",
                callback_data=f"mw_subject_toggle::{code}"
            )
        )

        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []

    if current_row:
        rows.append(current_row)

    rows.append([InlineKeyboardButton("➡️ Continue", callback_data="mw_subjects_continue")])
    rows.append([InlineKeyboardButton("⬅️ Back to Mock WAEC/NECO", callback_data="mock:waec")])
    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])

    return InlineKeyboardMarkup(rows)


def make_mockwaec_exam_ready_keyboard(subject_codes: list[str]) -> InlineKeyboardMarkup:
    rows = []

    for code in subject_codes:
        subject = get_course_subjects_for_code(code)
        if subject:
            rows.append([
                InlineKeyboardButton(
                    f"📘 Start with {subject['name']}",
                    callback_data=f"mw_start_subject::{code}"
                )
            ])

    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def make_mockwaec_join_room_keyboard(room_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👥 Join Room", callback_data=f"mwr_join::{room_code}")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def get_course_subjects_for_code(subject_code: str):
    from waec_loader import get_subject_by_code
    return get_subject_by_code(subject_code)


def make_mockwaec_question_answer_keyboard(
    subject_code: str,
    question_order: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("A", callback_data=f"mw_ans::{subject_code}::{question_order}::A"),
                InlineKeyboardButton("B", callback_data=f"mw_ans::{subject_code}::{question_order}::B"),
            ],
            [
                InlineKeyboardButton("C", callback_data=f"mw_ans::{subject_code}::{question_order}::C"),
                InlineKeyboardButton("D", callback_data=f"mw_ans::{subject_code}::{question_order}::D"),
            ],
            [InlineKeyboardButton("✅ Submit This Subject", callback_data="mw_submit_subject_confirm")],
            [InlineKeyboardButton("✅ Submit Exam Now", callback_data="mw_submit_exam_confirm")],
            [InlineKeyboardButton("🛑 End Exam", callback_data="mw_end_exam")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_mockwaec_next_subject_keyboard(subject_codes: list[str]) -> InlineKeyboardMarkup:
    rows = []

    for code in subject_codes:
        subject = get_subject_by_code(code)
        if subject:
            rows.append([
                InlineKeyboardButton(
                    f"📘 Start {subject['name']}",
                    callback_data=f"mw_start_subject::{code}"
                )
            ])

    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def make_mockwaec_submit_subject_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Yes, Submit This Subject", callback_data="mw_submit_subject_yes")],
            [InlineKeyboardButton("❌ No, Continue This Subject", callback_data="mw_submit_subject_no")],
        ]
    )


def make_mockwaec_final_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📄 Preview Result", callback_data="mw_review_all")],
            [InlineKeyboardButton("❌ Wrong Answers Review", callback_data="mw_review_wrong")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_mockwaec_submit_exam_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Yes, Submit Now", callback_data="mw_submit_exam_yes")],
            [InlineKeyboardButton("❌ No, Continue Exam", callback_data="mw_submit_exam_no")],
        ]
    )


def make_mockwaec_invitee_count_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1 Friend", callback_data="mw_invites_1"),
                InlineKeyboardButton("2 Friends", callback_data="mw_invites_2"),
            ],
            [
                InlineKeyboardButton("3 Friends", callback_data="mw_invites_3"),
                InlineKeyboardButton("4 Friends", callback_data="mw_invites_4"),
            ],
            [
                InlineKeyboardButton("5 Friends", callback_data="mw_invites_5"),
                InlineKeyboardButton("6 Friends", callback_data="mw_invites_6"),
            ],
            [
                InlineKeyboardButton("7 Friends", callback_data="mw_invites_7"),
                InlineKeyboardButton("8 Friends", callback_data="mw_invites_8"),
            ],
            [
                InlineKeyboardButton("9 Friends", callback_data="mw_invites_9"),
                InlineKeyboardButton("10 Friends", callback_data="mw_invites_10"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="mw_subjects_continue")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )



def make_mockwaec_stale_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶ Resume Exam", callback_data="mw_resume_exam")],
            [InlineKeyboardButton("🛑 End Exam", callback_data="mw_end_exam")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )

def make_mockwaec_time_up_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Submit Exam Now", callback_data="mw_submit_exam_confirm")],
            [InlineKeyboardButton("🛑 End Exam", callback_data="mw_end_exam")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_mockwaec_room_waiting_keyboard(
    *,
    is_host: bool,
    room_code: str | None = None,
    has_subjects: bool = False,
    has_paid: bool = False,
    is_ready: bool = False,
) -> InlineKeyboardMarkup:
    rows = []

    safe_room_code = str(room_code or "").strip().upper()
    share_url = None

    if safe_room_code:
        from os import getenv
        import urllib.parse

        bot_username = getenv("BOT_USERNAME", "NaijaPrizeGateBot")
        invite_link = f"https://t.me/{bot_username}?start=wcroom_{safe_room_code}"

        share_text = (
            "📝 Join my Mock WAEC room on NaijaPrizeGate!\n\n"
            f"Room Code: {safe_room_code}\n"
            "Tap the link below to join:"
        )

        share_url = (
            "https://t.me/share/url?"
            f"url={urllib.parse.quote(invite_link)}"
            f"&text={urllib.parse.quote(share_text)}"
        )

    if is_host:
        if share_url:
            rows.append([InlineKeyboardButton("📤 Share Room Link / Code", url=share_url)])
        rows.append([InlineKeyboardButton("🔄 Refresh Room", callback_data="mwr_refresh")])
        rows.append([InlineKeyboardButton("▶️ Start Match", callback_data="mwr_start")])
        rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])
    else:
        if not has_subjects:
            rows.append([InlineKeyboardButton("📚 Choose My Subjects", callback_data="mwr_pick_subjects")])
        elif not has_paid:
            rows.append([InlineKeyboardButton("💳 Pay Now", callback_data="mwr_pay_friend")])
        elif not is_ready:
            rows.append([InlineKeyboardButton("✅ Ready", callback_data="mwr_ready")])
        else:
            rows.append([InlineKeyboardButton("✅ You Are Ready", callback_data="mwr_ready_done")])

        rows.append([InlineKeyboardButton("🔄 Refresh Room", callback_data="mwr_refresh")])
        rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])

    return InlineKeyboardMarkup(rows)



# --------------------------------------
# Mock Time Remaining
# -------------------------------------
def format_mockwaec_time_remaining(exam_ends_at) -> str:
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


def format_mockwaec_duration_minutes(total_minutes: int) -> str:
    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours > 0 and minutes > 0:
        return f"{hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h"
    return f"{minutes}m"


# -----------------------------------------------------
# Extract Mock WAEC Room Code from Start Payload
# ----------------------------------------------------
def extract_mockwaec_room_code_from_start_payload(payload: str) -> str | None:
    payload = str(payload or "").strip()

    if not payload:
        return None

    if payload.startswith("wcroom_"):
        room_code = payload.replace("wcroom_", "", 1).strip().upper()
        return room_code or None

    return None


def get_mockwaec_live_room_status(room: dict, players: list[dict]) -> str:
    room_status = str((room or {}).get("status") or "waiting").strip().lower()

    if room_status in ("in_progress", "completed", "locked"):
        return room_status

    joined_count = len(players)

    if joined_count < 2:
        return "waiting"

    eligible_players = 0

    for player in players:
        is_host = bool(player.get("is_host"))
        payment_status = str(player.get("payment_status") or "").strip().lower()
        is_ready = bool(player.get("is_ready"))

        if is_host:
            eligible_players += 1
        elif payment_status == "successful" and is_ready:
            eligible_players += 1

    if eligible_players >= 2:
        return "ready"

    return "waiting"

# ====================================================================
# Message Builders
# ====================================================================
def build_mockwaec_welcome_text() -> str:
    return (
        "📝 *Welcome to Mock WAEC / NECO*\n\n"
        "This mock exam is designed to simulate the real WAEC / NECO experience.\n\n"
        "You can choose a minimum of *7 subjects* and a maximum of *9 subjects*.\n\n"
        "*English Language* is compulsory.\n\n"
        "To begin, tap *Choose Subjects* and select the subjects you want for your mock exam."
    )


def build_course_page_text(page: int, total_pages: int) -> str:
    return (
        "🎯 *Choose Your Intended Course*\n\n"
        "Select your course below and we will recommend a likely WAEC/NECO subject combination for your mock exam.\n\n"
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


def build_mockwaec_mode_text(subject_codes: list[str]) -> str:
    subject_lines = []

    for code in subject_codes:
        subject = get_subject_by_code(code)
        if subject:
            subject_lines.append(f"• {subject['name']}")

    joined_subjects = "\n".join(subject_lines)

    return (
        "✅ *Subjects Saved*\n\n"
        "*Your Mock WAEC / NECO subjects are:*\n"
        f"{joined_subjects}\n\n"
        "How would you like to take this mock exam?"
    )


def build_mockwaec_solo_payment_text(subject_codes: list[str]) -> str:
    subject_lines = []

    for code in subject_codes:
        subject = get_subject_by_code(code)
        if subject:
            subject_lines.append(f"• {subject['name']}")

    joined_subjects = "\n".join(subject_lines)

    return (
        "💳 *Mock WAEC / NECO Solo Access*\n\n"
        "*Subjects:*\n"
        f"{joined_subjects}\n\n"
        f"*Exam Fee:* ₦{MOCKWAEC_SOLO_FEE}\n\n"
        "Tap below to continue to payment."
    )


def build_mockwaec_subject_selection_text(selected_codes: list[str]) -> str:
    lines = [
        "📚 *Choose Your Subjects*",
        "",
        "Select a minimum of *7 subjects* and a maximum of *9 subjects*.",
        "",
        "*Compulsory:* English Language",
        "",
        f"*Selected:* {len(selected_codes)}",
    ]

    if selected_codes:
        lines.extend([
            "",
            "*Current Selection:*",
        ])

        for code in selected_codes:
            subject = get_subject_by_code(code)
            if subject:
                lines.append(f"• {subject['name']}")
    else:
        lines.extend([
            "",
            "_No subject selected yet._"
        ])

    return "\n".join(lines)


def build_mockwaec_exam_ready_text(subject_codes: list[str]) -> str:
    subject_lines = []

    for code in subject_codes:
        subject = get_subject_by_code(code)
        if subject:
            subject_lines.append(f"• {subject['name']}")

    joined_subjects = "\n".join(subject_lines)

    subject_count = len(subject_codes)
    duration_minutes = get_mockwaec_exam_duration_minutes(subject_count)
    formatted_duration = format_mockwaec_duration_minutes(duration_minutes)

    return (
        "📝 *Mock WAEC / NECO Exam Ready*\n\n"
        "*Your subjects:*\n"
        f"{joined_subjects}\n\n"
        f"*Total Subjects:* {subject_count}\n"
        f"*Allotted Time:* {formatted_duration}\n\n"
        "Choose the subject you want to start with first."
    )


def build_mockwaec_live_question_text(
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
        payload = get_question_payload(question_row)
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
        "📝 *Mock WAEC / NECO*",
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


def build_mockwaec_submit_subject_confirm_text() -> str:
    return (
        "⚠️ *Submit This Subject Now?*\n\n"
        "If you submit this subject now:\n"
        "• this subject will end immediately\n"
        "• unanswered questions in this subject will count as zero\n"
        "• you will move to your next remaining subject\n\n"
        "Are you sure you want to submit this subject now?"
    )


def build_mockwaec_subject_completed_text(
    *,
    completed_subject_code: str,
    score_100: int,
    remaining_subject_codes: list[str],
) -> str:
    completed_subject = get_subject_by_code(completed_subject_code)
    subject_name = completed_subject["name"] if completed_subject else completed_subject_code.upper()

    lines = [
        "✅ *Subject Completed*",
        "",
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

def build_mockwaec_submit_exam_confirm_text() -> str:
    return (
        "⚠️ *Submit Mock WAEC / NECO Now?*\n\n"
        "If you submit now:\n"
        "• your exam will end immediately\n"
        "• unanswered questions will count as zero\n"
        "• your current scores will be calculated and shown\n\n"
        "Are you sure you want to submit?"
    )

def build_mockwaec_final_result_text(
    *,
    subject_codes: list[str],
    scores: dict,
    answered_counts: dict | None = None,
    correct_counts: dict | None = None,
) -> str:
    answered_counts = answered_counts or {}
    correct_counts = correct_counts or {}

    lines = [
        "📊 *Mock WAEC / NECO Result*",
        "",
        "*Subject Grades:*",
        "",
    ]

    for code in subject_codes:
        subject = get_subject_by_code(code)
        subject_name = subject["name"] if subject else code.upper()
        score = int(scores.get(code) or 0)
        grade = get_mockwaec_grade_from_score(score)

        lines.append(f"*{subject_name}*: *{grade}* ({score}/100)")

    lines.extend([
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
        grade = get_mockwaec_grade_from_score(score)

        lines.extend([
            f"*{subject_name}*",
            f"Answered: {answered}",
            f"Correct: {correct}",
            f"Score: {score}/100",
            f"Grade: *{grade}*",
            "",
        ])

    return "\n".join(lines)


def build_mockwaec_invitee_count_text(subject_codes: list[str]) -> str:
    subject_lines = []

    for code in subject_codes:
        subject = get_subject_by_code(code)
        if subject:
            subject_lines.append(f"• {subject['name']}")

    joined_subjects = "\n".join(subject_lines)

    return (
        "👥 *Mock WAEC Multiplayer Setup*\n\n"
        "First, choose how many friends you want to invite.\n\n"
        "*Your current subjects:*\n"
        f"{joined_subjects}\n\n"
        "After that, you will continue to payment."
    )


def build_mockwaec_waiting_room_text(
    *,
    room_code: str,
    invite_link: str,
    room_status: str,
    players: list[dict],
    host_user_id: int,
    expected_players: int | None = None,
) -> str:
    total_players = len(players)
    required_players = int(expected_players or 0) if expected_players else 0
    invited_friends_count = max(0, required_players - 1) if required_players else 0

    normalized_status = str(room_status or "waiting").strip().lower()
    status_map = {
        "waiting": "⏳ Waiting",
        "ready": "✅ Ready",
        "in_progress": "📝 In Progress",
        "completed": "🏁 Completed",
        "locked": "🔒 Locked",
    }
    pretty_status = status_map.get(normalized_status, normalized_status.title())

    lines = [
        "👥 <b>Mock WAEC Multiplayer Room</b>",
        "",
        f"<b>Room Code:</b> <code>{room_code}</code>",
        f"<b>Status:</b> {pretty_status}",
    ]

    if required_players > 0:
        lines.append(f"<b>Players Joined:</b> {total_players} of {required_players}")
        lines.append(
            f"<b>Total Players Required:</b> {required_players} "
            f"(1 Host + {invited_friends_count} Friend{'s' if invited_friends_count != 1 else ''})"
        )
    else:
        lines.append(f"<b>Players Joined:</b> {total_players}")

    lines.extend([
        "",
        "<b>Invite Link:</b>",
        invite_link,
        "",
        "<b>Players in Room:</b>",
    ])

    if not players:
        lines.append("• No players yet.")
    else:
        for idx, player in enumerate(players, start=1):
            user_id = int(player.get("user_id") or 0)
            is_host = bool(player.get("is_host"))

            first_name = str(player.get("first_name") or "").strip()
            last_name = str(player.get("last_name") or "").strip()
            username = str(player.get("username") or "").strip()

            full_name = " ".join(x for x in [first_name, last_name] if x).strip()
            if full_name and username:
                display_name = f"{full_name} (@{username})"
            elif full_name:
                display_name = full_name
            elif username:
                display_name = f"@{username}"
            else:
                display_name = f"User {user_id}"

            try:
                subject_codes = json.loads(player.get("subject_codes_json") or "[]")
            except Exception:
                subject_codes = []

            subject_names = []
            if isinstance(subject_codes, list):
                for code in subject_codes:
                    subject = get_subject_by_code(str(code).strip().lower())
                    if subject:
                        subject_names.append(subject["name"])

            subject_text = ", ".join(subject_names) if subject_names else "Not Set"

            payment_status = str(player.get("payment_status") or "").strip().lower()
            is_paid = payment_status == "successful"
            is_ready = bool(player.get("is_ready"))

            role_label = "👑 Host" if is_host else "👤 Player"
            payment_label = "💳 Paid" if is_paid else "💰 Not Paid"
            ready_label = "✅ Ready" if is_ready else "⏳ Waiting"

            lines.append(f"{idx}. <b>{role_label}:</b> {display_name}")
            lines.append(f"   • <b>Subjects:</b> {subject_text}")
            lines.append(f"   • <b>Payment:</b> {payment_label}")
            lines.append(f"   • <b>Readiness:</b> {ready_label}")

    lines.append("")

    if normalized_status == "ready":
        lines.append("All required active players are ready. The host can now start the match.")
    elif normalized_status == "in_progress":
        lines.append("The match has started. Players can continue into the exam.")
    elif normalized_status == "completed":
        lines.append("This match has ended.")
    else:
        lines.append("Share the room code or invite link with your friends.")

    return "\n".join(lines)


# ----Question Has Passage-----------
def question_has_passage(question_row: dict) -> bool:
    try:
        payload = get_question_payload(question_row)
    except Exception:
        payload = {}

    raw_passage_text = str(payload.get("passage") or "").strip()
    question_type = str(payload.get("question_type") or "").strip().lower()

    return bool(raw_passage_text) or question_type == "comprehension_mcq"


def build_mockwaec_passage_text(
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
        "📝 *Mock WAEC / NECO*",
        "",
        f"*Subject:* {safe_subject_name}",
        (
            f"*Questions:* {question_start} \\- {question_end} of {total_questions}"
            if question_start != question_end
            else f"*Question:* {question_start} of {total_questions}"
        ),
    ]

    if exam_ends_at:
        remaining = format_mockwaec_time_remaining(exam_ends_at)
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


def build_mockwaec_question_only_text(
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
        "📝 *Mock WAEC / NECO*",
        "",
        f"*Subject:* {safe_subject_name}",
        f"*Question:* {question_number} of {total_questions}",
    ]

    if exam_ends_at:
        remaining = format_mockwaec_time_remaining(exam_ends_at)
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


def build_mockwaec_review_text(
    *,
    review_row: dict,
    review_index: int,
    total_reviews: int,
) -> str:
    subject_code = str(review_row.get("subject_code") or "").strip()
    subject = get_subject_by_code(subject_code)
    subject_name = subject["name"] if subject else subject_code.upper()

    payload = get_question_payload(review_row)

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
        "📄 *Mock WAEC / NECO Review*",
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


def make_mockwaec_review_nav_keyboard(
    *,
    mode: str,
    current_index: int,
    total_reviews: int,
) -> InlineKeyboardMarkup:
    nav_row = []

    if current_index > 0:
        nav_row.append(
            InlineKeyboardButton("⬅ Prev", callback_data=f"mw_review_nav::{mode}::{current_index - 1}")
        )

    if current_index < total_reviews - 1:
        nav_row.append(
            InlineKeyboardButton("Next ➡", callback_data=f"mw_review_nav::{mode}::{current_index + 1}")
        )

    rows = []
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("⬅ Back to Result", callback_data="mw_back_to_result")])
    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])

    return InlineKeyboardMarkup(rows)


def build_mockwaec_resume_prompt_text(
    *,
    subject_codes: list[str],
    completed_subjects: list[str],
    current_subject_code: str | None,
    current_question_index: int,
    exam_ends_at=None,
) -> str:
    remaining_subject_codes = [
        code for code in subject_codes if code not in completed_subjects
    ]

    lines = [
        "📝 *Resume Mock WAEC / NECO*",
        "",
    ]

    time_up = False

    if exam_ends_at:
        remaining = format_mockwaec_time_remaining(exam_ends_at)
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
            "You have an active Mock WAEC / NECO exam in progress.",
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


def make_mockwaec_resume_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶ Resume Exam", callback_data="mw_resume_exam")],
            [InlineKeyboardButton("🛑 End Exam", callback_data="mw_end_exam")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def build_mockwaec_continue_subject_choice_text(
    *,
    remaining_subject_codes: list[str],
) -> str:
    lines = [
        "📝 *Mock WAEC / NECO In Progress*",
        "",
        "*Choose the next subject to continue:*",
    ]

    for code in remaining_subject_codes:
        subject = get_subject_by_code(code)
        if subject:
            lines.append(f"• {subject['name']}")

    return "\n".join(lines)


def get_question_payload(question_row: dict) -> dict:
    raw = question_row.get("question_json")

    if isinstance(raw, dict):
        return raw

    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}

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

    last_passage_id_shown = str(context.user_data.get("mw_last_passage_id_shown") or "").strip()

    # If there is no passage_id, fall back to showing passage
    if not current_passage_id:
        return True

    return current_passage_id != last_passage_id_shown


def mark_passage_as_shown(question_row: dict, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["mw_last_passage_id_shown"] = get_question_passage_id(question_row)


async def clear_mockwaec_passage_message(
    *,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    passage_message_id = context.user_data.get("mw_active_passage_message_id")

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

    context.user_data["mw_active_passage_message_id"] = None
    context.user_data["mw_last_passage_id_shown"] = ""


def store_mockwaec_passage_message_id(
    *,
    message_id: int | None,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    context.user_data["mw_active_passage_message_id"] = message_id

# --------------------------------------------
# Mock WAEC / NECO Time Expire
# ------------------------------------------
def is_mockwaec_time_expired(exam_ends_at) -> bool:
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


async def build_mockwaec_result_from_session(
    *,
    payment_reference: str,
    course_code: str,
    subject_codes: list[str],
) -> tuple[str, InlineKeyboardMarkup] | None:
    async with get_async_session() as session:
        session_row = await get_mockwaec_session_by_payment_reference(
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
            stats = await get_mockwaec_subject_result_stats(
                session,
                payment_reference=payment_reference,
                subject_code=subject_code,
            )
            answered_counts[subject_code] = int(stats.get("answered_count") or 0)
            correct_counts[subject_code] = int(stats.get("correct_count") or 0)

    message_text = build_mockwaec_final_result_text(
        subject_codes=subject_codes,
        scores=scores,
        answered_counts=answered_counts,
        correct_counts=correct_counts,
    )
    markup = make_mockwaec_final_result_keyboard()
    return message_text, markup

# -------------------------------------
# Mock WAEC submit Subject Confirm Handler
# ---------------------------------------
async def mockwaec_submit_subject_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    message_text = build_mockwaec_submit_subject_confirm_text()
    markup = make_mockwaec_submit_subject_confirm_keyboard()

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


async def mockwaec_submit_subject_no_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer("Continue your current subject.")

    return await mockwaec_resume_exam_handler(update, context)


async def mockwaec_submit_subject_yes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    payment_reference = str(context.user_data.get("mw_payment_reference") or "").strip()
    course_code = str(context.user_data.get("mw_course_code") or "").strip()
    subject_codes = context.user_data.get("mw_subject_codes") or []

    if not payment_reference or not course_code or not subject_codes:
        return await query.message.reply_text(
            "⚠️ No active Mock WAEC / NECO exam session found."
        )

    async with get_async_session() as session:
        session_row = await get_mockwaec_session_by_payment_reference(
            session,
            payment_reference,
        )
        if not session_row:
            return await query.message.reply_text(
                "⚠️ Exam session not found."
            )

        current_subject_code = str(session_row.get("current_subject_code") or "").strip()
        if not current_subject_code:
            return await query.message.reply_text(
                "⚠️ No active subject found to submit."
            )

        await clear_mockwaec_passage_message(
            chat_id=query.message.chat_id,
            context=context,
        )

        score_info = await calculate_mockwaec_subject_score(
            session,
            payment_reference=payment_reference,
            subject_code=current_subject_code,
        )
        score_100 = int(score_info.get("score_100") or 0)

        updated_session = await mark_mockwaec_subject_completed(
            session,
            payment_reference=payment_reference,
            subject_code=current_subject_code,
            score=score_100,
        )
        await session.commit()

    if not updated_session:
        return await query.message.reply_text(
            "⚠️ Could not finalize this subject."
        )

    try:
        completed_subjects = json.loads((updated_session or {}).get("completed_subjects_json") or "[]")
    except Exception:
        completed_subjects = []

    try:
        scores = json.loads((updated_session or {}).get("scores_json") or "{}")
    except Exception:
        scores = {}

    remaining_subject_codes = [
        code for code in subject_codes
        if code not in completed_subjects
    ]

    context.user_data["mw_last_passage_id_shown"] = ""

    if remaining_subject_codes:
        message_text = build_mockwaec_subject_completed_text(
            completed_subject_code=current_subject_code,
            score_100=score_100,
            remaining_subject_codes=remaining_subject_codes,
        )
        markup = make_mockwaec_next_subject_keyboard(remaining_subject_codes)

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
            stats = await get_mockwaec_subject_result_stats(
                session,
                payment_reference=payment_reference,
                subject_code=code,
            )
            answered_counts[code] = int(stats.get("answered_count") or 0)
            correct_counts[code] = int(stats.get("correct_count") or 0)

    message_text = build_mockwaec_final_result_text(
        subject_codes=subject_codes,
        scores=scores,
        answered_counts=answered_counts,
        correct_counts=correct_counts,
    )
    markup = make_mockwaec_final_result_keyboard()

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

# ------------------------------------
# Finalize Mockwaec Exam Now
# ------------------------------------
async def finalize_mockwaec_exam_now(
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
            score_info = await calculate_mockwaec_subject_score(
                session,
                payment_reference=payment_reference,
                subject_code=subject_code,
            )
            scores[subject_code] = int(score_info.get("score_100") or 0)

            stats = await get_mockwaec_subject_result_stats(
                session,
                payment_reference=payment_reference,
                subject_code=subject_code,
            )
            answered_counts[subject_code] = int(stats.get("answered_count") or 0)
            correct_counts[subject_code] = int(stats.get("correct_count") or 0)

        await session.execute(
            text("""
                update public.mockwaec_sessions
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

    message_text = build_mockwaec_final_result_text(
        subject_codes=subject_codes,
        scores=scores,
        answered_counts=answered_counts,
        correct_counts=correct_counts,
    )
    markup = make_mockwaec_final_result_keyboard()
    return message_text, markup

# ====================================================================
# Entry Handler
# ====================================================================
async def mockwaec_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    async with get_async_session() as session:
        active_session = await get_latest_active_mockwaec_session_for_user(
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
            context.user_data["mw_course_code"] = course_code
            context.user_data["mw_subject_codes"] = subject_codes
            context.user_data["mw_mode"] = "solo"
            context.user_data["mw_room_code"] = None
            context.user_data["mw_payment_reference"] = payment_reference
            context.user_data["mw_session_id"] = active_session["id"]

            text = build_mockwaec_resume_prompt_text(
                subject_codes=subject_codes,
                completed_subjects=completed_subjects,
                current_subject_code=current_subject_code,
                current_question_index=current_question_index,
                exam_ends_at=active_session.get("exam_ends_at"),
            )

            if is_mockwaec_time_expired(active_session.get("exam_ends_at")):
                markup = make_mockwaec_time_up_keyboard()
            else:
                markup = make_mockwaec_resume_keyboard()

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

    text = build_mockwaec_welcome_text()
    markup = make_mockwaec_welcome_keyboard()

    context.user_data["mw_subject_codes"] = []
    context.user_data["mw_mode"] = None
    context.user_data["mw_room_code"] = None

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


async def mockwaec_subjects_open_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    selected_codes = context.user_data.get("mw_subject_codes") or []

    text = build_mockwaec_subject_selection_text(selected_codes)
    markup = make_mockwaec_subject_selection_keyboard(selected_codes)

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


async def mockwaec_subject_toggle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, subject_code = query.data.split("::", 1)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid subject selection.")

    subject = get_subject_by_code(subject_code)
    if not subject:
        return await query.message.reply_text("⚠️ Subject not found.")

    selected_codes = context.user_data.get("mw_subject_codes") or []

    if subject_code in selected_codes:
        selected_codes.remove(subject_code)
    else:
        if len(selected_codes) >= 9:
            return await query.answer(
                "You can select a maximum of 9 subjects.",
                show_alert=True,
            )
        selected_codes.append(subject_code)

    context.user_data["mw_subject_codes"] = selected_codes

    text = build_mockwaec_subject_selection_text(selected_codes)
    markup = make_mockwaec_subject_selection_keyboard(selected_codes)

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


async def mockwaec_subjects_continue_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    selected_codes = context.user_data.get("mw_subject_codes") or []

    if len(selected_codes) < 7:
        return await query.answer(
            "⚠️ Please choose a minimum of 7 subjects.",
            show_alert=True,
        )

    if len(selected_codes) > 9:
        return await query.answer(
            "⚠️ Please choose a maximum of 9 subjects.",
            show_alert=True,
        )

    if "eng" not in selected_codes:
        return await query.answer(
            "⚠️ English Language is compulsory. Please add it before continuing.",
            show_alert=True,
        )

    # ============================================================
    # ROOM INVITEE SUBJECT SELECTION FLOW
    # ============================================================
    if context.user_data.get("mw_subject_select_from_room"):
        room_code = str(context.user_data.get("mw_room_code") or "").strip().upper()
        if not room_code:
            return await query.answer("⚠️ No active room found.", show_alert=True)

        user = update.effective_user
        if not user:
            return await query.answer("⚠️ User not found.", show_alert=True)

        async with get_async_session() as session:
            room = await get_mockwaec_room_by_code(session, room_code=room_code)
            if not room:
                return await query.answer("⚠️ Room not found.", show_alert=True)

            await session.execute(
                text("""
                    update public.mockwaec_room_players
                    set
                        subject_codes_json = :subject_codes_json,
                        updated_at = now()
                    where upper(room_code) = :room_code
                      and user_id = :user_id
                """),
                {
                    "room_code": room_code,
                    "user_id": int(user.id),
                    "subject_codes_json": json.dumps(selected_codes),
                },
            )

            players = await list_mockwaec_room_players(
                session,
                room_code=room_code,
            )

            await session.commit()

        context.user_data["mw_subject_select_from_room"] = False

        bot_username = ""
        try:
            me = await context.bot.get_me()
            bot_username = me.username or ""
        except Exception:
            bot_username = ""

        invite_link = build_mockwaec_invite_link(bot_username, room_code)

        message_text = build_mockwaec_waiting_room_text(
            room_code=room_code,
            invite_link=invite_link,
            room_status="waiting",
            players=players,
            host_user_id=int(room.get("host_user_id") or 0),
            expected_players=int((room or {}).get("expected_players") or 0),
        )

        current_player = None
        for player in players:
            if int(player.get("user_id") or 0) == int(user.id):
                current_player = player
                break

        markup = make_mockwaec_room_waiting_keyboard(
            is_host=False,
            room_code=room_code,
            has_subjects=True,
            has_paid=bool((current_player or {}).get("has_paid")),
            is_ready=bool((current_player or {}).get("is_ready")),
        )

        try:
            await query.edit_message_text(
                text=message_text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except Exception:
            await query.message.reply_text(
                text=message_text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        return

    # ============================================================
    # NORMAL SOLO / DEFAULT FLOW
    # ============================================================
    subject_lines = []
    for code in selected_codes:
        subject = get_subject_by_code(code)
        if subject:
            subject_lines.append(f"• {subject['name']}")

    message_text = (
        "✅ *Subjects Saved*\n\n"
        "*Your selected Mock WAEC / NECO subjects are:*\n"
        f"{chr(10).join(subject_lines)}\n\n"
        "How would you like to take this mock exam?"
    )

    markup = make_mockwaec_mode_keyboard()

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
# Course Pagination Handler
# ====================================================================
async def mockwaec_course_page_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        page = int(query.data.replace("mw_course_page_", "", 1))
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
async def mockwaec_course_select_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    context.user_data["mw_course_code"] = course_code
    context.user_data["mw_subject_codes"] = [subject["code"] for subject in subjects]

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
async def mockwaec_use_course_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    subject_codes = [subject["code"] for subject in subjects]

    context.user_data["mw_course_code"] = course_code
    context.user_data["mw_subject_codes"] = subject_codes

    text = build_mockwaec_mode_text(subject_codes)
    markup = make_mockwaec_mode_keyboard()

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
async def mockwaec_mode_solo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    context.user_data["mw_mode"] = "solo"

    subject_codes = context.user_data.get("mw_subject_codes") or []
    if not subject_codes:
        return await query.message.reply_text(
            "⚠️ No saved subjects found. Please choose your subjects again.",
            reply_markup=make_mockwaec_welcome_keyboard(),
        )

    text = build_mockwaec_solo_payment_text(subject_codes)
    markup = make_mockwaec_solo_payment_keyboard()

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
async def mockwaec_mode_friends_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    context.user_data["mw_mode"] = "friends"

    subject_codes = context.user_data.get("mw_subject_codes") or []
    if not subject_codes:
        return await query.message.reply_text(
            "⚠️ No saved subjects found. Please choose your subjects again.",
            reply_markup=make_mockwaec_welcome_keyboard(),
        )

    text = build_mockwaec_invitee_count_text(subject_codes)
    markup = make_mockwaec_invitee_count_keyboard()

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


async def refresh_mockwaec_host_waiting_room(
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
        room = await get_mockwaec_room_by_code(
            session,
            room_code=room_code,
        )
        if not room:
            return

        host_user_id = int(room.get("host_user_id") or 0)
        host_waiting_message_id = room.get("host_waiting_message_id")

        if not host_user_id or not host_waiting_message_id:
            return

        players = await list_mockwaec_room_players(
            session,
            room_code=room_code,
        )

        live_status = get_mockwaec_live_room_status(room, players)
        all_players_ready = live_status == "ready"

        stored_status = str(room.get("status") or "waiting").strip().lower()

        # Auto-sync room status while still in lobby phase
        if stored_status not in ("in_progress", "completed", "locked"):
            if stored_status != live_status or bool(room.get("all_players_ready")) != all_players_ready:
                await session.execute(
                    text("""
                        update public.mockwaec_rooms
                        set
                            status = :status,
                            all_players_ready = :all_players_ready,
                            updated_at = now()
                        where upper(room_code) = :room_code
                    """),
                    {
                        "room_code": room_code,
                        "status": live_status,
                        "all_players_ready": all_players_ready,
                    },
                )
                await session.commit()

                room["status"] = live_status
                room["all_players_ready"] = all_players_ready
            else:
                room["status"] = stored_status
        else:
            live_status = stored_status

    invite_link = build_mockwaec_invite_link(bot_username, room_code)

    message_text = build_mockwaec_waiting_room_text(
        room_code=room_code,
        invite_link=invite_link,
        room_status=live_status,
        players=players,
        host_user_id=host_user_id,
        expected_players=int((room or {}).get("expected_players") or 0),
    )

    markup = make_mockwaec_room_waiting_keyboard(
        is_host=True,
        room_code=room_code,
        has_subjects=True,
        has_paid=True,
        is_ready=True,
    )

    try:
        await context.bot.edit_message_text(
            chat_id=host_user_id,
            message_id=int(host_waiting_message_id),
            text=message_text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception(
            "Failed to auto-refresh WAEC host waiting room | room_code=%s | host_user_id=%s | message_id=%s",
            room_code,
            host_user_id,
            host_waiting_message_id,
        )


async def mockwaec_room_pay_friend_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    if not user:
        return

    room_code = str(context.user_data.get("mw_room_code") or "").strip().upper()
    if not room_code:
        return await query.answer("⚠️ No active room found.", show_alert=True)

    async with get_async_session() as session:
        room = await get_mockwaec_room_by_code(
            session,
            room_code=room_code,
        )
        if not room:
            return await query.answer("⚠️ Room not found.", show_alert=True)

        player = await get_mockwaec_room_player(
            session,
            room_code=room_code,
            user_id=int(user.id),
        )
        if not player:
            return await query.answer("⚠️ You are not yet in this room.", show_alert=True)

        subject_codes_json = str(player.get("subject_codes_json") or "[]")

        try:
            subject_codes = json.loads(subject_codes_json)
        except Exception:
            subject_codes = []

        if not isinstance(subject_codes, list) or len(subject_codes) < 7:
            return await query.answer(
                "⚠️ Please choose your subjects first before payment.",
                show_alert=True,
            )

        payment_status = str(player.get("payment_status") or "").strip().lower()
        if payment_status == "successful":
            return await query.answer(
                "✅ You have already paid for this room.",
                show_alert=True,
            )

        amount = MOCKWAEC_SOLO_FEE
        tg_id = int(user.id)
        username = user.username or f"user_{tg_id}"
        email = f"{username}@naijaprizegate.ng"
        tx_ref = build_tx_ref("MOCKWAECROOMFRIEND")

        await create_pending_mockwaec_payment(
            session,
            payment_reference=tx_ref,
            user_id=tg_id,
            amount_paid=amount,
            course_code="custom",
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
            "product_type": "MOCKWAEC",
            "course_code": "custom",
            "exam_mode": "room_friend",
            "room_code": room_code,
        },
        product_type="MOCKWAEC",
    )

    if not checkout_url:
        async with get_async_session() as session:
            await session.execute(
                text("""
                    update public.mockwaec_payments
                    set
                        payment_status = 'expired',
                        updated_at = now()
                    where payment_reference = :payment_reference
                      and lower(coalesce(payment_status, '')) = 'pending'
                """),
                {"payment_reference": tx_ref},
            )
            await session.commit()

        return await query.answer(
            "⚠️ Payment service is unavailable right now. Please try again shortly.",
            show_alert=True,
        )

    message_text = (
        "💳 *Mock WAEC Room Payment*\n\n"
        f"*Room Code:* {room_code}\n"
        f"*Amount:* ₦{amount}\n\n"
        "Complete your payment to join this room as an active player."
    )

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Pay Securely", url=checkout_url)],
            [InlineKeyboardButton("⬅️ Back to Room", callback_data=f"mwr_join::{room_code}")],
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


async def mockwaec_room_ready_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    if not user:
        return

    room_code = str(context.user_data.get("mw_room_code") or "").strip().upper()
    if not room_code:
        return await query.answer("⚠️ No active room found.", show_alert=True)

    async with get_async_session() as session:
        room = await get_mockwaec_room_by_code(
            session,
            room_code=room_code,
        )
        if not room:
            return await query.answer("⚠️ Room not found.", show_alert=True)

        player = await get_mockwaec_room_player(
            session,
            room_code=room_code,
            user_id=int(user.id),
        )
        if not player:
            return await query.answer("⚠️ You are not in this room.", show_alert=True)

        payment_status = str(player.get("payment_status") or "").strip().lower()
        if payment_status != "successful":
            return await query.answer(
                "⚠️ Please pay first before getting ready.",
                show_alert=True,
            )

        try:
            subject_codes = json.loads(player.get("subject_codes_json") or "[]")
        except Exception:
            subject_codes = []

        if not isinstance(subject_codes, list) or len(subject_codes) < 7:
            return await query.answer(
                "⚠️ Please choose your subjects first.",
                show_alert=True,
            )

        already_ready = bool(player.get("is_ready"))
        if already_ready:
            return await query.answer(
                "✅ You are already marked ready.",
                show_alert=True,
            )

        await session.execute(
            text("""
                update public.mockwaec_room_players
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

        players = await list_mockwaec_room_players(
            session,
            room_code=room_code,
        )

        await session.commit()

    bot_username = ""
    try:
        me = await context.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""

    invite_link = build_mockwaec_invite_link(bot_username, room_code)

    message_text = build_mockwaec_waiting_room_text(
        room_code=room_code,
        invite_link=invite_link,
        room_status="waiting",
        players=players,
        host_user_id=int(room.get("host_user_id") or 0),
        expected_players=int((room or {}).get("expected_players") or 0),
    )

    markup = make_mockwaec_room_waiting_keyboard(
        is_host=False,
        room_code=room_code,
        has_subjects=True,
        has_paid=True,
        is_ready=True,
    )

    try:
        await query.edit_message_text(
            text=message_text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception:
        await query.message.reply_text(
            text=message_text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )

    try:
        await refresh_mockwaec_host_waiting_room(context, room_code)
    except Exception:
        logger.exception(
            "Failed to auto-refresh WAEC host waiting room after player ready | room_code=%s",
            room_code,
        )



async def mockwaec_room_join_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

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
        room = await get_mockwaec_room_by_code(session, room_code=room_code)
        if not room:
            return await query.answer("⚠️ Room not found.", show_alert=True)

        room_status = str(room.get("status") or "").strip().lower()
        if room_status != "waiting":
            return await query.answer(
                "⚠️ This room is no longer open for joining.",
                show_alert=True,
            )

        existing_player = await get_mockwaec_room_player(
            session,
            room_code=room_code,
            user_id=int(user.id),
        )

        if not existing_player:
            await add_mockwaec_room_player(
                session,
                room_code=room_code,
                user_id=int(user.id),
                course_code=None,
                subject_codes_json="[]",
                first_name=user.first_name,
                last_name=user.last_name,
                username=user.username,
            )

        players = await list_mockwaec_room_players(
            session,
            room_code=room_code,
        )

        await session.commit()

    context.user_data["mw_room_code"] = room_code
    context.user_data["mw_is_host"] = int(room.get("host_user_id") or 0) == int(user.id)

    invite_link = build_mockwaec_invite_link(bot_username, room_code)

    message_text = build_mockwaec_waiting_room_text(
        room_code=room_code,
        invite_link=invite_link,
        room_status="waiting",
        players=players,
        host_user_id=int(room.get("host_user_id") or 0),
        expected_players=int((room or {}).get("expected_players") or 0),
    )

    current_player = None
    for player in players:
        if int(player.get("user_id") or 0) == int(user.id):
            current_player = player
            break

    has_subjects = False
    try:
        current_subjects = json.loads((current_player or {}).get("subject_codes_json") or "[]")
        has_subjects = isinstance(current_subjects, list) and len(current_subjects) > 0
    except Exception:
        has_subjects = False

    has_paid = bool((current_player or {}).get("has_paid"))
    is_ready = bool((current_player or {}).get("is_ready"))

    markup = make_mockwaec_room_waiting_keyboard(
        is_host=bool(context.user_data["mw_is_host"]),
        room_code=room_code,
        has_subjects=has_subjects,
        has_paid=has_paid,
        is_ready=is_ready,
    )

    try:
        await query.edit_message_text(
            text=message_text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception:
        await query.message.reply_text(
            text=message_text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )

    try:
        await refresh_mockwaec_host_waiting_room(context, room_code)
    except Exception:
        logger.exception(
            "Failed to auto-refresh WAEC host waiting room after join | room_code=%s",
            room_code,
        )


async def mockwaec_room_refresh_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user:
        return

    room_code = str(context.user_data.get("mw_room_code") or "").strip().upper()
    if not room_code:
        return await query.answer("⚠️ No active room found.", show_alert=True)

    bot_username = ""
    try:
        me = await context.bot.get_me()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""

    async with get_async_session() as session:
        room = await get_mockwaec_room_by_code(session, room_code=room_code)
        if not room:
            return await query.answer("⚠️ Room not found.", show_alert=True)

        players = await list_mockwaec_room_players(
            session,
            room_code=room_code,
        )

    invite_link = build_mockwaec_invite_link(bot_username, room_code)

    message_text = build_mockwaec_waiting_room_text(
        room_code=room_code,
        invite_link=invite_link,
        room_status=str(room.get("status") or "waiting"),
        players=players,
        host_user_id=int(room.get("host_user_id") or 0),
        expected_players=int((room or {}).get("expected_players") or 0),
    )

    current_player = None
    for player in players:
        if int(player.get("user_id") or 0) == int(user.id):
            current_player = player
            break

    has_subjects = False
    try:
        current_subjects = json.loads((current_player or {}).get("subject_codes_json") or "[]")
        has_subjects = isinstance(current_subjects, list) and len(current_subjects) > 0
    except Exception:
        has_subjects = False

    has_paid = bool((current_player or {}).get("has_paid"))
    is_ready = bool((current_player or {}).get("is_ready"))

    markup = make_mockwaec_room_waiting_keyboard(
        is_host=int(room.get("host_user_id") or 0) == int(user.id),
        room_code=room_code,
        has_subjects=has_subjects,
        has_paid=has_paid,
        is_ready=is_ready,
    )

    try:
        await query.edit_message_text(
            text=message_text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception as e:
        error_text = str(e)

        if "Message is not modified" in error_text:
            return

        logger.exception(
            "Failed to refresh WAEC room message in place | room_code=%s | user_id=%s",
            room_code,
            int(user.id),
        )


async def mockwaec_invitee_count_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    if not user:
        return

    try:
        invitee_count = int(str(query.data).replace("mw_invites_", "", 1))
    except Exception:
        return await query.answer("⚠️ Invalid invitee count.", show_alert=True)

    if invitee_count < 1:
        return await query.answer("⚠️ Invitee count must be at least 1.", show_alert=True)

    subject_codes = context.user_data.get("mw_subject_codes") or []
    if not subject_codes:
        return await query.answer(
            "⚠️ No saved subjects found. Please choose your subjects again.",
            show_alert=True,
        )

    context.user_data["mw_mode"] = "friends"
    context.user_data["mw_invitee_count"] = invitee_count
    context.user_data["mw_required_player_count"] = invitee_count + 1

    amount = MOCKWAEC_SOLO_FEE
    tg_id = int(user.id)
    username = user.username or f"user_{tg_id}"
    email = f"{username}@naijaprizegate.ng"
    tx_ref = build_tx_ref("MOCKWAECFRIENDS")
    subject_codes_json = json.dumps(subject_codes)

    async with get_async_session() as session:
        await create_pending_mockwaec_payment(
            session,
            payment_reference=tx_ref,
            user_id=tg_id,
            amount_paid=amount,
            course_code="custom",
            subject_codes_json=subject_codes_json,
            exam_mode="friends",
            invitee_count=invitee_count,
            required_player_count=invitee_count + 1,
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
            "product_type": "MOCKWAEC",
            "course_code": "custom",
            "exam_mode": "friends",
            "invitee_count": str(invitee_count),
            "required_player_count": str(invitee_count + 1),
        },
        product_type="MOCKWAEC",
    )

    if not checkout_url:
        async with get_async_session() as session:
            await session.execute(
                text("""
                    update public.mockwaec_payments
                    set
                        payment_status = 'expired',
                        updated_at = now()
                    where payment_reference = :payment_reference
                      and lower(coalesce(payment_status, '')) = 'pending'
                """),
                {"payment_reference": tx_ref},
            )
            await session.commit()

        return await query.answer(
            "⚠️ Payment service is unavailable right now. Please try again shortly.",
            show_alert=True,
        )

    subject_names = []
    for code in subject_codes:
        subject = get_subject_by_code(code)
        if subject:
            subject_names.append(f"• {subject['name']}")

    message_text = (
        "👥 *Mock WAEC Multiplayer Host Access*\n\n"
        "*Subjects:*\n"
        f"{chr(10).join(subject_names)}\n\n"
        f"*Friends to Invite:* {invitee_count}\n"
        f"*Total Players Required:* {invitee_count + 1} (1 Host + {invitee_count} Friend{'s' if invitee_count != 1 else ''})\n\n"
        f"*Host Fee:* ₦{amount}\n\n"
        "You need to pay first before your multiplayer room can be created.\n\n"
        "Tap below to continue to payment."
    )

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"💳 Pay ₦{amount}", url=checkout_url)],
            [InlineKeyboardButton("⬅️ Back", callback_data="mw_mode_friends")],
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


async def mockwaec_room_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    if not user:
        return

    room_code = str(context.user_data.get("mw_room_code") or "").strip().upper()
    if not room_code:
        return await query.answer("No active room found.", show_alert=True)

    host_payment_reference = None
    host_session = None
    host_subject_codes = []
    host_user_id = int(user.id)

    async with get_async_session() as session:
        room = await get_mockwaec_room_by_code(
            session,
            room_code=room_code,
        )
        if not room:
            return await query.answer("Room not found.", show_alert=True)

        room_host_user_id = int(room.get("host_user_id") or 0)
        if int(user.id) != room_host_user_id:
            return await query.answer("Only the host can start the match.", show_alert=True)

        room_status = str(room.get("status") or "").strip().lower()

        if room_status == "in_progress":
            return await query.answer(
                "⚠️ This match has already started. Use Resume Match.",
                show_alert=True,
            )

        if room_status == "completed":
            return await query.answer(
                "⚠️ This match has already ended. Please create a new room for a new match.",
                show_alert=True,
            )

        players = await list_mockwaec_room_players(
            session,
            room_code=room_code,
        )

        joined_count = len(players)

        if joined_count < 2:
            return await query.answer(
                "⚠️ The Match needs at least 2 players.",
                show_alert=True,
            )

        eligible_players = []
        not_ready_players_exist = False

        for player in players:
            is_host = bool(player.get("is_host"))
            payment_status = str(player.get("payment_status") or "").strip().lower()
            is_ready = bool(player.get("is_ready"))

            if is_host:
                eligible_players.append(player)
                continue

            if payment_status == "successful" and is_ready:
                eligible_players.append(player)
            else:
                not_ready_players_exist = True

        if len(eligible_players) < 2:
            if not_ready_players_exist:
                return await query.answer(
                    "⚠️ Players must be ready before you start.",
                    show_alert=True,
                )
            return await query.answer(
                "⚠️ The Match needs at least 2 players.",
                show_alert=True,
            )

        try:
            for player in eligible_players:
                player_user_id = int(player.get("user_id") or 0)

                payment_result = await session.execute(
                    text("""
                        select
                            payment_reference,
                            subject_codes_json
                        from public.mockwaec_payments
                        where upper(room_code) = :room_code
                          and user_id = :user_id
                          and lower(coalesce(payment_status, '')) = 'successful'
                        order by updated_at desc, created_at desc
                        limit 1
                    """),
                    {
                        "room_code": room_code,
                        "user_id": player_user_id,
                    },
                )
                payment_row = payment_result.mappings().first()

                if not payment_row:
                    return await query.answer(
                        f"⚠️ Could not find a successful payment record for player {player_user_id}.",
                        show_alert=True,
                    )

                payment_reference = str(payment_row.get("payment_reference") or "").strip()
                raw_subject_codes = payment_row.get("subject_codes_json")

                if not payment_reference or raw_subject_codes is None:
                    return await query.answer(
                        f"⚠️ Payment data is incomplete for player {player_user_id}.",
                        show_alert=True,
                    )

                normalized_subject_codes = []
                if isinstance(raw_subject_codes, list):
                    normalized_subject_codes = raw_subject_codes
                elif isinstance(raw_subject_codes, str):
                    try:
                        parsed = json.loads(raw_subject_codes)
                        if isinstance(parsed, list):
                            normalized_subject_codes = parsed
                        else:
                            normalized_subject_codes = []
                    except Exception:
                        # fallback for old python-list-like strings
                        try:
                            import ast
                            parsed = ast.literal_eval(raw_subject_codes)
                            if isinstance(parsed, list):
                                normalized_subject_codes = parsed
                            else:
                                normalized_subject_codes = []
                        except Exception:
                            normalized_subject_codes = []
                else:
                    normalized_subject_codes = []

                if not normalized_subject_codes:
                    return await query.answer(
                        f"⚠️ Subject data is invalid for player {player_user_id}.",
                        show_alert=True,
                    )

                normalized_subject_codes_json = json.dumps(normalized_subject_codes)

                session_row = await get_or_create_mockwaec_session_from_payment(
                    session,
                    payment_reference=payment_reference,
                    user_id=player_user_id,
                    course_code="custom",
                    subject_codes_json=normalized_subject_codes_json,
                )

                if player_user_id == int(user.id):
                    host_payment_reference = payment_reference
                    host_session = session_row
                    host_subject_codes = normalized_subject_codes

            duration_minutes = int(room.get("duration_minutes") or 180)

            await session.execute(
                text("""
                    update public.mockwaec_rooms
                    set
                        status = 'in_progress',
                        all_players_ready = false,
                        started_by_host = true,
                        started_at = now(),
                        ends_at = now() + make_interval(mins => :duration_minutes),
                        updated_at = now()
                    where upper(room_code) = :room_code
                """),
                {
                    "room_code": room_code,
                    "duration_minutes": duration_minutes,
                },
            )

            await session.commit()

        except Exception as e:
            await session.rollback()
            logger.exception(
                "Failed to start multiplayer Mock WAEC room | room_code=%s | host_user_id=%s | err=%s",
                room_code,
                int(user.id),
                e,
            )
            return await query.answer(
                "⚠️ Could not start the match right now. Please try again.",
                show_alert=True,
            )

    try:
        await notify_mockwaec_room_players_match_started(
            context,
            room_code=room_code,
            host_user_id=host_user_id,
            players=eligible_players,
        )
    except Exception:
        logger.exception(
            "Failed to notify WAEC invitees that room match started | room_code=%s | host_user_id=%s",
            room_code,
            host_user_id,
        )

    if not host_payment_reference or not host_session or not host_subject_codes:
        return await query.answer(
            "⚠️ Match was marked started, but the host exam session could not be opened.",
            show_alert=True,
        )

    context.user_data["mw_course_code"] = "custom"
    context.user_data["mw_subject_codes"] = host_subject_codes
    context.user_data["mw_mode"] = "friends"
    context.user_data["mw_room_code"] = room_code
    context.user_data["mw_payment_reference"] = host_payment_reference
    context.user_data["mw_session_id"] = host_session["id"]

    message_text = build_mockwaec_exam_ready_text(host_subject_codes)
    markup = make_mockwaec_exam_ready_keyboard(host_subject_codes)

    try:
        await query.edit_message_text(
            text=message_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            text=message_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )


async def notify_mockwaec_room_players_match_started(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    room_code: str,
    host_user_id: int,
    players: list[dict],
) -> None:
    room_code = str(room_code or "").strip().upper()
    if not room_code:
        return

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 Resume Match", callback_data=f"mwr_resume_match::{room_code}")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )

    message_text = (
        "🚀 <b>Mock WAEC Multiplayer Match Started</b>\n\n"
        f"<b>Room Code:</b> <code>{room_code}</code>\n\n"
        "The host has started the match.\n"
        "Tap below to enter your Mock WAEC exam."
    )

    for player in players:
        player_user_id = int(player.get("user_id") or 0)
        if not player_user_id or player_user_id == int(host_user_id):
            continue

        try:
            await context.bot.send_message(
                chat_id=player_user_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception(
                "Failed to notify WAEC room player that match started | room_code=%s | player_user_id=%s",
                room_code,
                player_user_id,
            )


async def mockwaec_room_resume_match_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    if not user:
        return

    try:
        _, room_code = query.data.split("::", 1)
    except Exception:
        return await query.answer("Invalid room resume request.", show_alert=True)

    room_code = str(room_code or "").strip().upper()
    if not room_code:
        return await query.answer("Invalid room code.", show_alert=True)

    await query.answer()

    async with get_async_session() as session:
        room = await get_mockwaec_room_by_code(session, room_code=room_code)
        if not room:
            return await query.answer("Room not found.", show_alert=True)

        payment_result = await session.execute(
            text("""
                select
                    payment_reference,
                    subject_codes_json
                from public.mockwaec_payments
                where upper(room_code) = :room_code
                  and user_id = :user_id
                  and lower(coalesce(payment_status, '')) = 'successful'
                order by updated_at desc, created_at desc
                limit 1
            """),
            {
                "room_code": room_code,
                "user_id": int(user.id),
            },
        )
        payment_row = payment_result.mappings().first()

        if not payment_row:
            return await query.answer(
                "⚠️ Could not find your room payment record.",
                show_alert=True,
            )

        payment_reference = str(payment_row.get("payment_reference") or "").strip()
        raw_subject_codes = payment_row.get("subject_codes_json")

        normalized_subject_codes = []
        if isinstance(raw_subject_codes, list):
            normalized_subject_codes = raw_subject_codes
        elif isinstance(raw_subject_codes, str):
            try:
                parsed = json.loads(raw_subject_codes)
                if isinstance(parsed, list):
                    normalized_subject_codes = parsed
            except Exception:
                try:
                    import ast
                    parsed = ast.literal_eval(raw_subject_codes)
                    if isinstance(parsed, list):
                        normalized_subject_codes = parsed
                except Exception:
                    normalized_subject_codes = []

        if not payment_reference or not normalized_subject_codes:
            return await query.answer(
                "⚠️ Your room subject data is incomplete.",
                show_alert=True,
            )

        session_row = await get_or_create_mockwaec_session_from_payment(
            session,
            payment_reference=payment_reference,
            user_id=int(user.id),
            course_code="custom",
            subject_codes_json=json.dumps(normalized_subject_codes),
        )
        await session.commit()

    context.user_data["mw_course_code"] = "custom"
    context.user_data["mw_subject_codes"] = normalized_subject_codes
    context.user_data["mw_mode"] = "friends"
    context.user_data["mw_room_code"] = room_code
    context.user_data["mw_payment_reference"] = payment_reference
    context.user_data["mw_session_id"] = session_row["id"]

    message_text = build_mockwaec_exam_ready_text(normalized_subject_codes)
    markup = make_mockwaec_exam_ready_keyboard(normalized_subject_codes)

    try:
        await query.edit_message_text(
            text=message_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            text=message_text,
            parse_mode="Markdown",
            reply_markup=markup,
        )


async def mockwaec_room_pick_subjects_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    room_code = str(context.user_data.get("mw_room_code") or "").strip().upper()
    if not room_code:
        return await query.answer("⚠️ No active room found.", show_alert=True)

    async with get_async_session() as session:
        room = await get_mockwaec_room_by_code(session, room_code=room_code)
        if not room:
            return await query.answer("⚠️ Room not found.", show_alert=True)

        player = await get_mockwaec_room_player(
            session,
            room_code=room_code,
            user_id=int(query.from_user.id),
        )
        if not player:
            return await query.answer("⚠️ You are not in this room.", show_alert=True)

    context.user_data["mw_mode"] = "friends"
    context.user_data["mw_subject_select_from_room"] = True

    existing_subject_codes = []
    try:
        existing_subject_codes = json.loads(player.get("subject_codes_json") or "[]")
    except Exception:
        existing_subject_codes = []

    if not isinstance(existing_subject_codes, list):
        existing_subject_codes = []

    context.user_data["mw_subject_codes"] = existing_subject_codes

    text = build_mockwaec_subject_selection_text(existing_subject_codes)
    markup = make_mockwaec_subject_selection_keyboard(existing_subject_codes)

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
# Mock WAEC Pay Solo Handler
# -------------------------------------------
async def mockwaec_pay_solo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    subject_codes = context.user_data.get("mw_subject_codes") or []

    if not subject_codes:
        return await query.message.reply_text(
            "⚠️ Your Mock WAEC / NECO setup is incomplete.\n\nPlease choose your subjects again.",
            reply_markup=make_mockwaec_welcome_keyboard(),
        )

    amount = MOCKWAEC_SOLO_FEE
    user = query.from_user
    tg_id = user.id
    username = user.username or f"user_{tg_id}"
    email = f"{username}@naijaprizegate.ng"

    tx_ref = build_tx_ref("MOCKWAEC")
    subject_codes_json = json.dumps(subject_codes)

    async with get_async_session() as session:
        await create_pending_mockwaec_payment(
            session,
            payment_reference=tx_ref,
            user_id=tg_id,
            amount_paid=amount,
            course_code="custom",
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
            "product_type": "MOCKWAEC",
            "course_code": "custom",
            "exam_mode": "solo",
        },
        product_type="MOCKWAEC",
    )

    if not checkout_url:
        async with get_async_session() as session:
            await session.execute(
                text("""
                    update public.mockwaec_payments
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

    subject_names = []
    for code in subject_codes:
        subject = get_subject_by_code(code)
        if subject:
            subject_names.append(f"• {subject['name']}")

    message_text = (
        "💳 *Mock WAEC / NECO Payment*\n\n"
        "*Subjects:*\n"
        f"{chr(10).join(subject_names)}\n\n"
        f"*Amount:* ₦{amount}\n\n"
        "Tap below to complete your payment securely."
    )

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Pay Securely", url=checkout_url)],
            [InlineKeyboardButton("⬅️ Back", callback_data="mw_mode_solo")],
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
# Mockwaec Payment Success Handler
# ---------------------------------------------
async def mockwaec_payment_success_handler(
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
        payment = await get_mockwaec_payment(session, tx_ref)

        if not payment:
            await send_response(
                "⚠️ Mock WAEC / NECO payment record not found. Please contact support if payment was deducted."
            )
            return

        if str(payment.get("payment_status") or "").strip().lower() != "successful":
            await send_response(
                "⚠️ Your Mock WAEC / NECO payment is not yet marked successful. Please wait a moment and try again."
            )
            return

        course_code = str(payment.get("course_code") or "").strip()
        subject_codes_raw = payment.get("subject_codes_json") or "[]"
        exam_mode = str(payment.get("exam_mode") or "solo").strip().lower()
        invitee_count = int(payment.get("invitee_count") or 0)
        required_player_count = int(payment.get("required_player_count") or 0)
        payer_user_id = int(payment.get("user_id") or 0)

        try:
            subject_codes = json.loads(subject_codes_raw) if isinstance(subject_codes_raw, str) else subject_codes_raw
        except Exception:
            subject_codes = []

        if not isinstance(subject_codes, list):
            subject_codes = []

        if not subject_codes or payer_user_id <= 0:
            await send_response(
                "⚠️ Your saved Mock WAEC / NECO exam data is incomplete. Please contact support."
            )
            return

        # ============================================================
        # FRIENDS / MULTIPLAYER FLOW
        # ============================================================
        if exam_mode == "friends":
            room_code = ""
            players = []
            room = None

            try:
                total_required_players = (
                    required_player_count
                    if required_player_count > 0
                    else max(2, invitee_count + 1)
                )

                duration_minutes = get_mockwaec_exam_duration_minutes(len(subject_codes))

                room = await create_mockwaec_room(
                    session,
                    host_user_id=payer_user_id,
                    duration_minutes=duration_minutes,
                    required_player_count=total_required_players,
                )

                room_code = str((room or {}).get("room_code") or "").strip().upper()
                if not room_code:
                    raise ValueError("Room code was not created.")

                payer_tg_user = update.effective_user

                await add_mockwaec_room_player(
                    session,
                    room_code=room_code,
                    user_id=payer_user_id,
                    course_code=course_code,
                    subject_codes_json=json.dumps(subject_codes),
                    is_host=True,
                    has_paid=True,
                    first_name=(payer_tg_user.first_name if payer_tg_user else None),
                    last_name=(payer_tg_user.last_name if payer_tg_user else None),
                    username=(payer_tg_user.username if payer_tg_user else None),
                )

                await session.execute(
                    text("""
                        update public.mockwaec_room_players
                        set
                            payment_status = 'successful',
                            paid_at = coalesce(paid_at, now()),
                            is_ready = true,
                            ready_at = coalesce(ready_at, now()),
                            updated_at = now()
                        where upper(room_code) = :room_code
                          and user_id = :user_id
                    """),
                    {
                        "room_code": room_code,
                        "user_id": payer_user_id,
                    },
                )
                 
                players = await list_mockwaec_room_players(
                    session,
                    room_code=room_code,
                )

                await session.execute(
                    text("""
                        update public.mockwaec_payments
                        set
                            room_code = :room_code,
                            updated_at = now()
                        where payment_reference = :payment_reference
                    """),
                    {
                        "room_code": room_code,
                        "payment_reference": tx_ref,
                    },
                )

                await session.commit()

            except Exception as e:
                await session.rollback()
                logger.exception(
                    "Failed to create paid WAEC multiplayer room | tx_ref=%s | user_id=%s | err=%s",
                    tx_ref,
                    payer_user_id,
                    e,
                )
                await send_response(
                    "⚠️ Payment succeeded, but room creation failed. Please try again. If it still fails, contact support."
                )
                return

            context.user_data["mw_course_code"] = course_code
            context.user_data["mw_subject_codes"] = subject_codes
            context.user_data["mw_mode"] = "friends"
            context.user_data["mw_room_code"] = room_code
            context.user_data["mw_payment_reference"] = tx_ref
            context.user_data["mw_session_id"] = None
            context.user_data["mw_invitee_count"] = invitee_count
            context.user_data["mw_required_player_count"] = total_required_players

            bot_username = ""
            try:
                me = await context.bot.get_me()
                bot_username = me.username or ""
            except Exception:
                bot_username = ""

            invite_link = build_mockwaec_invite_link(bot_username, room_code)

            message_text = build_mockwaec_waiting_room_text(
                room_code=room_code,
                invite_link=invite_link,
                room_status="waiting",
                players=players,
                host_user_id=payer_user_id,
                expected_players=int((room or {}).get("expected_players") or 0),
            )

            markup = make_mockwaec_room_waiting_keyboard(
                is_host=True,
                room_code=room_code,
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
                        parse_mode="HTML",
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    sent_message = await query.message.reply_text(
                        text=message_text,
                        parse_mode="HTML",
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )

            elif update.message:
                sent_message = await update.message.reply_text(
                    text=message_text,
                    parse_mode="HTML",
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )

            if sent_message:
                async with get_async_session() as save_session:
                    await save_session.execute(
                        text("""
                            update public.mockwaec_rooms
                            set
                                host_waiting_message_id = :message_id,
                                updated_at = now()
                            where upper(room_code) = :room_code
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
            room_code = str(payment.get("room_code") or "").strip().upper()

            if not room_code:
                await send_response(
                    "⚠️ Payment succeeded, but your room code is missing from the payment record. Please contact support."
                )
                return

            try:
                room = await get_mockwaec_room_by_code(
                    session,
                    room_code=room_code,
                )
                if not room:
                    await send_response(
                        "⚠️ Payment succeeded, but the room was not found."
                    )
                    return

                payer_tg_user = update.effective_user

                existing_player = await get_mockwaec_room_player(
                    session,
                    room_code=room_code,
                    user_id=payer_user_id,
                )

                if existing_player:
                    await session.execute(
                        text("""
                            update public.mockwaec_room_players
                            set
                                subject_codes_json = :subject_codes_json,
                                payment_status = 'successful',
                                paid_at = now(),
                                updated_at = now()
                            where upper(room_code) = :room_code
                              and user_id = :user_id
                        """),
                        {
                            "room_code": room_code,
                            "user_id": payer_user_id,
                            "subject_codes_json": json.dumps(subject_codes),
                        },
                    )
                else:
                    await add_mockwaec_room_player(
                        session,
                        room_code=room_code,
                        user_id=payer_user_id,
                        course_code="custom",
                        subject_codes_json=json.dumps(subject_codes),
                        is_host=False,
                        has_paid=True,
                        first_name=(payer_tg_user.first_name if payer_tg_user else None),
                        last_name=(payer_tg_user.last_name if payer_tg_user else None),
                        username=(payer_tg_user.username if payer_tg_user else None),
                    )

                players = await list_mockwaec_room_players(
                    session,
                    room_code=room_code,
                )

                await session.commit()

            except Exception as e:
                await session.rollback()
                logger.exception(
                    "Failed to complete WAEC room friend payment flow | tx_ref=%s | room_code=%s | user_id=%s | err=%s",
                    tx_ref,
                    room_code,
                    payer_user_id,
                    e,
                )
                await send_response(
                    "⚠️ Payment succeeded, but your room access could not be updated right now. Please re-open the room."
                )
                return

            context.user_data["mw_course_code"] = "custom"
            context.user_data["mw_subject_codes"] = subject_codes
            context.user_data["mw_mode"] = "friends"
            context.user_data["mw_room_code"] = room_code
            context.user_data["mw_payment_reference"] = tx_ref
            context.user_data["mw_session_id"] = None

            bot_username = ""
            try:
                me = await context.bot.get_me()
                bot_username = me.username or ""
            except Exception:
                bot_username = ""

            invite_link = build_mockwaec_invite_link(bot_username, room_code)

            message_text = build_mockwaec_waiting_room_text(
                room_code=room_code,
                invite_link=invite_link,
                room_status="waiting",
                players=players,
                host_user_id=int((room or {}).get("host_user_id") or 0),
                expected_players=int((room or {}).get("expected_players") or 0),
            )

            current_player = None
            for player in players:
                if int(player.get("user_id") or 0) == int(payer_user_id):
                    current_player = player
                    break

            markup = make_mockwaec_room_waiting_keyboard(
                is_host=False,
                room_code=room_code,
                has_subjects=True,
                has_paid=True,
                is_ready=bool((current_player or {}).get("is_ready")),
            )

            await send_response(
                message_text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True,
            )

            try:
                await refresh_mockwaec_host_waiting_room(context, room_code)
            except Exception:
                logger.exception(
                    "Failed to auto-refresh WAEC host waiting room after invitee payment | room_code=%s",
                    room_code,
                )

            return
        
        # ============================================================
        # SOLO FLOW
        # ============================================================
        try:
            mw_session = await get_or_create_mockwaec_session_from_payment(
                session,
                payment_reference=tx_ref,
                user_id=payer_user_id,
                course_code=course_code,
                subject_codes_json=json.dumps(subject_codes),
            )
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.exception(
                "Failed to create Mock WAEC solo session | tx_ref=%s | err=%s",
                tx_ref,
                e,
            )
            await send_response(
                "⚠️ Payment succeeded, but your exam session could not be created right now. Please try again."
            )
            return

    context.user_data["mw_course_code"] = course_code
    context.user_data["mw_subject_codes"] = subject_codes
    context.user_data["mw_mode"] = exam_mode
    context.user_data["mw_room_code"] = None
    context.user_data["mw_payment_reference"] = tx_ref
    context.user_data["mw_session_id"] = mw_session["id"]

    message_text = build_mockwaec_exam_ready_text(subject_codes)
    markup = make_mockwaec_exam_ready_keyboard(subject_codes)

    await send_response(
        message_text,
        parse_mode="Markdown",
        reply_markup=markup,
    )


# ------------------------------------------------
# Mock WAEC Start Subject Handler
# -----------------------------------------------
async def mockwaec_start_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, subject_code = query.data.split("::", 1)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid subject selection.")

    payment_reference = context.user_data.get("mw_payment_reference")
    mw_subject_codes = context.user_data.get("mw_subject_codes") or []

    if not payment_reference:
        return await query.message.reply_text(
            "⚠️ Mock WAEC / NECO payment reference not found. Please restart from your paid exam link."
        )

    if subject_code not in mw_subject_codes:
        return await query.message.reply_text(
            "⚠️ That subject is not part of your current Mock WAEC / NECO subject combination."
        )

    user_id = query.from_user.id

    async with get_async_session() as session:
        try:
            active_session = await get_mockwaec_session_by_payment_reference(
                session,
                payment_reference,
            )

            if not active_session:
                return await query.message.reply_text(
                    "⚠️ Mock WAEC / NECO session not found."
                )

            if is_mockwaec_time_expired(active_session.get("exam_ends_at")):
                await session.execute(
                    text("""
                        update public.mockwaec_sessions
                        set
                            status = 'completed',
                            updated_at = now()
                        where payment_reference = :payment_reference
                    """),
                    {"payment_reference": payment_reference},
                )
                await session.commit()

                result_payload = await build_mockwaec_result_from_session(
                    payment_reference=payment_reference,
                    subject_codes=mw_subject_codes,
                )

                if not result_payload:
                    return await query.message.reply_text(
                        "⏰ Mock WAEC / NECO time is up.\n\nYour exam has ended."
                    )

                message_text, markup = result_payload
                timeout_text = (
                    "⏰ *Mock WAEC / NECO time is up.*\n\n"
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

            result = await start_mockwaec_subject(
                session,
                payment_reference=payment_reference,
                user_id=int(user_id),
                subject_code=subject_code,
            )
            await session.commit()

        except Exception as e:
            await session.rollback()
            logger.exception(
                "Failed to start Mock WAEC / NECO subject | tx_ref=%s | subject=%s | err=%s",
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

    context.user_data["mw_current_subject_code"] = subject_code
    context.user_data["mw_current_question_order"] = 1
    context.user_data["mw_last_passage_id_shown"] = ""
    context.user_data["mw_active_passage_message_id"] = None

    total_questions = get_mockwaec_subject_question_count(subject_code)

    question_text = build_mockwaec_question_only_text(
        subject_code=subject_code,
        question_row=current_question,
        question_number=1,
        total_questions=total_questions,
        exam_ends_at=session_row.get("exam_ends_at"),
    )

    markup = make_mockwaec_question_answer_keyboard(
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

        passage_text = build_mockwaec_passage_text(
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

            store_mockwaec_passage_message_id(
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

            store_mockwaec_passage_message_id(
                message_id=sent_passage.message_id,
                context=context,
            )

            await query.message.reply_text(
                question_text,
                parse_mode="MarkdownV2",
                reply_markup=markup,
            )
        return

    await clear_mockwaec_passage_message(
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
# Mock WAEC Answer Handler
# -----------------------------------------------
async def mockwaec_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, subject_code, question_order_raw, selected_option = query.data.split("::", 3)
        question_order = int(question_order_raw)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid answer submission.")

    payment_reference = context.user_data.get("mw_payment_reference")
    course_code = context.user_data.get("mw_course_code")
    subject_codes = context.user_data.get("mw_subject_codes") or []

    if not payment_reference or not course_code:
        return await query.message.reply_text(
            "⚠️ Mock WAEC / NECO exam session not found."
        )

    async with get_async_session() as session:
        try:
            active_session = await get_mockwaec_session_by_payment_reference(
                session,
                payment_reference,
            )

            if not active_session:
                return await query.message.reply_text(
                    "⚠️ Mock WAEC / NECO session not found."
                )

            # ---------------------------------------------------
            # STALE BUTTON / ENDED EXAM GUARD
            # ---------------------------------------------------
            current_subject_code = str(active_session.get("current_subject_code") or "").strip()
            current_question_index = int(active_session.get("current_question_index") or 0)
            expected_question_order = max(1, current_question_index + 1)
            session_status = str(active_session.get("status") or "").strip().lower()

            if session_status == "completed":
                result_payload = await build_mockwaec_result_from_session(
                    payment_reference=payment_reference,
                    course_code=course_code,
                    subject_codes=subject_codes,
                )

                if not result_payload:
                    return await query.message.reply_text(
                        "⚠️ This Mock WAEC / NECO exam has already ended."
                    )

                message_text, markup = result_payload
                ended_text = (
                    "⚠️ *This Mock WAEC / NECO exam has already ended.*\n\n"
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
                    reply_markup=make_mockwaec_stale_action_keyboard(),
                )

            # ---------------------------------------------------
            # TIME EXPIRY GUARD
            # ---------------------------------------------------
            if is_mockwaec_time_expired(active_session.get("exam_ends_at")):
                await session.execute(
                    text("""
                        update public.mockwaec_sessions
                        set
                            status = 'completed',
                            updated_at = now()
                        where payment_reference = :payment_reference
                    """),
                    {"payment_reference": payment_reference},
                )
                await session.commit()

                result_payload = await build_mockwaec_result_from_session(
                    payment_reference=payment_reference,
                    course_code=course_code,
                    subject_codes=subject_codes,
                )

                if not result_payload:
                    return await query.message.reply_text(
                        "⏰ Mock WAEC / NECO time is up.\n\nYour exam has ended."
                    )

                message_text, markup = result_payload
                timeout_text = (
                    "⏰ *Mock WAEC / NECO time is up.*\n\n"
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

            answer_result = await answer_mockwaec_question(
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

                session_row = await get_mockwaec_session_by_payment_reference(
                    session,
                    payment_reference,
                )

                context.user_data["mw_current_subject_code"] = subject_code
                context.user_data["mw_current_question_order"] = next_question_order

                question_text = build_mockwaec_question_only_text(
                    subject_code=subject_code,
                    question_row=next_question,
                    question_number=next_question_order,
                    total_questions=total_questions,
                    exam_ends_at=(session_row or {}).get("exam_ends_at"),
                )

                markup = make_mockwaec_question_answer_keyboard(
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
                                from public.mockwaec_subject_questions
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

                        passage_text = build_mockwaec_passage_text(
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

                            store_mockwaec_passage_message_id(
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

                            store_mockwaec_passage_message_id(
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
                await clear_mockwaec_passage_message(
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
                await clear_mockwaec_passage_message(
                    chat_id=query.message.chat_id,
                    context=context,
                )
                
                score_info = await calculate_mockwaec_subject_score(
                    session,
                    payment_reference=payment_reference,
                    subject_code=subject_code,
                )

                session_row = await mark_mockwaec_subject_completed(
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
                context.user_data["mw_last_passage_id_shown"] = ""

                if remaining_subject_codes:
                    message_text = build_mockwaec_subject_completed_text(
                        completed_subject_code=subject_code,
                        score_100=int(score_info["score_100"]),
                        remaining_subject_codes=remaining_subject_codes,
                    )
                    markup = make_mockwaec_next_subject_keyboard(remaining_subject_codes)
                else:
                    answered_counts = {}
                    correct_counts = {}

                    for code in subject_codes:
                        stats = await get_mockwaec_subject_result_stats(
                            session,
                            payment_reference=payment_reference,
                            subject_code=code,
                        )
                        answered_counts[code] = int(stats.get("answered_count") or 0)
                        correct_counts[code] = int(stats.get("correct_count") or 0)

                    message_text = build_mockwaec_final_result_text(
                        subject_codes=subject_codes,
                        scores=scores,
                        answered_counts=answered_counts,
                        correct_counts=correct_counts,
                    )
                    markup = make_mockwaec_final_result_keyboard()

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
                "Failed to process Mock WAEC / NECO answer | tx_ref=%s | subject=%s | q=%s | err=%s",
                payment_reference,
                subject_code,
                question_order,
                e,
            )
            return await query.message.reply_text(
                "⚠️ Could not process your answer right now. Please try again."
            )


# -------------------------------------------------
# Mock WAEC Return to Exam Ready Handler
# --------------------------------------------------
async def mockwaec_return_to_exam_ready_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    course_code = context.user_data.get("mw_course_code")
    subject_codes = context.user_data.get("mw_subject_codes") or []

    if not course_code or not subject_codes:
        return await query.message.reply_text(
            "⚠️ Mock WAEC / NECO exam state not found."
        )

    message_text = build_mockwaec_exam_ready_text(subject_codes)
    markup = make_mockwaec_exam_ready_keyboard(subject_codes)

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



async def mockwaec_review_open_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    mode = "all" if query.data == "mw_review_all" else "wrong"
    payment_reference = context.user_data.get("mw_payment_reference")
    subject_codes = context.user_data.get("mw_subject_codes") or []

    if not payment_reference:
        return await query.message.reply_text(
            "⚠️ Mock WAEC / NECO result session not found."
        )

    async with get_async_session() as session:
        review_rows = await get_mockwaec_review_rows(
            session,
            payment_reference=payment_reference,
            wrong_only=(mode == "wrong"),
        )

    review_rows = sort_review_rows_by_subject_order(review_rows, subject_codes)

    if not review_rows:
        if mode == "wrong":
            return await query.message.reply_text(
                "✅ No wrong answers found in this Mock WAEC / NECO result."
            )
        return await query.message.reply_text(
            "⚠️ No review items found for this Mock WAEC / NECO result."
        )

    context.user_data["mw_review_mode"] = mode
    context.user_data["mw_review_rows"] = review_rows
    context.user_data["mw_review_index"] = 0

    message_text = build_mockwaec_review_text(
        review_row=review_rows[0],
        review_index=1,
        total_reviews=len(review_rows),
    )
    markup = make_mockwaec_review_nav_keyboard(
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


async def mockwaec_review_nav_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, mode, index_raw = query.data.split("::", 2)
        index = int(index_raw)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid review navigation.")

    review_rows = context.user_data.get("mw_review_rows") or []
    if not review_rows:
        return await query.message.reply_text("⚠️ No review session found.")

    if index < 0 or index >= len(review_rows):
        return await query.message.reply_text("⚠️ Review item out of range.")

    context.user_data["mw_review_mode"] = mode
    context.user_data["mw_review_index"] = index

    message_text = build_mockwaec_review_text(
        review_row=review_rows[index],
        review_index=index + 1,
        total_reviews=len(review_rows),
    )
    markup = make_mockwaec_review_nav_keyboard(
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


async def mockwaec_back_to_result_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    payment_reference = context.user_data.get("mw_payment_reference")
    course_code = context.user_data.get("mw_course_code")
    subject_codes = context.user_data.get("mw_subject_codes") or []

    if not payment_reference or not course_code:
        return await query.message.reply_text(
            "⚠️ Mock WAEC / NECO result session not found."
        )

    async with get_async_session() as session:
        session_row = await get_mockwaec_session_by_payment_reference(
            session,
            payment_reference,
        )

        if not session_row:
            return await query.message.reply_text(
                "⚠️ Could not reload your Mock WAEC / NECO result."
            )

        try:
            scores = json.loads(session_row.get("scores_json") or "{}")
        except Exception:
            scores = {}

        answered_counts = {}
        correct_counts = {}

        for code in subject_codes:
            stats = await get_mockwaec_subject_result_stats(
                session,
                payment_reference=payment_reference,
                subject_code=code,
            )
            answered_counts[code] = int(stats.get("answered_count") or 0)
            correct_counts[code] = int(stats.get("correct_count") or 0)

    message_text = build_mockwaec_final_result_text(
        subject_codes=subject_codes,
        scores=scores,
        answered_counts=answered_counts,
        correct_counts=correct_counts,
    )
    markup = make_mockwaec_final_result_keyboard()

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
# Mock WAEC Resume Exam Handler
# ---------------------------------------
async def mockwaec_resume_exam_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user_id = query.from_user.id

    async with get_async_session() as session:
        active_session = await get_latest_active_mockwaec_session_for_user(
            session,
            user_id=int(user_id),
        )

    if not active_session:
        return await query.message.reply_text(
            "⚠️ No active Mock WAEC / NECO exam was found."
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
            "⚠️ This Mock WAEC / NECO session is incomplete and cannot be resumed."
        )

    context.user_data["mw_course_code"] = course_code
    context.user_data["mw_subject_codes"] = subject_codes
    context.user_data["mw_mode"] = "solo"
    context.user_data["mw_room_code"] = None
    context.user_data["mw_payment_reference"] = payment_reference
    context.user_data["mw_session_id"] = active_session["id"]

    if is_mockwaec_time_expired(active_session.get("exam_ends_at")):
        await clear_mockwaec_passage_message(
            chat_id=query.message.chat_id,
            context=context,
        )

        message_text = build_mockwaec_resume_prompt_text(
            subject_codes=subject_codes,
            completed_subjects=completed_subjects,
            current_subject_code=current_subject_code,
            current_question_index=current_question_index,
            exam_ends_at=active_session.get("exam_ends_at"),
        )
        markup = make_mockwaec_time_up_keyboard()

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
        await clear_mockwaec_passage_message(
            chat_id=query.message.chat_id,
            context=context,
        )
        
        answered_counts = {}
        correct_counts = {}

        async with get_async_session() as session:
            for code in subject_codes:
                stats = await get_mockwaec_subject_result_stats(
                    session,
                    payment_reference=payment_reference,
                    subject_code=code,
                )
                answered_counts[code] = int(stats.get("answered_count") or 0)
                correct_counts[code] = int(stats.get("correct_count") or 0)

        message_text = build_mockwaec_final_result_text(
            subject_codes=subject_codes,
            scores=scores,
            answered_counts=answered_counts,
            correct_counts=correct_counts,
        )
        markup = make_mockwaec_final_result_keyboard()

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
            question_row = await get_mockwaec_subject_question_by_order(
                session,
                payment_reference=payment_reference,
                subject_code=current_subject_code,
                question_order=next_question_order,
            )

        if question_row:
            context.user_data["mw_current_subject_code"] = current_subject_code
            context.user_data["mw_current_question_order"] = next_question_order

            total_questions = get_mockwaec_subject_question_count(current_subject_code)

            question_text = build_mockwaec_question_only_text(
                subject_code=current_subject_code,
                question_row=question_row,
                question_number=next_question_order,
                total_questions=total_questions,
                exam_ends_at=active_session.get("exam_ends_at"),
            )

            markup = make_mockwaec_question_answer_keyboard(
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
                            from public.mockwaec_subject_questions
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
                
                passage_text = build_mockwaec_passage_text(
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

                    store_mockwaec_passage_message_id(
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

                    store_mockwaec_passage_message_id(
                        message_id=sent_passage.message_id,
                        context=context,
                    )

                    await query.message.reply_text(
                        question_text,
                        parse_mode="MarkdownV2",
                        reply_markup=markup,
                    )
                return

            await clear_mockwaec_passage_message(
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

    if remaining_subject_codes:
        await clear_mockwaec_passage_message(
            chat_id=query.message.chat_id,
            context=context,
        )
        
        message_text = build_mockwaec_continue_subject_choice_text(
            remaining_subject_codes=remaining_subject_codes,
        )
        markup = make_mockwaec_next_subject_keyboard(remaining_subject_codes)

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

    await clear_mockwaec_passage_message(
        chat_id=query.message.chat_id,
        context=context,
    )
    
    answered_counts = {}
    correct_counts = {}

    async with get_async_session() as session:
        for code in subject_codes:
            stats = await get_mockwaec_subject_result_stats(
                session,
                payment_reference=payment_reference,
                subject_code=code,
            )
            answered_counts[code] = int(stats.get("answered_count") or 0)
            correct_counts[code] = int(stats.get("correct_count") or 0)

    message_text = build_mockwaec_final_result_text(
        subject_codes=subject_codes,
        scores=scores,
        answered_counts=answered_counts,
        correct_counts=correct_counts,
    )
    markup = make_mockwaec_final_result_keyboard()

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
# MockWAEC Submit Exam Confirmation
# -------------------------------------------
async def mockwaec_submit_exam_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    text = build_mockwaec_submit_exam_confirm_text()
    markup = make_mockwaec_submit_exam_confirm_keyboard()

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


async def mockwaec_submit_exam_no_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    payment_reference = context.user_data.get("mw_payment_reference")
    if not payment_reference:
        return await query.message.reply_text(
            "⚠️ No active Mock WAEC / NECO exam session found."
        )

    await mockwaec_resume_exam_handler(update, context)


async def mockwaec_submit_exam_yes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    payment_reference = context.user_data.get("mw_payment_reference")
    course_code = context.user_data.get("mw_course_code")
    subject_codes = context.user_data.get("mw_subject_codes") or []

    if not payment_reference or not course_code or not subject_codes:
        return await query.message.reply_text(
            "⚠️ No active Mock WAEC / NECO exam session found."
        )

    await clear_mockwaec_passage_message(
        chat_id=query.message.chat_id,
        context=context,
    )
    
    try:
        message_text, markup = await finalize_mockwaec_exam_now(
            payment_reference=payment_reference,
            course_code=course_code,
            subject_codes=subject_codes,
        )
    except Exception as e:
        logger.exception(
            "Failed to submit Mock WAEC / NECO exam early | tx_ref=%s | err=%s",
            payment_reference,
            e,
        )
        return await query.message.reply_text(
            "⚠️ Could not submit your exam right now. Please try again."
        )

    submit_text = (
        "✅ *Mock WAEC / NECO submitted successfully.*\n\n"
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


async def mockwaec_end_exam_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    payment_reference = context.user_data.get("mw_payment_reference")
    course_code = context.user_data.get("mw_course_code")
    subject_codes = context.user_data.get("mw_subject_codes") or []

    if not payment_reference or not course_code or not subject_codes:
        return await query.message.reply_text(
            "⚠️ No active Mock WAEC / NECO exam session found."
        )

    await clear_mockwaec_passage_message(
        chat_id=query.message.chat_id,
        context=context,
    )
    
    try:
        message_text, markup = await finalize_mockwaec_exam_now(
            payment_reference=payment_reference,
            course_code=course_code,
            subject_codes=subject_codes,
        )
    except Exception as e:
        logger.exception(
            "Failed to end Mock WAEC / NECO exam | tx_ref=%s | err=%s",
            payment_reference,
            e,
        )
        return await query.message.reply_text(
            "⚠️ Could not end your exam right now. Please try again."
        )

    end_text = (
        "🛑 *Mock WAEC / NECO exam ended.*\n\n"
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
    application.add_handler(CommandHandler("mockwaec", mockwaec_start_handler))
    application.add_handler(CallbackQueryHandler(mockwaec_start_handler, pattern=r"^mock:waec$"))
    application.add_handler(CallbackQueryHandler(mockwaec_subjects_open_handler, pattern=r"^mw_subjects_open$"))
    application.add_handler(CallbackQueryHandler(mockwaec_subject_toggle_handler, pattern=r"^mw_subject_toggle::"))
    application.add_handler(CallbackQueryHandler(mockwaec_subjects_continue_handler, pattern=r"^mw_subjects_continue$"))
    application.add_handler(CallbackQueryHandler(mockwaec_course_page_handler, pattern=r"^mw_course_page_"))
    application.add_handler(CallbackQueryHandler(mockwaec_course_select_handler, pattern=r"^mw_course_select::"))
    application.add_handler(CallbackQueryHandler(mockwaec_use_course_handler, pattern=r"^mw_use_course::"))
    application.add_handler(CallbackQueryHandler(mockwaec_mode_solo_handler, pattern=r"^mw_mode_solo$"))
    application.add_handler(CallbackQueryHandler(mockwaec_mode_friends_handler, pattern=r"^mw_mode_friends$"))
    application.add_handler(CallbackQueryHandler(mockwaec_room_pay_friend_handler, pattern=r"^mwr_pay_friend$"))
    application.add_handler(CallbackQueryHandler(mockwaec_room_join_handler, pattern=r"^mwr_join::"))
    application.add_handler(CallbackQueryHandler(mockwaec_room_ready_handler, pattern=r"^mwr_ready(?:_done)?$"))
    application.add_handler(CallbackQueryHandler(mockwaec_room_resume_match_handler, pattern=r"^mwr_resume_match::"))
    application.add_handler(CallbackQueryHandler(mockwaec_room_pick_subjects_handler, pattern=r"^mwr_pick_subjects$"))
    application.add_handler(CallbackQueryHandler(mockwaec_room_refresh_handler, pattern=r"^mwr_refresh$"))
    application.add_handler(CallbackQueryHandler(mockwaec_room_start_handler, pattern=r"^mwr_start$"))
    application.add_handler(CallbackQueryHandler(mockwaec_invitee_count_handler, pattern=r"^mw_invites_"))
    application.add_handler(CallbackQueryHandler(mockwaec_pay_solo_handler, pattern=r"^mw_pay_solo$"))
    application.add_handler(CallbackQueryHandler(mockwaec_start_subject_handler, pattern=r"^mw_start_subject::"))
    application.add_handler(CallbackQueryHandler(mockwaec_submit_exam_confirm_handler, pattern=r"^mw_submit_exam_confirm$"))
    application.add_handler(CallbackQueryHandler(mockwaec_submit_exam_yes_handler, pattern=r"^mw_submit_exam_yes$"))
    application.add_handler(CallbackQueryHandler(mockwaec_submit_exam_no_handler, pattern=r"^mw_submit_exam_no$"))
    application.add_handler(CallbackQueryHandler(mockwaec_submit_subject_confirm_handler, pattern=r"^mw_submit_subject_confirm$"))
    application.add_handler(CallbackQueryHandler(mockwaec_submit_subject_yes_handler, pattern=r"^mw_submit_subject_yes$"))
    application.add_handler(CallbackQueryHandler(mockwaec_submit_subject_no_handler, pattern=r"^mw_submit_subject_no$"))
    application.add_handler(CallbackQueryHandler(mockwaec_end_exam_handler, pattern=r"^mw_end_exam$"))
    application.add_handler(CallbackQueryHandler(mockwaec_answer_handler, pattern=r"^mw_ans::"))
    application.add_handler(CallbackQueryHandler(mockwaec_return_to_exam_ready_handler, pattern=r"^payok_mockwaec_return$"))
    application.add_handler(CallbackQueryHandler(mockwaec_resume_exam_handler, pattern=r"^mw_resume_exam$"))
    application.add_handler(CallbackQueryHandler(mockwaec_review_open_handler, pattern=r"^mw_review_(all|wrong)$"))
    application.add_handler(CallbackQueryHandler(mockwaec_review_nav_handler, pattern=r"^mw_review_nav::"))
    application.add_handler(CallbackQueryHandler(mockwaec_back_to_result_handler, pattern=r"^mw_back_to_result$"))


