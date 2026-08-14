# ======================================================
# services/finance/commission_service.py
# ======================================================

"""
Referral commission processing for the NaijaPrize Finance subsystem.

This module owns referral commission business logic.

Responsibilities:

- Validate a successful qualifying payment.
- Locate the referral relationship.
- Prevent duplicate commission processing.
- Calculate the referral commission.
- Obtain/create the referrer's wallet within the
  current transaction.
- Credit the commission through wallet_service.
- Mark the payment commission as processed.

This module does NOT:

- Verify payments with a payment gateway.
- Create referral relationships.
- Commit the database transaction.
- Implement wallet ledger mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Payment
from finance_models import ReferralORM
from services.finance.constants import (
    MINIMUM_QUALIFYING_PAYMENT,
    REFERRAL_COMMISSION_PERCENT,
)
from services.finance.wallet_service import (
    get_or_create_wallet,
    credit_referral_commission,
)


# ==========================================================
# Result
# ==========================================================

CommissionResultStatus = Literal[
    "processed",
    "already_processed",
    "not_eligible",
    "no_referral",
]


@dataclass(slots=True)
class CommissionResult:
    """
    Result returned by referral commission processing.
    """

    status: CommissionResultStatus
    payment_id: UUID
    referral_id: UUID | None = None
    referrer_user_id: UUID | None = None
    commission_amount: Decimal = Decimal("0.00")


# ==========================================================
# Process Referral Commission
# ==========================================================

async def process_referral_commission(
    session: AsyncSession,
    payment: Payment,
) -> CommissionResult:
    """
    Processes referral commission for a successful qualifying payment.

    Qualification rules:

    1. Payment must be successful.
    2. Payment must not already have its referral commission processed.
    3. Payment amount must meet the minimum qualifying amount.
    4. A referral relationship must exist for the paying user.

    The commission is calculated using the configured
    REFERRAL_COMMISSION_PERCENT.

    This function does NOT commit.

    The caller is responsible for committing or rolling back
    the surrounding transaction.
    """

    # ------------------------------------------------------
    # 1. Idempotency
    # ------------------------------------------------------

    if payment.referral_commission_processed:
        return CommissionResult(
            status="already_processed",
            payment_id=payment.id,
        )

    # ------------------------------------------------------
    # 2. Payment status
    # ------------------------------------------------------

    if str(payment.status).upper() != "COMPLETED":
        return CommissionResult(
            status="not_eligible",
            payment_id=payment.id,
        )

    # ------------------------------------------------------
    # 3. Minimum qualifying payment
    # ------------------------------------------------------

    payment_amount = Decimal(str(payment.amount))

    if payment_amount < MINIMUM_QUALIFYING_PAYMENT:
        return CommissionResult(
            status="not_eligible",
            payment_id=payment.id,
        )

    # ------------------------------------------------------
    # 4. Find referral relationship
    # ------------------------------------------------------

    statement = (
        select(ReferralORM)
        .where(
            ReferralORM.referred_user_id == payment.user_id
        )
        .limit(1)
    )

    result = await session.execute(statement)

    referral = result.scalar_one_or_none()

    if referral is None:
        return CommissionResult(
            status="no_referral",
            payment_id=payment.id,
        )

    # ------------------------------------------------------
    # 5. Calculate commission
    # ------------------------------------------------------

    commission_amount = (
        payment_amount * REFERRAL_COMMISSION_PERCENT
    ).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    if commission_amount <= Decimal("0.00"):
        return CommissionResult(
            status="not_eligible",
            payment_id=payment.id,
            referral_id=referral.id,
            referrer_user_id=referral.referrer_user_id,
        )

    # ------------------------------------------------------
    # 6. Get or create referrer's wallet
    # ------------------------------------------------------

    wallet = await get_or_create_wallet(
        session=session,
        user_id=referral.referrer_user_id,
    )

    # ------------------------------------------------------
    # 7. Credit commission
    # ------------------------------------------------------

    await credit_referral_commission(
        session=session,
        wallet=wallet,
        commission_amount=commission_amount,
    )

    # ------------------------------------------------------
    # 8. Mark payment as processed
    # ------------------------------------------------------

    payment.referral_commission_processed = True

    # ------------------------------------------------------
    # IMPORTANT:
    #
    # No commit here.
    #
    # The caller owns the transaction boundary.
    # ------------------------------------------------------

    return CommissionResult(
        status="processed",
        payment_id=payment.id,
        referral_id=referral.id,
        referrer_user_id=referral.referrer_user_id,
        commission_amount=commission_amount,
    )

