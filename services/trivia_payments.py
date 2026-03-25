# ====================================================
# services/trivia_payments.py
# ===================================================
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Payment
from helpers import get_or_create_user, add_tries
from services.flutterwave_client import calculate_tries

logger = logging.getLogger("trivia_payments")
logger.setLevel(logging.INFO)


async def get_trivia_payment(session: AsyncSession, tx_ref: str) -> Optional[Payment]:
    result = await session.execute(select(Payment).where(Payment.tx_ref == tx_ref))
    return result.scalar_one_or_none()


async def create_pending_trivia_payment(
    session: AsyncSession,
    *,
    tx_ref: str,
    tg_id: int,
    username: str | None,
    amount: int,
) -> Payment:
    existing = await get_trivia_payment(session, tx_ref)
    if existing:
        return existing

    payment = Payment(
        tx_ref=tx_ref,
        status="pending",
        credited_tries=0,
        flw_tx_id=None,
        user_id=None,
        amount=int(amount),
        tg_id=int(tg_id),
        username=(username or "Unknown")[:64],
    )
    session.add(payment)
    await session.flush()
    return payment


async def finalize_trivia_payment(
    session: AsyncSession,
    *,
    tx_ref: str,
    amount: int,
    tg_id: int,
    username: str | None,
    flw_tx_id: str | None = None,
) -> tuple[bool, Optional[Payment], int]:
    """
    Safe/idempotent Trivia finalizer.
    Returns (did_credit_now, payment_row, tries)
    """
    payment = await get_trivia_payment(session, tx_ref)

    if not payment:
        payment = Payment(
            tx_ref=tx_ref,
            status="pending",
            credited_tries=0,
            flw_tx_id=None,
            user_id=None,
            amount=int(amount),
            tg_id=int(tg_id),
            username=(username or "Unknown")[:64],
        )
        session.add(payment)
        await session.flush()

    tries = calculate_tries(int(amount))
    if tries <= 0:
        logger.error("❌ Invalid trivia tries for tx_ref=%s amount=%s", tx_ref, amount)
        return False, payment, 0

    # Lock the row so only one finalizer proceeds at a time
    result = await session.execute(
        select(Payment)
        .where(Payment.tx_ref == tx_ref)
        .with_for_update()
    )
    locked_payment = result.scalar_one_or_none()

    if not locked_payment:
        return False, None, 0

    if locked_payment.status == "successful" and int(locked_payment.credited_tries or 0) > 0:
        return False, locked_payment, int(locked_payment.credited_tries or 0)

    locked_payment.status = "successful"
    locked_payment.amount = int(amount)
    locked_payment.flw_tx_id = str(flw_tx_id) if flw_tx_id else locked_payment.flw_tx_id
    locked_payment.tg_id = int(tg_id)
    locked_payment.username = (username or locked_payment.username or "Unknown")[:64]
    locked_payment.credited_tries = tries
    await session.flush()

    user = await get_or_create_user(
        session,
        tg_id=int(tg_id),
        username=locked_payment.username,
    )
    await add_tries(session, user, tries, paid=True)

    await session.flush()
    return True, locked_payment, tries

