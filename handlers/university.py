# ====================================================================
# handlers/university.py
# ====================================================================

import json
import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from sqlalchemy import text
from helpers import md_escape

from services.flutterwave_client import create_checkout, build_tx_ref
from services.university_payments import create_pending_university_payment
from db import get_async_session
from university_loader import (
    get_university_categories,
    get_university_category_by_code,
    get_university_subjects_by_category,
    get_university_subject_by_code,

    get_university_modules,
    get_university_module_by_id,

    get_university_module_topics,
    get_university_topic_by_id,

    load_university_topic_questions,

    prepare_university_topic_question_batch,
    prepare_university_course_mock_batch,
)

logger = logging.getLogger(__name__)

# ---------------------
# DB helpers
# --------------------
async def ensure_university_user_access(user_id: int):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    insert into university_user_access (user_id)
                    values (:user_id)
                    on conflict (user_id) do nothing
                """),
                {"user_id": user_id},
            )


async def get_university_user_access(user_id: int) -> Optional[dict]:
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                select
                    free_questions_remaining,
                    paid_question_credits,
                    mock_sessions_available,
                    total_questions_used
                from university_user_access
                where user_id = :user_id
            """),
            {"user_id": user_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

async def create_university_session(
    user_id: int,
    subject_code: str,
    topic_id: str,
    question_target: int,
    mode: str = "topic_practice",
) -> int:
    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    insert into university_sessions (
                        user_id,
                        subject_code,
                        topic_id,
                        mode,
                        question_target,
                        status
                    )
                    values (
                        :user_id,
                        :subject_code,
                        :topic_id,
                        :mode,
                        :question_target,
                        'active'
                    )
                    returning id
                """),
                {
                    "user_id": user_id,
                    "subject_code": subject_code,
                    "topic_id": topic_id,
                    "mode": mode,
                    "question_target": question_target,
                },
            )
            session_id = result.scalar_one()
            return int(session_id)


async def get_seen_question_ids_for_topic(user_id: int, subject_code: str, topic_id: str) -> list[str]:
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                select question_id
                from university_user_topic_history
                where user_id = :user_id
                  and subject_code = :subject_code
                  and topic_id = :topic_id
            """),
            {
                "user_id": user_id,
                "subject_code": subject_code,
                "topic_id": topic_id,
            },
        )
        rows = result.fetchall()
        return [str(row[0]) for row in rows]


async def reset_topic_history(user_id: int, subject_code: str, topic_id: str):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    delete from university_user_topic_history
                    where user_id = :user_id
                      and subject_code = :subject_code
                      and topic_id = :topic_id
                """),
                {
                    "user_id": user_id,
                    "subject_code": subject_code,
                    "topic_id": topic_id,
                },
            )


async def get_paid_question_credits(user_id: int) -> int:
    access = await get_university_user_access(user_id)
    return int((access or {}).get("paid_question_credits", 0))

async def get_mock_sessions_available(user_id: int) -> int:
    access = await get_university_user_access(user_id)
    return int((access or {}).get("mock_sessions_available", 0))


async def deduct_one_mock_session(user_id: int) -> bool:
    """
    Deduct 1 mock session if available.
    Returns True if deducted, False otherwise.
    """
    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    update university_user_access
                    set
                        mock_sessions_available = mock_sessions_available - 1,
                        updated_at = now()
                    where user_id = :user_id
                      and mock_sessions_available > 0
                    returning mock_sessions_available
                """),
                {"user_id": user_id},
            )
            row = result.first()
            return row is not None


async def deduct_one_free_question(user_id: int) -> bool:
    """
    Deduct 1 free question if available.
    Returns True if deducted, False otherwise.
    """
    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    update university_user_access
                    set
                        free_questions_remaining = free_questions_remaining - 1,
                        total_questions_used = total_questions_used + 1,
                        updated_at = now()
                    where user_id = :user_id
                      and free_questions_remaining > 0
                    returning free_questions_remaining
                """),
                {"user_id": user_id},
            )
            row = result.first()
            return row is not None


async def deduct_one_paid_question(user_id: int) -> bool:
    """
    Deduct 1 paid question credit if available.
    Returns True if deducted, False otherwise.
    """
    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    update university_user_access
                    set
                        paid_question_credits = paid_question_credits - 1,
                        total_questions_used = total_questions_used + 1,
                        updated_at = now()
                    where user_id = :user_id
                      and paid_question_credits > 0
                    returning paid_question_credits
                """),
                {"user_id": user_id},
            )
            row = result.first()
            return row is not None


async def add_question_to_topic_history(
    user_id: int,
    subject_code: str,
    topic_id: str,
    question_id: str,
):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    insert into university_user_topic_history (
                        user_id,
                        subject_code,
                        topic_id,
                        question_id
                    )
                    values (
                        :user_id,
                        :subject_code,
                        :topic_id,
                        :question_id
                    )
                    on conflict (user_id, subject_code, topic_id, question_id) do nothing
                """),
                {
                    "user_id": user_id,
                    "subject_code": subject_code,
                    "topic_id": topic_id,
                    "question_id": question_id,
                },
            )


async def record_university_attempt(
    session_id: int,
    user_id: int,
    subject_code: str,
    topic_id: str,
    question_id: str,
    selected_option: str,
    correct_option: str,
    is_correct: bool,
):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    insert into university_attempts (
                        session_id,
                        user_id,
                        subject_code,
                        topic_id,
                        question_id,
                        selected_option,
                        correct_option,
                        is_correct
                    )
                    values (
                        :session_id,
                        :user_id,
                        :subject_code,
                        :topic_id,
                        :question_id,
                        :selected_option,
                        :correct_option,
                        :is_correct
                    )
                """),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "subject_code": subject_code,
                    "topic_id": topic_id,
                    "question_id": question_id,
                    "selected_option": selected_option,
                    "correct_option": correct_option,
                    "is_correct": is_correct,
                },
            )


async def increment_university_session_served(session_id: int):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    update university_sessions
                    set
                        questions_served = questions_served + 1
                    where id = :session_id
                """),
                {"session_id": session_id},
            )


async def increment_university_session_result(session_id: int, is_correct: bool):
    async with get_async_session() as session:
        async with session.begin():
            if is_correct:
                await session.execute(
                    text("""
                        update university_sessions
                        set
                            correct_count = correct_count + 1
                        where id = :session_id
                    """),
                    {"session_id": session_id},
                )
            else:
                await session.execute(
                    text("""
                        update university_sessions
                        set
                            wrong_count = wrong_count + 1
                        where id = :session_id
                    """),
                    {"session_id": session_id},
                )


async def complete_university_session(session_id: int):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    update university_sessions
                    set
                        status = 'completed',
                        ended_at = now()
                    where id = :session_id
                """),
                {"session_id": session_id},
            )


async def clear_university_session_state(context: ContextTypes.DEFAULT_TYPE):
    keys_to_clear = [
        "ut_session_id",
        "ut_session_mode",
        "ut_question_batch",
        "ut_question_ids",
        "ut_current_index",
        "ut_session_target",
        "ut_correct_count",
        "ut_wrong_count",
        "ut_current_question",
        "ut_answered_current",
        "ut_served_question_ids",
        "ut_shown_passages",
        "ut_last_passage",
        "ut_last_passage_id_shown",
        "ut_active_passage_message_id",
        "ut_last_selected_option",
        "ut_last_correct_option",
    ]

    for key in keys_to_clear:
        context.user_data.pop(key, None)


# =============================
# Mock-by-course helpers
# =============================
def get_university_mock_question_count(subject_code: str) -> int:
    subject_code = str(subject_code or "").strip().lower()
    return 40


def get_university_mock_duration_minutes(subject_code: str) -> int:
    subject_code = str(subject_code or "").strip().lower()
    return 30


def extract_correct_option(question: dict) -> str:
    for key in ("correct_option", "correct_answer", "answer", "correctAnswer"):
        value = question.get(key)
        if value:
            return str(value).strip().upper()
    return ""


def format_university_mock_time_remaining(exam_ends_at) -> str:
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


def is_university_mock_time_expired(exam_ends_at) -> bool:
    if not exam_ends_at:
        return False

    if isinstance(exam_ends_at, str):
        try:
            exam_ends_at = datetime.fromisoformat(exam_ends_at.replace("Z", "+00:00"))
        except Exception:
            return False

    if exam_ends_at.tzinfo is None:
        exam_ends_at = exam_ends_at.replace(tzinfo=timezone.utc)

    return datetime.now(timezone.utc) >= exam_ends_at


async def get_seen_question_ids_for_course(user_id: int, subject_code: str) -> list[str]:
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                select distinct question_id
                from university_user_topic_history
                where user_id = :user_id
                  and subject_code = :subject_code
            """),
            {
                "user_id": user_id,
                "subject_code": subject_code,
            },
        )
        rows = result.fetchall()
        return [str(row[0]) for row in rows]


async def reset_course_history(user_id: int, subject_code: str):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    delete from university_user_topic_history
                    where user_id = :user_id
                      and subject_code = :subject_code
                """),
                {
                    "user_id": user_id,
                    "subject_code": subject_code,
                },
            )


async def add_question_to_course_history(
    user_id: int,
    subject_code: str,
    question_id: str,
    topic_id: str | None = None,
):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    insert into university_user_topic_history (
                        user_id,
                        subject_code,
                        topic_id,
                        question_id
                    )
                    values (
                        :user_id,
                        :subject_code,
                        :topic_id,
                        :question_id
                    )
                    on conflict (user_id, subject_code, topic_id, question_id) do nothing
                """),
                {
                    "user_id": user_id,
                    "subject_code": subject_code,
                    "topic_id": topic_id or "__mock_course__",
                    "question_id": question_id,
                },
            )


async def deduct_paid_questions(user_id: int, question_count: int) -> bool:
    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    update university_user_access
                    set
                        paid_question_credits = paid_question_credits - :question_count,
                        total_questions_used = total_questions_used + :question_count,
                        updated_at = now()
                    where user_id = :user_id
                      and paid_question_credits >= :question_count
                    returning paid_question_credits
                """),
                {
                    "user_id": user_id,
                    "question_count": int(question_count),
                },
            )
            row = result.first()
            return row is not None


