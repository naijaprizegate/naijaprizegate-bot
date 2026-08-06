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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finance_models import ReferralWalletORM

from .exceptions import WalletAlreadyExistsError
from .models import ReferralWallet

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
