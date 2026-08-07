# ======================================================
# services/finance/withdrawal_service.py
# ======================================================

"""
Withdrawal service for the NaijaPrize Finance subsystem.

This module is responsible for all withdrawal-related
database operations and business workflows.

Responsibilities:

- Create withdrawal requests
- Retrieve withdrawal requests
- Approve withdrawals
- Reject withdrawals
- Cancel withdrawals
- Complete withdrawals
- Record withdrawal events

This module orchestrates wallet operations.

Wallet balance mutations are delegated to
wallet_service.py.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from finance_models import (
    ReferralWalletORM,
    WithdrawalRequestORM,
)

from .enums import (
    WithdrawalMethod,
    WithdrawalStatus,
    WalletTransactionCode,
    WalletTransactionType,
)

from .models import WithdrawalRequest

from .wallet_service import (
    reserve_wallet_funds,
    release_reserved_wallet_funds,
    consume_reserved_wallet_funds,
    record_wallet_transaction,
)


# -------------------------------
# Create Withdrawal Request
# -------------------------------
async def create_withdrawal_request(
    session: AsyncSession,
    wallet: ReferralWalletORM,
    amount: Decimal,
    withdrawal_method: WithdrawalMethod,
    account_name: str,
    account_number: str,
    bank_name: str,
) -> WithdrawalRequest:
    """
    Creates a new withdrawal request.

    This workflow:

    1. Reserves wallet funds.
    2. Creates the withdrawal request.
    3. Records the reservation event.
    4. Returns the business model.

    This function does NOT commit the transaction.

    The calling workflow is responsible for
    committing or rolling back the transaction.

    Raises:
        InvalidWalletAmountError
            If the withdrawal amount is invalid.

        InsufficientWalletBalanceError
            If available wallet balance is insufficient.
    """

    await reserve_wallet_funds(
        wallet=wallet,
        amount=amount,
    )

    withdrawal = WithdrawalRequestORM(
        wallet_id=wallet.id,
        user_id=wallet.user_id,
        amount=amount,
        withdrawal_method=withdrawal_method,
        account_name=account_name,
        account_number=account_number,
        bank_name=bank_name,
        status=WithdrawalStatus.PENDING,
    )

    session.add(withdrawal)

    await record_wallet_transaction(
        session=session,
        wallet=wallet,
        transaction_code=WalletTransactionCode.WITHDRAWAL_RESERVED,
        transaction_type=WalletTransactionType.DEBIT,
        amount=amount,
        balance_before=wallet.balance,
        balance_after=wallet.balance,
        description="Withdrawal request created.",
        remarks=(
            "Funds reserved pending withdrawal approval."
        ),
    )

    await session.flush()

    return _to_withdrawal_request(withdrawal)


# -------------------------------
# ORM → Business Model
# -------------------------------
def _to_withdrawal_request(
    withdrawal: WithdrawalRequestORM,
) -> WithdrawalRequest:
    """
    Converts a WithdrawalRequestORM into
    the WithdrawalRequest business model.
    """

    return WithdrawalRequest(
        id=withdrawal.id,
        wallet_id=withdrawal.wallet_id,
        user_id=withdrawal.user_id,
        amount=withdrawal.amount,
        withdrawal_method=withdrawal.withdrawal_method,
        account_name=withdrawal.account_name,
        account_number=withdrawal.account_number,
        bank_name=withdrawal.bank_name,
        status=withdrawal.status,
        approved_by=withdrawal.approved_by,
        approved_at=withdrawal.approved_at,
        rejected_by=withdrawal.rejected_by,
        rejected_at=withdrawal.rejected_at,
        rejection_reason=withdrawal.rejection_reason,
        completed_at=withdrawal.completed_at,
        created_at=withdrawal.created_at,
        updated_at=withdrawal.updated_at,
    )


# -------------------------------
# Get Withdrawal
# -------------------------------
async def get_withdrawal(
    session: AsyncSession,
    withdrawal_id: UUID,
) -> WithdrawalRequest:
    """
    Retrieves a withdrawal request by its ID.

    Raises:
        WithdrawalNotFoundError
            If the withdrawal request does not exist.
    """

    statement = (
        select(WithdrawalRequestORM)
        .where(
            WithdrawalRequestORM.id == withdrawal_id
        )
    )

    result = await session.execute(statement)

    withdrawal = result.scalar_one_or_none()

    if withdrawal is None:
        raise WithdrawalNotFoundError(
            f"Withdrawal request not found: {withdrawal_id}"
        )

    return _to_withdrawal_request(withdrawal)


# -------------------------------
# Get Pending Withdrawals
# -------------------------------
async def get_pending_withdrawals(
    session: AsyncSession,
) -> list[WithdrawalRequest]:
    """
    Retrieves all pending withdrawal requests.
    """

    statement = (
        select(WithdrawalRequestORM)
        .where(
            WithdrawalRequestORM.status
            == WithdrawalStatus.PENDING
        )
        .order_by(
            WithdrawalRequestORM.created_at.asc()
        )
    )

    result = await session.execute(statement)

    withdrawals = result.scalars().all()

    return [
        _to_withdrawal_request(withdrawal)
        for withdrawal in withdrawals
    ]


# -------------------------------
# Approve Withdrawal
# -------------------------------
async def approve_withdrawal(
    session: AsyncSession,
    withdrawal: WithdrawalRequestORM,
    approved_by: UUID,
) -> None:
    """
    Approves a pending withdrawal request.

    This function updates the withdrawal status
    but does not complete the withdrawal.

    The transaction is not committed.

    Raises:
        WithdrawalApprovalError
            If the withdrawal cannot be approved.
    """

    if withdrawal.status != WithdrawalStatus.PENDING:
        raise WithdrawalApprovalError(
            "Only pending withdrawals can be approved."
        )

    withdrawal.status = WithdrawalStatus.APPROVED
    withdrawal.approved_by = approved_by
    withdrawal.approved_at = func.now()
