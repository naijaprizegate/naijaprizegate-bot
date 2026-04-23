# ====================================================
# services/mockjamb_payments.py
# ====================================================
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("mockjamb_payments")
logger.setLevel(logging.INFO)


async def get_mockjamb_payment(session: AsyncSession, payment_reference: str) -> dict | None:
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
                invitee_count,
                required_player_count,
                room_code,
                created_at,
                updated_at
            from public.mockjamb_payments
            where payment_reference = :payment_reference
            limit 1
        """),
        {"payment_reference": payment_reference},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def create_pending_mockjamb_payment(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
    amount_paid: int,
    course_code: str,
    subject_codes_json: str,
    exam_mode: str = "solo",
    invitee_count: int | None = None,
    required_player_count: int | None = None,
    room_code: str | None = None,
) -> dict:
    existing = await get_mockjamb_payment(session, payment_reference)
    if existing:
        return existing

    if int(amount_paid) <= 0:
        raise ValueError(f"Invalid Mock JAMB amount: {amount_paid}")

    normalized_room_code = str(room_code or "").strip().upper() or None

    await session.execute(
        text("""
            insert into public.mockjamb_payments (
                payment_reference,
                user_id,
                amount_paid,
                payment_status,
                course_code,
                subject_codes_json,
                exam_mode,
                invitee_count,
                required_player_count,
                room_code,
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
                :invitee_count,
                :required_player_count,
                :room_code,
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
            "invitee_count": int(invitee_count) if invitee_count is not None else None,
            "required_player_count": int(required_player_count) if required_player_count is not None else None,
            "room_code": normalized_room_code,
        },
    )

    await session.flush()
    return await get_mockjamb_payment(session, payment_reference)

