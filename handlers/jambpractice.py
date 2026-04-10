# ====================================================================
# handlers/jambpractice.py
# ====================================================================

import json
import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from sqlalchemy import text
from helpers import md_escape

from services.flutterwave_client import create_checkout, build_tx_ref, calculate_jamb_credits
from services.jamb_payments import create_pending_jamb_payment
from db import get_async_session
from jamb_loader import (
    get_jamb_subjects,
    get_subject_topics,
    get_subject_by_code,
    prepare_topic_question_batch,
    prepare_subject_question_batch,
    prepare_use_of_english_batch,
)

logger = logging.getLogger(__name__)

TOPICS_PER_PAGE = 7


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


async def create_jamb_session(
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
                    insert into jamb_sessions (
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
                from jamb_user_topic_history
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
                    delete from jamb_user_topic_history
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
    access = await get_jamb_user_access(user_id)
    return int((access or {}).get("paid_question_credits", 0))

async def get_mock_sessions_available(user_id: int) -> int:
    access = await get_jamb_user_access(user_id)
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
                    update jamb_user_access
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
                    update jamb_user_access
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
                    update jamb_user_access
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
                    insert into jamb_user_topic_history (
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


async def record_jamb_attempt(
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
                    insert into jamb_attempts (
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


async def increment_jamb_session_served(session_id: int):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    update jamb_sessions
                    set
                        questions_served = questions_served + 1
                    where id = :session_id
                """),
                {"session_id": session_id},
            )


async def increment_jamb_session_result(session_id: int, is_correct: bool):
    async with get_async_session() as session:
        async with session.begin():
            if is_correct:
                await session.execute(
                    text("""
                        update jamb_sessions
                        set
                            correct_count = correct_count + 1
                        where id = :session_id
                    """),
                    {"session_id": session_id},
                )
            else:
                await session.execute(
                    text("""
                        update jamb_sessions
                        set
                            wrong_count = wrong_count + 1
                        where id = :session_id
                    """),
                    {"session_id": session_id},
                )


async def complete_jamb_session(session_id: int):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    update jamb_sessions
                    set
                        status = 'completed',
                        ended_at = now()
                    where id = :session_id
                """),
                {"session_id": session_id},
            )


# =============================
# Mock-by-subject helpers
# =============================
def get_jamb_mock_question_count(subject_code: str) -> int:
    subject_code = str(subject_code or "").strip().lower()
    return 60 if subject_code == "eng" else 40


def get_jamb_mock_duration_minutes(subject_code: str) -> int:
    subject_code = str(subject_code or "").strip().lower()
    return 45 if subject_code == "eng" else 30


def extract_correct_option(question: dict) -> str:
    for key in ("correct_option", "correct_answer", "answer", "correctAnswer"):
        value = question.get(key)
        if value:
            return str(value).strip().upper()
    return ""


def format_jamb_mock_time_remaining(exam_ends_at) -> str:
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


def is_jamb_mock_time_expired(exam_ends_at) -> bool:
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


async def get_seen_question_ids_for_subject(user_id: int, subject_code: str) -> list[str]:
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                select distinct question_id
                from jamb_user_topic_history
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


async def reset_subject_history(user_id: int, subject_code: str):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    delete from jamb_user_topic_history
                    where user_id = :user_id
                      and subject_code = :subject_code
                """),
                {
                    "user_id": user_id,
                    "subject_code": subject_code,
                },
            )


async def add_question_to_subject_history(
    user_id: int,
    subject_code: str,
    question_id: str,
    topic_id: str | None = None,
):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    insert into jamb_user_topic_history (
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
                    "topic_id": topic_id or "__mock_subject__",
                    "question_id": question_id,
                },
            )


async def deduct_paid_questions(user_id: int, question_count: int) -> bool:
    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    update jamb_user_access
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


async def get_latest_active_jamb_mock_session_for_user(user_id: int, subject_code: str) -> Optional[dict]:
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
                from jamb_sessions
                where user_id = :user_id
                  and subject_code = :subject_code
                  and mode = 'mock_utme'
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


async def set_jamb_session_current_question_index(session_id: int, current_question_index: int):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    update jamb_sessions
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


async def get_jamb_session_paper(session_id: int) -> list[dict]:
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
                from jamb_session_questions
                where session_id = :session_id
                order by question_order asc
            """),
            {"session_id": int(session_id)},
        )
        rows = result.mappings().all()
        return [dict(row) for row in rows]


async def create_jamb_subject_mock_paper_if_needed(
    user_id: int,
    session_id: int,
    subject_code: str,
) -> dict:
    existing_paper = await get_jamb_session_paper(session_id)
    if existing_paper:
        return {
            "created_now": False,
            "paper_rows": existing_paper,
            "selected_count": len(existing_paper),
            "cycle_reset": False,
        }

    seen_question_ids = await get_seen_question_ids_for_subject(user_id, subject_code)
    requested_count = get_jamb_mock_question_count(subject_code)

    if subject_code == "eng":
        batch = prepare_use_of_english_batch(
            seen_question_ids=seen_question_ids,
        )
    else:
        batch = prepare_subject_question_batch(
            subject_code=subject_code,
            requested_count=requested_count,
            seen_question_ids=seen_question_ids,
        )

    if batch.get("cycle_reset"):
        await reset_subject_history(user_id, subject_code)

    selected_questions = batch["selected_questions"]

    async with get_async_session() as session:
        async with session.begin():
            for idx, question in enumerate(selected_questions, start=1):
                await session.execute(
                    text("""
                        insert into jamb_session_questions (
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
        await add_question_to_subject_history(
            user_id=user_id,
            subject_code=subject_code,
            question_id=str(question.get("id")),
            topic_id=str(question.get("topic_id") or "__mock_subject__"),
        )

    paper_rows = await get_jamb_session_paper(session_id)

    return {
        "created_now": True,
        "paper_rows": paper_rows,
        "selected_count": len(paper_rows),
        "cycle_reset": bool(batch.get("cycle_reset")),
    }


async def create_jamb_mock_session(
    user_id: int,
    subject_code: str,
    question_target: int,
) -> int:
    exam_ends_at = datetime.now(timezone.utc) + timedelta(
        minutes=get_jamb_mock_duration_minutes(subject_code)
    )

    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    insert into jamb_sessions (
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
                        'mock_utme',
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
                    "topic_id": "__mock_subject__",
                    "question_target": int(question_target),
                    "exam_ends_at": exam_ends_at,
                },
            )
            session_id = result.scalar_one()
            return int(session_id)


async def get_jamb_session_by_id(session_id: int) -> Optional[dict]:
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
                from jamb_sessions
                where id = :session_id
                limit 1
            """),
            {"session_id": int(session_id)},
        )
        row = result.mappings().first()
        return dict(row) if row else None


def build_jamb_mock_resume_text(subject_name: str, next_question_no: int, exam_ends_at) -> str:
    safe_subject_name = md_escape(str(subject_name))
    safe_next_question_no = md_escape(str(next_question_no))
    safe_remaining = md_escape(format_jamb_mock_time_remaining(exam_ends_at))

    return (
        f"📝 *Resume Mock UTME*\n\n"
        f"📘 Subject: *{safe_subject_name}*\n"
        f"⏱ Time Remaining: *{safe_remaining}*\n"
        f"➡ Resume From: *Question {safe_next_question_no}*\n\n"
        "Tap below to continue your subject mock\\."
    )


def make_jamb_mock_resume_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶ Resume Mock", callback_data="jp_mock_resume")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def build_jamb_mock_access_text(subject_name: str, question_count: int, mock_sessions_available: int) -> str:
    safe_subject_name = md_escape(str(subject_name))
    safe_question_count = md_escape(str(question_count))
    safe_mock_sessions = md_escape(str(mock_sessions_available))
    duration_text = "45 minutes" if question_count == 60 else "30 minutes"
    safe_duration = md_escape(duration_text)

    return (
        f"📝 *Mock UTME \\(By Subject\\)*\n\n"
        f"📘 Subject: *{safe_subject_name}*\n"
        f"📚 Questions: *{safe_question_count}*\n"
        f"⏱ Time Allowed: *{safe_duration}*\n"
        f"🎟 Mock Sessions Available: *{safe_mock_sessions}*\n\n"
        "This mode uses a full subject paper and is paid separately from topic practice\\.\n"
        "If you already have a mock session, you can start immediately\\."
    )


def make_jamb_mock_access_keyboard(subject_code: str, can_start: bool) -> InlineKeyboardMarkup:
    rows = []

    if can_start:
        rows.append([InlineKeyboardButton("▶ Start Mock Now", callback_data="jp_mock_start_paid")])

    rows.extend([
        [InlineKeyboardButton("🎟 Get 1 Mock Session — ₦100", callback_data="jp_mock_buy_1")],
        [InlineKeyboardButton("🎟 Get 2 Mock Sessions — ₦200", callback_data="jp_mock_buy_2")],
        [InlineKeyboardButton("🎟 Get 3 Mock Sessions — ₦300", callback_data="jp_mock_buy_3")],
        [InlineKeyboardButton("🎟 Get 4 Mock Sessions — ₦400", callback_data="jp_mock_buy_4")],
        [InlineKeyboardButton("🎟 Get 5 Mock Sessions — ₦500", callback_data="jp_mock_buy_5")],
        [InlineKeyboardButton("⬅️ Back to Mode", callback_data=f"jp_back_mode_{subject_code}")],
        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
    ])

    return InlineKeyboardMarkup(rows)


def build_jamb_batch_from_paper_rows(paper_rows: list[dict]) -> list[dict]:
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
            [InlineKeyboardButton("📝 Mock UTME (By Subject)", callback_data=f"jp_mode_mock_{subject_code}")],
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


def make_topic_access_keyboard_for_subject(
    subject_code: str,
    has_free_trial: bool,
    has_paid_credits: bool,
):
    rows = []

    if has_free_trial:
        rows.append([InlineKeyboardButton("🎁 Use Free Trial (5 Questions)", callback_data="jp_start_free")])

    if has_paid_credits:
        rows.append([InlineKeyboardButton("✅ Use Paid Credits", callback_data="jp_use_paid")])

    rows.extend([
        [InlineKeyboardButton("💳 Get 50 Questions — ₦100", callback_data="jp_buy_50")],
        [InlineKeyboardButton("💳 Get 100 Questions — ₦200", callback_data="jp_buy_100")],
        [InlineKeyboardButton("💳 Get 150 Questions — ₦300", callback_data="jp_buy_150")],
        [InlineKeyboardButton("💳 Get 200 Questions — ₦400", callback_data="jp_buy_200")],
        [InlineKeyboardButton("⬅️ Back to Topics", callback_data=f"jp_topicpage_{subject_code}_1")],
        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
    ])

    return InlineKeyboardMarkup(rows)


def make_after_answer_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➡️ Next", callback_data="jp_next")],
            [InlineKeyboardButton("📖 Answer Details", callback_data="jp_details")],
            [InlineKeyboardButton("🏠 End Practice", callback_data="menu:main")],
        ]
    )


