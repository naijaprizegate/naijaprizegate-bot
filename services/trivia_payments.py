# ====================================================
# services/trivia_payments.py
# ====================================================

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Payment
from helpers import get_or_create_user, add_tries
from services.flutterwave_client import calculate_tries

logger = logging.getLogger("trivia_payments")
logger.setLevel(logging.INFO)


async def get_trivia_payment(
    session: AsyncSession,
    tx_ref: str,
) -> Optional[Payment]:
    result = await session.execute(
        select(Payment).where(Payment.tx_ref == tx_ref)
    )
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

    # Resolve the real application user now.
    # The current payments table requires user_id.
    user = await get_or_create_user(
        session,
        tg_id=int(tg_id),
        username=username,
    )

    payment = Payment(
        user_id=user.id,
        tg_id=int(tg_id),
        payment_provider="FLUTTERWAVE",
        tx_ref=tx_ref,
        amount=int(amount),
        payment_type_code="TRIVIA_PLAY",
        status="PENDING",
        payment_metadata={
            "tg_id": str(tg_id),
            "username": username,
            "product_type": "TRIVIA",
        },
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

    Returns:
        (did_credit_now, payment_row, tries)
    """

    payment = await get_trivia_payment(session, tx_ref)

    # Resolve the real application user before creating/reconciling
    # the payment record because payments.user_id is NOT NULL.
    user = await get_or_create_user(
        session,
        tg_id=int(tg_id),
        username=username,
    )

    if not payment:
        payment = Payment(
            user_id=user.id,
            tg_id=int(tg_id),
            payment_provider="FLUTTERWAVE",
            tx_ref=tx_ref,
            amount=int(amount),
            payment_type_code="TRIVIA_PLAY",
            status="PENDING",
            payment_metadata={
                "tg_id": str(tg_id),
                "username": username,
                "product_type": "TRIVIA",
            },
        )

        session.add(payment)
        await session.flush()

    tries = calculate_tries(int(amount))

    if tries <= 0:
        logger.error(
            "❌ Invalid trivia tries for tx_ref=%s amount=%s",
            tx_ref,
            amount,
        )
        return False, payment, 0

    # Lock the payment row so only one finalizer can process
    # this transaction at a time.
    result = await session.execute(
        select(Payment)
        .where(Payment.tx_ref == tx_ref)
        .with_for_update()
    )

    locked_payment = result.scalar_one_or_none()

    if not locked_payment:
        return False, None, 0

    # Canonical idempotency check.
    # The number of credited tries is derived from payment.amount;
    # it is no longer stored in the payments table.
    if locked_payment.status == "COMPLETED":
        existing_tries = calculate_tries(
            int(locked_payment.amount or 0)
        )

        return False, locked_payment, existing_tries

    # Reconcile the canonical payment fields.
    locked_payment.user_id = user.id
    locked_payment.tg_id = int(tg_id)
    locked_payment.payment_provider = "FLUTTERWAVE"
    locked_payment.payment_type_code = "TRIVIA_PLAY"
    locked_payment.amount = int(amount)
    locked_payment.status = "COMPLETED"

    if flw_tx_id:
        locked_payment.gateway_transaction_id = str(flw_tx_id)

    locked_payment.gateway_status = "successful"
    locked_payment.verified_at = datetime.now(timezone.utc)
    locked_payment.credited_at = datetime.now(timezone.utc)
    locked_payment.processed_at = datetime.now(timezone.utc)

    locked_payment.payment_metadata = {
        "tg_id": str(tg_id),
        "username": username,
        "product_type": "TRIVIA",
    }

    await session.flush()

    # Credit Trivia only after the payment row has been locked
    # and marked as successfully processed within this transaction.
    await add_tries(
        session,
        user,
        tries,
        paid=True,
    )

    await session.flush()

    return True, locked_payment, tries

