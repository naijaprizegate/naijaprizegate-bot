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

import secrets
import string

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
# Module Constants
# ==========================================================

ZERO = Decimal("0")


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


# ==========================================================
# Withdrawal Helpers
# ==========================================================

def calculate_maximum_withdrawal(
    available_points: int,
) -> Decimal:
    """
    Calculates the maximum amount a user can withdraw
    based on currently available Premium Points.
    """

    if available_points < 0:
        raise ValueError("Available points cannot be negative.")

    blocks = available_points // POINTS_PER_WITHDRAWAL_BLOCK

    maximum = WITHDRAWAL_BLOCK_AMOUNT * Decimal(blocks)

    return maximum


def calculate_required_points(
    withdrawal_amount: Decimal,
) -> int:
    """
    Calculates the Premium Points required
    for a withdrawal amount.
    """

    if withdrawal_amount < ZERO:
        raise ValueError("Withdrawal amount cannot be negative.")

    if withdrawal_amount % WITHDRAWAL_BLOCK_AMOUNT != ZERO:
        raise ValueError(
            "Withdrawal amount must be a multiple "
            "of the withdrawal block amount."
        )

    blocks = int(
        withdrawal_amount // WITHDRAWAL_BLOCK_AMOUNT
    )

    required_points = (
        blocks * POINTS_PER_WITHDRAWAL_BLOCK
    )

    return required_points


def generate_withdrawal_options(
    wallet_balance: Decimal,
    maximum_withdrawal: Decimal,
) -> list[WithdrawalOption]:
    """
    Generates every valid withdrawal option
    available to the user.
    """
    
    if wallet_balance < ZERO:
        raise ValueError("Wallet balance cannot be negative.")

    if maximum_withdrawal < ZERO:
        raise ValueError("Maximum withdrawal cannot be negative.")

    if maximum_withdrawal > wallet_balance:
        raise ValueError(
            "Maximum withdrawal cannot exceed "
            "wallet balance."
        )
        
    if maximum_withdrawal == ZERO:
        return []
    
    options: list[WithdrawalOption] = []

    blocks = int(
        maximum_withdrawal // WITHDRAWAL_BLOCK_AMOUNT
    )

    for block in range(1, blocks + 1):

        amount = WITHDRAWAL_BLOCK_AMOUNT * Decimal(block)

        points_required = calculate_required_points(amount)

        remaining_balance = wallet_balance - amount

        options.append(
            WithdrawalOption(
                amount=amount,
                points_required=points_required,
                available_after_withdrawal=remaining_balance,
            )
        )

    return options


def preview_withdrawal(
    wallet_balance: Decimal,
    available_points: int,
    withdrawal_amount: Decimal,
) -> WithdrawalPreview:
    """
    Builds a preview of a withdrawal before it is submitted.
    """

    if wallet_balance < ZERO:
        raise ValueError("Wallet balance cannot be negative.")

    if available_points < 0:
        raise ValueError("Available points cannot be negative.")

    if withdrawal_amount < ZERO:
        raise ValueError("Withdrawal amount cannot be negative.")

    if withdrawal_amount > wallet_balance:
        raise ValueError(
            "Withdrawal amount cannot exceed wallet balance."
        )

    points_used = calculate_required_points(
        withdrawal_amount
    )
    
    if points_used > available_points:
        raise ValueError(
            "Insufficient available Premium Points."
        )

    wallet_after = wallet_balance - withdrawal_amount

    points_after = available_points - points_used

    return WithdrawalPreview(
        amount=withdrawal_amount,
        wallet_before=wallet_balance,
        wallet_after=wallet_after,
        points_before=available_points,
        points_used=points_used,
        points_after=points_after,
    )
    

# ==========================================================
# Wallet Helpers
# ==========================================================
WALLET_PREFIX = "NPW"

WALLET_ALPHABET = (
    "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
)


def generate_wallet_code() -> str:
    """
    Generates a public wallet code.

    Example:

        NPW-8X4K-2M9Q
    """

    first_group = "".join(
        secrets.choice(WALLET_ALPHABET)
        for _ in range(4)
    )

    second_group = "".join(
        secrets.choice(WALLET_ALPHABET)
        for _ in range(4)
    )

    return (
        f"{WALLET_PREFIX}-"
        f"{first_group}-"
        f"{second_group}"
    )
