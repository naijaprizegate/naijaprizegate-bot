# =====================================================
# services/finance/constants.py
# =====================================================

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

# Generation 1 (direct referral) earns 5%.
REFERRAL_COMMISSION_PERCENT = Decimal("0.05")

# Generations 2 through 5 each earn an additional 1%.
MULTI_GENERATION_COMMISSION_PERCENT = Decimal("0.01")

# Maximum number of referral generations that can earn commission.
MAX_REFERRAL_GENERATIONS = 5

MINIMUM_QUALIFYING_PAYMENT = Decimal("100.00")


# ==========================================================
# Withdrawal Rules
# ==========================================================

MIN_WITHDRAWAL_AMOUNT = Decimal("2000.00")

WITHDRAWAL_BLOCK_AMOUNT = Decimal("2000.00")

POINTS_PER_WITHDRAWAL_BLOCK = 4


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


# =========================================================
# Decimal Precisionn
# =========================================================

ZERO_AMOUNT = Decimal("0.00")

