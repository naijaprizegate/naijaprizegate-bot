# ======================================================
# services/finance/reporting_service.py
# ======================================================

"""
Read-only reporting service for the NaijaPrize Finance subsystem.

Responsibilities:

- Build wallet summaries.
- Read wallet transaction history.
- Read referral statistics.
- Read withdrawal statistics.
- Read Premium Points balances.
- Provide finance dashboard/reporting data.

This module MUST NOT:

- Credit wallets.
- Debit wallets.
- Create wallet transactions.
- Create referrals.
- Process commissions.
- Create withdrawals.
- Approve withdrawals.
- Modify Premium Points.

All functions are read-only unless explicitly documented otherwise.

The caller owns the session lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from finance_models import (
    PremiumPointTransactionORM,
    ReferralORM,
    ReferralWalletORM,
    UserPremiumPointsORM,
    WalletTransactionORM,
    WithdrawalRequestORM,
)
from services.finance.enums import (
    CommissionStatus,
    WalletTransactionCode,
    WalletTransactionStatus,
    WithdrawalStatus,
)
from services.finance.models import (
    WalletSummary,
)


# ==========================================================
# Reporting Result Models
# ==========================================================


@dataclass(slots=True)
class ReferralReport:
    """
    Referral statistics for a user.
    """

    total_referrals: int
    active_referrals: int
    pending_referrals: int
    inactive_referrals: int


@dataclass(slots=True)
class CommissionReport:
    """
    Referral commission statistics for a user's wallet.
    """

    transaction_count: int
    total_commission: Decimal
    total_reversed: Decimal
    net_commission: Decimal


@dataclass(slots=True)
class WithdrawalReport:
    """
    Withdrawal statistics for a user's wallet.
    """

    total_requests: int
    pending_amount: Decimal
    approved_amount: Decimal
    completed_amount: Decimal
    rejected_amount: Decimal
    cancelled_amount: Decimal


@dataclass(slots=True)
class FinanceReport:
    """
    Complete read-only finance report for a user.
    """

    wallet: WalletSummary
    referrals: ReferralReport
    commissions: CommissionReport
    withdrawals: WithdrawalReport


@dataclass(slots=True)
class WalletTransactionReport:
    """
    Read-only representation of a wallet ledger transaction.
    """

    id: UUID
    wallet_id: UUID
    user_id: UUID

    referral_id: UUID | None
    payment_id: UUID | None

    transaction_reference: str
    transaction_code: str
    transaction_type: str

    amount: Decimal
    balance_before: Decimal
    balance_after: Decimal

    status: str

    description: str | None
    remarks: str | None

    created_at: datetime
    processed_at: datetime | None


# ==========================================================
# Wallet Summary
# ==========================================================


async def get_wallet_summary(
    session: AsyncSession,
    user_id: UUID,
) -> WalletSummary:
    """
    Returns the user's current wallet summary.

    This is a read-only operation.
    """

    wallet_result = await session.execute(
        select(ReferralWalletORM)
        .where(
            ReferralWalletORM.user_id == user_id
        )
    )

    wallet = wallet_result.scalar_one_or_none()

    if wallet is None:
        return WalletSummary(
            balance=Decimal("0.00"),
            available_balance=Decimal("0.00"),
            total_earned=Decimal("0.00"),
            total_withdrawn=Decimal("0.00"),
            pending_withdrawals=Decimal("0.00"),
            total_reversed=Decimal("0.00"),
            eligible_points=0,
            reserved_points=0,
            available_points=0,
            maximum_withdrawal=Decimal("0.00"),
        )

    # ------------------------------------------------------
    # Premium Points
    # ------------------------------------------------------

    points_result = await session.execute(
        select(UserPremiumPointsORM)
        .where(
            UserPremiumPointsORM.user_id == user_id
        )
    )

    points = points_result.scalar_one_or_none()

    eligible_points = (
        points.eligible_points
        if points is not None
        else 0
    )

    reserved_points = (
        points.reserved_points
        if points is not None
        else 0
    )

    available_points = max(
        eligible_points - reserved_points,
        0,
    )

    # ------------------------------------------------------
    # Pending withdrawals
    # ------------------------------------------------------

    pending_result = await session.execute(
        select(
            func.coalesce(
                func.sum(WithdrawalRequestORM.amount),
                Decimal("0.00"),
            )
        )
        .where(
            WithdrawalRequestORM.wallet_id == wallet.id,
            WithdrawalRequestORM.status.in_(
                [
                    WithdrawalStatus.PENDING.value,
                    WithdrawalStatus.PROCESSING.value,
                    WithdrawalStatus.APPROVED.value,
                ]
            ),
        )
    )

    pending_withdrawals = (
        pending_result.scalar_one()
        or Decimal("0.00")
    )

    # ------------------------------------------------------
    # Maximum withdrawal
    #
    # The reporting layer does not invent withdrawal rules.
    # It reports the wallet amount available right now.
    # ------------------------------------------------------

    available_balance = (
        wallet.balance - pending_withdrawals
    )

    if available_balance < Decimal("0.00"):
        available_balance = Decimal("0.00")

    return WalletSummary(
        balance=wallet.balance,
        available_balance=available_balance,
        total_earned=wallet.total_earned,
        total_withdrawn=wallet.total_withdrawn,
        pending_withdrawals=pending_withdrawals,
        total_reversed=wallet.total_reversed,
        eligible_points=eligible_points,
        reserved_points=reserved_points,
        available_points=available_points,
        maximum_withdrawal=available_balance,
    )


# ==========================================================
# Wallet Transactions
# ==========================================================


def _to_transaction_report(
    transaction: WalletTransactionORM,
) -> WalletTransactionReport:
    """
    Converts ORM transaction into reporting representation.
    """

    return WalletTransactionReport(
        id=transaction.id,
        wallet_id=transaction.wallet_id,
        user_id=transaction.user_id,
        referral_id=transaction.referral_id,
        payment_id=transaction.payment_id,
        transaction_reference=transaction.transaction_reference,
        transaction_code=transaction.transaction_code,
        transaction_type=transaction.transaction_type,
        amount=transaction.amount,
        balance_before=transaction.balance_before,
        balance_after=transaction.balance_after,
        status=transaction.status,
        description=transaction.description,
        remarks=transaction.remarks,
        created_at=transaction.created_at,
        processed_at=transaction.processed_at,
    )


async def get_wallet_transactions(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[WalletTransactionReport]:
    """
    Returns wallet transactions for a user.

    Results are newest first.
    """

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    if limit > 500:
        raise ValueError("limit cannot exceed 500.")

    if offset < 0:
        raise ValueError("offset cannot be negative.")

    result = await session.execute(
        select(WalletTransactionORM)
        .where(
            WalletTransactionORM.user_id == user_id
        )
        .order_by(
            WalletTransactionORM.created_at.desc()
        )
        .offset(offset)
        .limit(limit)
    )

    transactions = result.scalars().all()

    return [
        _to_transaction_report(transaction)
        for transaction in transactions
    ]


# ==========================================================
# Commission Report
# ==========================================================


async def get_commission_report(
    session: AsyncSession,
    user_id: UUID,
) -> CommissionReport:
    """
    Returns referral commission statistics.

    Only completed commission credits and reversals
    are included.
    """

    # ------------------------------------------------------
    # Total commission credits
    # ------------------------------------------------------

    credit_result = await session.execute(
        select(
            func.count(WalletTransactionORM.id),
            func.coalesce(
                func.sum(WalletTransactionORM.amount),
                Decimal("0.00"),
            ),
        )
        .where(
            WalletTransactionORM.user_id == user_id,
            WalletTransactionORM.transaction_code
            == WalletTransactionCode.REFERRAL_COMMISSION.value,
            WalletTransactionORM.transaction_type == "credit",
            WalletTransactionORM.status
            == WalletTransactionStatus.COMPLETED.value,
        )
    )

    credit_count, total_commission = (
        credit_result.one()
    )

    # ------------------------------------------------------
    # Commission reversals
    # ------------------------------------------------------

    reversal_result = await session.execute(
        select(
            func.count(WalletTransactionORM.id),
            func.coalesce(
                func.sum(WalletTransactionORM.amount),
                Decimal("0.00"),
            ),
        )
        .where(
            WalletTransactionORM.user_id == user_id,
            WalletTransactionORM.transaction_code
            == WalletTransactionCode.COMMISSION_REVERSAL.value,
            WalletTransactionORM.status.in_(
                [
                    WalletTransactionStatus.COMPLETED.value,
                    WalletTransactionStatus.REVERSED.value,
                ]
            ),
        )
    )

    reversal_count, total_reversed = (
        reversal_result.one()
    )

    total_commission = (
        total_commission
        or Decimal("0.00")
    )

    total_reversed = (
        total_reversed
        or Decimal("0.00")
    )

    net_commission = (
        total_commission - total_reversed
    )

    return CommissionReport(
        transaction_count=int(credit_count or 0),
        total_commission=total_commission,
        total_reversed=total_reversed,
        net_commission=net_commission,
    )


# ==========================================================
# Referral Report
# ==========================================================


async def get_referral_report(
    session: AsyncSession,
    user_id: UUID,
) -> ReferralReport:
    """
    Returns referral relationship statistics.
    """

    total_result = await session.execute(
        select(func.count(ReferralORM.id))
        .where(
            ReferralORM.referrer_user_id == user_id
        )
    )

    total_referrals = int(
        total_result.scalar_one() or 0
    )

    active_result = await session.execute(
        select(func.count(ReferralORM.id))
        .where(
            ReferralORM.referrer_user_id == user_id,
            ReferralORM.status == "active",
        )
    )

    active_referrals = int(
        active_result.scalar_one() or 0
    )

    pending_result = await session.execute(
        select(func.count(ReferralORM.id))
        .where(
            ReferralORM.referrer_user_id == user_id,
            ReferralORM.status == "pending",
        )
    )

    pending_referrals = int(
        pending_result.scalar_one() or 0
    )

    inactive_result = await session.execute(
        select(func.count(ReferralORM.id))
        .where(
            ReferralORM.referrer_user_id == user_id,
            ReferralORM.status == "inactive",
        )
    )

    inactive_referrals = int(
        inactive_result.scalar_one() or 0
    )

    return ReferralReport(
        total_referrals=total_referrals,
        active_referrals=active_referrals,
        pending_referrals=pending_referrals,
        inactive_referrals=inactive_referrals,
    )


# ==========================================================
# Withdrawal Report
# ==========================================================


async def get_withdrawal_report(
    session: AsyncSession,
    user_id: UUID,
) -> WithdrawalReport:
    """
    Returns withdrawal statistics for a user.
    """

    result = await session.execute(
        select(WithdrawalRequestORM)
        .where(
            WithdrawalRequestORM.user_id == user_id
        )
    )

    withdrawals = result.scalars().all()

    pending_amount = Decimal("0.00")
    approved_amount = Decimal("0.00")
    completed_amount = Decimal("0.00")
    rejected_amount = Decimal("0.00")
    cancelled_amount = Decimal("0.00")

    for withdrawal in withdrawals:

        amount = withdrawal.amount

        if withdrawal.status in (
            WithdrawalStatus.PENDING.value,
            WithdrawalStatus.PROCESSING.value,
        ):
            pending_amount += amount

        elif withdrawal.status == WithdrawalStatus.APPROVED.value:
            approved_amount += amount

        elif withdrawal.status == WithdrawalStatus.COMPLETED.value:
            completed_amount += amount

        elif withdrawal.status == WithdrawalStatus.REJECTED.value:
            rejected_amount += amount

        elif withdrawal.status == WithdrawalStatus.CANCELLED.value:
            cancelled_amount += amount

    return WithdrawalReport(
        total_requests=len(withdrawals),
        pending_amount=pending_amount,
        approved_amount=approved_amount,
        completed_amount=completed_amount,
        rejected_amount=rejected_amount,
        cancelled_amount=cancelled_amount,
    )


# ==========================================================
# Complete Finance Report
# ==========================================================


async def get_finance_report(
    session: AsyncSession,
    user_id: UUID,
) -> FinanceReport:
    """
    Returns the complete read-only finance report for a user.
    """

    wallet = await get_wallet_summary(
        session=session,
        user_id=user_id,
    )

    referrals = await get_referral_report(
        session=session,
        user_id=user_id,
    )

    commissions = await get_commission_report(
        session=session,
        user_id=user_id,
    )

    withdrawals = await get_withdrawal_report(
        session=session,
        user_id=user_id,
    )

    return FinanceReport(
        wallet=wallet,
        referrals=referrals,
        commissions=commissions,
        withdrawals=withdrawals,
    )


# ==========================================================
# Commission Transactions
# ==========================================================


async def get_commission_transactions(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[WalletTransactionReport]:
    """
    Returns only referral commission transactions.
    """

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    if limit > 500:
        raise ValueError("limit cannot exceed 500.")

    if offset < 0:
        raise ValueError("offset cannot be negative.")

    result = await session.execute(
        select(WalletTransactionORM)
        .where(
            WalletTransactionORM.user_id == user_id,
            WalletTransactionORM.transaction_code
            == WalletTransactionCode.REFERRAL_COMMISSION.value,
        )
        .order_by(
            WalletTransactionORM.created_at.desc()
        )
        .offset(offset)
        .limit(limit)
    )

    transactions = result.scalars().all()

    return [
        _to_transaction_report(transaction)
        for transaction in transactions
    ]


# ==========================================================
# Referral Commission Count
# ==========================================================


async def count_commission_transactions(
    session: AsyncSession,
    user_id: UUID,
) -> int:
    """
    Counts completed referral commission transactions.
    """

    result = await session.execute(
        select(
            func.count(WalletTransactionORM.id)
        )
        .where(
            WalletTransactionORM.user_id == user_id,
            WalletTransactionORM.transaction_code
            == WalletTransactionCode.REFERRAL_COMMISSION.value,
            WalletTransactionORM.transaction_type == "credit",
            WalletTransactionORM.status
            == WalletTransactionStatus.COMPLETED.value,
        )
    )

    return int(result.scalar_one() or 0)


# ==========================================================
# Read Premium Points
# ==========================================================


async def get_premium_points_summary(
    session: AsyncSession,
    user_id: UUID,
) -> dict[str, int]:
    """
    Returns the current Premium Points balances.

    This function is read-only.
    """

    result = await session.execute(
        select(UserPremiumPointsORM)
        .where(
            UserPremiumPointsORM.user_id == user_id
        )
    )

    points = result.scalar_one_or_none()

    if points is None:
        return {
            "lifetime_points": 0,
            "eligible_points": 0,
            "reserved_points": 0,
            "available_points": 0,
            "total_points_used": 0,
        }

    available_points = max(
        points.eligible_points - points.reserved_points,
        0,
    )

    return {
        "lifetime_points": points.lifetime_points,
        "eligible_points": points.eligible_points,
        "reserved_points": points.reserved_points,
        "available_points": available_points,
        "total_points_used": points.total_points_used,
    }


# ==========================================================
# Premium Point Transaction History
# ==========================================================


async def get_premium_point_transactions(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[PremiumPointTransactionORM]:
    """
    Returns Premium Point ledger records.

    ORM objects are returned intentionally because the reporting
    layer already exposes the complete persistence record.
    """

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    if limit > 500:
        raise ValueError("limit cannot exceed 500.")

    if offset < 0:
        raise ValueError("offset cannot be negative.")

    result = await session.execute(
        select(PremiumPointTransactionORM)
        .where(
            PremiumPointTransactionORM.user_id == user_id
        )
        .order_by(
            PremiumPointTransactionORM.created_at.desc()
        )
        .offset(offset)
        .limit(limit)
    )

    return list(result.scalars().all())
