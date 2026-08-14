# ======================================================
# services/finance/referral_finance.py
# ======================================================

"""
Referral relationship service for the NaijaPrize Finance subsystem.

Responsibilities:

- Create referral relationships.
- Prevent duplicate referral relationships.
- Locate a user's referrer.
- Locate referrals made by a user.
- Activate pending referrals.
- Read referral relationship information.

This module does NOT:

- Calculate referral commissions.
- Credit wallets.
- Create wallet transactions.
- Commit transactions.

The caller owns the transaction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finance_models import ReferralORM
from services.finance.exceptions import ReferralNotFoundError


# ==========================================================
# Result Models
# ==========================================================


@dataclass(slots=True)
class ReferralResult:
    """
    Lightweight business representation of a referral relationship.
    """

    id: UUID
    referrer_user_id: UUID
    referred_user_id: UUID
    referral_code_used: str
    status: str
    created_at: datetime
    activated_at: datetime | None
    notes: str | None


# ==========================================================
# Internal Mapper
# ==========================================================


def _to_referral_result(
    referral: ReferralORM,
) -> ReferralResult:
    """
    Converts ReferralORM into a business-level ReferralResult.
    """

    return ReferralResult(
        id=referral.id,
        referrer_user_id=referral.referrer_user_id,
        referred_user_id=referral.referred_user_id,
        referral_code_used=referral.referral_code_used,
        status=referral.status,
        created_at=referral.created_at,
        activated_at=referral.activated_at,
        notes=referral.notes,
    )


# ==========================================================
# Create Referral
# ==========================================================


async def create_referral(
    session: AsyncSession,
    *,
    referrer_user_id: UUID,
    referred_user_id: UUID,
    referral_code_used: str,
    status: str = "pending",
    notes: str | None = None,
) -> ReferralResult:
    """
    Creates a referral relationship.

    Rules:

    - A user cannot refer themselves.
    - A referred user cannot have another referral relationship.
    - The referral code must be non-empty.

    This function does NOT commit.
    """

    if referrer_user_id == referred_user_id:
        raise ValueError(
            "A user cannot refer themselves."
        )

    referral_code_used = referral_code_used.strip()

    if not referral_code_used:
        raise ValueError(
            "Referral code cannot be empty."
        )

    # ------------------------------------------------------
    # Check whether referred user already has a referrer
    # ------------------------------------------------------

    existing_result = await session.execute(
        select(ReferralORM)
        .where(
            ReferralORM.referred_user_id == referred_user_id
        )
        .limit(1)
    )

    existing = existing_result.scalar_one_or_none()

    if existing is not None:
        raise ValueError(
            "This user already has a referral relationship."
        )

    # ------------------------------------------------------
    # Create referral
    # ------------------------------------------------------

    referral = ReferralORM(
        referrer_user_id=referrer_user_id,
        referred_user_id=referred_user_id,
        referral_code_used=referral_code_used,
        status=status,
        notes=notes,
    )

    session.add(referral)

    await session.flush()

    return _to_referral_result(referral)


# ==========================================================
# Get Referral By ID
# ==========================================================


async def get_referral(
    session: AsyncSession,
    referral_id: UUID,
) -> ReferralResult:
    """
    Returns a referral relationship by ID.

    Raises:
        ReferralNotFoundError
    """

    result = await session.execute(
        select(ReferralORM)
        .where(
            ReferralORM.id == referral_id
        )
    )

    referral = result.scalar_one_or_none()

    if referral is None:
        raise ReferralNotFoundError(
            f"Referral {referral_id} was not found."
        )

    return _to_referral_result(referral)


# ==========================================================
# Get Referral For Referred User
# ==========================================================


async def get_referral_for_user(
    session: AsyncSession,
    referred_user_id: UUID,
) -> ReferralResult | None:
    """
    Returns the referral relationship belonging to a referred user.

    Returns None when the user has no referrer.
    """

    result = await session.execute(
        select(ReferralORM)
        .where(
            ReferralORM.referred_user_id == referred_user_id
        )
        .limit(1)
    )

    referral = result.scalar_one_or_none()

    if referral is None:
        return None

    return _to_referral_result(referral)


# ==========================================================
# Get Referrals Made By User
# ==========================================================


async def get_referrals_by_referrer(
    session: AsyncSession,
    referrer_user_id: UUID,
) -> list[ReferralResult]:
    """
    Returns all users referred by a particular referrer.
    """

    result = await session.execute(
        select(ReferralORM)
        .where(
            ReferralORM.referrer_user_id == referrer_user_id
        )
        .order_by(
            ReferralORM.created_at.desc()
        )
    )

    referrals = result.scalars().all()

    return [
        _to_referral_result(referral)
        for referral in referrals
    ]


# ==========================================================
# Get Referrals By Status
# ==========================================================


async def get_referrals_by_status(
    session: AsyncSession,
    *,
    referrer_user_id: UUID,
    status: str,
) -> list[ReferralResult]:
    """
    Returns referrals belonging to a referrer filtered by status.
    """

    result = await session.execute(
        select(ReferralORM)
        .where(
            ReferralORM.referrer_user_id == referrer_user_id,
            ReferralORM.status == status,
        )
        .order_by(
            ReferralORM.created_at.desc()
        )
    )

    referrals = result.scalars().all()

    return [
        _to_referral_result(referral)
        for referral in referrals
    ]


# ==========================================================
# Activate Referral
# ==========================================================


async def activate_referral(
    session: AsyncSession,
    referral_id: UUID,
) -> ReferralResult:
    """
    Activates a pending referral.

    This function does NOT calculate or pay commission.

    Commission processing remains the responsibility of
    commission_service.py.
    """

    result = await session.execute(
        select(ReferralORM)
        .where(
            ReferralORM.id == referral_id
        )
    )

    referral = result.scalar_one_or_none()

    if referral is None:
        raise ReferralNotFoundError(
            f"Referral {referral_id} was not found."
        )

    if referral.status == "active":
        return _to_referral_result(referral)

    if referral.status != "pending":
        raise ValueError(
            f"Referral cannot be activated from status "
            f"'{referral.status}'."
        )

    referral.status = "active"
    referral.activated_at = datetime.utcnow()

    await session.flush()

    return _to_referral_result(referral)


# ==========================================================
# Deactivate / Close Referral
# ==========================================================


async def deactivate_referral(
    session: AsyncSession,
    referral_id: UUID,
    *,
    notes: str | None = None,
) -> ReferralResult:
    """
    Marks an active referral relationship as inactive.

    This does not reverse any commission.

    Commission reversal belongs to a separate financial workflow.
    """

    result = await session.execute(
        select(ReferralORM)
        .where(
            ReferralORM.id == referral_id
        )
    )

    referral = result.scalar_one_or_none()

    if referral is None:
        raise ReferralNotFoundError(
            f"Referral {referral_id} was not found."
        )

    referral.status = "inactive"

    if notes is not None:
        referral.notes = notes

    await session.flush()

    return _to_referral_result(referral)


# ==========================================================
# Count Referrals
# ==========================================================


async def count_referrals(
    session: AsyncSession,
    *,
    referrer_user_id: UUID,
    status: str | None = None,
) -> int:
    """
    Counts referrals belonging to a referrer.
    """

    from sqlalchemy import func

    statement = select(
        func.count(ReferralORM.id)
    ).where(
        ReferralORM.referrer_user_id == referrer_user_id
    )

    if status is not None:
        statement = statement.where(
            ReferralORM.status == status
        )

    result = await session.execute(statement)

    return int(result.scalar_one() or 0)
