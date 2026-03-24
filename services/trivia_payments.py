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
    Idempotent finalizer.
    Returns (did_credit_now, payment_row, tries)
    """
    payment = await get_trivia_payment(session, tx_ref)

    if payment and payment.status == "successful" and int(payment.credited_tries or 0) > 0:
        return False, payment, int(payment.credited_tries or 0)

    tries = calculate_tries(int(amount))
    if tries <= 0:
        logger.error("❌ Invalid trivia tries for tx_ref=%s amount=%s", tx_ref, amount)
        return False, payment, 0

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

    payment.status = "successful"
    payment.amount = int(amount)
    payment.flw_tx_id = str(flw_tx_id) if flw_tx_id else payment.flw_tx_id
    payment.tg_id = int(tg_id)
    payment.username = (username or payment.username or "Unknown")[:64]

    user = await get_or_create_user(session, tg_id=int(tg_id), username=payment.username)
    await add_tries(session, user, tries, paid=True)

    payment.credited_tries = tries
    await session.flush()
    return True, payment, tries
