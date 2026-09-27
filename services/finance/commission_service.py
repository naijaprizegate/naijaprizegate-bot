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
from services.finance.referral_finance import activate_referral
from services.finance.constants import (
    MAX_REFERRAL_GENERATIONS,
    MINIMUM_QUALIFYING_PAYMENT,
    MULTI_GENERATION_COMMISSION_PERCENT,
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
    commission_recipients: list[dict] | None = None


# ==========================================================
# Referral Chain
# ==========================================================

async def _get_referral_chain(
    session: AsyncSession,
    user_id: UUID,
    max_generations: int,
) -> list[tuple[int, ReferralORM]]:
    """
    Returns the referral chain above a user.

    Each tuple contains:

        (generation, referral_relationship)

    Generation 1 is the user's direct referrer.
    Generation 2 is the referrer of Generation 1.
    Generation 3 is the referrer of Generation 2.
    And so on.

    The chain stops when:

    - max_generations is reached,
    - a referrer cannot be found, or
    - a circular referral relationship is detected.

    This function does not modify or commit anything.
    """

    chain: list[tuple[int, ReferralORM]] = []
    current_user_id = user_id
    visited_user_ids: set[UUID] = {user_id}

    for generation in range(1, max_generations + 1):
        statement = (
            select(ReferralORM)
            .where(
                ReferralORM.referred_user_id == current_user_id
            )
            .limit(1)
        )

        result = await session.execute(statement)
        referral = result.scalar_one_or_none()

        if referral is None:
            break

        referrer_user_id = referral.referrer_user_id

        if referrer_user_id in visited_user_ids:
            break

        chain.append((generation, referral))

        visited_user_ids.add(referrer_user_id)
        current_user_id = referrer_user_id

    return chain


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
    # 4. Build referral chain
    # ------------------------------------------------------

    referral_chain = await _get_referral_chain(
        session=session,
        user_id=payment.user_id,
        max_generations=MAX_REFERRAL_GENERATIONS,
    )

    if not referral_chain:
        return CommissionResult(
            status="no_referral",
            payment_id=payment.id,
        )

    # ------------------------------------------------------
    # 5. Determine how many generations should be paid
    # ------------------------------------------------------
    #
    # Generation 1 keeps the existing 5% commission for
    # all qualifying payment types.
    #
    # Generations 2-5 receive the additional 1% only
    # when the payment is a paid Trivia play.
    # ------------------------------------------------------

    is_trivia_play = (
        str(payment.payment_type_code).upper()
        == "TRIVIA_PLAY"
    )

    if is_trivia_play:
        commission_chain = referral_chain
    else:
        # Preserve the existing behavior for all other
        # qualifying payment types: pay only the direct
        # referrer.
        commission_chain = referral_chain[:1]

    # ------------------------------------------------------
    # 6. Calculate and distribute commissions
    # ------------------------------------------------------

    direct_commission_amount = Decimal("0.00")
    direct_referral_id: UUID | None = None
    direct_referrer_user_id: UUID | None = None
    commission_recipients: list[dict] = []

    for generation, referral in commission_chain:

        # --------------------------------------------------
        # Generation 1 = existing 5% commission
        # --------------------------------------------------

        if generation == 1:
            commission_percent = REFERRAL_COMMISSION_PERCENT

            direct_referral_id = referral.id
            direct_referrer_user_id = referral.referrer_user_id

        # --------------------------------------------------
        # Generations 2-5 = additional 1%
        # --------------------------------------------------

        else:
            commission_percent = (
                MULTI_GENERATION_COMMISSION_PERCENT
            )

        commission_amount = (
            payment_amount * commission_percent
        ).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        if commission_amount <= Decimal("0.00"):
            continue

        # --------------------------------------------------
        # Get or create recipient wallet
        # --------------------------------------------------

        wallet = await get_or_create_wallet(
            session=session,
            user_id=referral.referrer_user_id,
        )

        # --------------------------------------------------
        # Credit recipient commission
        # --------------------------------------------------

        await credit_referral_commission(
            session=session,
            wallet=wallet,
            commission_amount=commission_amount,
        )

        # --------------------------------------------------
        # Activate only the direct referral relationship.
        #
        # The existing system activates a referral when
        # that referred user personally makes a qualifying
        # payment.
        #
        # A Generation 2-5 commission does NOT mean that
        # the intermediate referral personally qualified.
        # --------------------------------------------------

        if generation == 1:
            await activate_referral(
                session=session,
                referral_id=referral.id,
            )

        # --------------------------------------------------
        # Keep the existing result semantics:
        #
        # commission_amount refers to the direct
        # referrer's 5% reward.
        # --------------------------------------------------

        if generation == 1:
            direct_commission_amount = commission_amount

        commission_recipients.append(
            {
                "generation": generation,
                "referrer_user_id": referral.referrer_user_id,
                "commission_amount": commission_amount,
            }
        )

    if direct_commission_amount <= Decimal("0.00"):
        return CommissionResult(
            status="not_eligible",
            payment_id=payment.id,
            referral_id=direct_referral_id,
            referrer_user_id=direct_referrer_user_id,
        )

    # ------------------------------------------------------
    # 7. Mark payment as processed
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
        referral_id=direct_referral_id,
        referrer_user_id=direct_referrer_user_id,
        commission_amount=direct_commission_amount,
        commission_recipients=commission_recipients,
    )

