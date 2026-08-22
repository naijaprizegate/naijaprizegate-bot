# ==========================================
# services/finance/premium_points.py
# ==========================================

"""
Finance Premium Point service.

This module is responsible ONLY for Finance withdrawal-
qualification points and their lifecycle.

It does NOT:

- display trivia questions;
- determine whether an answer is correct;
- send Telegram messages;
- modify playtrivia.py;
- approve withdrawals;
- process bank accounts;
- initiate provider payments.

Business rules belong here.
Database persistence is handled through the Finance ORM models.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finance_models import (
    PremiumPointTransactionORM,
    ReferralWalletORM,
    UserPremiumPointsORM,
    WithdrawalEligibilitySessionORM,
    WithdrawalRequestORM,
)


# ==========================================================
# Premium Point Rules
# ==========================================================

# Every ₦2,000 of withdrawal value requires 4 Premium Points.
POINT_BLOCK_AMOUNT = Decimal("2000.00")
POINTS_PER_BLOCK = 4


# ==========================================================
# Eligibility Session Rules
# ==========================================================

# The user has one hour from the moment the eligibility
# session starts to complete qualification and submit the
# withdrawal.
ELIGIBILITY_SESSION_DURATION = timedelta(hours=1)


# ==========================================================
# Withdrawal Processing Rules
# ==========================================================

# Once the actual withdrawal request is submitted, Admin
# has 24 hours to act before the request expires and the
# reserved Premium Points are released.
WITHDRAWAL_PROCESSING_DURATION = timedelta(hours=24)


# ==========================================================
# Eligibility Session Statuses
# ==========================================================

ELIGIBILITY_STATUS_ACTIVE = "ACTIVE"
ELIGIBILITY_STATUS_COMPLETED = "COMPLETED"
ELIGIBILITY_STATUS_CANCELLED = "CANCELLED"
ELIGIBILITY_STATUS_EXPIRED = "EXPIRED"

ELIGIBILITY_TERMINAL_STATUSES = frozenset(
    {
        ELIGIBILITY_STATUS_COMPLETED,
        ELIGIBILITY_STATUS_CANCELLED,
        ELIGIBILITY_STATUS_EXPIRED,
    }
)


# ==========================================================
# Withdrawal Statuses
# ==========================================================

WITHDRAWAL_STATUS_PENDING = "PENDING"
WITHDRAWAL_STATUS_PROCESSING = "PROCESSING"
WITHDRAWAL_STATUS_COMPLETED = "COMPLETED"
WITHDRAWAL_STATUS_REJECTED = "REJECTED"
WITHDRAWAL_STATUS_CANCELLED = "CANCELLED"
WITHDRAWAL_STATUS_EXPIRED = "EXPIRED"
WITHDRAWAL_STATUS_FAILED = "FAILED"
WITHDRAWAL_STATUS_ON_HOLD = "ON_HOLD"


# ==========================================================
# Service Invariants
# ==========================================================

# Premium point mutations must preserve these invariants:
#
# 1. One qualifying event can award a point only once.
# 2. One eligibility session can reserve points only once.
# 3. Reserved points can be released at most once.
# 4. Reserved points can be consumed at most once.
# 5. eligible_points can never become negative.
# 6. reserved_points can never become negative.
# 7. Completed or expired sessions cannot receive points.
# 8. Reserved points cannot be used for another withdrawal.
# 9. Payment uncertainty must not release reserved points.
# 10. Successful payment must consume reserved points exactly once.


# -----------------------------------------
# Calculate Required Points
# -----------------------------------------

def calculate_required_points(amount: Decimal) -> int:
    """
    Calculate the Premium Premium Points required
    for a withdrawal amount.

    Every ₦2,000 withdrawal block requires 4 Premium Points.

    Examples:
        ₦2,000  → 4 points
        ₦4,000  → 8 points
        ₦6,000  → 12 points

    Raises:
        ValueError:
            If the amount is not positive or is not an
            exact multiple of the Premium point block amount.
    """

    if amount <= Decimal("0"):
        raise ValueError("Withdrawal amount must be greater than zero.")

    if amount % POINT_BLOCK_AMOUNT != Decimal("0"):
        raise ValueError(
            "Withdrawal amount must be an exact multiple of ₦2,000."
        )

    blocks = int(amount / POINT_BLOCK_AMOUNT)

    return blocks * POINTS_PER_BLOCK



# ------------------------------------------------
# Start Withdrawal Eligibility
# ------------------------------------------------

async def start_withdrawal_eligibility(
    session: AsyncSession,
    user_id: UUID,
    wallet_id: UUID,
    amount: Decimal,
) -> WithdrawalEligibilitySessionORM:
    """
    Start a one-hour Finance withdrawal eligibility session.

    A user may have only one ACTIVE eligibility session at a time.

    The user's referral wallet row is locked for the duration
    of this database transaction so concurrent requests for the
    same wallet cannot create duplicate active sessions.

    The session records:

    - requested withdrawal amount;
    - required Premium Points;
    - zero points earned initially;
    - ACTIVE status;
    - start time;
    - one-hour expiry time.

    The caller owns the database transaction and is responsible
    for committing the transaction.

    Raises:
        ValueError:
            If the withdrawal amount is invalid, the wallet does
            not belong to the user, or the user already has an
            active eligibility session.
    """

    required_points = calculate_required_points(amount)

    # ------------------------------------------------------
    # Lock the user's wallet row.
    #
    # This serializes concurrent eligibility-session starts
    # for the same wallet.
    # ------------------------------------------------------

    wallet_result = await session.execute(
        select(ReferralWalletORM)
        .where(
            ReferralWalletORM.id == wallet_id,
            ReferralWalletORM.user_id == user_id,
        )
        .with_for_update()
    )

    wallet = wallet_result.scalar_one_or_none()

    if wallet is None:
        raise ValueError(
            "Referral wallet does not belong to this user."
        )

    # ------------------------------------------------------
    # Check for an existing active eligibility session.
    #
    # An ACTIVE session is valid only while it has not expired.
    # If the previous session has expired, transition it to
    # EXPIRED and allow the user to start a new qualification.
    # ------------------------------------------------------

    result = await session.execute(
        select(WithdrawalEligibilitySessionORM)
        .where(
            WithdrawalEligibilitySessionORM.user_id == user_id,
            WithdrawalEligibilitySessionORM.status
            == ELIGIBILITY_STATUS_ACTIVE,
        )
        .order_by(
            WithdrawalEligibilitySessionORM.started_at.desc()
        )
        .limit(1)
        .with_for_update()
    )

    existing_session = result.scalar_one_or_none()

    if existing_session is not None:
        now = datetime.now(timezone.utc)

        if now >= existing_session.expires_at:
            existing_session.status = ELIGIBILITY_STATUS_EXPIRED

            await session.flush()

        else:
            raise ValueError(
                "User already has an active withdrawal eligibility session."
            )

    # ------------------------------------------------------
    # Create the new eligibility session.
    # ------------------------------------------------------

    now = datetime.now(timezone.utc)

    eligibility_session = WithdrawalEligibilitySessionORM(
        user_id=user_id,
        wallet_id=wallet_id,
        requested_amount=amount,
        required_points=required_points,
        points_earned=0,
        status=ELIGIBILITY_STATUS_ACTIVE,
        started_at=now,
        expires_at=now + ELIGIBILITY_SESSION_DURATION,
    )

    session.add(eligibility_session)

    # Flush so the new session is persisted and receives its
    # database-generated identity while remaining in the
    # caller's transaction.
    await session.flush()

    return eligibility_session


# ------------------------------------------------
# Validate Eligibility Session
# ------------------------------------------------

async def validate_eligibility_session(
    session: AsyncSession,
    user_id: UUID,
    session_id: UUID,
) -> WithdrawalEligibilitySessionORM:
    """
    Validate a withdrawal eligibility session.

    The eligibility-session row is locked while it is being
    validated so concurrent Premium Point events cannot
    simultaneously operate on the same session state.

    If an ACTIVE session has reached or passed its expiry time,
    it is transitioned to EXPIRED and flushed within the
    caller's transaction.

    The caller owns the database transaction and is responsible
    for committing or rolling back the transaction.

    Raises:
        ValueError:
            If the session does not belong to the user.
    """

    result = await session.execute(
        select(WithdrawalEligibilitySessionORM)
        .where(
            WithdrawalEligibilitySessionORM.id == session_id,
            WithdrawalEligibilitySessionORM.user_id == user_id,
        )
        .with_for_update()
    )

    eligibility_session = result.scalar_one_or_none()

    if eligibility_session is None:
        raise ValueError(
            "Withdrawal eligibility session does not belong to this user."
        )

    # ------------------------------------------------------
    # Already terminal or otherwise inactive.
    #
    # Return the current state without changing it.
    # The caller must not award points unless the status
    # is ACTIVE.
    # ------------------------------------------------------

    if eligibility_session.status != ELIGIBILITY_STATUS_ACTIVE:
        return eligibility_session

    # ------------------------------------------------------
    # Check the one-hour qualification deadline.
    # ------------------------------------------------------

    now = datetime.now(timezone.utc)

    if now >= eligibility_session.expires_at:
        eligibility_session.status = ELIGIBILITY_STATUS_EXPIRED

        await session.flush()

        return eligibility_session

    return eligibility_session



# ------------------------------------------------
# Award Premium Point
# ------------------------------------------------

async def award_premium_point(
    session: AsyncSession,
    user_id: UUID,
    session_id: UUID,
    idempotency_key: str,
    reference_id: UUID | None = None,
) -> bool:
    """
    Award exactly one Premium Point for a verified
    qualifying event.

    This function does NOT determine whether the underlying
    event qualifies. The caller must already have verified
    that the event is eligible for a Premium Point.

    The operation is idempotent:

    - A new idempotency key awards exactly one point.
    - A previously processed idempotency key awards no point.
    - Concurrent attempts are serialized through the user's
      UserPremiumPointsORM row.

    The operation updates, atomically within the caller's
    transaction:

        lifetime_points
        eligible_points
        points_earned
        eligibility session status
        premium point transaction ledger

    When the required number of points has been earned, the
    eligibility session is automatically marked COMPLETED.

    The caller owns the database transaction and is responsible
    for committing or rolling back the transaction.

    Returns:
        True:
            A new point was awarded.

        False:
            No point was awarded because the event was already
            processed, the session was no longer active, the
            session had expired, or the required points had
            already been reached.

    Raises:
        ValueError:
            If the idempotency key is empty or the Premium Points
            record does not exist.
    """

    if not idempotency_key or not idempotency_key.strip():
        raise ValueError(
            "A valid idempotency key is required."
        )

    # ------------------------------------------------------
    # Validate and lock the eligibility session.
    # ------------------------------------------------------

    eligibility_session = await validate_eligibility_session(
        session=session,
        user_id=user_id,
        session_id=session_id,
    )

    # ------------------------------------------------------
    # If the session expired, validate_eligibility_session()
    # has already transitioned it to EXPIRED and flushed it.
    #
    # We return normally so the caller can commit that state
    # change rather than rolling it back through an exception.
    # ------------------------------------------------------

    if eligibility_session.status != ELIGIBILITY_STATUS_ACTIVE:
        return False

    # ------------------------------------------------------
    # Lock the user's Premium Points row.
    #
    # This serializes concurrent point-awarding operations
    # for the same user.
    # ------------------------------------------------------

    points_result = await session.execute(
        select(UserPremiumPointsORM)
        .where(
            UserPremiumPointsORM.user_id == user_id,
        )
        .with_for_update()
    )

    user_points = points_result.scalar_one_or_none()

    if user_points is None:
        raise ValueError(
            "User Premium Points record does not exist."
        )

    # ------------------------------------------------------
    # Idempotency check.
    #
    # Because the user's Premium Points row is locked,
    # concurrent point-awarding operations for this user
    # are serialized before this check.
    # ------------------------------------------------------

    transaction_result = await session.execute(
        select(PremiumPointTransactionORM)
        .where(
            PremiumPointTransactionORM.user_id == user_id,
            PremiumPointTransactionORM.idempotency_key
            == idempotency_key,
        )
        .limit(1)
    )

    existing_transaction = transaction_result.scalar_one_or_none()

    if existing_transaction is not None:
        return False

    # ------------------------------------------------------
    # Qualification ceiling.
    #
    # Never award more points than this session requires.
    # ------------------------------------------------------

    if (
        eligibility_session.points_earned
        >= eligibility_session.required_points
    ):
        if eligibility_session.status == ELIGIBILITY_STATUS_ACTIVE:
            eligibility_session.status = ELIGIBILITY_STATUS_COMPLETED
            eligibility_session.completed_at = datetime.now(timezone.utc)

            await session.flush()

        return False

    # ------------------------------------------------------
    # Award exactly one Premium Point.
    # ------------------------------------------------------

    user_points.lifetime_points += 1
    user_points.eligible_points += 1

    eligibility_session.points_earned += 1

    now = datetime.now(timezone.utc)

    user_points.last_point_earned_at = now

    # ------------------------------------------------------
    # Complete the eligibility session when the required
    # number of points has now been reached.
    # ------------------------------------------------------

    if (
        eligibility_session.points_earned
        >= eligibility_session.required_points
    ):
        eligibility_session.points_earned = (
            eligibility_session.required_points
        )
        eligibility_session.status = ELIGIBILITY_STATUS_COMPLETED
        eligibility_session.completed_at = now

    # ------------------------------------------------------
    # Create immutable point-ledger record.
    # ------------------------------------------------------

    point_transaction = PremiumPointTransactionORM(
        user_id=user_id,
        eligibility_session_id=session_id,
        reference_id=reference_id,
        points=1,
        transaction_code="FINANCE_ELIGIBILITY_POINT",
        status="COMPLETED",
        source="SYSTEM",
        idempotency_key=idempotency_key,
        description="Finance withdrawal eligibility point earned.",
    )

    session.add(point_transaction)

    # ------------------------------------------------------
    # Flush all mutations together within the caller's
    # existing transaction.
    # ------------------------------------------------------

    await session.flush()

    return True



# ------------------------------------------------
# Reserve Premium Points
# ------------------------------------------------

async def reserve_premium_points(
    session: AsyncSession,
    user_id: UUID,
    session_id: UUID,
    withdrawal_id: UUID,
) -> int:
    """
    Reserve the Premium Points required for a
    completed withdrawal eligibility session.

    Lock order:
        1. Withdrawal
        2. Eligibility Session
        3. User Premium Points

    The withdrawal must already exist in PENDING status
    before Premium Points can be reserved.

    The operation atomically:

    - verifies the withdrawal belongs to the user;
    - verifies the withdrawal is PENDING;
    - verifies the eligibility session belongs to the user;
    - verifies the eligibility session is COMPLETED;
    - prevents duplicate reservation;
    - verifies all required points were earned;
    - moves eligible_points to reserved_points;
    - links the eligibility session to the withdrawal;
    - creates the RESERVED Premium Point ledger record.

    The caller owns the database transaction and is responsible
    for committing or rolling back the transaction.

    Returns:
        int:
            The number of Premium Points successfully reserved.

    Raises:
        ValueError:
            If the withdrawal or eligibility session is invalid,
            the session has already been reserved, the required
            points have not been earned, or the user does not
            have enough eligible points.
    """

    # ------------------------------------------------------
    # Lock the actual withdrawal FIRST.
    # ------------------------------------------------------

    withdrawal_result = await session.execute(
        select(WithdrawalRequestORM)
        .where(
            WithdrawalRequestORM.id == withdrawal_id,
        )
        .with_for_update()
    )

    withdrawal = withdrawal_result.scalar_one_or_none()

    if withdrawal is None:
        raise ValueError(
            "Withdrawal request does not exist."
        )

    # ------------------------------------------------------
    # Verify withdrawal ownership.
    # ------------------------------------------------------

    if withdrawal.user_id != user_id:
        raise ValueError(
            "Withdrawal request does not belong to this user."
        )

    # ------------------------------------------------------
    # Premium Points may only be reserved while the
    # withdrawal is awaiting review.
    # ------------------------------------------------------

    if withdrawal.status != WITHDRAWAL_STATUS_PENDING:
        raise ValueError(
            "Premium Points can only be reserved for a "
            "PENDING withdrawal."
        )

    # ------------------------------------------------------
    # Lock the eligibility session SECOND.
    # ------------------------------------------------------

    result = await session.execute(
        select(WithdrawalEligibilitySessionORM)
        .where(
            WithdrawalEligibilitySessionORM.id == session_id,
            WithdrawalEligibilitySessionORM.user_id == user_id,
        )
        .with_for_update()
    )

    eligibility_session = result.scalar_one_or_none()

    if eligibility_session is None:
        raise ValueError(
            "Withdrawal eligibility session does not belong "
            "to this user."
        )

    # ------------------------------------------------------
    # The eligibility session must have completed
    # qualification before points can be reserved.
    # ------------------------------------------------------

    if eligibility_session.status != ELIGIBILITY_STATUS_COMPLETED:
        raise ValueError(
            "Withdrawal eligibility session has not completed "
            "qualification."
        )

    # ------------------------------------------------------
    # Prevent duplicate reservation.
    # ------------------------------------------------------

    if eligibility_session.withdrawal_id is not None:
        raise ValueError(
            "Premium Points have already been reserved for this "
            "eligibility session."
        )

    # ------------------------------------------------------
    # Verify that the session earned all required points.
    # ------------------------------------------------------

    if (
        eligibility_session.points_earned
        < eligibility_session.required_points
    ):
        raise ValueError(
            "Eligibility session has not earned all required "
            "Premium Points."
        )

    points_to_reserve = eligibility_session.required_points

    # ------------------------------------------------------
    # Lock the user's Premium Points record THIRD.
    # ------------------------------------------------------

    points_result = await session.execute(
        select(UserPremiumPointsORM)
        .where(
            UserPremiumPointsORM.user_id == user_id,
        )
        .with_for_update()
    )

    user_points = points_result.scalar_one_or_none()

    if user_points is None:
        raise ValueError(
            "User Premium Points record does not exist."
        )

    # ------------------------------------------------------
    # Verify sufficient eligible points.
    # ------------------------------------------------------

    if user_points.eligible_points < points_to_reserve:
        raise ValueError(
            "User does not have enough eligible Premium Points "
            "to reserve this withdrawal."
        )

    # ------------------------------------------------------
    # Atomically move the points from eligible to reserved.
    # ------------------------------------------------------

    user_points.eligible_points -= points_to_reserve
    user_points.reserved_points += points_to_reserve

    # ------------------------------------------------------
    # Link the eligibility session to the verified withdrawal.
    # ------------------------------------------------------

    eligibility_session.withdrawal_id = withdrawal_id

    # ------------------------------------------------------
    # Create the immutable RESERVED ledger record.
    # ------------------------------------------------------

    reserve_idempotency_key = (
        f"withdrawal-reserve:{withdrawal_id}"
    )

    reserve_transaction = PremiumPointTransactionORM(
        user_id=user_id,
        points=points_to_reserve,
        transaction_code="FINANCE_WITHDRAWAL_POINTS_RESERVED",
        status="COMPLETED",
        withdrawal_id=withdrawal_id,
        reference_id=withdrawal_id,
        idempotency_key=reserve_idempotency_key,
        source="SYSTEM",
        description="Finance withdrawal points reserved.",
        eligibility_session_id=eligibility_session.id,
    )

    session.add(reserve_transaction)

    # ------------------------------------------------------
    # Flush the point mutation, session linkage, and ledger
    # record together inside the caller's transaction.
    # ------------------------------------------------------

    await session.flush()

    return points_to_reserve



# ------------------------------------------------
# Release Reserved Premium Points
# ------------------------------------------------

async def release_reserved_premium_points(
    session: AsyncSession,
    user_id: UUID,
    withdrawal_id: UUID,
) -> int:
    """
    Release Premium Points reserved for a withdrawal
    that is no longer proceeding to successful payment.

    Release is permitted only for terminal/non-completed
    withdrawal states.

    Reserved Premium Points must NOT be released merely because
    payment is uncertain, processing, or on hold.

    Lock order:
        1. Withdrawal
        2. Eligibility Session
        3. User Premium Points

    The operation is exactly-once through the durable
    FINANCE_WITHDRAWAL_POINTS_RELEASED ledger record.

    The operation atomically:

        reserved_points -= released amount
        eligible_points += released amount
        creates a RELEASED ledger transaction

    The existing eligibility-session withdrawal relationship
    is intentionally preserved for permanent auditability.

    The caller owns the database transaction and is responsible
    for committing or rolling back the transaction.

    Returns:
        int:
            The number of Premium Points released.

        0:
            Nothing was released because the reservation had
            already been released.

    Raises:
        ValueError:
            If the withdrawal does not belong to the user,
            the withdrawal is in a state where release is not
            permitted, the eligibility session is missing,
            or the Premium Point balances are inconsistent.
    """

    # ------------------------------------------------------
    # Lock the withdrawal FIRST.
    # ------------------------------------------------------

    withdrawal_result = await session.execute(
        select(WithdrawalRequestORM)
        .where(
            WithdrawalRequestORM.id == withdrawal_id,
            WithdrawalRequestORM.user_id == user_id,
        )
        .with_for_update()
    )

    withdrawal = withdrawal_result.scalar_one_or_none()

    if withdrawal is None:
        raise ValueError(
            "Withdrawal request does not belong to this user."
        )

    # ------------------------------------------------------
    # Payment uncertainty must never release reserved points.
    #
    # PROCESSING and ON_HOLD explicitly remain reserved.
    # ------------------------------------------------------

    if withdrawal.status in {
        WITHDRAWAL_STATUS_PROCESSING,
        WITHDRAWAL_STATUS_ON_HOLD,
    }:
        raise ValueError(
            "Reserved Premium Points cannot be released while "
            f"withdrawal status is {withdrawal.status}."
        )

    # ------------------------------------------------------
    # Only a non-successful terminal withdrawal may release
    # the reserved Premium Points.
    # ------------------------------------------------------

    if withdrawal.status not in {
        WITHDRAWAL_STATUS_REJECTED,
        WITHDRAWAL_STATUS_CANCELLED,
        WITHDRAWAL_STATUS_EXPIRED,
        WITHDRAWAL_STATUS_FAILED,
    }:
        raise ValueError(
            "Reserved Premium Points cannot be released for "
            f"withdrawal status {withdrawal.status}."
        )

    # ------------------------------------------------------
    # Lock the eligibility session SECOND.
    # ------------------------------------------------------

    eligibility_result = await session.execute(
        select(WithdrawalEligibilitySessionORM)
        .where(
            WithdrawalEligibilitySessionORM.withdrawal_id
            == withdrawal_id,
            WithdrawalEligibilitySessionORM.user_id == user_id,
        )
        .with_for_update()
    )

    eligibility_session = eligibility_result.scalar_one_or_none()

    if eligibility_session is None:
        raise ValueError(
            "Withdrawal does not have a linked eligibility session."
        )

    # ------------------------------------------------------
    # Durable exactly-once release check.
    # ------------------------------------------------------

    release_idempotency_key = (
        f"withdrawal-release:{withdrawal_id}"
    )

    release_result = await session.execute(
        select(PremiumPointTransactionORM)
        .where(
            PremiumPointTransactionORM.user_id == user_id,
            PremiumPointTransactionORM.withdrawal_id
            == withdrawal_id,
            PremiumPointTransactionORM.transaction_code
            == "FINANCE_WITHDRAWAL_POINTS_RELEASED",
            PremiumPointTransactionORM.idempotency_key
            == release_idempotency_key,
        )
        .limit(1)
    )

    existing_release = release_result.scalar_one_or_none()

    if existing_release is not None:
        return 0

    # ------------------------------------------------------
    # Determine the number of points originally reserved
    # for this withdrawal.
    # ------------------------------------------------------

    points_to_release = eligibility_session.required_points

    if points_to_release <= 0:
        raise ValueError(
            "Eligibility session contains no Premium Points to release."
        )

    # ------------------------------------------------------
    # Lock the user's Premium Points record THIRD.
    # ------------------------------------------------------

    points_result = await session.execute(
        select(UserPremiumPointsORM)
        .where(
            UserPremiumPointsORM.user_id == user_id,
        )
        .with_for_update()
    )

    user_points = points_result.scalar_one_or_none()

    if user_points is None:
        raise ValueError(
            "User Premium Points record does not exist."
        )

    # ------------------------------------------------------
    # Defensive invariant:
    #
    # Never allow reserved_points to become negative.
    # ------------------------------------------------------

    if user_points.reserved_points < points_to_release:
        raise ValueError(
            "Reserved Premium Points are inconsistent with "
            "the eligibility session."
        )

    # ------------------------------------------------------
    # Release the reserved points back to eligible points.
    # ------------------------------------------------------

    user_points.reserved_points -= points_to_release
    user_points.eligible_points += points_to_release

    # ------------------------------------------------------
    # Create the immutable RELEASED ledger record.
    # ------------------------------------------------------

    release_transaction = PremiumPointTransactionORM(
        user_id=user_id,
        withdrawal_id=withdrawal_id,
        eligibility_session_id=eligibility_session.id,
        reference_id=withdrawal_id,
        points=points_to_release,
        transaction_code="FINANCE_WITHDRAWAL_POINTS_RELEASED",
        status="COMPLETED",
        source="SYSTEM",
        idempotency_key=release_idempotency_key,
        description=(
            "Reserved Finance withdrawal points released "
            "after withdrawal termination."
        ),
    )

    session.add(release_transaction)

    # ------------------------------------------------------
    # Flush the balance mutation and immutable release ledger
    # record within the caller's transaction.
    # ------------------------------------------------------

    await session.flush()

    return points_to_release


# ------------------------------------------------
# Consume Reserved Premium Points
# ------------------------------------------------

async def consume_reserved_finance_points(
    session: AsyncSession,
    user_id: UUID,
    withdrawal_id: UUID,
) -> int:
    """
    Consume Finance Premium Points reserved for a withdrawal
    after the withdrawal payment has been confirmed successful.

    This operation is permitted only when:

        - the withdrawal belongs to the user;
        - the withdrawal status is COMPLETED;
        - the eligibility session belongs to the user;
        - the eligibility session is COMPLETED;
        - the withdrawal has a valid completed_at timestamp.

    Payment-uncertain states must never consume reserved
    Premium Points.

    The operation is idempotent through a durable
    PremiumPointTransactionORM ledger record using the
    deterministic idempotency key:

        withdrawal-consume:<withdrawal_id>

    The operation atomically:

        reserved_points -= reserved amount
        total_points_used += reserved amount
        last_withdrawal_at = withdrawal.completed_at
        creates a CONSUMED ledger transaction

    The caller owns the database transaction and is responsible
    for committing or rolling back the transaction.

    Returns:
        int:
            The number of Premium Points consumed.

        0:
            Nothing was consumed because the reservation had
            already been consumed.

    Raises:
        ValueError:
            If the withdrawal does not belong to the user, the
            withdrawal is not COMPLETED, the eligibility session
            is missing or not COMPLETED, completed_at is missing,
            or the Premium Point balances are inconsistent.
    """

    # ------------------------------------------------------
    # Lock the withdrawal.
    # ------------------------------------------------------

    withdrawal_result = await session.execute(
        select(WithdrawalRequestORM)
        .where(
            WithdrawalRequestORM.id == withdrawal_id,
            WithdrawalRequestORM.user_id == user_id,
        )
        .with_for_update()
    )

    withdrawal = withdrawal_result.scalar_one_or_none()

    if withdrawal is None:
        raise ValueError(
            "Withdrawal request does not belong to this user."
        )

    # ------------------------------------------------------
    # Only a confirmed COMPLETED withdrawal may consume
    # reserved Premium Points.
    # ------------------------------------------------------

    if withdrawal.status != WITHDRAWAL_STATUS_COMPLETED:
        raise ValueError(
            "Reserved Premium Points cannot be consumed while "
            f"withdrawal status is {withdrawal.status}."
        )

    # ------------------------------------------------------
    # The withdrawal must have an authoritative completion
    # timestamp.
    # ------------------------------------------------------

    if withdrawal.completed_at is None:
        raise ValueError(
            "Completed withdrawal is missing its completion timestamp."
        )

    # ------------------------------------------------------
    # Find and lock the eligibility session linked to this
    # withdrawal.
    # ------------------------------------------------------

    eligibility_result = await session.execute(
        select(WithdrawalEligibilitySessionORM)
        .where(
            WithdrawalEligibilitySessionORM.withdrawal_id
            == withdrawal_id,
            WithdrawalEligibilitySessionORM.user_id == user_id,
        )
        .with_for_update()
    )

    eligibility_session = eligibility_result.scalar_one_or_none()

    if eligibility_session is None:
        raise ValueError(
            "Withdrawal does not have a linked eligibility session."
        )

    # ------------------------------------------------------
    # The eligibility session itself must have completed
    # qualification before its reserved points can be consumed.
    # ------------------------------------------------------

    if eligibility_session.status != ELIGIBILITY_STATUS_COMPLETED:
        raise ValueError(
            "Reserved Premium Points can only be consumed from "
            "a completed eligibility session."
        )

    # ------------------------------------------------------
    # Durable consume idempotency.
    # ------------------------------------------------------

    consume_idempotency_key = (
        f"withdrawal-consume:{withdrawal_id}"
    )

    consume_result = await session.execute(
        select(PremiumPointTransactionORM)
        .where(
            PremiumPointTransactionORM.user_id == user_id,
            PremiumPointTransactionORM.withdrawal_id
            == withdrawal_id,
            PremiumPointTransactionORM.transaction_code
            == "FINANCE_WITHDRAWAL_POINTS_CONSUMED",
            PremiumPointTransactionORM.idempotency_key
            == consume_idempotency_key,
        )
        .limit(1)
    )

    existing_consume = consume_result.scalar_one_or_none()

    if existing_consume is not None:
        return 0

    # ------------------------------------------------------
    # Determine the number of points reserved for this
    # withdrawal.
    # ------------------------------------------------------

    points_to_consume = eligibility_session.required_points

    if points_to_consume <= 0:
        raise ValueError(
            "Eligibility session contains no Premium Points to consume."
        )

    # ------------------------------------------------------
    # Lock the user's Premium Points record.
    # ------------------------------------------------------

    points_result = await session.execute(
        select(UserPremiumPointsORM)
        .where(
            UserPremiumPointsORM.user_id == user_id,
        )
        .with_for_update()
    )

    user_points = points_result.scalar_one_or_none()

    if user_points is None:
        raise ValueError(
            "User Premium Points record does not exist."
        )

    # ------------------------------------------------------
    # Defensive invariant:
    #
    # Never allow reserved_points to become negative.
    # ------------------------------------------------------

    if user_points.reserved_points < points_to_consume:
        raise ValueError(
            "Reserved Premium Points are inconsistent with "
            "the eligibility session."
        )

    # ------------------------------------------------------
    # Consume the reserved points.
    #
    # Use the authoritative withdrawal completion timestamp,
    # not the time this Finance service happens to execute.
    # ------------------------------------------------------

    user_points.reserved_points -= points_to_consume
    user_points.total_points_used += points_to_consume
    user_points.last_withdrawal_at = withdrawal.completed_at

    # ------------------------------------------------------
    # Create the immutable CONSUME ledger record.
    # ------------------------------------------------------

    consume_transaction = PremiumPointTransactionORM(
        user_id=user_id,
        withdrawal_id=withdrawal_id,
        eligibility_session_id=eligibility_session.id,
        reference_id=withdrawal_id,
        points=points_to_consume,
        transaction_code="FINANCE_WITHDRAWAL_POINTS_CONSUMED",
        status="COMPLETED",
        source="SYSTEM",
        idempotency_key=consume_idempotency_key,
        description=(
            "Reserved Finance withdrawal points consumed "
            "after confirmed successful payment."
        ),
    )

    session.add(consume_transaction)

    # ------------------------------------------------------
    # Flush the balance mutation and immutable consume ledger
    # record within the caller's transaction.
    # ------------------------------------------------------

    await session.flush()

    return points_to_consume


# ========================================================
# Internal Type Aliases / Shared Values
# ========================================================

PointAmount = int
MoneyAmount = Decimal
Timestamp = datetime
UserId = UUID
SessionId = UUID
WithdrawalId = UUID