def make_after_details_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➡️ Next", callback_data="jp_next")],
            [InlineKeyboardButton("🏠 End Practice", callback_data="menu:main")],
        ]
    )


def make_paid_session_count_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("10", callback_data="jp_paidcount_10"),
                InlineKeyboardButton("20", callback_data="jp_paidcount_20"),
            ],
            [
                InlineKeyboardButton("30", callback_data="jp_paidcount_30"),
                InlineKeyboardButton("50", callback_data="jp_paidcount_50"),
            ],
            [InlineKeyboardButton("⬅️ Back to JAMB Practice", callback_data="jambpractice")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


# =============================
# Message builders
# =============================
def build_welcome_text(free_remaining: int, paid_credits: int) -> str:
    safe_free_remaining = md_escape(str(free_remaining))
    safe_paid_credits = md_escape(str(paid_credits))

    return (
        "🎓 *Welcome to JAMB Practice*\n\n"
        "Practice original UTME\\-style questions by subject and topic\\.\n"
        "You can study with detailed answer explanations after each question\\.\n\n"
        "*How it works:*\n"
        "• First\\-time users get *5 free questions*\n"
        "• After that, it costs *₦100 per 50 questions*\n"
        "• Questions are served topic by topic\n"
        "• Repeats are avoided until you exhaust the topic bank\n\n"
        "*Disclaimer:*\n"
        "This is an independent study tool and not an official JAMB platform\\.\n\n"
        f"🎁 Free questions left: *{safe_free_remaining}*\n"
        f"💳 Paid question credits: *{safe_paid_credits}*\n\n"
        "Please choose a subject below\\."
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
        parse_mode="MarkdownV2",
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
        parse_mode="MarkdownV2",
        reply_markup=make_mode_keyboard(subject_code),
    )

# --------------------------------
# JAMB SUBJECT MOCK SCREEN
# -------------------------------- 
async def open_jamb_subject_mock_screen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    subject_code: str,
):
    context.user_data["jp_subject_code"] = subject_code
    context.user_data["jp_mode"] = "mock_utme"
    context.user_data["jp_topic_id"] = None

    tg = update.effective_user
    user_id = tg.id

    subject = get_subject_by_code(subject_code)
    if not subject:
        return await update.effective_message.reply_text(
            "⚠️ Subject not found\\.",
            parse_mode="MarkdownV2",
        )

    active_session = await get_latest_active_jamb_mock_session_for_user(
        user_id=user_id,
        subject_code=subject_code,
    )

    if active_session and not is_jamb_mock_time_expired(active_session.get("exam_ends_at")):
        context.user_data["jp_session_id"] = int(active_session["id"])
        context.user_data["jp_session_mode"] = "mock_utme"

        next_question_no = max(1, int(active_session.get("current_question_index") or 0) + 1)

        return await update.effective_message.reply_text(
            build_jamb_mock_resume_text(
                subject_name=subject["name"],
                next_question_no=next_question_no,
                exam_ends_at=active_session.get("exam_ends_at"),
            ),
            parse_mode="MarkdownV2",
            reply_markup=make_jamb_mock_resume_keyboard(),
        )

    mock_sessions_available = await get_mock_sessions_available(user_id)
    question_count = get_jamb_mock_question_count(subject_code)
    can_start = mock_sessions_available >= 1

    return await update.effective_message.reply_text(
        build_jamb_mock_access_text(
            subject_name=subject["name"],
            question_count=question_count,
            mock_sessions_available=mock_sessions_available,
        ),
        parse_mode="MarkdownV2",
        reply_markup=make_jamb_mock_access_keyboard(subject_code, can_start),
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

    if data.startswith("jp_mode_mock_"):
        subject_code = data.replace("jp_mode_mock_", "", 1)
        return await open_jamb_subject_mock_screen(update, context, subject_code)


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
        return await query.message.reply_text(
            "⚠️ Invalid topic page\\.",
            parse_mode="MarkdownV2",
        )

    context.user_data["jp_subject_code"] = subject_code
    context.user_data["jp_topic_page"] = page

    subject = get_subject_by_code(subject_code)
    kb, page, total_pages = make_topics_keyboard(subject_code, page)

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
        return await query.message.reply_text(
            "⚠️ Invalid topic selection\\.",
            parse_mode="MarkdownV2",
        )

    topics = get_subject_topics(subject_code)
    selected_topic = next((t for t in topics if t["id"] == topic_id), None)

    if not selected_topic:
        return await query.message.reply_text(
            "⚠️ Topic not found\\.",
            parse_mode="MarkdownV2",
        )

    context.user_data["jp_subject_code"] = subject_code
    context.user_data["jp_topic_id"] = topic_id

    tg = update.effective_user
    await ensure_jamb_user_access(tg.id)
    access = await get_jamb_user_access(tg.id)

    free_remaining = int((access or {}).get("free_questions_remaining", 0))
    paid_credits = int((access or {}).get("paid_question_credits", 0))
    has_free_trial = free_remaining > 0
    has_paid_credits = paid_credits > 0

    safe_topic_title = md_escape(str(selected_topic["title"]))
    safe_free_remaining = md_escape(str(free_remaining))
    safe_paid_credits = md_escape(str(paid_credits))

    await query.message.reply_text(
        f"✅ *Topic selected:* {safe_topic_title}\n\n"
        f"🎁 Free questions left: *{safe_free_remaining}*\n"
        f"💳 Paid question credits: *{safe_paid_credits}*\n\n"
        "Choose how you want to continue:",
        parse_mode="MarkdownV2",
        reply_markup=make_topic_access_keyboard_for_subject(
            subject_code,
            has_free_trial,
            has_paid_credits,
        ),
    )


# =============================
# Free trial start
# =============================
async def jamb_start_free_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    tg = update.effective_user
    user_id = tg.id

    subject_code = context.user_data.get("jp_subject_code")
    topic_id = context.user_data.get("jp_topic_id")

    if not subject_code or not topic_id:
        return await query.message.reply_text(
            "⚠️ Topic session data missing\\. Please choose your subject and topic again\\.",
            parse_mode="MarkdownV2",
            reply_markup=make_subject_keyboard(),
        )

    access = await get_jamb_user_access(user_id)
    free_remaining = int((access or {}).get("free_questions_remaining", 0))

    if free_remaining <= 0:
        return await query.message.reply_text(
            "⚠️ You have no free JAMB questions left\\.\n\nPlease buy a question pack to continue\\.",
            parse_mode="MarkdownV2",
        )

    requested_count = min(5, free_remaining)

    seen_question_ids = await get_seen_question_ids_for_topic(
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
    )

    batch = prepare_topic_question_batch(
        subject_code=subject_code,
        topic_id=topic_id,
        requested_count=requested_count,
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

    session_id = await create_jamb_session(
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
        question_target=len(selected_questions),
        mode="topic_practice",
    )

    context.user_data["jp_session_id"] = session_id
    context.user_data["jp_session_mode"] = "free_trial"
    context.user_data["jp_question_batch"] = selected_questions
    context.user_data["jp_question_ids"] = selected_question_ids
    context.user_data["jp_current_index"] = 0
    context.user_data["jp_session_target"] = len(selected_questions)
    context.user_data["jp_correct_count"] = 0
    context.user_data["jp_wrong_count"] = 0
    context.user_data["jp_current_question"] = None
    context.user_data["jp_answered_current"] = False
    context.user_data["jp_served_question_ids"] = []
    context.user_data["jp_shown_passages"] = []
    context.user_data["jp_last_passage"] = None

    topic = next((t for t in get_subject_topics(subject_code) if t["id"] == topic_id), None)
    topic_title = topic["title"] if topic else topic_id

    subject = get_subject_by_code(subject_code)
    subject_name = subject["name"] if subject else subject_code

    safe_subject_name = md_escape(str(subject_name))
    safe_topic_title = md_escape(str(topic_title))
    safe_question_count = md_escape(str(len(selected_questions)))

    reset_note = (
        "\n♻️ Topic cycle reset because you already exhausted this topic before\\."
        if batch["cycle_reset"]
        else ""
    )

    await query.message.reply_text(
        f"🎉 *Free Trial Started*\n\n"
        f"📘 Subject: *{safe_subject_name}*\n"
        f"🧪 Topic: *{safe_topic_title}*\n"
        f"📚 Questions in this session: *{safe_question_count}*"
        f"{reset_note}\n\n"
        "Next step: we will now start serving Question 1\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶ Start Questions", callback_data="jp_serve_first")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )


# =============================
# Buy question pack
# =============================
async def jamb_buy_pack_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data
    pack_size = data.replace("jp_buy_", "", 1)

    pricing_map = {
        "50": 100,
        "100": 200,
        "150": 300,
        "200": 400,
    }

    if pack_size not in pricing_map:
        return await query.message.reply_text(
            "⚠️ Invalid JAMB package selected\\.",
            parse_mode="MarkdownV2",
        )

    amount = pricing_map[pack_size]
    credits = calculate_jamb_credits(amount)

    user = query.from_user
    tg_id = user.id
    username = user.username or f"user_{tg_id}"
    email = f"{username}@naijaprizegate.ng"

    tx_ref = build_tx_ref("JAMB")

    async with get_async_session() as session:
        await create_pending_jamb_payment(
            session,
            payment_reference=tx_ref,
            user_id=tg_id,
            amount_paid=amount,
            question_credits_added=credits,
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
            "product_type": "JAMB",
        },
        product_type="JAMB",
    )

    if not checkout_url:
        async with get_async_session() as session:
            await session.execute(
                text("""
                    update jamb_payments
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
        f"💳 *JAMB Question Pack Selected*\n\n"
        f"📚 Questions: *{safe_credits}*\n"
        f"💰 Amount: *₦{safe_amount}*\n\n"
        "After successful payment, your JAMB question credits will be added automatically\\.\n\n"
        "Tap below to complete payment\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💳 Pay Securely", url=checkout_url)],
                [InlineKeyboardButton("⬅️ Back to JAMB Practice", callback_data="jambpractice")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )


async def jamb_mock_buy_session_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data
    session_count_str = data.replace("jp_mock_buy_", "", 1)

    user = query.from_user
    tg_id = user.id
    username = user.username or f"user_{tg_id}"
    email = f"{username}@naijaprizegate.ng"

    subject_code = context.user_data.get("jp_subject_code")
    if not subject_code:
        return await query.message.reply_text(
            "⚠️ Subject session data missing\\. Please choose your subject again\\.",
            parse_mode="MarkdownV2",
        )
    
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

    session_count = int(session_count_str)
    amount = pricing_map[session_count_str]


    tx_ref = build_tx_ref("JAMBMOCKSUBJECT")

    async with get_async_session() as session:
        async with session.begin():
            await create_pending_jamb_payment(
                session,
                payment_reference=tx_ref,
                user_id=tg_id,
                amount_paid=amount,
                question_credits_added=0,
                mock_sessions_added=session_count,
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
            "product_type": "JAMBMOCKSUBJECT",
            "mock_sessions_added": str(session_count),
            "subject_code": str(subject_code),
        },
        product_type="JAMBMOCKSUBJECT",
    )

    if not checkout_url:
        async with get_async_session() as session:
            async with session.begin():
                await session.execute(
                    text("""
                        update jamb_payments
                        set
                            payment_status = 'expired',
                            updated_at = now()
                        where payment_reference = :payment_reference
                          and lower(coalesce(payment_status, '')) = 'pending'
                    """),
                    {"payment_reference": tx_ref},
                )

        return await query.message.reply_text(
            "⚠️ Payment service unavailable\\. Please try again shortly\\.",
            parse_mode="MarkdownV2",
        )

    safe_sessions = md_escape(str(session_count))
    safe_amount = md_escape(str(amount))

    await query.message.reply_text(
        f"🎟 *Mock Session Package Selected*\n\n"
        f"🎫 Sessions: *{safe_sessions}*\n"
        f"💰 Amount: *₦{safe_amount}*\n\n"
        "After successful payment, your mock sessions will be added automatically\\.\n\n"
        "Tap below to complete payment\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💳 Pay Securely", url=checkout_url)],
                [InlineKeyboardButton("⬅️ Back to Mode", callback_data=f"jp_back_mode_{context.user_data.get('jp_subject_code')}")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )


# =============================
# Question serving
# =============================
async def send_current_jamb_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_mode = context.user_data.get("jp_session_mode")
    session_id = context.user_data.get("jp_session_id")
    batch = context.user_data.get("jp_question_batch") or []
    current_index = int(context.user_data.get("jp_current_index", 0))

    # Reload mock paper from DB if needed
    if session_mode == "mock_utme" and not batch and session_id:
        paper_rows = await get_jamb_session_paper(int(session_id))
        batch = build_jamb_batch_from_paper_rows(paper_rows)
        context.user_data["jp_question_batch"] = batch
        context.user_data["jp_question_ids"] = [str(q.get("id")) for q in batch if q.get("id")]

    if not batch:
        return await update.effective_message.reply_text(
            "⚠️ No active JAMB question session found\\.",
            parse_mode="MarkdownV2",
        )

    session_row = None
    if session_mode == "mock_utme" and session_id:
        session_row = await get_jamb_session_by_id(int(session_id))
        if not session_row:
            return await update.effective_message.reply_text(
                "⚠️ Mock session could not be reloaded\\.",
                parse_mode="MarkdownV2",
            )

        if is_jamb_mock_time_expired(session_row.get("exam_ends_at")):
            await complete_jamb_session(int(session_id))

            safe_total = md_escape(str(len(batch)))
            safe_correct_count = md_escape(str(session_row.get("correct_count") or 0))
            safe_wrong_count = md_escape(str(session_row.get("wrong_count") or 0))

            return await update.effective_message.reply_text(
                f"⏰ *Mock time is up\\.*\n\n"
                f"📚 Total Questions: *{safe_total}*\n"
                f"✅ Correct: *{safe_correct_count}*\n"
                f"❌ Wrong: *{safe_wrong_count}*\n\n"
                "This subject mock has ended\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🎓 JAMB Practice", callback_data="jambpractice")],
                        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
                    ]
                ),
            )

    if current_index >= len(batch):
        if session_id:
            await complete_jamb_session(int(session_id))

        correct_count = int(context.user_data.get("jp_correct_count", 0))
        wrong_count = int(context.user_data.get("jp_wrong_count", 0))
        total = len(batch)

        safe_total = md_escape(str(total))
        safe_correct_count = md_escape(str(correct_count))
        safe_wrong_count = md_escape(str(wrong_count))

        title = "✅ *Mock Completed*" if session_mode == "mock_utme" else "✅ *Practice Completed*"
        outro = (
            "Great job\\. You can return to JAMB Practice for another subject\\."
            if session_mode == "mock_utme"
            else "Great job\\. You can return to JAMB Practice for another topic\\."
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
                    [InlineKeyboardButton("🎓 JAMB Practice", callback_data="jambpractice")],
                    [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
                ]
            ),
        )

    question = batch[current_index]
    question_type = question.get("question_type", "mcq")
    passage_id = question.get("passage_id")
    passage_title = question.get("passage_title", "Comprehension Passage")
    passage = question.get("passage", "")

    question_id = str(question["id"])
    user_id = update.effective_user.id
    subject_code = context.user_data.get("jp_subject_code")
    topic_id = str(question.get("topic_id") or context.user_data.get("jp_topic_id") or "__mock_subject__")
    served_question_ids = context.user_data.get("jp_served_question_ids", [])
    shown_passages = context.user_data.get("jp_shown_passages", [])
    last_passage = context.user_data.get("jp_last_passage")

    # Charge and record history when question is served, not when answered
    if question_id not in served_question_ids:
        if session_mode == "free_trial":
            deducted = await deduct_one_free_question(user_id)
            if not deducted:
                return await update.effective_message.reply_text(
                    "⚠️ You have no free question balance left\\.\n\nPlease buy a question pack to continue\\.",
                    parse_mode="MarkdownV2",
                )

            await add_question_to_topic_history(
                user_id=user_id,
                subject_code=subject_code,
                topic_id=topic_id,
                question_id=question_id,
            )

            if session_id:
                await increment_jamb_session_served(int(session_id))

        elif session_mode == "paid_session":
            deducted = await deduct_one_paid_question(user_id)
            if not deducted:
                return await update.effective_message.reply_text(
                    "⚠️ You have no paid JAMB question credits left\\.\n\nPlease buy another question pack to continue\\.",
                    parse_mode="MarkdownV2",
                )

            await add_question_to_topic_history(
                user_id=user_id,
                subject_code=subject_code,
                topic_id=topic_id,
                question_id=question_id,
            )

            if session_id:
                await increment_jamb_session_served(int(session_id))

        elif session_mode == "mock_utme":
            if session_id:
                await increment_jamb_session_served(int(session_id))

        served_question_ids.append(question_id)
        context.user_data["jp_served_question_ids"] = served_question_ids

    context.user_data["jp_current_question"] = question
    context.user_data["jp_answered_current"] = False

    if session_mode == "mock_utme" and session_id:
        await set_jamb_session_current_question_index(int(session_id), current_index)

    # Show comprehension passage before the question
    if question_type == "comprehension_mcq" and passage:
        should_show_passage = False

        if passage_id:
            if passage_id not in shown_passages:
                should_show_passage = True
                shown_passages.append(passage_id)
                context.user_data["jp_shown_passages"] = shown_passages
        else:
            if passage != last_passage:
                should_show_passage = True
                context.user_data["jp_last_passage"] = passage

        if should_show_passage:
            safe_passage_title = md_escape(str(passage_title))
            safe_passage = md_escape(str(passage))

            await update.effective_message.reply_text(
                f"📖 *{safe_passage_title}*\n\n{safe_passage}",
                parse_mode="MarkdownV2",
            )

    options = question.get("options", {})

    safe_question_text = md_escape(str(question.get("question") or "Question unavailable."))
    safe_question_no = md_escape(str(current_index + 1))
    safe_total = md_escape(str(len(batch)))

    header_lines = []
    if session_mode == "mock_utme":
        remaining = format_jamb_mock_time_remaining((session_row or {}).get("exam_ends_at"))
        safe_remaining = md_escape(str(remaining))
        header_lines.append("📝 *Mock UTME \\(By Subject\\)*")
        header_lines.append(f"⏱ Time Remaining: *{safe_remaining}*")
    else:
        header_lines.append("📘 *JAMB Practice*")

    header_lines.append(f"Question {safe_question_no} of {safe_total}")

    option_lines = []
    for key in ["A", "B", "C", "D", "E"]:
        if key in options:
            safe_option_text = md_escape(str(options[key]))
            option_lines.append(f"{key}\\. {safe_option_text}")

    text_msg = (
        "\n".join(header_lines)
        + "\n\n"
        + f"{safe_question_text}\n\n"
        + "\n".join(option_lines)
    )

    rows = []
    answer_row = []

    for key in ["A", "B", "C", "D", "E"]:
        if key in options:
            answer_row.append(
                InlineKeyboardButton(key, callback_data=f"jp_ans::{key}")
            )
            if len(answer_row) == 2:
                rows.append(answer_row)
                answer_row = []

    if answer_row:
        rows.append(answer_row)

    rows.append([InlineKeyboardButton("🏠 End Practice", callback_data="menu:main")])

    await update.effective_message.reply_text(
        text_msg,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# ----------------------------------------
# JAMB Serve First Handler
# ----------------------------------------  
async def jamb_serve_first_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    await send_current_jamb_question(update, context)


# =============================
# Answer handling
# =============================
async def jamb_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    if context.user_data.get("jp_answered_current", False):
        return await query.answer("You already answered this question\\.", show_alert=False)

    try:
        _, selected_option = query.data.split("::", 1)
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid answer selection\\.",
            parse_mode="MarkdownV2",
        )

    question = context.user_data.get("jp_current_question")
    if not question:
        return await query.message.reply_text(
            "⚠️ No active question found\\.",
            parse_mode="MarkdownV2",
        )

    user_id = update.effective_user.id
    session_id_raw = context.user_data.get("jp_session_id")
    if not session_id_raw:
        return await query.message.reply_text(
            "⚠️ Session expired\\. Please start again from JAMB Practice\\.",
            parse_mode="MarkdownV2",
        )

    session_id = int(session_id_raw)
    session_mode = context.user_data.get("jp_session_mode")
    subject_code = context.user_data.get("jp_subject_code")
    topic_id = str(question.get("topic_id") or context.user_data.get("jp_topic_id") or "__mock_subject__")
    question_id = str(question["id"])
    correct_option = str(question["answer"]).strip().upper()
    selected_option = str(selected_option).strip().upper()
    is_correct = selected_option == correct_option
    question_order = int(context.user_data.get("jp_current_index", 0)) + 1

    await record_jamb_attempt(
        session_id=session_id,
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
        question_id=question_id,
        selected_option=selected_option,
        correct_option=correct_option,
        is_correct=is_correct,
    )

    await increment_jamb_session_result(session_id, is_correct)

    if session_mode == "mock_utme":
        async with get_async_session() as session:
            async with session.begin():
                await session.execute(
                    text("""
                        update jamb_session_questions
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

    context.user_data["jp_answered_current"] = True
    context.user_data["jp_last_selected_option"] = selected_option
    context.user_data["jp_last_correct_option"] = correct_option

    if is_correct:
        context.user_data["jp_correct_count"] = int(context.user_data.get("jp_correct_count", 0)) + 1
        result_text = "✅ *Correct\\!*"
    else:
        context.user_data["jp_wrong_count"] = int(context.user_data.get("jp_wrong_count", 0)) + 1

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
# JAMB Answer Details Handler
# --------------------------------------------
async def jamb_answer_details_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    question = context.user_data.get("jp_current_question")
    if not question:
        return await query.message.reply_text(
            "⚠️ No answered question found\\.",
            parse_mode="MarkdownV2",
        )

    explanation = question.get("explanation", {})

    question_restate = explanation.get("question_restate", "")
    principle = explanation.get("principle", "")
    steps = explanation.get("steps", [])
    if not isinstance(steps, list):
        steps = []

    final_answer = explanation.get("final_answer", "")
    simple_explanation = explanation.get("simple_explanation", "")

    lines = ["📖 *Answer Details*\n"]

    if question_restate:
        lines.append(f"*Question Restated*\n{md_escape(str(question_restate))}\n")

    if principle:
        lines.append(f"*Principle*\n{md_escape(str(principle))}\n")

    if steps:
        lines.append("*Step\\-by\\-step Solution*")
        for i, step in enumerate(steps, start=1):
            lines.append(f"{i}\\. {md_escape(str(step))}")
        lines.append("")

    if final_answer:
        lines.append(f"*Final Answer*\n{md_escape(str(final_answer))}\n")

    if simple_explanation:
        lines.append(f"*Simple Explanation*\n{md_escape(str(simple_explanation))}")

    await query.message.reply_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=make_after_details_keyboard(),
    )

# ------------------------------
# JAMB Next Handler
# -----------------------------
async def jamb_next_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    current_index = int(context.user_data.get("jp_current_index", 0))
    context.user_data["jp_current_index"] = current_index + 1

    await send_current_jamb_question(update, context)


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

    safe_subject_name = md_escape(str(subject["name"]))

    await query.message.reply_text(
        f"📘 *You selected:* {safe_subject_name}\n\n"
        "How would you like to practice\\?",
        parse_mode="MarkdownV2",
        reply_markup=make_mode_keyboard(subject_code),
    )

# --------------------------------------
# Jamb Use Paid Handler
# -------------------------------------
async def jamb_use_paid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user_id = update.effective_user.id
    paid_credits = await get_paid_question_credits(user_id)

    if paid_credits <= 0:
        return await query.message.reply_text(
            "⚠️ You do not have any paid JAMB question credits yet\\.\n\nPlease buy a question pack first\\.",
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
# JAMB Paid Count Handler
# ----------------------------------
async def jamb_paid_count_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        requested_count = int(query.data.replace("jp_paidcount_", "", 1))
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid paid session size\\.",
            parse_mode="MarkdownV2",
        )

    user_id = update.effective_user.id
    subject_code = context.user_data.get("jp_subject_code")
    topic_id = context.user_data.get("jp_topic_id")

    if not subject_code or not topic_id:
        return await query.message.reply_text(
            "⚠️ Topic session data missing\\. Please choose your subject and topic again\\.",
            parse_mode="MarkdownV2",
        )

    paid_credits = await get_paid_question_credits(user_id)
    if paid_credits <= 0:
        return await query.message.reply_text(
            "⚠️ You do not have enough paid JAMB credits\\.\n\nPlease buy a question pack first\\.",
            parse_mode="MarkdownV2",
        )

    actual_count = min(requested_count, paid_credits)

    seen_question_ids = await get_seen_question_ids_for_topic(
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
    )

    batch = prepare_topic_question_batch(
        subject_code=subject_code,
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

    session_id = await create_jamb_session(
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
        question_target=len(selected_questions),
        mode="topic_practice",
    )

    context.user_data["jp_session_id"] = session_id
    context.user_data["jp_session_mode"] = "paid_session"
    context.user_data["jp_question_batch"] = selected_questions
    context.user_data["jp_question_ids"] = selected_question_ids
    context.user_data["jp_current_index"] = 0
    context.user_data["jp_session_target"] = len(selected_questions)
    context.user_data["jp_correct_count"] = 0
    context.user_data["jp_wrong_count"] = 0
    context.user_data["jp_current_question"] = None
    context.user_data["jp_answered_current"] = False
    context.user_data["jp_served_question_ids"] = []
    context.user_data["jp_shown_passages"] = []
    context.user_data["jp_last_passage"] = None

    topic = next((t for t in get_subject_topics(subject_code) if t["id"] == topic_id), None)
    topic_title = topic["title"] if topic else topic_id

    subject = get_subject_by_code(subject_code)
    subject_name = subject["name"] if subject else subject_code

    safe_subject_name = md_escape(str(subject_name))
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
        f"📘 Subject: *{safe_subject_name}*\n"
        f"🧪 Topic: *{safe_topic_title}*\n"
        f"📚 Questions in this session: *{safe_question_count}*"
        f"{adjusted_note}"
        f"{reset_note}\n\n"
        "Tap below to start Question 1\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶ Start Questions", callback_data="jp_serve_first")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )


async def jamb_mock_start_paid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    tg = update.effective_user
    user_id = tg.id

    subject_code = context.user_data.get("jp_subject_code")
    if not subject_code:
        return await query.message.reply_text(
            "⚠️ Subject session data missing\\. Please choose your subject again\\.",
            parse_mode="MarkdownV2",
        )

    subject = get_subject_by_code(subject_code)
    if not subject:
        return await query.message.reply_text(
            "⚠️ Subject not found\\.",
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

    question_target = get_jamb_mock_question_count(subject_code)

    session_id = await create_jamb_mock_session(
        user_id=user_id,
        subject_code=subject_code,
        question_target=question_target,
    )

    paper_info = await create_jamb_subject_mock_paper_if_needed(
        user_id=user_id,
        session_id=session_id,
        subject_code=subject_code,
    )

    paper_rows = paper_info.get("paper_rows") or []
    batch = build_jamb_batch_from_paper_rows(paper_rows)

    if not batch:
        return await query.message.reply_text(
            "⚠️ No active questions could be prepared for this mock paper yet\\.",
            parse_mode="MarkdownV2",
        )

    session_row = await get_jamb_session_by_id(session_id)
    exam_ends_at = (session_row or {}).get("exam_ends_at")

    context.user_data["jp_session_id"] = session_id
    context.user_data["jp_session_mode"] = "mock_utme"
    context.user_data["jp_mode"] = "mock_utme"
    context.user_data["jp_topic_id"] = None
    context.user_data["jp_question_batch"] = batch
    context.user_data["jp_question_ids"] = [str(q.get("id")) for q in batch if q.get("id")]
    context.user_data["jp_current_index"] = 0
    context.user_data["jp_session_target"] = len(batch)
    context.user_data["jp_correct_count"] = 0
    context.user_data["jp_wrong_count"] = 0
    context.user_data["jp_current_question"] = None
    context.user_data["jp_answered_current"] = False
    context.user_data["jp_served_question_ids"] = []
    context.user_data["jp_shown_passages"] = []
    context.user_data["jp_last_passage"] = None

    safe_subject_name = md_escape(str(subject["name"]))
    safe_question_target = md_escape(str(question_target))
    safe_duration = md_escape(format_jamb_mock_time_remaining(exam_ends_at))

    reset_note = (
        "\n♻️ Subject cycle reset because you already exhausted the unseen pool before\\."
        if paper_info.get("cycle_reset")
        else ""
    )

    await query.message.reply_text(
        f"📝 *Subject Mock Started*\n\n"
        f"📘 Subject: *{safe_subject_name}*\n"
        f"📚 Questions: *{safe_question_target}*\n"
        f"⏱ Time Allowed: *{safe_duration}*"
        f"{reset_note}\n\n"
        "Tap below to start Question 1\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶ Start Questions", callback_data="jp_serve_first")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )


async def jamb_mock_resume_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    tg = update.effective_user
    user_id = tg.id
    subject_code = context.user_data.get("jp_subject_code")

    if not subject_code:
        return await query.message.reply_text(
            "⚠️ Subject session data missing\\. Please choose your subject again\\.",
            parse_mode="MarkdownV2",
        )

    active_session = await get_latest_active_jamb_mock_session_for_user(
        user_id=user_id,
        subject_code=subject_code,
    )

    if not active_session:
        return await query.message.reply_text(
            "⚠️ No active subject mock was found for this subject\\.",
            parse_mode="MarkdownV2",
        )

    session_id = int(active_session["id"])

    if is_jamb_mock_time_expired(active_session.get("exam_ends_at")):
        await complete_jamb_session(session_id)

        safe_correct = md_escape(str(active_session.get("correct_count") or 0))
        safe_wrong = md_escape(str(active_session.get("wrong_count") or 0))
        safe_total = md_escape(str(active_session.get("question_target") or 0))

        return await query.message.reply_text(
            f"⏰ *Mock time is up\\.*\n\n"
            f"📚 Total Questions: *{safe_total}*\n"
            f"✅ Correct: *{safe_correct}*\n"
            f"❌ Wrong: *{safe_wrong}*\n\n"
            "This subject mock has ended\\.",
            parse_mode="MarkdownV2",
        )

    paper_rows = await get_jamb_session_paper(session_id)
    batch = build_jamb_batch_from_paper_rows(paper_rows)

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

    context.user_data["jp_session_id"] = session_id
    context.user_data["jp_session_mode"] = "mock_utme"
    context.user_data["jp_mode"] = "mock_utme"
    context.user_data["jp_question_batch"] = batch
    context.user_data["jp_question_ids"] = [str(q.get("id")) for q in batch if q.get("id")]
    context.user_data["jp_current_index"] = current_index
    context.user_data["jp_session_target"] = len(batch)
    context.user_data["jp_correct_count"] = int(active_session.get("correct_count") or 0)
    context.user_data["jp_wrong_count"] = int(active_session.get("wrong_count") or 0)
    context.user_data["jp_current_question"] = None
    context.user_data["jp_answered_current"] = False
    context.user_data["jp_served_question_ids"] = already_served_ids
    context.user_data["jp_shown_passages"] = []
    context.user_data["jp_last_passage"] = None

    await send_current_jamb_question(update, context)