async def get_latest_active_university_mock_session_for_user(user_id: int, subject_code: str) -> Optional[dict]:
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                select
                    id,
                    user_id,
                    subject_code,
                    topic_id,
                    mode,
                    question_target,
                    questions_served,
                    correct_count,
                    wrong_count,
                    current_question_index,
                    exam_ends_at,
                    status,
                    created_at,
                    ended_at,
                    updated_at
                from university_sessions
                where user_id = :user_id
                  and subject_code = :subject_code
                  and mode = 'course_mock'
                  and status = 'active'
                order by id desc
                limit 1
            """),
            {
                "user_id": int(user_id),
                "subject_code": subject_code,
            },
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def set_university_session_current_question_index(session_id: int, current_question_index: int):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    update university_sessions
                    set
                        current_question_index = :current_question_index,
                        updated_at = now()
                    where id = :session_id
                """),
                {
                    "session_id": int(session_id),
                    "current_question_index": int(current_question_index),
                },
            )


async def get_university_session_paper(session_id: int) -> list[dict]:
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                select
                    id,
                    session_id,
                    user_id,
                    subject_code,
                    question_id,
                    question_order,
                    question_json,
                    correct_option,
                    selected_option,
                    is_correct,
                    created_at,
                    updated_at
                from university_session_questions
                where session_id = :session_id
                order by question_order asc
            """),
            {"session_id": int(session_id)},
        )
        rows = result.mappings().all()
        return [dict(row) for row in rows]


async def create_university_course_mock_paper_if_needed(
    user_id: int,
    session_id: int,
    subject_code: str,
    category_code: str,
) -> dict:
    existing_paper = await get_university_session_paper(session_id)
    if existing_paper:
        return {
            "created_now": False,
            "paper_rows": existing_paper,
            "selected_count": len(existing_paper),
            "cycle_reset": False,
        }

    seen_question_ids = await get_seen_question_ids_for_course(user_id, subject_code)
    requested_count = get_university_mock_question_count(subject_code)

    batch = prepare_university_course_mock_batch(
        category_code=category_code,
        subject_code=subject_code,
        requested_count=requested_count,
        seen_question_ids=seen_question_ids,
    )

    if batch.get("cycle_reset"):
        await reset_course_history(user_id, subject_code)

    selected_questions = batch["selected_questions"]

    async with get_async_session() as session:
        async with session.begin():
            for idx, question in enumerate(selected_questions, start=1):
                await session.execute(
                    text("""
                        insert into university_session_questions (
                            session_id,
                            user_id,
                            subject_code,
                            question_id,
                            question_order,
                            question_json,
                            correct_option,
                            selected_option,
                            is_correct,
                            created_at,
                            updated_at
                        )
                        values (
                            :session_id,
                            :user_id,
                            :subject_code,
                            :question_id,
                            :question_order,
                            :question_json,
                            :correct_option,
                            null,
                            null,
                            now(),
                            now()
                        )
                        on conflict (session_id, question_id) do nothing
                    """),
                    {
                        "session_id": int(session_id),
                        "user_id": int(user_id),
                        "subject_code": subject_code,
                        "question_id": str(question.get("id")),
                        "question_order": idx,
                        "question_json": json.dumps(question),
                        "correct_option": extract_correct_option(question),
                    },
                )

    for question in selected_questions:
        await add_question_to_course_history(
            user_id=user_id,
            subject_code=subject_code,
            question_id=str(question.get("id")),
            topic_id=str(question.get("topic_id") or "__mock_course__"),
        )

    paper_rows = await get_university_session_paper(session_id)

    return {
        "created_now": True,
        "paper_rows": paper_rows,
        "selected_count": len(paper_rows),
        "cycle_reset": bool(batch.get("cycle_reset")),
    }


async def create_university_mock_session(
    user_id: int,
    subject_code: str,
    question_target: int,
) -> int:
    exam_ends_at = datetime.now(timezone.utc) + timedelta(
        minutes=get_university_mock_duration_minutes(subject_code)
    )

    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    insert into university_sessions (
                        user_id,
                        subject_code,
                        topic_id,
                        mode,
                        question_target,
                        current_question_index,
                        exam_ends_at,
                        status,
                        updated_at
                    )
                    values (
                        :user_id,
                        :subject_code,
                        :topic_id,
                        'course_mock',
                        :question_target,
                        0,
                        :exam_ends_at,
                        'active',
                        now()
                    )
                    returning id
                """),
                {
                    "user_id": int(user_id),
                    "subject_code": subject_code,
                    "topic_id": "__mock_course__",
                    "question_target": int(question_target),
                    "exam_ends_at": exam_ends_at,
                },
            )
            session_id = result.scalar_one()
            return int(session_id)


