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
