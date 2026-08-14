# ======================================================
# services/finance/enums.py
# ======================================================

"""
Enumerations for the NaijaPrize Finance subsystem.

These enums define the official domain vocabulary
used throughout the finance services.

Using enums instead of string literals provides:

- Type safety
- Better IDE support
- Fewer spelling mistakes
- Easier refactoring
- Clearer business intent
"""

from __future__ import annotations

from enum import StrEnum


# ==========================================================
# Wallet Transaction Type
# ==========================================================

class WalletTransactionType(StrEnum):
    """
    High-level financial direction of a wallet transaction.
    Identifies how a wallet transaction affects
    the wallet ledger.
    """

    CREDIT = "credit"

    DEBIT = "debit"


# ==========================================================
# Wallet Transaction Code
# ==========================================================

class WalletTransactionCode(StrEnum):
    """
    Business reason for a wallet transaction.
    """

    REFERRAL_COMMISSION = "REFERRAL_COMMISSION"

    COMMISSION_REVERSAL = "COMMISSION_REVERSAL"

    WITHDRAWAL_REQUEST = "WITHDRAWAL_REQUEST"

    WITHDRAWAL_APPROVED = "WITHDRAWAL_APPROVED"

    WITHDRAWAL_REJECTED = "WITHDRAWAL_REJECTED"

    WITHDRAWAL_CANCELLED = "WITHDRAWAL_CANCELLED"


# ==========================================================
# Wallet Transaction Status
# ==========================================================

class WalletTransactionStatus(StrEnum):
    """
    Processing status of a wallet transaction.
    """

    PENDING = "pending"

    COMPLETED = "completed"

    FAILED = "failed"

    REVERSED = "reversed"


# ==========================================================
# Withdrawal Status
# ==========================================================

class WithdrawalStatus(StrEnum):
    """
    Lifecycle state of a withdrawal request.
    """

    PENDING = "PENDING"

    APPROVED = "APPROVED"

    REJECTED = "REJECTED"

    CANCELLED = "CANCELLED"

    COMPLETED = "COMPLETED"

    PROCESSING = "PROCESSING"

    FAILED = "FAILED"


# ==========================================================
# Withdrawal Method
# ==========================================================

class WithdrawalMethod(StrEnum):
    """
    Supported withdrawal methods.
    """

    BANK_TRANSFER = "BANK_TRANSFER"


# ==========================================================
# Commission Status
# ==========================================================

class CommissionStatus(StrEnum):
    """
    Processing state of referral commission.
    """

    PENDING = "PENDING"

    PAID = "PAID"

    REVERSED = "REVERSED"

