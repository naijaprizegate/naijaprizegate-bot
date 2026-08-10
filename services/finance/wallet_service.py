# =====================================================
# services/finance/wallet_service.py
# =====================================================

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
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession

from finance_models import (
    ReferralWalletORM,
    WalletTransactionORM,
)

from .exceptions import (
    InsufficientReservedFundsError,
    InsufficientWalletBalanceError,
    InvalidWalletAmountError,
    WalletAlreadyExistsError,
    WalletNotFoundError,
)
from .enums import (
    WalletTransactionCode,
    WalletTransactionStatus,
    WalletTransactionType,
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

    return _to_referral_wallet(wallet)


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

    return _to_referral_wallet(wallet)


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

    return _to_referral_wallet(wallet)


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
# ORM → Business Model Mapper
# -------------------------------
def _to_referral_wallet(
    wallet: ReferralWalletORM,
) -> ReferralWallet:
    """
    Converts a ReferralWalletORM into a
    ReferralWallet business model.
    """

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
# Credit Wallet
# -------------------------------
async def credit_wallet(
    wallet: ReferralWalletORM,
    amount: Decimal,
) -> ReferralWalletORM:
    """
    Credits a referral wallet.

    This function modifies the tracked ORM object.

    It does not query the database.
    It does not commit the transaction.

    Raises:
        InvalidWalletAmountError
            If the credit amount is not greater than zero.
    """

    if amount <= Decimal("0"):
        raise InvalidWalletAmountError(
            "Credit amount must be greater than zero."
        )

    wallet.balance += amount

    wallet.total_earned += amount

    wallet.last_transaction_at = func.now()

    return wallet


# -------------------------------
# Record Wallet Transaction
# -------------------------------
async def record_wallet_transaction(
    session: AsyncSession,
    wallet: ReferralWalletORM,
    transaction_code: WalletTransactionCode,
    transaction_type: WalletTransactionType,
    amount: Decimal,
    balance_before: Decimal,
    balance_after: Decimal,
    description: str | None = None,
    remarks: str | None = None,
) -> None:
    """
    Records a wallet transaction in the ledger.

    This function adds a WalletTransactionORM object
    to the current session.

    It does not commit the transaction.
    """

    transaction = WalletTransactionORM(
        wallet_id=wallet.id,
        user_id=wallet.user_id,
        transaction_code=transaction_code,
        transaction_type=transaction_type,
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_after,
        status=WalletTransactionStatus.COMPLETED,
        description=description,
        remarks=remarks,
    )

    session.add(transaction)


# -------------------------------
# Debit Wallet
# -------------------------------
async def debit_wallet(
    wallet: ReferralWalletORM,
    amount: Decimal,
) -> ReferralWalletORM:
    """
    Debits a referral wallet.

    This function modifies the tracked ORM object.

    It does not query the database.
    It does not commit the transaction.

    Raises:
        InvalidWalletAmountError
            If the debit amount is not greater than zero.

        InsufficientWalletBalanceError
            If the available wallet balance is insufficient.
    """

    if amount <= Decimal("0"):
        raise InvalidWalletAmountError(
            "Debit amount must be greater than zero."
        )

    available_balance = (
        wallet.balance
        - wallet.total_pending_withdrawals
    )

    if amount > available_balance:
        raise InsufficientWalletBalanceError(
            "Insufficient available wallet balance."
        )

    wallet.balance -= amount

    wallet.last_transaction_at = func.now()

    return wallet


# -------------------------------
# Reserve Wallet Funds
# -------------------------------
async def reserve_wallet_funds(
    wallet: ReferralWalletORM,
    amount: Decimal,
) -> ReferralWalletORM:
    """
    Reserves funds in a referral wallet for a pending withdrawal.

    This function modifies the tracked ORM object.

    It does not query the database.
    It does not commit the transaction.

    Raises:
        InvalidWalletAmountError
            If the reservation amount is not greater than zero.

        InsufficientWalletBalanceError
            If the available wallet balance is insufficient.
    """

    if amount <= Decimal("0"):
        raise InvalidWalletAmountError(
            "Reservation amount must be greater than zero."
        )

    available_balance = (
        wallet.balance
        - wallet.total_pending_withdrawals
    )

    if amount > available_balance:
        raise InsufficientWalletBalanceError(
            "Insufficient available wallet balance."
        )

    wallet.total_pending_withdrawals += amount

    wallet.last_transaction_at = func.now()

    return wallet


# -------------------------------
# Release Reserved Wallet Funds
# -------------------------------
async def release_reserved_wallet_funds(
    wallet: ReferralWalletORM,
    amount: Decimal,
) -> ReferralWalletORM:
    """
    Releases previously reserved wallet funds.

    This function modifies the tracked ORM object.

    It does not query the database.
    It does not commit the transaction.

    Raises:

        InvalidWalletAmountError
            If the release amount is not greater than zero.
        
        InsufficientReservedFundsError
            If the release amount exceeds the
            currently reserved funds.
    """

    if amount <= Decimal("0"):
        raise InvalidWalletAmountError(
            "Release amount must be greater than zero."
        )

    if amount > wallet.total_pending_withdrawals:
        raise InsufficientReservedFundsError(
            "Cannot release more funds than are currently reserved."
        )
    
    wallet.total_pending_withdrawals -= amount

    wallet.last_transaction_at = func.now()

    return wallet


# -------------------------------
# Credit Referral Commission
# -------------------------------
async def credit_referral_commission(
    session: AsyncSession,
    wallet: ReferralWalletORM,
    commission_amount: Decimal,
) -> None:
    """
    Credits referral commission to a referral wallet.

    This workflow:

    1. Validates the commission amount.
    2. Credits the wallet.
    3. Records the wallet transaction.

    This function does NOT commit the transaction.

    The calling workflow is responsible for:

    - Updating referral records.
    - Updating payment records.
    - Awarding Premium Points (if applicable).
    - Sending notifications.
    - Committing or rolling back the transaction.

    Raises:
        InvalidWalletAmountError
            If the commission amount is invalid.
    """

    if commission_amount <= Decimal("0"):
        raise InvalidWalletAmountError(
            "Commission amount must be greater than zero."
        )

    balance_before = wallet.balance

    await credit_wallet(
        wallet=wallet,
        amount=commission_amount,
    )

    balance_after = wallet.balance

    await record_wallet_transaction(
        session=session,
        wallet=wallet,
        transaction_code=WalletTransactionCode.REFERRAL_COMMISSION,
        transaction_type=WalletTransactionType.CREDIT,
        amount=commission_amount,
        balance_before=balance_before,
        balance_after=balance_after,
        description="Referral commission credited.",
        remarks=(
            "Referral commission generated from a "
            "successful payment."
        ),
    )

    # TODO:
        # Replace free-text remarks with structured
        # transaction references when WalletTransactionORM
        # supports:
        #
        # reference_type
        # reference_id
        # metadata

# -------------------------------
# Consume Reserved Wallet Funds
# -------------------------------
async def consume_reserved_wallet_funds(
    wallet: ReferralWalletORM,
    amount: Decimal,
) -> ReferralWalletORM:
    """
    Consumes previously reserved wallet funds.

    This function is used when a pending withdrawal
    is successfully completed.

    Raises:
        InvalidWalletAmountError
            If the amount is not greater than zero.

        InsufficientReservedFundsError
            If the reserved funds are insufficient.
    """

    if amount <= Decimal("0"):
        raise InvalidWalletAmountError(
            "Consume amount must be greater than zero."
        )

    if amount > wallet.total_pending_withdrawals:
        raise InsufficientReservedFundsError(
            "Cannot consume more funds than are currently reserved."
        )

    wallet.total_pending_withdrawals -= amount

    wallet.balance -= amount

    wallet.last_transaction_at = func.now()

    return wallet

