
# ==========================================
# services/finance/premium_points.py
# ==========================================

"""
Finance Premium Point service.

This module is responsible ONLY for Finance withdrawal-
qualification points and their lifecycle.

It does NOT:

- display trivia questions;
- determine whether an answer is correct;
- send Telegram messages;
- modify playtrivia.py;
- approve withdrawals;
- process bank accounts;
- initiate provider payments.

Business rules belong here.
Database persistence is handled through the Finance ORM models.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID


# ==========================================================
# Finance Point Rules
# ==========================================================

# Every ₦2,000 of withdrawal value requires 4 Finance Points.
POINT_BLOCK_AMOUNT = Decimal("2000.00")
POINTS_PER_BLOCK = 4


# ==========================================================
# Eligibility Session Rules
# ==========================================================

# The user has one hour from the moment the eligibility
# session starts to complete qualification and submit the
# withdrawal.
ELIGIBILITY_SESSION_DURATION = timedelta(hours=1)


# ==========================================================
# Withdrawal Processing Rules
# ==========================================================

# Once the actual withdrawal request is submitted, Admin
# has 24 hours to act before the request expires and the
# reserved Finance Points are released.
WITHDRAWAL_PROCESSING_DURATION = timedelta(hours=24)


# ==========================================================
# Eligibility Session Statuses
# ==========================================================

ELIGIBILITY_STATUS_ACTIVE = "ACTIVE"
ELIGIBILITY_STATUS_COMPLETED = "COMPLETED"
ELIGIBILITY_STATUS_CANCELLED = "CANCELLED"
ELIGIBILITY_STATUS_EXPIRED = "EXPIRED"

ELIGIBILITY_TERMINAL_STATUSES = frozenset(
    {
        ELIGIBILITY_STATUS_COMPLETED,
        ELIGIBILITY_STATUS_CANCELLED,
        ELIGIBILITY_STATUS_EXPIRED,
    }
)


# ==========================================================
# Withdrawal Statuses
# ==========================================================

WITHDRAWAL_STATUS_PENDING = "PENDING"
WITHDRAWAL_STATUS_APPROVED = "APPROVED"
WITHDRAWAL_STATUS_PROCESSING = "PROCESSING"
WITHDRAWAL_STATUS_COMPLETED = "COMPLETED"
WITHDRAWAL_STATUS_REJECTED = "REJECTED"
WITHDRAWAL_STATUS_CANCELLED = "CANCELLED"
WITHDRAWAL_STATUS_EXPIRED = "EXPIRED"
WITHDRAWAL_STATUS_FAILED = "FAILED"
WITHDRAWAL_STATUS_ON_HOLD = "ON_HOLD"


# ==========================================================
# Service Invariants
# ==========================================================

# Finance point mutations must preserve these invariants:
#
# 1. One qualifying event can award a point only once.
# 2. One eligibility session can reserve points only once.
# 3. Reserved points can be released at most once.
# 4. Reserved points can be consumed at most once.
# 5. eligible_points can never become negative.
# 6. reserved_points can never become negative.
# 7. Completed or expired sessions cannot receive points.
# 8. Reserved points cannot be used for another withdrawal.
# 9. Payment uncertainty must not release reserved points.
# 10. Successful payment must consume reserved points exactly once.


# ==========================================================
# Internal Type Aliases / Shared Values
# ==========================================================

PointAmount = int
MoneyAmount = Decimal
Timestamp = datetime
UserId = UUID
SessionId = UUID
WithdrawalId = UUID