async def get_university_session_by_id(session_id: int) -> Optional[dict]:
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                select
                    id,
                    user_id,
                    subject_code,
                    topic_id,
                    mode,
                    question_target,
                    questions_served,
                    correct_count,
                    wrong_count,
                    current_question_index,
                    exam_ends_at,
                    status,
                    created_at,
                    ended_at,
                    updated_at
                from university_sessions
                where id = :session_id
                limit 1
            """),
            {"session_id": int(session_id)},
        )
        row = result.mappings().first()
        return dict(row) if row else None


def build_university_mock_resume_text(course_name: str, next_question_no: int, exam_ends_at) -> str:
    safe_course_name = md_escape(str(course_name))
    safe_next_question_no = md_escape(str(next_question_no))
    safe_remaining = md_escape(format_university_mock_time_remaining(exam_ends_at))

    return (
        f"📝 *Resume Course Mock*\n\n"
        f"📘 course: *{safe_course_name}*\n"
        f"⏱ Time Remaining: *{safe_remaining}*\n"
        f"➡ Resume From: *Question {safe_next_question_no}*\n\n"
        "Tap below to continue your course mock\\."
    )


def make_university_mock_resume_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶ Resume Mock", callback_data="ut_mock_resume")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def build_university_mock_access_text(course_name: str, question_count: int, mock_sessions_available: int) -> str:
    safe_course_name = md_escape(str(course_name))
    safe_question_count = md_escape(str(question_count))
    safe_mock_sessions = md_escape(str(mock_sessions_available))
    duration_text = "45 minutes" if question_count == 60 else "30 minutes"
    safe_duration = md_escape(duration_text)

    return (
        "📝 *Course Mock \\(By course\\)*\n\n"
        "This is a *full course mock exam*\\.\n\n"
        f"📘 course: *{safe_course_name}*\n"
        f"📚 Total Questions: *{safe_question_count}*\n"
        f"⏱ Time Allowed: *{safe_duration}*\n"
        f"🎟 Mock Sessions Available: *{safe_mock_sessions}*\n\n"
        "*What this means:*\n"
        "• You will answer a *full paper* for this course\n"
        "• *The courses* have *40 questions*\n\n"
        "• If you want to practise only one topic, use *By Topics*\n"
        "• If you want to write the full course like an exam, use *Course Mock \\(By course\\)*\n\n"
        "*Before you start:*\n"
        "• If you already have at least *1 mock session*, you can start now\n"
        "• If your mock sessions are *0*, get a mock session first\n\n"
        "Choose what you want to do below\\."
    )


def make_university_mock_access_keyboard(
    category_code: str,
    subject_code: str,
    can_start: bool,
) -> InlineKeyboardMarkup:

    rows = []

    if can_start:
        rows.append([
            InlineKeyboardButton(
                "▶ Start Mock Now",
                callback_data="ut_mock_start_paid"
            )
        ])

    rows.extend([
        [InlineKeyboardButton("🎟 Get 1 Mock Session — ₦100", callback_data="ut_mock_buy_1")],
        [InlineKeyboardButton("🎟 Get 2 Mock Sessions — ₦200", callback_data="ut_mock_buy_2")],
        [InlineKeyboardButton("🎟 Get 3 Mock Sessions — ₦300", callback_data="ut_mock_buy_3")],
        [InlineKeyboardButton("🎟 Get 4 Mock Sessions — ₦400", callback_data="ut_mock_buy_4")],
        [InlineKeyboardButton("🎟 Get 5 Mock Sessions — ₦500", callback_data="ut_mock_buy_5")],

        [
            InlineKeyboardButton(
                "⬅️ Back to Mode",
                callback_data=f"ut_back_mode::{category_code}::{subject_code}"
            )
        ],

        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
    ])

    return InlineKeyboardMarkup(rows)


def build_university_batch_from_paper_rows(paper_rows: list[dict]) -> list[dict]:
    batch = []

    for row in paper_rows:
        payload = row.get("question_json")

        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        elif not isinstance(payload, dict):
            payload = {}

        batch.append(payload)

    return batch

# =============================
# Keyboards
# =============================

# -----Category Keyboard------
def make_category_keyboard():

    categories = get_university_categories()

    rows = []
    row = []

    for category in categories:

        row.append(
            InlineKeyboardButton(
                category["name"],
                callback_data=f"ut_cat_{category['code']}"
            )
        )

        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            "🏠 Back to Main Menu",
            callback_data="menu:main"
        )
    ])

    return InlineKeyboardMarkup(rows)

# ---Subject Keyboard---------
def make_subject_keyboard(category_code: str):

    subjects = get_university_subjects_by_category(category_code)

    rows = []
    row = []

    for subject in subjects:

        row.append(
            InlineKeyboardButton(
                subject["name"],
                callback_data=f"ut_subj_{subject['code']}"
            )
        )

        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            "⬅️ Back to Categories",
            callback_data="university"
        )
    ])

    return InlineKeyboardMarkup(rows)


# ---Module Keyboard-----
def make_module_keyboard(category_code: str, subject_code: str):
    modules = get_university_modules(
        category_code,
        subject_code,
    )

    rows = []

    for module in modules:
        rows.append([
            InlineKeyboardButton(
                f"{module['number']}. {module['title']}",
                callback_data=(
                    f"ut_module::"
                    f"{category_code}::"
                    f"{subject_code}::"
                    f"{module['id']}"
                )
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ Back to Subjects",
            callback_data=f"ut_back_subjects::{category_code}"
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "🏠 Back to Main Menu",
            callback_data="menu:main"
        )
    ])

    return InlineKeyboardMarkup(rows)


# ---Mode Keyboard-------
def make_mode_keyboard(subject_code: str):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📚 By Topics", callback_data=f"ut_mode_topics_{subject_code}")],
            [InlineKeyboardButton("📝 Course Mock (By course)", callback_data=f"ut_mode_mock_{subject_code}")],
            [InlineKeyboardButton("⬅️ Back to Subjects", callback_data=f"ut_back_subjects::{category_code}")]
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


# ----Topic Keyboard------
def make_topics_keyboard(
    category_code: str,
    subject_code: str,
    module_id: str,
):
    topics = get_university_module_topics(
        category_code,
        subject_code,
        module_id,
    )

    rows = []

    for topic in topics:
        rows.append([
            InlineKeyboardButton(
                f"{topic['number']}. {topic['title']}",
                callback_data=(
                    f"ut_topic::"
                    f"{category_code}::"
                    f"{subject_code}::"
                    f"{module_id}::"
                    f"{topic['id']}"
                )
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ Back to Modules",
            callback_data=(
                f"ut_back_modules::"
                f"{category_code}::"
                f"{subject_code}"
            )
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "🏠 Back to Main Menu",
            callback_data="menu:main"
        )
    ])

    return InlineKeyboardMarkup(rows)


# ---Topic Access Keyboard for Courses----
def make_topic_access_keyboard_for_course(
    category_code: str,
    subject_code: str,
    module_id: str,
    has_free_trial: bool,
    has_paid_credits: bool,
):
    rows = []

    # =====================================
    # FREE TRIAL
    # =====================================
    if has_free_trial:
        rows.append(
            [
                InlineKeyboardButton(
                    "🎁 Use Free Trial (5 Questions)",
                    callback_data="ut_start_free",
                )
            ]
        )

    # =====================================
    # USE PAID CREDITS
    # =====================================
    if has_paid_credits:
        rows.append(
            [
                InlineKeyboardButton(
                    "✅ Use Paid Credits",
                    callback_data="ut_use_paid",
                )
            ]
        )

    # =====================================
    # BUY QUESTION PACKS
    # =====================================
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    "💳 Get 50 Questions — ₦100",
                    callback_data="ut_buy_50",
                )
            ],
            [
                InlineKeyboardButton(
                    "💳 Get 100 Questions — ₦200",
                    callback_data="ut_buy_100",
                )
            ],
            [
                InlineKeyboardButton(
                    "💳 Get 150 Questions — ₦300",
                    callback_data="ut_buy_150",
                )
            ],
            [
                InlineKeyboardButton(
                    "💳 Get 200 Questions — ₦400",
                    callback_data="ut_buy_200",
                )
            ],

            # =====================================
            # BACK TO TOPICS
            # =====================================
            [
                InlineKeyboardButton(
                    "⬅️ Back to Topics",
                    callback_data=(
                        f"ut_back_module::{category_code}"
                        f"::{subject_code}"
                        f"::{module_id}"
                    ),
                )
            ],

            # =====================================
            # MAIN MENU
            # =====================================
            [
                InlineKeyboardButton(
                    "🏠 Back to Main Menu",
                    callback_data="menu:main",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(rows)


def make_after_answer_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➡️ Next", callback_data="ut_next")],
            [InlineKeyboardButton("📖 Answer Details", callback_data="ut_details")],
            [InlineKeyboardButton("🏠 End Practice", callback_data="ut_end_session")],
        ]
    )


def make_after_details_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➡️ Next", callback_data="ut_next")],
            [InlineKeyboardButton("🏠 End Practice", callback_data="ut_end_session")],
        ]
    )


def make_paid_session_count_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("10", callback_data="ut_paidcount_10"),
                InlineKeyboardButton("20", callback_data="ut_paidcount_20"),
            ],
            [
                InlineKeyboardButton("30", callback_data="ut_paidcount_30"),
                InlineKeyboardButton("50", callback_data="ut_paidcount_50"),
            ],
            [InlineKeyboardButton("⬅️ Back to UNIVERSITY Practice", callback_data="university")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


# =============================
# Message builders
# =============================
def build_welcome_text(
    free_remaining: int,
    paid_credits: int,
    mock_sessions_available: int,
) -> str:
    safe_free_remaining = md_escape(str(free_remaining))
    safe_paid_credits = md_escape(str(paid_credits))
    safe_mock_sessions = md_escape(str(mock_sessions_available))

    return (
        "🎓 *Welcome to University Tutorials*\n\n"
        "This section helps you practise for UNIVERSITY in *two different ways*:\n\n"
        "1\\. *By Topics* \n"
        "   You choose one course, then choose one topic under that course, and practise questions from that topic\\.\n\n"
        "2\\. *Course Mock \\(By course\\)* \n"
        "   You choose one full course and answer it like an exam paper\\.\n"
        "   *The courses* have *40 questions*\\.\n\n"
        "*How payment works:*\n"
        "• *First\\-time users* get *5 free questions* for *By Topics* practice only\n"
        "• *By Topics* uses *paid question credits*\n"
        "• *Course Mock \\(By course\\)* does *not* use question credits\n"
        "• *Course Mock \\(By course\\)* uses *mock sessions* instead\n"
        "• *1 course mock \\= 1 mock session*\n\n"
        "*Simple examples:*\n"
        "• If you want to practise *Chemistry \\> Acids, Bases and Salts*, use *By Topics*\n"
        "• If you want to write a full *Chemistry mock exam*, use *Course Mock \\(By course\\)*\n\n"
        "*Your current balances:*\n"
        f"🎁 Free questions left: *{safe_free_remaining}*\n"
        f"💳 Paid question credits: *{safe_paid_credits}*\n"
        f"🎟 Mock sessions available: *{safe_mock_sessions}*\n\n"
        "*Disclaimer:*\n"
        "This is an independent study tool and not an official UNIVERSITY platform\\.\n\n"
        "Please choose a course below\\."
    )

# =============================
# Entry point
# =============================
async def university_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    if not tg:
        return

    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

    await ensure_university_user_access(tg.id)
    access = await get_university_user_access(tg.id)

    free_remaining = int((access or {}).get("free_questions_remaining", 5))
    paid_credits = int((access or {}).get("paid_question_credits", 0))
    mock_sessions_available = int((access or {}).get("mock_sessions_available", 0))

    context.user_data["ut_subject_code"] = None
    context.user_data["ut_mode"] = None
    context.user_data["ut_topic_id"] = None
    context.user_data["ut_module_id"] = None
    context.user_data["ut_topic_page"] = 1

    text_msg = build_welcome_text(
        free_remaining,
        paid_credits,
        mock_sessions_available,
    )

    await update.effective_message.reply_text(
        text_msg,
        parse_mode="MarkdownV2",
        reply_markup=make_category_keyboard(),
    )

# ==============================
# University Category Handler
# ==============================
async def university_category_handler(update, context):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    category_code = query.data.replace("ut_cat_", "", 1)

    category = get_university_category_by_code(category_code)

    if not category:
        return await query.message.reply_text(
            "⚠️ Category not found."
        )

    context.user_data["ut_category_code"] = category_code

    safe_category_name = md_escape(str(category["name"]))

    await query.message.reply_text(
        f"📚 *{safe_category_name}*\n\nChoose a subject\\.",
        parse_mode="MarkdownV2",
        reply_markup=make_subject_keyboard(category_code),
    )

# =============================
# course selected
# =============================
async def university_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, _, subject_code = query.data.split("_", 2)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid course selection.")

    subject = get_university_subject_by_code(subject_code)
    if not subject:
        return await query.message.reply_text("⚠️ course not found or inactive.")

    context.user_data["ut_subject_code"] = subject_code
    context.user_data["ut_mode"] = None
    context.user_data["ut_topic_id"] = None
    context.user_data["ut_module_id"] = None
    context.user_data["ut_topic_page"] = 1

    await query.message.reply_text(
        f"📘 *You selected:* {subject['name']}\n\n"
        "How would you like to practice?",
        parse_mode="MarkdownV2",
        reply_markup=make_mode_keyboard(
            category_code, 
            subject_code),
        )

# --------------------------------
# UNIVERSITY course MOCK SCREEN
# -------------------------------- 
async def open_university_course_mock_screen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    category_code: str,
    subject_code: str,
):
    context.user_data["ut_category_code"] = category_code
    context.user_data["ut_subject_code"] = subject_code
    context.user_data["ut_mode"] = "course_mock"
    context.user_data["ut_topic_id"] = None
    context.user_data["ut_module_id"] = None

    tg = update.effective_user
    user_id = tg.id

    subject = get_university_subject_by_code(subject_code)

    if not subject:
        return await update.effective_message.reply_text(
            "⚠️ Course not found\\.",
            parse_mode="MarkdownV2",
        )

    active_session = await get_latest_active_university_mock_session_for_user(
        user_id=user_id,
        subject_code=subject_code,
    )

    if active_session and not is_university_mock_time_expired(
        active_session.get("exam_ends_at")
    ):
        context.user_data["ut_session_id"] = int(active_session["id"])
        context.user_data["ut_session_mode"] = "course_mock"

        next_question_no = max(
            1,
            int(active_session.get("current_question_index") or 0) + 1,
        )

        return await update.effective_message.reply_text(
            build_university_mock_resume_text(
                course_name=subject["name"],
                next_question_no=next_question_no,
                exam_ends_at=active_session.get("exam_ends_at"),
            ),
            parse_mode="MarkdownV2",
            reply_markup=make_university_mock_resume_keyboard(),
        )

    mock_sessions_available = await get_mock_sessions_available(user_id)

    question_count = get_university_mock_question_count(subject_code)

    can_start = mock_sessions_available >= 1

    return await update.effective_message.reply_text(
        build_university_mock_access_text(
            course_name=subject["name"],
            question_count=question_count,
            mock_sessions_available=mock_sessions_available,
        ),
        parse_mode="MarkdownV2",
        reply_markup=make_university_mock_access_keyboard(
            category_code,
            subject_code,
            can_start,
        ),
    )


async def university_mock_buy_session_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data or ""
    session_count_str = data.replace("ut_mock_buy_", "", 1)

    pricing_map = {
        "1": 100,
        "2": 200,
        "3": 300,
        "4": 400,
        "5": 500,
    }

    if session_count_str not in pricing_map:
        return await query.message.reply_text(
            "⚠️ Invalid mock session package selected\\.",
            parse_mode="MarkdownV2",
        )

    subject_code = context.user_data.get("ut_subject_code")
    if not subject_code:
        return await query.message.reply_text(
            "⚠️ course session data missing\\. Please choose your course again\\.",
            parse_mode="MarkdownV2",
        )

    session_count = int(session_count_str)
    amount = pricing_map[session_count_str]

    user = query.from_user
    tg_id = user.id
    username = user.username or f"user_{tg_id}"
    email = f"{username}@naijaprizegate.ng"

    tx_ref = build_tx_ref("UNIVERSITYMOCKcourse")

    async with get_async_session() as session:
        async with session.begin():
            await create_pending_university_payment(
                session,
                payment_reference=tx_ref,
                user_id=tg_id,
                amount_paid=amount,
                question_credits_added=0,
                mock_sessions_added=session_count,
                subject_code=subject_code,
                topic_id=None,
            )

    checkout_url = await create_checkout(
        user_id=tg_id,
        amount=amount,
        username=username,
        email=email,
        tx_ref=tx_ref,
        meta={
            "tg_id": str(tg_id),
            "username": username,
            "product_type": "UNIVERSITYMOCKcourse",
            "subject_code": subject_code,
        },
        product_type="UNIVERSITYMOCKcourse",
    )

    if not checkout_url:
        return await query.message.reply_text(
            "⚠️ Payment service unavailable\\. Please try again shortly\\.",
            parse_mode="MarkdownV2",
        )

    safe_amount = md_escape(str(amount))
    safe_session_count = md_escape(str(session_count))

    await query.message.reply_text(
        f"🎟 *Course Mock Session Selected*\n\n"
        f"🧾 Sessions: *{safe_session_count}*\n"
        f"💰 Amount: *₦{safe_amount}*\n\n"
        "After successful payment, your mock session will be added automatically\\.\n\n"
        "Tap below to complete payment\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💳 Pay Securely", url=checkout_url)],
                [InlineKeyboardButton("⬅️ Back to Mock Screen", callback_data=f"ut_mode_mock_{subject_code}")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )

# =============================
# Mode selected
# =============================
async def university_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data

    if data.startswith("ut_mode_topics_"):
        subject_code = data.replace("ut_mode_topics_", "", 1)

        category_code = context.user_data.get("ut_category_code")

        context.user_data["ut_subject_code"] = subject_code
        context.user_data["ut_mode"] = "topic_practice"
        context.user_data["ut_module_id"] = None
        context.user_data["ut_topic_id"] = None

        return await query.message.reply_text(
            "📚 *Choose a Module*",
            parse_mode="MarkdownV2",
            reply_markup=make_module_keyboard(
                category_code,
                subject_code,
            ),
        )

    if data.startswith("ut_mode_mock_"):

        subject_code = data.replace(
            "ut_mode_mock_",
            "",
            1,
        )

        category_code = context.user_data.get(
            "ut_category_code"
        )

        return await open_university_course_mock_screen(
            update,
            context,
            category_code,
            subject_code,
        )


# ---------------------------------
# University Module Handler
# ---------------------------------
async def university_module_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    try:
        _, category_code, subject_code, module_id = query.data.split("::")
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid module selection."
        )

    module = get_university_module_by_id(
        category_code,
        subject_code,
        module_id,
    )

    if not module:
        return await query.message.reply_text(
            "⚠️ Module not found."
        )

    context.user_data["ut_category_code"] = category_code
    context.user_data["ut_subject_code"] = subject_code
    context.user_data["ut_module_id"] = module_id

    safe_module_title = md_escape(
        str(module["title"])
    )

    await query.message.reply_text(
        f"📚 *{safe_module_title}*\n\n"
        "Choose a topic\\.",
        parse_mode="MarkdownV2",
        reply_markup=make_topics_keyboard(
            category_code,
            subject_code,
            module_id,
        ),
    )

# =========================================
# Send University Topic Access Screen
# =========================================
async def send_university_topic_access_screen(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    category_code: str,
    subject_code: str,
    module_id: str,
    topic_id: str,
    user_id: int,
):
    # =====================================
    # LOAD TOPICS
    # =====================================
    topics = get_university_module_topics(
        category_code,
        subject_code,
        module_id,
    )

    selected_topic = next(
        (
            t for t in topics
            if t["id"] == topic_id
        ),
        None,
    )

    # =====================================
    # VALIDATE TOPIC
    # =====================================
    if not selected_topic:
        return await message.reply_text(
            "⚠️ Topic not found\\.",
            parse_mode="MarkdownV2",
        )

    # =====================================
    # SAVE FLOW STATE
    # =====================================
    context.user_data["ut_category_code"] = category_code
    context.user_data["ut_subject_code"] = subject_code
    context.user_data["ut_module_id"] = module_id
    context.user_data["ut_topic_id"] = topic_id

    # =====================================
    # ENSURE USER ACCESS
    # =====================================
    await ensure_university_user_access(user_id)

    access = await get_university_user_access(user_id)

    free_remaining = int(
        (access or {}).get(
            "free_questions_remaining",
            0,
        )
    )

    paid_credits = int(
        (access or {}).get(
            "paid_question_credits",
            0,
        )
    )

    has_free_trial = free_remaining > 0
    has_paid_credits = paid_credits > 0

    # =====================================
    # SAFE DISPLAY VALUES
    # =====================================
    safe_topic_title = md_escape(
        str(selected_topic["title"])
    )

    safe_free_remaining = md_escape(
        str(free_remaining)
    )

    safe_paid_credits = md_escape(
        str(paid_credits)
    )

    # =====================================
    # ACCESS SCREEN
    # =====================================
    await message.reply_text(
        f"✅ Topic selected: *{safe_topic_title}*\n\n"
        f"🎁 Free questions left: *{safe_free_remaining}*\n"
        f"💳 Paid question credits: *{safe_paid_credits}*\n\n"
        "Choose how you want to continue:",
        parse_mode="MarkdownV2",
        reply_markup=make_topic_access_keyboard_for_course(
            category_code,
            subject_code,
            module_id,
            has_free_trial,
            has_paid_credits,
        ),
    )


# ====================================================
# University Topic Handler
# ====================================================
async def university_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    # =====================================
    # CALLBACK FORMAT:
    # ut_topic::category::subject::module::topic
    # =====================================
    try:
        _, category_code, subject_code, module_id, topic_id = (
            query.data.split("::")
        )

    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid topic selection\\.",
            parse_mode="MarkdownV2",
        )

    # =====================================
    # SAVE FLOW STATE
    # =====================================
    context.user_data["ut_category_code"] = category_code
    context.user_data["ut_subject_code"] = subject_code
    context.user_data["ut_module_id"] = module_id
    context.user_data["ut_topic_id"] = topic_id

    # =====================================
    # OPEN ACCESS SCREEN
    # =====================================
    await send_university_topic_access_screen(
        query.message,
        context,
        category_code=category_code,
        subject_code=subject_code,
        module_id=module_id,
        topic_id=topic_id,
        user_id=update.effective_user.id,
    )


# =============================
# Free trial start
# =============================
async def university_start_free_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    tg = update.effective_user
    user_id = tg.id

    # =============================
    # LOAD FLOW STATE
    # =============================
    category_code = context.user_data.get("ut_category_code")
    subject_code = context.user_data.get("ut_subject_code")
    module_id = context.user_data.get("ut_module_id")
    topic_id = context.user_data.get("ut_topic_id")

    # =============================
    # VALIDATE FLOW STATE
    # =============================
    if not category_code or not subject_code or not module_id or not topic_id:
        return await query.message.reply_text(
            "⚠️ Topic session data missing\\. Please choose your course and topic again\\.",
            parse_mode="MarkdownV2",
            reply_markup=make_category_keyboard(),
        )

    # =============================
    # CHECK USER ACCESS
    # =============================
    access = await get_university_user_access(user_id)

    free_remaining = int(
        (access or {}).get("free_questions_remaining", 0)
    )

    if free_remaining <= 0:
        return await query.message.reply_text(
            "⚠️ You have no free UNIVERSITY questions left\\.\n\n"
            "Please buy a question pack to continue\\.",
            parse_mode="MarkdownV2",
        )

    requested_count = min(5, free_remaining)

    # =============================
    # LOAD HISTORY
    # =============================
    seen_question_ids = await get_seen_question_ids_for_topic(
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
    )

    # =============================
    # PREPARE QUESTION BATCH
    # =============================
    batch = prepare_university_topic_question_batch(
        category_code=category_code,
        subject_code=subject_code,
        module_id=module_id,
        topic_id=topic_id,
        requested_count=requested_count,
        seen_question_ids=seen_question_ids,
    )

    if batch["cycle_reset"]:
        await reset_topic_history(
            user_id,
            subject_code,
            topic_id,
        )

    selected_questions = batch["selected_questions"]
    selected_question_ids = batch["selected_question_ids"]

    if not selected_questions:
        return await query.message.reply_text(
            "⚠️ No active questions found for this topic yet\\.",
            parse_mode="MarkdownV2",
        )

    # =============================
    # CREATE SESSION
    # =============================
    session_id = await create_university_session(
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
        question_target=len(selected_questions),
        mode="topic_practice",
    )

    # =============================
    # SAVE SESSION STATE
    # =============================
    context.user_data["ut_category_code"] = category_code
    context.user_data["ut_subject_code"] = subject_code
    context.user_data["ut_module_id"] = module_id
    context.user_data["ut_topic_id"] = topic_id

    context.user_data["ut_session_id"] = session_id
    context.user_data["ut_session_mode"] = "free_trial"

    context.user_data["ut_question_batch"] = selected_questions
    context.user_data["ut_question_ids"] = selected_question_ids

    context.user_data["ut_current_index"] = 0
    context.user_data["ut_session_target"] = len(selected_questions)

    context.user_data["ut_correct_count"] = 0
    context.user_data["ut_wrong_count"] = 0

    context.user_data["ut_current_question"] = None
    context.user_data["ut_answered_current"] = False

    context.user_data["ut_served_question_ids"] = []

    context.user_data["ut_shown_passages"] = []
    context.user_data["ut_last_passage"] = None
    context.user_data["ut_last_passage_id_shown"] = ""
    context.user_data["ut_active_passage_message_id"] = None

    # =============================
    # LOAD TOPIC TITLE
    # =============================
    topic = next(
        (
            t for t in get_university_module_topics(
                category_code,
                subject_code,
                module_id,
            )
            if t["id"] == topic_id
        ),
        None,
    )

    topic_title = topic["title"] if topic else topic_id

    # =============================
    # LOAD SUBJECT
    # =============================
    subject = get_university_subject_by_code(subject_code)

    course_name = (
        subject["name"]
        if subject
        else subject_code
    )

    # =============================
    # SAFE DISPLAY TEXT
    # =============================
    safe_course_name = md_escape(str(course_name))
    safe_topic_title = md_escape(str(topic_title))
    safe_question_count = md_escape(
        str(len(selected_questions))
    )

    reset_note = (
        "\n♻️ Topic cycle reset because you already exhausted this topic before\\."
        if batch["cycle_reset"]
        else ""
    )

    # =============================
    # SUCCESS MESSAGE
    # =============================
    await query.message.reply_text(
        f"🎉 *Free Trial Started*\n\n"
        f"📘 Course: *{safe_course_name}*\n"
        f"🧪 Topic: *{safe_topic_title}*\n"
        f"📚 Questions in this session: *{safe_question_count}*"
        f"{reset_note}\n\n"
        "Next step: we will now start serving Question 1\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "▶ Start Questions",
                        callback_data="ut_serve_first",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 Back to Main Menu",
                        callback_data="menu:main",
                    )
                ],
            ]
        ),
    )


# =============================
# Buy question pack
# =============================
async def university_buy_pack_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data
    pack_size = data.replace("ut_buy_", "", 1)

    pricing_map = {
        "50": 100,
        "100": 200,
        "150": 300,
        "200": 400,
    }

    if pack_size not in pricing_map:
        return await query.message.reply_text(
            "⚠️ Invalid UNIVERSITY package selected\\.",
            parse_mode="MarkdownV2",
        )

    amount = pricing_map[pack_size]
    credits = int(pack_size)

    user = query.from_user
    tg_id = user.id
    username = user.username or f"user_{tg_id}"
    email = f"{username}@naijaprizegate.ng"

    subject_code = context.user_data.get("ut_subject_code")
    topic_id = context.user_data.get("ut_topic_id")

    tx_ref = build_tx_ref("UNIVERSITY")

    async with get_async_session() as session:
        await create_pending_university_payment(
            session,
            payment_reference=tx_ref,
            user_id=tg_id,
            amount_paid=amount,
            question_credits_added=credits,
            subject_code=subject_code,
            topic_id=topic_id,
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
            "product_type": "UNIVERSITY",
            "subject_code": str(subject_code) if subject_code else "",
            "topic_id": str(topic_id) if topic_id else "",
        },
        product_type="UNIVERSITY",
    )

    if not checkout_url:
        async with get_async_session() as session:
            await session.execute(
                text("""
                    update university_payments
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
            "⚠️ Payment service unavailable\\. Please try again shortly\\.",
            parse_mode="MarkdownV2",
        )

    safe_credits = md_escape(str(credits))
    safe_amount = md_escape(str(amount))

    await query.message.reply_text(
        f"💳 *UNIVERSITY Question Pack Selected*\n\n"
        f"📚 Questions: *{safe_credits}*\n"
        f"💰 Amount: *₦{safe_amount}*\n\n"
        "After successful payment, your UNIVERSITY question credits will be added automatically\\.\n\n"
        "Tap below to complete payment\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💳 Pay Securely", url=checkout_url)],
                [InlineKeyboardButton("⬅️ Back to UNIVERSITY Practice", callback_data="university")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )


