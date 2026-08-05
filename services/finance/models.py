# ======================================================
# services/finance/models.py
# ======================================================

"""
Data models for the NaijaPrize Finance subsystem.

These dataclasses represent finance-related database records
and business objects.

This module contains NO business logic and NO database queries.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional, Any
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
