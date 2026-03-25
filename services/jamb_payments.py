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
    question_credits_added: int | None = None,
) -> dict:
    existing = await get_jamb_payment(session, payment_reference)
    if existing:
        return existing

    credits = int(question_credits_added or calculate_jamb_credits(int(amount_paid)))
    if credits <= 0:
        raise ValueError(f"Invalid JAMB credits for amount {amount_paid}")

    await session.execute(
        text("""
            insert into jamb_payments (
                payment_reference,
                user_id,
                amount_paid,
                question_credits_added,
                payment_status,
                created_at,
                updated_at
            )
            values (
                :payment_reference,
                :user_id,
                :amount_paid,
                :question_credits_added,
                'pending',
                now(),
                now()
            )
        """),
        {
            "payment_reference": payment_reference,
            "user_id": int(user_id),
            "amount_paid": int(amount_paid),
            "question_credits_added": credits,
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
) -> tuple[bool, dict | None, int]:
    """
    Safe/idempotent JAMB finalizer.
    Claims the payment row first, then credits jamb_user_access.
    Returns (did_credit_now, payment_row, credits)
    """
    payment = await get_jamb_payment(session, payment_reference)

    if not payment:
        payment = await create_pending_jamb_payment(
            session,
            payment_reference=payment_reference,
            user_id=int(user_id),
            amount_paid=int(amount_paid),
            question_credits_added=question_credits_added,
        )

    credits = int(payment.get("question_credits_added") or question_credits_added or 0)
    if credits <= 0:
        credits = calculate_jamb_credits(int(amount_paid))

    if credits <= 0:
        logger.error(
            "❌ Invalid JAMB credits | payment_reference=%s | amount=%s",
            payment_reference,
            amount_paid,
        )
        return False, payment, 0

    # Claim row first. Only one request can win this.
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
        return False, latest, int((latest or {}).get("question_credits_added") or credits)

    await session.execute(
        text("""
            insert into jamb_user_access (user_id)
            values (:user_id)
            on conflict (user_id) do nothing
        """),
        {"user_id": int(user_id)},
    )

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
    return True, latest, int(credits)

