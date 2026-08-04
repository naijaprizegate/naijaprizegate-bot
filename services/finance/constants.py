# ======================================================
# services/finance/constants.py
# ======================================================

"""
Financial subsystem constants for NaijaPrize.

This module contains immutable business rules used throughout
the referral and finance services.

No business logic should be written here.
"""

from decimal import Decimal

# ==========================================================
# Referral Commission
# ==========================================================

REFERRAL_COMMISSION_PERCENT = Decimal("0.05")   # 5%

MINIMUM_QUALIFYING_PAYMENT = Decimal("100.00")

# ==========================================================
# Withdrawal Rules
# ==========================================================

MIN_WITHDRAWAL_AMOUNT = Decimal("2000.00")

WITHDRAWAL_BLOCK_AMOUNT = Decimal("2000.00")

POINTS_PER_WITHDRAWAL_BLOCK = 4

# ==========================================================
# Wallet Transaction Types
# ==========================================================

TXN_COMMISSION_CREDIT = "commission_credit"

TXN_COMMISSION_REVERSAL = "commission_reversal"

TXN_WITHDRAWAL_REQUEST = "withdrawal_request"

TXN_WITHDRAWAL_APPROVED = "withdrawal_approved"

TXN_WITHDRAWAL_REJECTED = "withdrawal_rejected"

TXN_ADMIN_ADJUSTMENT = "admin_adjustment"

# ==========================================================
# Withdrawal Status
# ==========================================================

WITHDRAWAL_PENDING = "pending"

WITHDRAWAL_APPROVED = "approved"

WITHDRAWAL_REJECTED = "rejected"

WITHDRAWAL_CANCELLED = "cancelled"

# ==========================================================
# Wallet Balance Types
# ==========================================================

BALANCE_AVAILABLE = "available"

BALANCE_RESERVED = "reserved"

BALANCE_TOTAL = "total"

# ==========================================================
# Premium Points
# ==========================================================

POINTS_AVAILABLE = "available"

POINTS_RESERVED = "reserved"

POINTS_RESET_REASON_WITHDRAWAL = "withdrawal"

# ==========================================================
# Payment Status
# ==========================================================

PAYMENT_PENDING = "pending"

PAYMENT_CONFIRMED = "confirmed"

PAYMENT_CANCELLED = "cancelled"

PAYMENT_FAILED = "failed"

PAYMENT_REFUNDED = "refunded"

PAYMENT_REVERSED = "reversed"


# ==========================================================
# Withdrawal Limits
# ==========================================================

MAX_PENDING_WITHDRAWALS = 1

# ===================================
# CURRENCY
# ===================================
NAIRA_SYMBOL = "₦"