def get_ut_passage_id(question: dict) -> str:
    """
    Return a stable passage identifier for a question.
    Prefer explicit passage_id. Fall back to question_type + passage text.
    """
    passage_id = str(question.get("passage_id") or "").strip()
    if passage_id:
        return passage_id

    passage = str(question.get("passage") or "").strip()
    question_type = str(question.get("question_type") or "").strip().lower()

    if passage and question_type in {"comprehension_mcq", "summary_mcq"}:
        return f"{question_type}::{passage[:120]}"

    return ""


def question_has_passage(question: dict) -> bool:
    passage = str(question.get("passage") or "").strip()
    question_type = str(question.get("question_type") or "").strip().lower()
    return bool(passage and question_type in {"comprehension_mcq", "summary_mcq"})


def should_show_ut_passage_for_question(
    question: dict,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """
    Show passage only if:
    - the question belongs to a passage block, and
    - it is a different passage from the last shown one
    """
    if not question_has_passage(question):
        return False

    current_passage_id = get_ut_passage_id(question)
    last_passage_id = str(context.user_data.get("ut_last_passage_id_shown") or "").strip()

    return current_passage_id != last_passage_id


def mark_ut_passage_as_shown(question: dict, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["ut_last_passage_id_shown"] = get_ut_passage_id(question)


def store_ut_passage_message_id(
    *,
    message_id: int | None,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    context.user_data["ut_active_passage_message_id"] = message_id


async def clear_ut_passage_message(
    *,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    passage_message_id = context.user_data.get("ut_active_passage_message_id")
    if not passage_message_id:
        return

    try:
        await context.bot.delete_message(
            chat_id=chat_id,
            message_id=int(passage_message_id),
        )
    except Exception:
        pass

    context.user_data["ut_active_passage_message_id"] = None
    context.user_data["ut_last_passage_id_shown"] = ""


def get_ut_passage_question_range(batch: list[dict], current_index: int) -> tuple[int, int]:
    """
    For the current question, find the full contiguous block of questions
    that belong to the same passage.
    Returns 1-based question numbers.
    """
    if current_index < 0 or current_index >= len(batch):
        return (current_index + 1, current_index + 1)

    current_question = batch[current_index]
    current_passage_id = get_ut_passage_id(current_question)

    if not current_passage_id:
        q_no = current_index + 1
        return (q_no, q_no)

    start = current_index
    end = current_index

    # scan backward
    i = current_index - 1
    while i >= 0:
        if get_ut_passage_id(batch[i]) == current_passage_id:
            start = i
            i -= 1
        else:
            break

    # scan forward
    i = current_index + 1
    while i < len(batch):
        if get_ut_passage_id(batch[i]) == current_passage_id:
            end = i
            i += 1
        else:
            break

    return (start + 1, end + 1)


def build_ut_passage_text(
    *,
    course_name: str,
    question_start: int,
    question_end: int,
    total_questions: int,
    exam_ends_at,
    question: dict,
) -> str:
    safe_course_name = md_escape(str(course_name))
    safe_q_start = md_escape(str(question_start))
    safe_q_end = md_escape(str(question_end))
    safe_total = md_escape(str(total_questions))

    passage_title = str(question.get("passage_title") or "Passage").strip()
    passage_text = str(question.get("passage") or "").strip()

    safe_passage_title = md_escape(passage_title)
    safe_passage_text = md_escape(passage_text)

    lines = [
        "📝 *Mock UNIVERSITY / UTME*" if str(question.get("_session_mode") or "") == "course_mock" else "📘 *UNIVERSITY Practice*",
        "",
        f"course: *{safe_course_name}*",
        f"Questions: *{safe_q_start} \\- {safe_q_end} of {safe_total}*",
    ]

    if exam_ends_at:
        safe_remaining = md_escape(str(format_university_mock_time_remaining(exam_ends_at)))
        lines.append(f"⏱ Time remaining: *{safe_remaining}*")

    lines.extend([
        "",
        f"Passage Title: *{safe_passage_title}*",
        "",
        "Passage:",
        safe_passage_text,
    ])

    return "\n".join(lines)


# =============================
# Question serving
# =============================
async def send_current_university_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_mode = context.user_data.get("ut_session_mode")
    session_id = context.user_data.get("ut_session_id")
    batch = context.user_data.get("ut_question_batch") or []
    current_index = int(context.user_data.get("ut_current_index", 0))

    # Reload mock paper from DB if needed
    if session_mode == "course_mock" and not batch and session_id:
        paper_rows = await get_university_session_paper(int(session_id))

        batch = build_university_batch_from_paper_rows(paper_rows)

        context.user_data["ut_question_batch"] = batch
        context.user_data["ut_question_ids"] = [
            str(q.get("id"))
            for q in batch
            if q.get("id")
        ]

    if not batch:
        return await update.effective_message.reply_text(
            "⚠️ No active UNIVERSITY question session found\\.",
            parse_mode="MarkdownV2",
        )

    session_row = None

    if session_mode == "course_mock" and session_id:
        session_row = await get_university_session_by_id(int(session_id))

        if not session_row:
            return await update.effective_message.reply_text(
                "⚠️ Mock session could not be reloaded\\.",
                parse_mode="MarkdownV2",
            )

        if is_university_mock_time_expired(session_row.get("exam_ends_at")):
            await clear_ut_passage_message(
                chat_id=update.effective_message.chat_id,
                context=context,
            )

            await complete_university_session(int(session_id))

            safe_total = md_escape(str(len(batch)))
            safe_correct_count = md_escape(
                str(session_row.get("correct_count") or 0)
            )
            safe_wrong_count = md_escape(
                str(session_row.get("wrong_count") or 0)
            )

            return await update.effective_message.reply_text(
                f"⏰ *Mock time is up\\.*\n\n"
                f"📚 Total Questions: *{safe_total}*\n"
                f"✅ Correct: *{safe_correct_count}*\n"
                f"❌ Wrong: *{safe_wrong_count}*\n\n"
                "This course mock has ended\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🎓 UNIVERSITY Practice",
                                callback_data="university",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "🏠 Back to Main Menu",
                                callback_data="menu:main",
                            )
                        ],
                    ]
                ),
            )

    # =============================
    # SESSION COMPLETED
    # =============================
    if current_index >= len(batch):
        await clear_ut_passage_message(
            chat_id=update.effective_message.chat_id,
            context=context,
        )

        if session_id:
            await complete_university_session(int(session_id))

        correct_count = int(
            context.user_data.get("ut_correct_count", 0)
        )

        wrong_count = int(
            context.user_data.get("ut_wrong_count", 0)
        )

        total = len(batch)

        safe_total = md_escape(str(total))
        safe_correct_count = md_escape(str(correct_count))
        safe_wrong_count = md_escape(str(wrong_count))

        title = (
            "✅ *Mock Completed*"
            if session_mode == "course_mock"
            else "✅ *Practice Completed*"
        )

        outro = (
            "Great job\\. You can return to UNIVERSITY Practice for another course\\."
            if session_mode == "course_mock"
            else "Great job\\. You can return to UNIVERSITY Practice for another topic\\."
        )

        return await update.effective_message.reply_text(
            f"{title}\n\n"
            f"📚 Total Questions: *{safe_total}*\n"
            f"✅ Correct: *{safe_correct_count}*\n"
            f"❌ Wrong: *{safe_wrong_count}*\n\n"
            f"{outro}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🎓 UNIVERSITY Practice",
                            callback_data="university",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🏠 Back to Main Menu",
                            callback_data="menu:main",
                        )
                    ],
                ]
            ),
        )

    # =============================
    # CURRENT QUESTION
    # =============================
    question = batch[current_index]

    question_id = str(question["id"])

    user_id = update.effective_user.id

    category_code = context.user_data.get("ut_category_code")
    subject_code = context.user_data.get("ut_subject_code")
    module_id = context.user_data.get("ut_module_id")

    # preserve module state
    context.user_data["ut_module_id"] = module_id

    topic_id = str(
        question.get("topic_id")
        or context.user_data.get("ut_topic_id")
        or "__mock_course__"
    )

    served_question_ids = context.user_data.get(
        "ut_served_question_ids",
        [],
    )

    # =============================
    # CHARGE ONLY WHEN SERVED
    # =============================
    if question_id not in served_question_ids:

        if session_mode == "free_trial":
            deducted = await deduct_one_free_question(user_id)

            if not deducted:
                return await update.effective_message.reply_text(
                    "⚠️ You have no free question balance left\\.\n\n"
                    "Please buy a question pack to continue\\.",
                    parse_mode="MarkdownV2",
                )

            await add_question_to_topic_history(
                user_id=user_id,
                subject_code=subject_code,
                topic_id=topic_id,
                question_id=question_id,
            )

            if session_id:
                await increment_university_session_served(
                    int(session_id)
                )

        elif session_mode == "paid_session":
            deducted = await deduct_one_paid_question(user_id)

            if not deducted:
                return await update.effective_message.reply_text(
                    "⚠️ You have no paid UNIVERSITY question credits left\\.\n\n"
                    "Please buy another question pack to continue\\.",
                    parse_mode="MarkdownV2",
                )

            await add_question_to_topic_history(
                user_id=user_id,
                subject_code=subject_code,
                topic_id=topic_id,
                question_id=question_id,
            )

            if session_id:
                await increment_university_session_served(
                    int(session_id)
                )

        elif session_mode == "course_mock":
            if session_id:
                await increment_university_session_served(
                    int(session_id)
                )

        served_question_ids.append(question_id)

        context.user_data["ut_served_question_ids"] = (
            served_question_ids
        )

    # =============================
    # STORE CURRENT QUESTION
    # =============================
    context.user_data["ut_current_question"] = question
    context.user_data["ut_answered_current"] = False

    # =============================
    # UPDATE MOCK INDEX
    # =============================
    if session_mode == "course_mock" and session_id:
        await set_university_session_current_question_index(
            int(session_id),
            current_index,
        )

    # =============================
    # LOAD SUBJECT
    # =============================
    subject = get_university_subject_by_code(subject_code)

    if not subject:
        subject = {"name": subject_code}

    course_name = subject.get("name", subject_code)

    # tag for downstream builders
    question["_session_mode"] = session_mode

    # =============================
    # PASSAGE HANDLING
    # =============================
    if question_has_passage(question):

        if should_show_ut_passage_for_question(question, context):

            passage_start, passage_end = (
                get_ut_passage_question_range(
                    batch,
                    current_index,
                )
            )

            passage_text_msg = build_ut_passage_text(
                course_name=course_name,
                question_start=passage_start,
                question_end=passage_end,
                total_questions=len(batch),
                exam_ends_at=(
                    (session_row or {}).get("exam_ends_at")
                    if session_mode == "course_mock"
                    else None
                ),
                question=question,
            )

            mark_ut_passage_as_shown(question, context)

            sent_passage = await update.effective_message.reply_text(
                passage_text_msg,
                parse_mode="MarkdownV2",
            )

            store_ut_passage_message_id(
                message_id=sent_passage.message_id,
                context=context,
            )

    else:
        await clear_ut_passage_message(
            chat_id=update.effective_message.chat_id,
            context=context,
        )

    # =============================
    # QUESTION DISPLAY
    # =============================
    options = question.get("options", {})

    safe_question_text = md_escape(
        str(question.get("question") or "Question unavailable.")
    )

    safe_question_no = md_escape(str(current_index + 1))
    safe_total = md_escape(str(len(batch)))

    header_lines = []

    if session_mode == "course_mock":
        remaining = format_university_mock_time_remaining(
            (session_row or {}).get("exam_ends_at")
        )

        safe_remaining = md_escape(str(remaining))

        header_lines.append(
            "📝 *University Course Mock*"
        )

        header_lines.append(
            f"⏱ Time Remaining: *{safe_remaining}*"
        )

    else:
        header_lines.append(
            "📘 *UNIVERSITY Practice*"
        )

    header_lines.append(
        f"Question {safe_question_no} of {safe_total}"
    )

    option_lines = []

    for key in ["A", "B", "C", "D", "E"]:
        if key in options:
            safe_option_text = md_escape(
                str(options[key])
            )

            option_lines.append(
                f"{key}\\. {safe_option_text}"
            )

    text_msg = (
        "\n".join(header_lines)
        + "\n\n"
        + f"{safe_question_text}\n\n"
        + "\n".join(option_lines)
    )

    # =============================
    # ANSWER BUTTONS
    # =============================
    rows = []
    answer_row = []

    for key in ["A", "B", "C", "D", "E"]:

        if key in options:
            answer_row.append(
                InlineKeyboardButton(
                    key,
                    callback_data=f"ut_ans::{key}",
                )
            )

            if len(answer_row) == 2:
                rows.append(answer_row)
                answer_row = []

    if answer_row:
        rows.append(answer_row)

    rows.append(
        [
            InlineKeyboardButton(
                "🏠 End Practice",
                callback_data="ut_end_session",
            )
        ]
    )

    await update.effective_message.reply_text(
        text_msg,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(rows),
    )



