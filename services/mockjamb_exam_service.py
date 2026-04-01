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
)

logger = logging.getLogger("mockjamb_exam_service")
logger.setLevel(logging.INFO)

MOCKJAMB_SUBJECT_QUESTION_COUNT = 50


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
    requested_count: int = MOCKJAMB_SUBJECT_QUESTION_COUNT,
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

    paper_info = await create_mockjamb_subject_paper_if_needed(
        session,
        payment_reference=payment_reference,
        user_id=int(user_id),
        subject_code=subject_code,
        requested_count=MOCKJAMB_SUBJECT_QUESTION_COUNT,
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
