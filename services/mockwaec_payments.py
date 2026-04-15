# ======================================================
# services/mockwaec_payments.py
# ======================================================
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("mockwaec_payments")
logger.setLevel(logging.INFO)


async def get_mockwaec_payment(session: AsyncSession, payment_reference: str) -> dict | None:
    result = await session.execute(
        text("""
            select
                payment_reference,
                user_id,
                amount_paid,
                payment_status,
                course_code,
                subject_codes_json,
                exam_mode,
                created_at,
                updated_at
            from public.mockwaec_payments
            where payment_reference = :payment_reference
            limit 1
        """),
        {"payment_reference": payment_reference},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def create_pending_mockwaec_payment(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
    amount_paid: int,
    course_code: str,
    subject_codes_json: str,
    exam_mode: str = "solo",
) -> dict:
    existing = await get_mockwaec_payment(session, payment_reference)
    if existing:
        return existing

    if int(amount_paid) <= 0:
        raise ValueError(f"Invalid Mock JAMB amount: {amount_paid}")

    await session.execute(
        text("""
            insert into public.mockwaec_payments (
                payment_reference,
                user_id,
                amount_paid,
                payment_status,
                course_code,
                subject_codes_json,
                exam_mode,
                created_at,
                updated_at
            )
            values (
                :payment_reference,
                :user_id,
                :amount_paid,
                'pending',
                :course_code,
                :subject_codes_json,
                :exam_mode,
                now(),
                now()
            )
        """),
        {
            "payment_reference": payment_reference,
            "user_id": int(user_id),
            "amount_paid": int(amount_paid),
            "course_code": course_code,
            "subject_codes_json": subject_codes_json,
            "exam_mode": exam_mode,
        },
    )
    await session.flush()
    return await get_mockwaec_payment(session, payment_reference)


async def finalize_mockwaec_payment(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
) -> tuple[bool, dict | None]:
    """
    Safe/idempotent Mock JAMB finalizer.
    Only marks payment successful for now.
    Returns (did_finalize_now, payment_row)
    """
    payment = await get_mockwaec_payment(session, payment_reference)
    if not payment:
        logger.error(
            "❌ Mock JAMB payment not found during finalize | payment_reference=%s | user_id=%s",
            payment_reference,
            user_id,
        )
        return False, None

    claimed = await session.execute(
        text("""
            update public.mockwaec_payments
            set
                payment_status = 'successful',
                updated_at = now()
            where payment_reference = :payment_reference
              and user_id = :user_id
              and lower(coalesce(payment_status, '')) <> 'successful'
            returning payment_reference
        """),
        {
            "payment_reference": payment_reference,
            "user_id": int(user_id),
        },
    )

    claimed_row = claimed.first()
    latest = await get_mockwaec_payment(session, payment_reference)

    if not claimed_row:
        return False, latest

    return True, latest