# ----------------------------------------
# UNIVERSITY Serve First Handler
# ----------------------------------------  
async def university_serve_first_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    await send_current_university_question(update, context)


# =============================
# Answer handling
# =============================
async def university_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    if context.user_data.get("ut_answered_current", False):
        return await query.answer("You already answered this question\\.", show_alert=False)

    try:
        _, selected_option = query.data.split("::", 1)
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid answer selection\\.",
            parse_mode="MarkdownV2",
        )

    question = context.user_data.get("ut_current_question")
    if not question:
        return await query.message.reply_text(
            "⚠️ No active question found\\.",
            parse_mode="MarkdownV2",
        )

    user_id = update.effective_user.id
    session_id_raw = context.user_data.get("ut_session_id")
    if not session_id_raw:
        return await query.message.reply_text(
            "⚠️ Session expired\\. Please start again from UNIVERSITY Practice\\.",
            parse_mode="MarkdownV2",
        )

    session_id = int(session_id_raw)
    session_mode = context.user_data.get("ut_session_mode")
    subject_code = context.user_data.get("ut_subject_code")
    topic_id = str(question.get("topic_id") or context.user_data.get("ut_topic_id") or "__mock_course__")
    question_id = str(question["id"])
    correct_option = str(question["answer"]).strip().upper()
    selected_option = str(selected_option).strip().upper()
    is_correct = selected_option == correct_option
    question_order = int(context.user_data.get("ut_current_index", 0)) + 1

    await record_university_attempt(
        session_id=session_id,
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
        question_id=question_id,
        selected_option=selected_option,
        correct_option=correct_option,
        is_correct=is_correct,
    )

    await increment_university_session_result(session_id, is_correct)

    if session_mode == "course_mock":
        async with get_async_session() as session:
            async with session.begin():
                await session.execute(
                    text("""
                        update university_session_questions
                        set
                            selected_option = :selected_option,
                            is_correct = :is_correct,
                            updated_at = now()
                        where session_id = :session_id
                          and question_order = :question_order
                    """),
                    {
                        "session_id": int(session_id),
                        "question_order": int(question_order),
                        "selected_option": selected_option,
                        "is_correct": bool(is_correct),
                    },
                )

    context.user_data["ut_answered_current"] = True
    context.user_data["ut_last_selected_option"] = selected_option
    context.user_data["ut_last_correct_option"] = correct_option

    if is_correct:
        context.user_data["ut_correct_count"] = int(context.user_data.get("ut_correct_count", 0)) + 1
        result_text = "✅ *Correct\\!*"
    else:
        context.user_data["ut_wrong_count"] = int(context.user_data.get("ut_wrong_count", 0)) + 1

        safe_correct_option = md_escape(str(correct_option))
        safe_correct_option_text = md_escape(str(question["options"].get(correct_option, "---")))

        result_text = (
            f"❌ *Wrong\\!*\n\n"
            f"Correct answer: *{safe_correct_option}* \\- {safe_correct_option_text}"
        )

    await query.message.reply_text(
        result_text,
        parse_mode="MarkdownV2",
        reply_markup=make_after_answer_keyboard(),
    )


