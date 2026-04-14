# ======================================================
# services/jamb_payments.py
# =====================================================
import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.flutterwave_client import calculate_jamb_credits

logger = logging.getLogger("jamb_payments")
logger.setLevel(logging.INFO)


async def get_jamb_payment(session: AsyncSession, payment_reference: str) -> dict | None:
    result = await session.execute(
        text("""
            select
                payment_reference,
                user_id,
                amount_paid,
                question_credits_added,
                mock_sessions_added,
                subject_code,
                topic_id,
                payment_status,
                created_at,
                updated_at
            from jamb_payments
            where payment_reference = :payment_reference
            limit 1
        """),
        {"payment_reference": payment_reference},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def create_pending_jamb_payment(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
    amount_paid: int,
    question_credits_added: int = 0,
    mock_sessions_added: int = 0,
    subject_code: str | None = None,
    topic_id: str | None = None,
):
    existing = await get_jamb_payment(session, payment_reference)
    if existing:
        return existing

    credits = int(question_credits_added or 0)
    mock_sessions = int(mock_sessions_added or 0)

    if credits <= 0 and mock_sessions <= 0:
        credits = int(calculate_jamb_credits(int(amount_paid)))

    if credits <= 0 and mock_sessions <= 0:
        raise ValueError(f"Invalid JAMB payment package for amount {amount_paid}")

    await session.execute(
        text("""
            insert into jamb_payments (
                payment_reference,
                user_id,
                amount_paid,
                question_credits_added,
                mock_sessions_added,
                payment_status,
                subject_code,
                topic_id,
                created_at,
                updated_at
            )
            values (
                :payment_reference,
                :user_id,
                :amount_paid,
                :question_credits_added,
                :mock_sessions_added,
                'pending',
                :subject_code,
                :topic_id,
                now(),
                now()
            )
        """),
        {
            "payment_reference": payment_reference,
            "user_id": int(user_id),
            "amount_paid": int(amount_paid),
            "question_credits_added": int(credits),
            "mock_sessions_added": int(mock_sessions),
            "subject_code": subject_code,
            "topic_id": topic_id,
        },
    )
    await session.flush()
    return await get_jamb_payment(session, payment_reference)


async def finalize_jamb_payment(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
    amount_paid: int,
    question_credits_added: int | None = None,
    mock_sessions_added: int | None = None,
) -> tuple[bool, dict | None, int, int]:
    """
    Safe/idempotent JAMB finalizer.
    Can credit either:
    - question credits, or
    - mock sessions

    Returns:
    (did_credit_now, payment_row, credits_added, mock_sessions_added)
    """
    payment = await get_jamb_payment(session, payment_reference)

    if not payment:
        payment = await create_pending_jamb_payment(
            session,
            payment_reference=payment_reference,
            user_id=int(user_id),
            amount_paid=int(amount_paid),
            question_credits_added=question_credits_added,
            mock_sessions_added=mock_sessions_added,
        )

    credits = int(payment.get("question_credits_added") or question_credits_added or 0)
    mock_sessions = int(payment.get("mock_sessions_added") or mock_sessions_added or 0)

    if credits <= 0 and mock_sessions <= 0:
        credits = calculate_jamb_credits(int(amount_paid))

    if credits <= 0 and mock_sessions <= 0:
        logger.error(
            "❌ Invalid JAMB package | payment_reference=%s | amount=%s",
            payment_reference,
            amount_paid,
        )
        return False, payment, 0, 0

    claimed = await session.execute(
        text("""
            update jamb_payments
            set
                payment_status = 'successful',
                updated_at = now()
            where payment_reference = :payment_reference
              and lower(coalesce(payment_status, '')) <> 'successful'
            returning payment_reference
        """),
        {"payment_reference": payment_reference},
    )

    claimed_row = claimed.first()
    if not claimed_row:
        latest = await get_jamb_payment(session, payment_reference)
        return (
            False,
            latest,
            int((latest or {}).get("question_credits_added") or 0),
            int((latest or {}).get("mock_sessions_added") or 0),
        )

    await session.execute(
        text("""
            insert into jamb_user_access (user_id)
            values (:user_id)
            on conflict (user_id) do nothing
        """),
        {"user_id": int(user_id)},
    )

    if mock_sessions > 0:
        await session.execute(
            text("""
                update jamb_user_access
                set
                    mock_sessions_available = mock_sessions_available + :mock_sessions,
                    updated_at = now()
                where user_id = :user_id
            """),
            {
                "user_id": int(user_id),
                "mock_sessions": int(mock_sessions),
            },
        )

    elif credits > 0:
        await session.execute(
            text("""
                update jamb_user_access
                set
                    paid_question_credits = paid_question_credits + :credits,
                    updated_at = now()
                where user_id = :user_id
            """),
            {
                "user_id": int(user_id),
                "credits": int(credits),
            },
        )

    latest = await get_jamb_payment(session, payment_reference)
    return True, latest, int(credits), int(mock_sessions)

