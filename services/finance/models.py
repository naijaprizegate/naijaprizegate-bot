# ======================================================
# services/finance/models.py
# ======================================================

"""
Data models for the NaijaPrize Finance subsystem.

These dataclasses represent finance-related database records
and business objects.

This module contains NO business logic and NO database queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

# ==========================================================
# Referral Wallet
# ==========================================================

@dataclass(slots=True)
class ReferralWallet:
    """
    Represents a user's referral wallet.
    """

    id: UUID
    user_id: UUID
    wallet_code: str

    balance: Decimal
    total_earned: Decimal
    total_withdrawn: Decimal
    total_pending_withdrawals: Decimal
    total_reversed: Decimal

    is_locked: bool
    locked_reason: Optional[str]

    last_transaction_at: Optional[datetime]

    created_at: datetime
    updated_at: datetime


# ==========================================================
# Wallet Transaction
# ==========================================================

@dataclass(slots=True)
class WalletTransaction:
    """
    Represents a single wallet ledger transaction.
    """

    id: UUID
    wallet_id: UUID
    user_id: UUID

    referral_id: Optional[UUID]
    payment_id: Optional[UUID]

    transaction_reference: str

    transaction_code: str
    transaction_type: str

    amount: Decimal

    balance_before: Decimal
    balance_after: Decimal

    status: str

    description: Optional[str]
    remarks: Optional[str]

    created_at: datetime
    processed_at: Optional[datetime]


# ==========================================================
# User Premium Points
# ==========================================================

@dataclass(slots=True)
class UserPremiumPoints:
    """
    Represents a user's Premium Points account.
    """

    id: UUID
    user_id: UUID

    lifetime_points: int
    eligible_points: int
    reserved_points: int
    total_points_used: int

    last_point_earned_at: Optional[datetime]
    last_withdrawal_at: Optional[datetime]
    points_reset_at: Optional[datetime]

    created_at: datetime
    updated_at: datetime


# ==========================================================
# Premium Point Transaction
# ==========================================================

@dataclass(slots=True)
class PremiumPointTransaction:
    """
    Represents a single Premium Point ledger transaction.
    """

    id: UUID
    user_id: UUID

    points: int

    transaction_code: str
    status: str

    withdrawal_id: Optional[UUID]
    referral_id: Optional[UUID]
    reference_id: Optional[UUID]

    idempotency_key: Optional[str]

    source: str

    created_by: Optional[UUID]

    description: Optional[str]
    remarks: Optional[str]

    metadata: dict[str, Any] = field(default_factory=dict)

    created_at: datetime


# ==========================================================
# User Bank Account
# ==========================================================

@dataclass(slots=True)
class UserBankAccount:
    """
    Represents a user's registered bank account.
    """

    id: UUID
    user_id: UUID

    bank_code: str
    bank_name: str

    account_number: str
    account_name: str

    is_verified: bool
    verified_at: Optional[datetime]

    is_default: bool
    is_active: bool

    created_at: datetime
    updated_at: datetime


# ==========================================================
# Referral Withdrawal
# ==========================================================

@dataclass(slots=True)
class ReferralWithdrawal:
    """
    Represents a user's referral withdrawal request.
    """

    id: UUID
    wallet_id: UUID
    user_id: UUID

    wallet_transaction_id: Optional[UUID]

    amount: Decimal

    status: str

    payment_reference: Optional[str]
    provider_reference: Optional[str]

    approved_by: Optional[UUID]

    rejection_reason: Optional[str]
    admin_note: Optional[str]

    requested_at: datetime
    approved_at: Optional[datetime]
    paid_at: Optional[datetime]

    created_at: datetime
    updated_at: datetime

    bank_account_id: Optional[UUID]

    points_used: int


# ==========================================================
# Payment (Finance View)
# ==========================================================

@dataclass(slots=True)
class Payment:
    """
    Finance view of a payment record.

    Contains only the fields required by the finance subsystem.
    """

    id: UUID
    user_id: UUID

    amount: Decimal

    payment_type_code: str

    status: str

    referral_commission_processed: bool

    verified_at: Optional[datetime]
    processed_at: Optional[datetime]


# ==========================================================
# Business Models
# ==========================================================

@dataclass(slots=True)
class WithdrawalOption:
    """
    Represents a single withdrawal option available to a user.
    """

    amount: Decimal
    points_required: int
    available_after_withdrawal: Decimal


@dataclass(slots=True)
class WithdrawalEligibility:
    """
    Represents a user's withdrawal eligibility.
    """

    wallet_balance: Decimal

    eligible_points: int
    reserved_points: int
    available_points: int

    maximum_withdrawal: Decimal

    is_eligible: bool

    options: list[WithdrawalOption] = field(default_factory=list)


@dataclass(slots=True)
class WalletSummary:
    """
    Summary of a user's referral wallet dashboard.
    """

    balance: Decimal
    available_balance: Decimal

    total_earned: Decimal
    total_withdrawn: Decimal
    pending_withdrawals: Decimal
    total_reversed: Decimal

    eligible_points: int
    reserved_points: int
    available_points: int

    maximum_withdrawal: Decimal
