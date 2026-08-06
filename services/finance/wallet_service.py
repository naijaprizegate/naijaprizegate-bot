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

from uuid import UUID
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finance_models import ReferralWalletORM

from .exceptions import (
    WalletAlreadyExistsError,
    WalletNotFoundError,
)
from .models import ReferralWallet
from .helpers import generate_wallet_code

# -------------------------------
# Check Wallet
# -------------------------------
async def wallet_exists(
    session: AsyncSession,
    user_id: UUID,
) -> bool:
    """
    Returns True if the user already has
    a referral wallet.
    """

    statement = (
        select(ReferralWalletORM)
        .where(
            ReferralWalletORM.user_id == user_id
        )
    )

    result = await session.execute(statement)

    wallet = result.scalar_one_or_none()

    return wallet is not None


# -------------------------------
# Create Wallet
# -------------------------------
async def create_wallet(
    session: AsyncSession,
    user_id: UUID,
) -> ReferralWallet:
    """
    Creates a referral wallet for a user.

    Raises:
        WalletAlreadyExistsError
            If the user already has a wallet.
    """

    if await wallet_exists(session, user_id):
        raise WalletAlreadyExistsError(
            f"Referral wallet already exists for user {user_id}"
        )

    wallet = ReferralWalletORM(
        user_id=user_id,
        wallet_code=generate_wallet_code(),
    )

    session.add(wallet)

    await session.commit()

    await session.refresh(wallet)

    return ReferralWallet(
        id=wallet.id,
        user_id=wallet.user_id,
        wallet_code=wallet.wallet_code,
        balance=wallet.balance,
        total_earned=wallet.total_earned,
        total_withdrawn=wallet.total_withdrawn,
        total_pending_withdrawals=wallet.total_pending_withdrawals,
        total_reversed=wallet.total_reversed,
        is_locked=wallet.is_locked,
        locked_reason=wallet.locked_reason,
        last_transaction_at=wallet.last_transaction_at,
        created_at=wallet.created_at,
        updated_at=wallet.updated_at,
    )


# -------------------------------
# Get Wallet
# -------------------------------
async def get_wallet(
    session: AsyncSession,
    user_id: UUID,
) -> ReferralWallet:
    """
    Retrieves a user's referral wallet.

    Raises:
        WalletNotFoundError
            If the wallet does not exist.
    """

    statement = (
        select(ReferralWalletORM)
        .where(
            ReferralWalletORM.user_id == user_id
        )
    )

    result = await session.execute(statement)

    wallet = result.scalar_one_or_none()

    if wallet is None:
        raise WalletNotFoundError(
            f"Referral wallet not found for user {user_id}"
        )

    return ReferralWallet(
        id=wallet.id,
        user_id=wallet.user_id,
        wallet_code=wallet.wallet_code,
        balance=wallet.balance,
        total_earned=wallet.total_earned,
        total_withdrawn=wallet.total_withdrawn,
        total_pending_withdrawals=wallet.total_pending_withdrawals,
        total_reversed=wallet.total_reversed,
        is_locked=wallet.is_locked,
        locked_reason=wallet.locked_reason,
        last_transaction_at=wallet.last_transaction_at,
        created_at=wallet.created_at,
        updated_at=wallet.updated_at,
    )


# -------------------------------
# Get Wallet By Code
# -------------------------------
async def get_wallet_by_code(
    session: AsyncSession,
    wallet_code: str,
) -> ReferralWallet:
    """
    Retrieves a referral wallet using its public wallet code.

    Raises:
        WalletNotFoundError
            If the wallet does not exist.
    """

    statement = (
        select(ReferralWalletORM)
        .where(
            ReferralWalletORM.wallet_code == wallet_code
        )
    )

    result = await session.execute(statement)

    wallet = result.scalar_one_or_none()

    if wallet is None:
        raise WalletNotFoundError(
            f"Referral wallet not found: {wallet_code}"
        )

    return ReferralWallet(
        id=wallet.id,
        user_id=wallet.user_id,
        wallet_code=wallet.wallet_code,
        balance=wallet.balance,
        total_earned=wallet.total_earned,
        total_withdrawn=wallet.total_withdrawn,
        total_pending_withdrawals=wallet.total_pending_withdrawals,
        total_reversed=wallet.total_reversed,
        is_locked=wallet.is_locked,
        locked_reason=wallet.locked_reason,
        last_transaction_at=wallet.last_transaction_at,
        created_at=wallet.created_at,
        updated_at=wallet.updated_at,
    )


# -------------------------------
# Internal ORM Helper
# -------------------------------
async def _get_wallet_orm(
    session: AsyncSession,
    user_id: UUID,
) -> ReferralWalletORM:
    """
    Retrieves the tracked wallet ORM object.

    Internal helper used by write operations.

    Raises:
        WalletNotFoundError
            If the wallet does not exist.
    """

    statement = (
        select(ReferralWalletORM)
        .where(
            ReferralWalletORM.user_id == user_id
        )
    )

    result = await session.execute(statement)

    wallet = result.scalar_one_or_none()

    if wallet is None:
        raise WalletNotFoundError(
            f"Referral wallet not found for user {user_id}"
        )

    return wallet


# -------------------------------
# Credit Wallet
# -----------------------------
async def credit_wallet(
    session: AsyncSession,
    user_id: UUID,
    amount: Decimal,
) -> ReferralWallet:

