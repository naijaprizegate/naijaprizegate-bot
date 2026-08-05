# ======================================================
# services/finance/wallet_service.py
# ======================================================

"""
Wallet service for the NaijaPrize Finance subsystem.

This module is responsible for all wallet-related
database operations and business workflows.

Responsibilities:

- Create referral wallets
- Retrieve wallet information
- Credit wallet balances
- Debit wallet balances
- Reserve funds for withdrawals
- Release reserved funds
- Record wallet transactions
- Build wallet summaries

This module does NOT contain referral logic,
Premium Point logic, or withdrawal approval logic.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import AsyncSessionLocal

from .exceptions import (
    FinanceError,
    InsufficientWalletBalanceError,
)

from .helpers import (
    calculate_available_points,
    calculate_maximum_withdrawal,
)

from .models import (
    ReferralWallet,
    WalletSummary,
    WalletTransaction,
)
