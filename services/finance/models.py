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
    reserved_at: Optional[datetime]
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

    created_at: datetime

    metadata: dict[str, Any] = field(default_factory=dict)


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
# Withdrawal Request
# ==========================================================

@dataclass(slots=True)
class WithdrawalRequest:
    """
    Represents a user's referral wallet withdrawal request.

    This is the canonical business representation used by
    the withdrawal service.

    The model contains:

    1. Core withdrawal workflow fields used by
       withdrawal_service.py.

    2. Additional financial/provider metadata associated
       with the withdrawal lifecycle.
    """

    # ------------------------------------------------------
    # Identity
    # ------------------------------------------------------

    id: UUID
    wallet_id: UUID
    user_id: UUID

    # ------------------------------------------------------
    # Withdrawal Request
    # ------------------------------------------------------

    amount: Decimal

    withdrawal_method: str

    account_name: str
    account_number: str
    bank_name: str

    status: str

    # ------------------------------------------------------
    # Approval Audit
    # ------------------------------------------------------

    approved_by: Optional[UUID]
    approved_at: Optional[datetime]

    # ------------------------------------------------------
    # Rejection Audit
    # ------------------------------------------------------

    rejected_by: Optional[UUID]
    rejected_at: Optional[datetime]
    rejection_reason: Optional[str]

    # ------------------------------------------------------
    # Cancellation Audit
    # ------------------------------------------------------

    cancelled_by: Optional[UUID]
    cancelled_at: Optional[datetime]
    cancellation_reason: Optional[str]

    # ------------------------------------------------------
    # Completion Audit
    # ------------------------------------------------------

    completed_at: Optional[datetime]

    # ------------------------------------------------------
    # Audit Timestamps
    #
    # Required fields must appear before fields with defaults
    # in a dataclass.
    # ------------------------------------------------------

    created_at: datetime
    updated_at: datetime

    # ------------------------------------------------------
    # Financial / Provider Metadata
    # ------------------------------------------------------

    wallet_transaction_id: Optional[UUID] = None

    payment_reference: Optional[str] = None

    provider_reference: Optional[str] = None

    admin_note: Optional[str] = None

    paid_at: Optional[datetime] = None

    bank_account_id: Optional[UUID] = None

    points_used: int = 0


# ==========================================================
# Backward-Compatible Alias
# ==========================================================

# ReferralWithdrawal was the previous business-model name.
#
# Keep the alias temporarily so existing Finance code that
# imports ReferralWithdrawal does not immediately break while
# the withdrawal subsystem is being migrated to the canonical
# WithdrawalRequest model.

ReferralWithdrawal = WithdrawalRequest


# ==========================================================
# Withdrawal Option
# ==========================================================

@dataclass(slots=True)
class WithdrawalOption:
    """
    Represents a single withdrawal option available to a user.
    """

    amount: Decimal
    points_required: int
    available_after_withdrawal: Decimal


# ==========================================================
# Withdrawal Eligibility
# ==========================================================

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


# ==========================================================
# Wallet Summary
# ==========================================================

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


# ==========================================================
# Withdrawal Preview
# ==========================================================

@dataclass(slots=True)
class WithdrawalPreview:
    """
    Preview of a withdrawal before the user submits the request.
    """

    amount: Decimal

    wallet_before: Decimal
    wallet_after: Decimal

    points_before: int
    points_used: int
    points_after: int