# =============================
# Register handlers
# =============================
def register_handlers(application):
    application.add_handler(CommandHandler("jambpractice", jambpractice_handler))
    application.add_handler(CallbackQueryHandler(jambpractice_handler, pattern=r"^jambpractice$"))
    application.add_handler(CallbackQueryHandler(jamb_subject_handler, pattern=r"^jp_subj_"))
    application.add_handler(CallbackQueryHandler(jamb_mode_handler, pattern=r"^jp_mode_"))

    application.add_handler(CallbackQueryHandler(jamb_mock_start_paid_handler, pattern=r"^jp_mock_start_paid$"))
    application.add_handler(CallbackQueryHandler(jamb_mock_resume_handler, pattern=r"^jp_mock_resume$"))

    application.add_handler(CallbackQueryHandler(jamb_topic_page_handler, pattern=r"^jp_topicpage_"))
    application.add_handler(CallbackQueryHandler(jamb_topic_handler, pattern=r"^jp_topic::"))
    application.add_handler(CallbackQueryHandler(jamb_back_mode_handler, pattern=r"^jp_back_mode_"))
    application.add_handler(CallbackQueryHandler(jamb_start_free_handler, pattern=r"^jp_start_free$"))
    application.add_handler(CallbackQueryHandler(jamb_use_paid_handler, pattern=r"^jp_use_paid$"))
    application.add_handler(CallbackQueryHandler(jamb_paid_count_handler, pattern=r"^jp_paidcount_"))
    application.add_handler(CallbackQueryHandler(jamb_buy_pack_handler, pattern=r"^jp_buy_"))
    application.add_handler(CallbackQueryHandler(jamb_mock_buy_session_handler, pattern=r"^jp_mock_buy_"))
    application.add_handler(CallbackQueryHandler(jamb_serve_first_handler, pattern=r"^jp_serve_first$"))
    application.add_handler(CallbackQueryHandler(jamb_answer_handler, pattern=r"^jp_ans::"))
    application.add_handler(CallbackQueryHandler(jamb_answer_details_handler, pattern=r"^jp_details$"))
    application.add_handler(CallbackQueryHandler(jamb_next_handler, pattern=r"^jp_next$"))