# --------------------------------------------
# UNIVERSITY Answer Details Handler
# --------------------------------------------
async def university_answer_details_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    question = context.user_data.get("ut_current_question")
    if not question:
        return await query.message.reply_text(
            "⚠️ No answered question found.",
            parse_mode="HTML",
        )

    explanation = question.get("explanation", {})

    question_restate = explanation.get("question_restate", "")
    core_concept = explanation.get("core_concept", "")
    why_this_matters = explanation.get("why_this_matters", "")
    reasoning_steps = explanation.get("step_by_step_reasoning", [])
    wrong_options = explanation.get("why_other_options_are_wrong", [])
    real_life_connection = explanation.get("real_life_connection", "")
    memory_tip = explanation.get("memory_tip", "")
    final_answer = explanation.get("final_answer", "")
    beginner_summary = explanation.get("beginner_friendly_summary", "")

    # SAFE HTML ESCAPING
    safe_question_restate = escape(str(question_restate))
    safe_core_concept = escape(str(core_concept))
    safe_why_this_matters = escape(str(why_this_matters))
    safe_real_life_connection = escape(str(real_life_connection))
    safe_memory_tip = escape(str(memory_tip))
    safe_final_answer = escape(str(final_answer))
    safe_beginner_summary = escape(str(beginner_summary))

    lines = ["📖 <b>Answer Details</b>\n"]

    if question_restate:
        lines.append(
            f"🔹 <b>Question Restated</b>\n{safe_question_restate}\n"
        )

    if core_concept:
        lines.append(
            f"🧠 <b>Core Concept</b>\n{safe_core_concept}\n"
        )

    if why_this_matters:
        lines.append(
            f"🎯 <b>Why This Matters</b>\n{safe_why_this_matters}\n"
        )

    if reasoning_steps:
        lines.append("🪜 <b>Step-by-Step Reasoning</b>")

        for i, step in enumerate(reasoning_steps, start=1):
            safe_step = escape(str(step))
            lines.append(f"{i}. {safe_step}")

        lines.append("")

    if wrong_options:
        lines.append("❌ <b>Why Other Options Are Wrong</b>")

        for item in wrong_options:
            safe_item = escape(str(item))
            lines.append(f"• {safe_item}")

        lines.append("")

    if real_life_connection:
        lines.append(
            f"🌍 <b>Real-Life Connection</b>\n{safe_real_life_connection}\n"
        )

    if memory_tip:
        lines.append(
            f"🧩 <b>Memory Tip</b>\n{safe_memory_tip}\n"
        )

    if final_answer:
        lines.append(
            f"✅ <b>Final Answer</b>\n{safe_final_answer}\n"
        )

    if beginner_summary:
        lines.append(
            f"📘 <b>Beginner-Friendly Summary</b>\n{safe_beginner_summary}"
        )

    await query.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=make_after_details_keyboard(),
    )


