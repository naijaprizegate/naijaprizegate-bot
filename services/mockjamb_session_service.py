# ======================================================
# services/mockjamb_session_service.py
# ======================================================
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("mockjamb_session_service")
logger.setLevel(logging.INFO)

MOCKJAMB_EXAM_DURATION_MINUTES = 120


async def get_mockjamb_session_by_payment_reference(
    session: AsyncSession,
    payment_reference: str,
) -> dict | None:
    result = await session.execute(
        text("""
            select
                id,
                payment_reference,
                user_id,
                course_code,
                subject_codes_json,
                completed_subjects_json,
                scores_json,
                current_subject_code,
                current_question_index,
                exam_started_at,
                exam_ends_at,
                status,
                created_at,
                updated_at
            from public.mockjamb_sessions
            where payment_reference = :payment_reference
            limit 1
        """),
        {"payment_reference": payment_reference},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def create_mockjamb_session(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
    course_code: str,
    subject_codes_json: str,
) -> dict:
    existing = await get_mockjamb_session_by_payment_reference(session, payment_reference)
    if existing:
        return existing

    await session.execute(
        text("""
            insert into public.mockjamb_sessions (
                payment_reference,
                user_id,
                course_code,
                subject_codes_json,
                completed_subjects_json,
                scores_json,
                current_subject_code,
                current_question_index,
                exam_started_at,
                exam_ends_at,
                status,
                created_at,
                updated_at
            )
            values (
                :payment_reference,
                :user_id,
                :course_code,
                :subject_codes_json,
                '[]',
                '{}',
                null,
                0,
                null,
                null,
                'ready',
                now(),
                now()
            )
        """),
        {
            "payment_reference": payment_reference,
            "user_id": int(user_id),
            "course_code": course_code,
            "subject_codes_json": subject_codes_json,
        },
    )
    await session.flush()
    return await get_mockjamb_session_by_payment_reference(session, payment_reference)


async def get_or_create_mockjamb_session_from_payment(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
    course_code: str,
    subject_codes_json: str,
) -> dict:
    existing = await get_mockjamb_session_by_payment_reference(session, payment_reference)
    if existing:
        return existing

    return await create_mockjamb_session(
        session,
        payment_reference=payment_reference,
        user_id=user_id,
        course_code=course_code,
        subject_codes_json=subject_codes_json,
    )


async def start_mockjamb_session_if_needed(
    session: AsyncSession,
    *,
    payment_reference: str,
) -> dict | None:
    existing = await get_mockjamb_session_by_payment_reference(session, payment_reference)
    if not existing:
        return None

    if existing.get("exam_started_at") and existing.get("exam_ends_at"):
        return existing

    now = datetime.now(timezone.utc)
    ends_at = now + timedelta(minutes=MOCKJAMB_EXAM_DURATION_MINUTES)

    await session.execute(
        text("""
            update public.mockjamb_sessions
            set
                exam_started_at = :exam_started_at,
                exam_ends_at = :exam_ends_at,
                status = 'in_progress',
                updated_at = now()
            where payment_reference = :payment_reference
        """),
        {
            "payment_reference": payment_reference,
            "exam_started_at": now,
            "exam_ends_at": ends_at,
        },
    )
    await session.flush()
    return await get_mockjamb_session_by_payment_reference(session, payment_reference)


async def set_mockjamb_current_subject(
    session: AsyncSession,
    *,
    payment_reference: str,
    subject_code: str,
) -> dict | None:
    await session.execute(
        text("""
            update public.mockjamb_sessions
            set
                current_subject_code = :subject_code,
                current_question_index = 0,
                updated_at = now()
            where payment_reference = :payment_reference
        """),
        {
            "payment_reference": payment_reference,
            "subject_code": subject_code,
        },
    )
    await session.flush()
    return await get_mockjamb_session_by_payment_reference(session, payment_reference)


async def mark_mockjamb_subject_completed(
    session: AsyncSession,
    *,
    payment_reference: str,
    subject_code: str,
    score: int,
) -> dict | None:
    existing = await get_mockjamb_session_by_payment_reference(session, payment_reference)
    if not existing:
        return None

    completed_subjects = []
    scores = {}

    try:
        completed_subjects = json.loads(existing.get("completed_subjects_json") or "[]")
    except Exception:
        completed_subjects = []

    try:
        scores = json.loads(existing.get("scores_json") or "{}")
    except Exception:
        scores = {}

    if subject_code not in completed_subjects:
        completed_subjects.append(subject_code)

    scores[subject_code] = int(score)

    new_status = "completed" if len(completed_subjects) >= 4 else "in_progress"

    await session.execute(
        text("""
            update public.mockjamb_sessions
            set
                completed_subjects_json = :completed_subjects_json,
                scores_json = :scores_json,
                current_subject_code = null,
                current_question_index = 0,
                status = :status,
                updated_at = now()
            where payment_reference = :payment_reference
        """),
        {
            "payment_reference": payment_reference,
            "completed_subjects_json": json.dumps(completed_subjects),
            "scores_json": json.dumps(scores),
            "status": new_status,
        },
    )
    await session.flush()
    return await get_mockjamb_session_by_payment_reference(session, payment_reference)


async def get_seen_mockjamb_question_ids(
    session: AsyncSession,
    *,
    user_id: int,
    subject_code: str,
) -> list[str]:
    result = await session.execute(
        text("""
            select question_id
            from public.mockjamb_seen_questions
            where user_id = :user_id
              and subject_code = :subject_code
              and source_type = 'mockjamb'
            order by id asc
        """),
        {
            "user_id": int(user_id),
            "subject_code": subject_code,
        },
    )
    rows = result.fetchall()
    return [str(row[0]) for row in rows]


async def record_seen_mockjamb_questions(
    session: AsyncSession,
    *,
    user_id: int,
    subject_code: str,
    question_ids: list[str],
) -> None:
    for question_id in question_ids:
        await session.execute(
            text("""
                insert into public.mockjamb_seen_questions (
                    user_id,
                    subject_code,
                    question_id,
                    source_type,
                    created_at
                )
                values (
                    :user_id,
                    :subject_code,
                    :question_id,
                    'mockjamb',
                    now()
                )
                on conflict (user_id, subject_code, question_id, source_type) do nothing
            """),
            {
                "user_id": int(user_id),
                "subject_code": subject_code,
                "question_id": str(question_id),
            },
        )
