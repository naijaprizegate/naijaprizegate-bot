# ======================================================
# services/mockjamb_exam_service.py
# ======================================================
import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jamb_loader import prepare_subject_question_batch
from services.mockjamb_session_service import (
    get_seen_mockjamb_question_ids,
    record_seen_mockjamb_questions,
    start_mockjamb_session_if_needed,
    set_mockjamb_current_subject,
    get_mockjamb_session_by_payment_reference,
    mark_mockjamb_subject_completed,
)

logger = logging.getLogger("mockjamb_exam_service")
logger.setLevel(logging.INFO)


def get_mockjamb_subject_question_count(subject_code: str) -> int:
    subject_code = str(subject_code or "").strip().lower()
    if subject_code == "eng":
        return 60
    return 40


def _extract_correct_option(question: dict[str, Any]) -> str | None:
    for key in ("correct_option", "correct_answer", "answer", "correctAnswer"):
        value = question.get(key)
        if value:
            return str(value).strip()
    return None


async def get_mockjamb_subject_paper(
    session: AsyncSession,
    *,
    payment_reference: str,
    subject_code: str,
) -> list[dict]:
    result = await session.execute(
        text("""
            select
                id,
                session_id,
                payment_reference,
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
    rows = result.mappings().all()
    return [dict(row) for row in rows]


async def get_mockjamb_subject_question_by_order(
    session: AsyncSession,
    *,
    payment_reference: str,
    subject_code: str,
    question_order: int,
) -> dict | None:
    result = await session.execute(
        text("""
            select
                id,
                session_id,
                payment_reference,
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
            from public.mockjamb_subject_questions
            where payment_reference = :payment_reference
              and subject_code = :subject_code
              and question_order = :question_order
            limit 1
        """),
        {
            "payment_reference": payment_reference,
            "subject_code": subject_code,
            "question_order": int(question_order),
        },
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def create_mockjamb_subject_paper_if_needed(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
    subject_code: str,
    requested_count: int | None = None,
) -> dict:
    existing_session = await get_mockjamb_session_by_payment_reference(session, payment_reference)
    if not existing_session:
        raise ValueError(f"Mock JAMB session not found for payment_reference={payment_reference}")

    existing_paper = await get_mockjamb_subject_paper(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
    )
    if existing_paper:
        return {
            "created_now": False,
            "cycle_reset": False,
            "selected_count": len(existing_paper),
            "paper_rows": existing_paper,
            "selected_question_ids": [row["question_id"] for row in existing_paper],
        }

    seen_question_ids = await get_seen_mockjamb_question_ids(
        session,
        user_id=int(user_id),
        subject_code=subject_code,
    )

    if requested_count is None:
        requested_count = get_mockjamb_subject_question_count(subject_code)

    batch = prepare_subject_question_batch(
        subject_code=subject_code,
        requested_count=requested_count,
        seen_question_ids=seen_question_ids,
    )

    selected_questions = batch["selected_questions"]
    selected_question_ids = batch["selected_question_ids"]
    session_id = int(existing_session["id"])

    for idx, question in enumerate(selected_questions, start=1):
        await session.execute(
            text("""
                insert into public.mockjamb_subject_questions (
                    session_id,
                    payment_reference,
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
                    :payment_reference,
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
                on conflict (payment_reference, subject_code, question_id) do nothing
            """),
            {
                "session_id": session_id,
                "payment_reference": payment_reference,
                "user_id": int(user_id),
                "subject_code": subject_code,
                "question_id": str(question.get("id")),
                "question_order": idx,
                "question_json": json.dumps(question),
                "correct_option": _extract_correct_option(question),
            },
        )

    await record_seen_mockjamb_questions(
        session,
        user_id=int(user_id),
        subject_code=subject_code,
        question_ids=selected_question_ids,
    )

    await session.flush()

    paper_rows = await get_mockjamb_subject_paper(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
    )

    return {
        "created_now": True,
        "cycle_reset": batch["cycle_reset"],
        "selected_count": batch["selected_count"],
        "paper_rows": paper_rows,
        "selected_question_ids": selected_question_ids,
    }


async def start_mockjamb_subject(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
    subject_code: str,
) -> dict:
    session_row = await start_mockjamb_session_if_needed(
        session,
        payment_reference=payment_reference,
    )
    if not session_row:
        raise ValueError(f"Mock JAMB session not found for payment_reference={payment_reference}")

    session_row = await set_mockjamb_current_subject(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
    )

    requested_count = get_mockjamb_subject_question_count(subject_code)

    paper_info = await create_mockjamb_subject_paper_if_needed(
        session,
        payment_reference=payment_reference,
        user_id=int(user_id),
        subject_code=subject_code,
        requested_count=requested_count,
    )

    current_question = await get_mockjamb_subject_question_by_order(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
        question_order=1,
    )

    return {
        "session": session_row,
        "paper_info": paper_info,
        "current_question": current_question,
    }


async def answer_mockjamb_question(
    session: AsyncSession,
    *,
    payment_reference: str,
    subject_code: str,
    question_order: int,
    selected_option: str,
) -> dict:
    current_question = await get_mockjamb_subject_question_by_order(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
        question_order=question_order,
    )

    if not current_question:
        return {
            "status": "error",
            "reason": "question_not_found",
        }

    selected_option = str(selected_option).strip().upper()
    correct_option = str(current_question.get("correct_option") or "").strip().upper()
    is_correct = selected_option == correct_option if correct_option else False

    await session.execute(
        text("""
            update public.mockjamb_subject_questions
            set
                selected_option = :selected_option,
                is_correct = :is_correct,
                updated_at = now()
            where payment_reference = :payment_reference
              and subject_code = :subject_code
              and question_order = :question_order
        """),
        {
            "payment_reference": payment_reference,
            "subject_code": subject_code,
            "question_order": int(question_order),
            "selected_option": selected_option,
            "is_correct": bool(is_correct),
        },
    )

    paper_rows = await get_mockjamb_subject_paper(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
    )
    total_questions = len(paper_rows)

    next_question_order = int(question_order) + 1

    if next_question_order > total_questions:
        return {
            "status": "completed_subject",
            "selected_option": selected_option,
            "is_correct": is_correct,
            "total_questions": total_questions,
        }

    next_question = await get_mockjamb_subject_question_by_order(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
        question_order=next_question_order,
    )

    await session.execute(
        text("""
            update public.mockjamb_sessions
            set
                current_question_index = :current_question_index,
                updated_at = now()
            where payment_reference = :payment_reference
        """),
        {
            "payment_reference": payment_reference,
            "current_question_index": int(next_question_order - 1),
        },
    )

    return {
        "status": "next_question",
        "selected_option": selected_option,
        "is_correct": is_correct,
        "next_question": next_question,
        "next_question_order": next_question_order,
        "total_questions": total_questions,
    }


async def calculate_mockjamb_subject_score(
    session: AsyncSession,
    *,
    payment_reference: str,
    subject_code: str,
) -> dict:
    result = await session.execute(
        text("""
            select
                count(*) as total_questions,
                coalesce(sum(case when is_correct = true then 1 else 0 end), 0) as correct_count
            from public.mockjamb_subject_questions
            where payment_reference = :payment_reference
              and subject_code = :subject_code
        """),
        {
            "payment_reference": payment_reference,
            "subject_code": subject_code,
        },
    )
    row = result.mappings().first() or {}

    total_questions = int(row.get("total_questions") or 0)
    correct_count = int(row.get("correct_count") or 0)

    expected_total = get_mockjamb_subject_question_count(subject_code)

    if expected_total <= 0:
        score_100 = 0
    else:
        score_100 = round((correct_count / expected_total) * 100)

    return {
        "total_questions": total_questions,
        "correct_count": correct_count,
        "score_100": int(score_100),
    }


async def get_mockjamb_review_rows(
    session: AsyncSession,
    *,
    payment_reference: str,
    wrong_only: bool = False,
) -> list[dict]:
    if wrong_only:
        result = await session.execute(
            text("""
                select
                    id,
                    session_id,
                    payment_reference,
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
                from public.mockjamb_subject_questions
                where payment_reference = :payment_reference
                  and selected_option is not null
                  and coalesce(is_correct, false) = false
                order by subject_code asc, question_order asc
            """),
            {"payment_reference": payment_reference},
        )
    else:
        result = await session.execute(
            text("""
                select
                    id,
                    session_id,
                    payment_reference,
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
                from public.mockjamb_subject_questions
                where payment_reference = :payment_reference
                  and selected_option is not null
                order by subject_code asc, question_order asc
            """),
            {"payment_reference": payment_reference},
        )

    rows = result.mappings().all()
    return [dict(row) for row in rows]