# ------------------------------
# UNIVERSITY Next Handler
# -----------------------------
async def university_next_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    current_index = int(context.user_data.get("ut_current_index", 0))
    context.user_data["ut_current_index"] = current_index + 1

    await send_current_university_question(update, context)


# ------------------------------
# UNIVERSITY End Session Handler
# ------------------------------
async def university_end_session_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    session_id = context.user_data.get("ut_session_id")
    session_mode = context.user_data.get("ut_session_mode")

    if session_mode == "course_mock" and session_id:
        await clear_ut_passage_message(
            chat_id=query.message.chat_id,
            context=context,
        )
        await complete_university_session(int(session_id))

    await clear_university_session_state(context)

    from handlers.core import go_start_callback
    await go_start_callback(update, context)

# =============================
# Back to mode
# =============================
async def university_back_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, category_code, subject_code = query.data.split("::")
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid back navigation\\.",
            parse_mode="MarkdownV2",
        )

    subject = get_university_subject_by_code(subject_code)

    if not subject:
        return await query.message.reply_text(
            "⚠️ Subject not found\\.",
            parse_mode="MarkdownV2",
        )

    context.user_data["ut_category_code"] = category_code
    context.user_data["ut_subject_code"] = subject_code
    context.user_data["ut_mode"] = None
    context.user_data["ut_topic_id"] = None
    context.user_data["ut_module_id"] = None

    safe_course_name = md_escape(str(subject["name"]))

    await query.message.reply_text(
        f"📘 *You selected:* {safe_course_name}\n\n"
        "How would you like to practice\\?",
        parse_mode="MarkdownV2",
        reply_markup=make_mode_keyboard(
            category_code,
            subject_code,
        ),
    )

# -------------------------------------
# University Back to Module Topics Handler
# ----------------------------------------
async def university_back_to_module_topics_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    try:
        _, category_code, subject_code, module_id = (
            query.data.split("::")
        )

    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid module navigation\\.",
            parse_mode="MarkdownV2",
        )

    context.user_data["ut_category_code"] = category_code
    context.user_data["ut_subject_code"] = subject_code
    context.user_data["ut_module_id"] = module_id

    subject = get_university_subject_by_code(subject_code)

    topics = get_university_module_topics(
        category_code,
        subject_code,
        module_id,
    )

    if not topics:
        return await query.message.reply_text(
            "⚠️ No topics found for this module yet\\.",
            parse_mode="MarkdownV2",
        )

    rows = []

    for topic in topics:
        rows.append(
            [
                InlineKeyboardButton(
                    topic["title"],
                    callback_data=(
                        f"ut_topic::{category_code}"
                        f"::{subject_code}"
                        f"::{module_id}"
                        f"::{topic['id']}"
                    ),
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                "🏠 Back to Main Menu",
                callback_data="menu:main",
            )
        ]
    )

    course_name = (
        subject["name"]
        if subject
        else subject_code
    )

    safe_course_name = md_escape(str(course_name))

    await query.message.reply_text(
        f"📚 *{safe_course_name} Topics*\n\n"
        "Choose a topic below\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# --------------------------------------
# University Back Module Handler
# --------------------------------------
async def university_back_modules_handler(update, context):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    try:
        _, category_code, subject_code = (
            query.data.split("::")
        )

    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid module navigation\\.",
            parse_mode="MarkdownV2",
        )

    # =====================================
    # SAVE FLOW STATE
    # =====================================
    context.user_data["ut_category_code"] = category_code
    context.user_data["ut_subject_code"] = subject_code
    context.user_data["ut_module_id"] = None
    context.user_data["ut_topic_id"] = None

    await query.message.reply_text(
        "📚 *Choose a Module*",
        parse_mode="MarkdownV2",
        reply_markup=make_module_keyboard(
            category_code,
            subject_code,
        ),
    )

# --------------------------------------
# University Back Subject Handler
# ------------------------------------
async def university_back_subjects_handler(update, context):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    try:
        _, category_code = query.data.split("::")
    except Exception:
        return

    category = get_university_category_by_code(category_code)

    if not category:
        return

    await query.message.reply_text(
        f"📚 *{md_escape(category['name'])}*\n\nChoose a subject\\.",
        parse_mode="MarkdownV2",
        reply_markup=make_subject_keyboard(category_code),
    )

# --------------------------------------
# University Use Paid Handler
# -------------------------------------
async def university_use_paid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user_id = update.effective_user.id
    paid_credits = await get_paid_question_credits(user_id)

    if paid_credits <= 0:
        return await query.message.reply_text(
            "⚠️ You do not have any paid UNIVERSITY question credits yet\\.\n\nPlease buy a question pack first\\.",
            parse_mode="MarkdownV2",
        )

    safe_paid_credits = md_escape(str(paid_credits))

    await query.message.reply_text(
        f"💳 *Use Paid Credits*\n\n"
        f"You currently have *{safe_paid_credits} paid question credits*\\.\n\n"
        "How many questions do you want to answer in this session\\?",
        parse_mode="MarkdownV2",
        reply_markup=make_paid_session_count_keyboard(),
    )


# ----------------------------------
# UNIVERSITY Paid Count Handler
# ----------------------------------
async def university_paid_count_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        requested_count = int(query.data.replace("ut_paidcount_", "", 1))
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid paid session size\\.",
            parse_mode="MarkdownV2",
        )

    user_id = update.effective_user.id

    category_code = context.user_data.get("ut_category_code")
    subject_code = context.user_data.get("ut_subject_code")
    module_id = context.user_data.get("ut_module_id")
    topic_id = context.user_data.get("ut_topic_id")

    if not subject_code or not topic_id:
        return await query.message.reply_text(
            "⚠️ Topic session data missing\\. Please choose your course and topic again\\.",
            parse_mode="MarkdownV2",
        )

    paid_credits = await get_paid_question_credits(user_id)
    if paid_credits <= 0:
        return await query.message.reply_text(
            "⚠️ You do not have enough paid UNIVERSITY credits\\.\n\nPlease buy a question pack first\\.",
            parse_mode="MarkdownV2",
        )

    actual_count = min(requested_count, paid_credits)

    seen_question_ids = await get_seen_question_ids_for_topic(
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
    )

    batch = prepare_university_topic_question_batch(
        category_code=category_code,
        subject_code=subject_code,
        module_id=module_id,
        topic_id=topic_id,
        requested_count=actual_count,
        seen_question_ids=seen_question_ids,
    )

    if batch["cycle_reset"]:
        await reset_topic_history(user_id, subject_code, topic_id)

    selected_questions = batch["selected_questions"]
    selected_question_ids = batch["selected_question_ids"]

    if not selected_questions:
        return await query.message.reply_text(
            "⚠️ No active questions found for this topic yet\\.",
            parse_mode="MarkdownV2",
        )

    session_id = await create_university_session(
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
        question_target=len(selected_questions),
        mode="topic_practice",
    )

    context.user_data["ut_session_id"] = session_id
    context.user_data["ut_session_mode"] = "paid_session"
    context.user_data["ut_question_batch"] = selected_questions
    context.user_data["ut_question_ids"] = selected_question_ids
    context.user_data["ut_current_index"] = 0
    context.user_data["ut_session_target"] = len(selected_questions)
    context.user_data["ut_correct_count"] = 0
    context.user_data["ut_wrong_count"] = 0
    context.user_data["ut_current_question"] = None
    context.user_data["ut_answered_current"] = False
    context.user_data["ut_served_question_ids"] = []
    context.user_data["ut_shown_passages"] = []
    context.user_data["ut_last_passage"] = None
    context.user_data["ut_last_passage_id_shown"] = ""
    context.user_data["ut_active_passage_message_id"] = None

    topic = next(
        (
            t for t in get_university_module_topics(
                category_code,
                subject_code,
                module_id,
            )
            if t["id"] == topic_id
        ),
        None,
    )
    
    topic_title = topic["title"] if topic else topic_id

    subject = get_university_subject_by_code(subject_code)
    course_name = subject["name"] if subject else subject_code

    safe_course_name = md_escape(str(course_name))
    safe_topic_title = md_escape(str(topic_title))
    safe_question_count = md_escape(str(len(selected_questions)))
    safe_requested_count = md_escape(str(requested_count))
    safe_paid_credits = md_escape(str(paid_credits))

    reset_note = (
        "\n♻️ Topic cycle reset because you already exhausted this topic before\\."
        if batch["cycle_reset"]
        else ""
    )

    adjusted_note = ""
    if actual_count < requested_count:
        adjusted_note = (
            f"\nℹ️ You requested *{safe_requested_count}*, but you currently have "
            f"*{safe_paid_credits}* paid credits available\\."
        )

    await query.message.reply_text(
        f"✅ *Paid Session Started*\n\n"
        f"📘 course: *{safe_course_name}*\n"
        f"🧪 Topic: *{safe_topic_title}*\n"
        f"📚 Questions in this session: *{safe_question_count}*"
        f"{adjusted_note}"
        f"{reset_note}\n\n"
        "Tap below to start Question 1\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶ Start Questions", callback_data="ut_serve_first")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )


