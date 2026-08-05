# ======================================================
# services/finance/helpers.py
# ======================================================

"""
Helper functions for the NaijaPrize Finance subsystem.

This module contains pure helper functions used by the
finance services.

Rules:

- No database queries
- No Supabase client
- No Telegram code
- No business workflows
- No side effects

Given the same inputs, every function must return the
same output.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from uuid import uuid4

from .constants import (
    MIN_WITHDRAWAL_AMOUNT,
    WITHDRAWAL_BLOCK_AMOUNT,
    POINTS_PER_WITHDRAWAL_BLOCK,
    NAIRA_SYMBOL,
)

from .models import (
    WithdrawalOption,
    WithdrawalPreview,
    WithdrawalEligibility,
)

# ==========================================================
# Premium Point Helpers
# ==========================================================

def calculate_available_points(
    eligible_points: int,
    reserved_points: int,
) -> int:
    """
    Calculates the Premium Points currently available
    for new withdrawal requests.
    """

    if eligible_points < 0:
        raise ValueError("Eligible points cannot be negative.")

    if reserved_points < 0:
        raise ValueError("Reserved points cannot be negative.")

    available = eligible_points - reserved_points

    return max(0, available)
