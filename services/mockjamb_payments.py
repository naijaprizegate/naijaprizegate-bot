# ======================================================
# services/mockjamb_payments.py
# ======================================================
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("mockjamb_payments")
logger.setLevel(logging.INFO)

async def get_mockjamb_payment(
    session: AsyncSession,
    payment_reference: str,
    *,
    for_update: bool = False,
) -> dict | None:
    query = """
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
            updated_at,
            verified_at,
            credited_at,
            processed_at
        from public.mockjamb_payments
        where payment_reference = :payment_reference
        limit 1
    """

    if for_update:
        query += " FOR UPDATE"

    result = await session.execute(
        text(query),
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


async def finalize_mockjamb_payment(
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
    payment = await get_mockjamb_payment(session, payment_reference)
    if not payment:
        logger.error(
            "❌ Mock JAMB payment not found during finalize | payment_reference=%s | user_id=%s",
            payment_reference,
            user_id,
        )
        return False, None

    claimed = await session.execute(
        text("""
            update public.mockjamb_payments
            set
                payment_status = 'successful',
                verified_at = COALESCE(verified_at, now()),
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
    latest = await get_mockjamb_payment(session, payment_reference)

    if not claimed_row:
        return False, latest

    return True, latest


async def mark_mockjamb_payment_provisioned(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
) -> None:
    """
    Mark Mock JAMB access as provisioned.

    Call inside the same transaction as the successful exam
    session or room-player provisioning operation.
    """
    result = await session.execute(
        text("""
            UPDATE public.mockjamb_payments
            SET
                credited_at = COALESCE(credited_at, now()),
                processed_at = COALESCE(processed_at, now()),
                updated_at = now()
            WHERE payment_reference = :payment_reference
              AND user_id = :user_id
              AND LOWER(COALESCE(payment_status, '')) = 'successful'
            RETURNING payment_reference
        """),
        {
            "payment_reference": payment_reference,
            "user_id": int(user_id),
        },
    )

    if not result.first():
        raise RuntimeError(
            "Mock JAMB payment could not be marked as provisioned "
            f"(payment_reference={payment_reference}, user_id={user_id})"
        )