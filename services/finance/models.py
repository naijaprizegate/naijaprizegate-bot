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