async def university_mock_start_paid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    tg = update.effective_user
    user_id = tg.id

    category_code = context.user_data.get("ut_category_code")
    subject_code = context.user_data.get("ut_subject_code")
    if not subject_code:
        return await query.message.reply_text(
            "⚠️ course session data missing\\. Please choose your course again\\.",
            parse_mode="MarkdownV2",
        )

    subject = get_university_subject_by_code(subject_code)
    if not subject:
        return await query.message.reply_text(
            "⚠️ course not found\\.",
            parse_mode="MarkdownV2",
        )

    available_sessions = await get_mock_sessions_available(user_id)

    if available_sessions < 1:
        safe_available = md_escape(str(available_sessions))
        return await query.message.reply_text(
            f"⚠️ You do not have any mock sessions available\\.\n\n"
            f"🎟 Available mock sessions: *{safe_available}*\n\n"
            "Please buy a mock session first\\.",
            parse_mode="MarkdownV2",
        )

    deducted = await deduct_one_mock_session(user_id)
    if not deducted:
        return await query.message.reply_text(
            "⚠️ Could not reserve your mock session right now\\. Please try again\\.",
            parse_mode="MarkdownV2",
        )

    question_target = get_university_mock_question_count(subject_code)

    session_id = await create_university_mock_session(
        user_id=user_id,
        subject_code=subject_code,
        question_target=question_target,
    )

    paper_info = await create_university_course_mock_paper_if_needed(
        user_id=user_id,
        session_id=session_id,
        subject_code=subject_code,
        category_code=category_code,
    )

    paper_rows = paper_info.get("paper_rows") or []
    batch = build_university_batch_from_paper_rows(paper_rows)

    if not batch:
        return await query.message.reply_text(
            "⚠️ No active questions could be prepared for this mock paper yet\\.",
            parse_mode="MarkdownV2",
        )

    session_row = await get_university_session_by_id(session_id)
    exam_ends_at = (session_row or {}).get("exam_ends_at")

    context.user_data["ut_session_id"] = session_id
    context.user_data["ut_session_mode"] = "course_mock"
    context.user_data["ut_mode"] = "course_mock"
    context.user_data["ut_topic_id"] = None
    context.user_data["ut_module_id"] = None
    context.user_data["ut_question_batch"] = batch
    context.user_data["ut_question_ids"] = [str(q.get("id")) for q in batch if q.get("id")]
    context.user_data["ut_current_index"] = 0
    context.user_data["ut_session_target"] = len(batch)
    context.user_data["ut_correct_count"] = 0
    context.user_data["ut_wrong_count"] = 0
    context.user_data["ut_current_question"] = None
    context.user_data["ut_answered_current"] = False
    context.user_data["ut_served_question_ids"] = []
    context.user_data["ut_shown_passages"] = []
    context.user_data["ut_last_passage"] = None
    context.user_data["ut_last_passage_id_shown"] = ""
    context.user_data["ut_active_passage_message_id"] = None

    safe_course_name = md_escape(str(subject["name"]))
    safe_question_target = md_escape(str(question_target))
    safe_duration = md_escape(format_university_mock_time_remaining(exam_ends_at))

    reset_note = (
        "\n♻️ course cycle reset because you already exhausted the unseen pool before\\."
        if paper_info.get("cycle_reset")
        else ""
    )

    await query.message.reply_text(
        f"📝 *course Mock Started*\n\n"
        f"📘 course: *{safe_course_name}*\n"
        f"📚 Questions: *{safe_question_target}*\n"
        f"⏱ Time Allowed: *{safe_duration}*"
        f"{reset_note}\n\n"
        "Tap below to start Question 1\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶ Start Questions", callback_data="ut_serve_first")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="ut_end_session")],
            ]
        ),
    )


async def university_mock_resume_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    tg = update.effective_user
    user_id = tg.id
    subject_code = context.user_data.get("ut_subject_code")

    if not subject_code:
        return await query.message.reply_text(
            "⚠️ course session data missing\\. Please choose your course again\\.",
            parse_mode="MarkdownV2",
        )

    active_session = await get_latest_active_university_mock_session_for_user(
        user_id=user_id,
        subject_code=subject_code,
    )

    if not active_session:
        return await query.message.reply_text(
            "⚠️ No active course mock was found for this course\\.",
            parse_mode="MarkdownV2",
        )

    session_id = int(active_session["id"])

    if is_university_mock_time_expired(active_session.get("exam_ends_at")):
        await complete_university_session(session_id)

        safe_correct = md_escape(str(active_session.get("correct_count") or 0))
        safe_wrong = md_escape(str(active_session.get("wrong_count") or 0))
        safe_total = md_escape(str(active_session.get("question_target") or 0))

        return await query.message.reply_text(
            f"⏰ *Mock time is up\\.*\n\n"
            f"📚 Total Questions: *{safe_total}*\n"
            f"✅ Correct: *{safe_correct}*\n"
            f"❌ Wrong: *{safe_wrong}*\n\n"
            "This course mock has ended\\.",
            parse_mode="MarkdownV2",
        )

    paper_rows = await get_university_session_paper(session_id)
    batch = build_university_batch_from_paper_rows(paper_rows)

    if not batch:
        return await query.message.reply_text(
            "⚠️ Could not reload your saved mock paper\\.",
            parse_mode="MarkdownV2",
        )

    current_index = int(active_session.get("current_question_index") or 0)

    already_served_ids = []
    for i, row in enumerate(paper_rows):
        if i <= current_index:
            qid = row.get("question_id")
            if qid:
                already_served_ids.append(str(qid))

    context.user_data["ut_session_id"] = session_id
    context.user_data["ut_session_mode"] = "course_mock"
    context.user_data["ut_mode"] = "course_mock"
    context.user_data["ut_question_batch"] = batch
    context.user_data["ut_question_ids"] = [str(q.get("id")) for q in batch if q.get("id")]
    context.user_data["ut_current_index"] = current_index
    context.user_data["ut_session_target"] = len(batch)
    context.user_data["ut_correct_count"] = int(active_session.get("correct_count") or 0)
    context.user_data["ut_wrong_count"] = int(active_session.get("wrong_count") or 0)
    context.user_data["ut_current_question"] = None
    context.user_data["ut_answered_current"] = False
    context.user_data["ut_served_question_ids"] = already_served_ids
    context.user_data["ut_shown_passages"] = []
    context.user_data["ut_last_passage"] = None
    context.user_data["ut_last_passage_id_shown"] = ""
    context.user_data["ut_active_passage_message_id"] = None

    await send_current_university_question(update, context)


# =============================
# Register handlers
# =============================
def register_handlers(application):
    application.add_handler(CommandHandler("university", university_handler))
    application.add_handler(CallbackQueryHandler(university_handler, pattern=r"^(university|uni_start)$"))
    application.add_handler(CallbackQueryHandler(university_category_handler, pattern=r"^ut_cat_"))
    application.add_handler(CallbackQueryHandler(university_subject_handler, pattern=r"^ut_subj_"))
    application.add_handler(CallbackQueryHandler(university_module_handler, pattern=r"^ut_module::"))
    application.add_handler(CallbackQueryHandler(university_mode_handler, pattern=r"^ut_mode_"))

    application.add_handler(CallbackQueryHandler(university_mock_start_paid_handler, pattern=r"^ut_mock_start_paid$"))
    application.add_handler(CallbackQueryHandler(university_mock_resume_handler, pattern=r"^ut_mock_resume$"))

    application.add_handler(CallbackQueryHandler(university_topic_handler, pattern=r"^ut_topic::"))
    application.add_handler(CallbackQueryHandler(university_back_mode_handler, pattern=r"^ut_back_mode::"))
    application.add_handler(CallbackQueryHandler(university_back_subjects_handler, pattern=r"^ut_back_subjects::"))
    application.add_handler(CallbackQueryHandler(university_back_to_module_topics_handler, pattern=r"^ut_back_module::"))
    
    application.add_handler(CallbackQueryHandler(university_back_modules_handler, pattern=r"^ut_back_modules::"))
    application.add_handler(CallbackQueryHandler(university_start_free_handler, pattern=r"^ut_start_free$"))
    application.add_handler(CallbackQueryHandler(university_use_paid_handler, pattern=r"^ut_use_paid$"))
    application.add_handler(CallbackQueryHandler(university_paid_count_handler, pattern=r"^ut_paidcount_"))
    application.add_handler(CallbackQueryHandler(university_buy_pack_handler, pattern=r"^ut_buy_"))
    application.add_handler(CallbackQueryHandler(university_mock_buy_session_handler, pattern=r"^ut_mock_buy_"))
    application.add_handler(CallbackQueryHandler(university_serve_first_handler, pattern=r"^ut_serve_first$"))
    application.add_handler(CallbackQueryHandler(university_end_session_handler, pattern=r"^ut_end_session$"))
    application.add_handler(CallbackQueryHandler(university_answer_handler, pattern=r"^ut_ans::"))
    application.add_handler(CallbackQueryHandler(university_answer_details_handler, pattern=r"^ut_details$"))
    application.add_handler(CallbackQueryHandler(university_next_handler, pattern=r"^ut_next$"))
    
