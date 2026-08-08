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

from sqlalchemy import select, func
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
    _get_wallet_orm,
    reserve_wallet_funds,
    release_reserved_wallet_funds,
    consume_reserved_wallet_funds,
    record_wallet_transaction,
)

from .exceptions import (
    WithdrawalNotFoundError,
    WithdrawalApprovalError,
    WithdrawalCompletionError,
    WithdrawalRejectionError,
    WithdrawalCancellationError,
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
        transaction_type=WalletTransactionType.RESERVATION,
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

    Returns:
        list[WithdrawalRequest]
            A list of pending withdrawal requests.
            Returns an empty list if none exist.
    """

    return await get_withdrawals_by_status(
        session=session,
        status=WithdrawalStatus.PENDING,
    )


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



# -------------------------------
# Complete Withdrawal
# -------------------------------
async def complete_withdrawal(
    session: AsyncSession,
    withdrawal: WithdrawalRequestORM,
) -> None:
    """
    Completes an approved withdrawal.

    This workflow:

    1. Consumes the reserved wallet funds.
    2. Updates the wallet withdrawal statistics.
    3. Records the wallet transaction.
    4. Marks the withdrawal as completed.

    This function does not commit the transaction.

    Raises:
        WithdrawalCompletionError
            If the withdrawal cannot be completed.
    """

    if withdrawal.status != WithdrawalStatus.APPROVED:
        raise WithdrawalCompletionError(
            "Only approved withdrawals can be completed."
        )

    wallet = await _get_wallet_orm(
        session=session,
        user_id=withdrawal.user_id,
    )

    balance_before = wallet.balance

    await consume_reserved_wallet_funds(
        wallet=wallet,
        amount=withdrawal.amount,
    )

    wallet.total_withdrawn += withdrawal.amount

    balance_after = wallet.balance

    await record_wallet_transaction(
        session=session,
        wallet=wallet,
        transaction_code=WalletTransactionCode.WITHDRAWAL_COMPLETED,
        transaction_type=WalletTransactionType.DEBIT,
        amount=withdrawal.amount,
        balance_before=balance_before,
        balance_after=balance_after,
        description="Withdrawal completed.",
        remarks=(
            "Reserved funds successfully paid out."
        ),
    )

    withdrawal.status = WithdrawalStatus.COMPLETED
    withdrawal.completed_at = func.now()


# -------------------------------
# Reject Withdrawal
# -------------------------------
async def reject_withdrawal(
    session: AsyncSession,
    withdrawal: WithdrawalRequestORM,
    rejected_by: UUID,
    reason: str,
) -> None:
    """
    Rejects a pending withdrawal request.

    This workflow:

    1. Releases the reserved wallet funds.
    2. Records the reservation release.
    3. Marks the withdrawal as rejected.

    This function does not commit the transaction.

    Raises:
        WithdrawalRejectionError
            If the withdrawal cannot be rejected.
    """

    if withdrawal.status != WithdrawalStatus.PENDING:
        raise WithdrawalRejectionError(
            "Only pending withdrawals can be rejected."
        )

    wallet = await _get_wallet_orm(
        session=session,
        user_id=withdrawal.user_id,
    )

    balance_before = wallet.balance

    await release_reserved_wallet_funds(
        wallet=wallet,
        amount=withdrawal.amount,
    )

    balance_after = wallet.balance

    await record_wallet_transaction(
        session=session,
        wallet=wallet,
        transaction_code=WalletTransactionCode.WITHDRAWAL_REJECTED,
        transaction_type=WalletTransactionType.RESERVATION,
        amount=withdrawal.amount,
        balance_before=balance_before,
        balance_after=balance_after,
        description="Withdrawal rejected.",
        remarks=reason,
    )

    withdrawal.status = WithdrawalStatus.REJECTED
    withdrawal.rejected_by = rejected_by
    withdrawal.rejected_at = func.now()
    withdrawal.rejection_reason = reason


# -------------------------------
# Cancel Withdrawal
# -------------------------------
async def cancel_withdrawal(
    session: AsyncSession,
    withdrawal: WithdrawalRequestORM,
    cancelled_by: UUID,
    reason: str,
) -> None:
    """
    Cancels a pending withdrawal request.

    This workflow:

    1. Releases the reserved wallet funds.
    2. Records the cancellation event.
    3. Marks the withdrawal as cancelled.

    This function does not commit the transaction.

    Raises:
        WithdrawalCancellationError
            If the withdrawal cannot be cancelled.
    """

    if withdrawal.status != WithdrawalStatus.PENDING:
        raise WithdrawalCancellationError(
            "Only pending withdrawals can be cancelled."
        )

    wallet = await _get_wallet_orm(
        session=session,
        user_id=withdrawal.user_id,
    )

    balance_before = wallet.balance

    await release_reserved_wallet_funds(
        wallet=wallet,
        amount=withdrawal.amount,
    )

    balance_after = wallet.balance

    await record_wallet_transaction(
        session=session,
        wallet=wallet,
        transaction_code=WalletTransactionCode.WITHDRAWAL_CANCELLED,
        transaction_type=WalletTransactionType.RESERVATION,
        amount=withdrawal.amount,
        balance_before=balance_before,
        balance_after=balance_after,
        description="Withdrawal cancelled by user.",
        remarks=reason,
    )

    withdrawal.status = WithdrawalStatus.CANCELLED
    withdrawal.cancelled_by = cancelled_by
    withdrawal.cancelled_at = func.now()
    withdrawal.cancellation_reason = reason


# -------------------------------
# Get Withdrawals By User
# -------------------------------
async def get_withdrawals_by_user(
    session: AsyncSession,
    user_id: UUID,
) -> list[WithdrawalRequest]:
    """
    Retrieves all withdrawal requests belonging to a user.

    Returns:
        list[WithdrawalRequest]
            A list of the user's withdrawal requests.
            Returns an empty list if none exist.
    """

    statement = (
        select(WithdrawalRequestORM)
        .where(
            WithdrawalRequestORM.user_id == user_id
        )
        .order_by(
            WithdrawalRequestORM.created_at.asc(),
            WithdrawalRequestORM.id.asc(),
        )
    )

    result = await session.execute(statement)

    withdrawals = result.scalars().all()

    return [
        _to_withdrawal_request(withdrawal)
        for withdrawal in withdrawals
    ]


# -------------------------------
# Get Withdrawals By Status
# -------------------------------
async def get_withdrawals_by_status(
    session: AsyncSession,
    status: WithdrawalStatus,
) -> list[WithdrawalRequest]:
    """
    Retrieves all withdrawal requests matching
    the specified withdrawal status.

    Returns:
        list[WithdrawalRequest]
            A list of matching withdrawal requests.
            Returns an empty list if none exist.
    """

    statement = (
        select(WithdrawalRequestORM)
        .where(
            WithdrawalRequestORM.status == status
        )
        .order_by(
            WithdrawalRequestORM.created_at.asc(),
            WithdrawalRequestORM.id.asc(),
        )
    )

    result = await session.execute(statement)

    withdrawals = result.scalars().all()

    return [
        _to_withdrawal_request(withdrawal)
        for withdrawal in withdrawals
    ]


# --------------------------------
# Get Pending Withdrawal Count
# --------------------------------
async def get_pending_withdrawal_count(
    session: AsyncSession,
) -> int:
    """
    Retrieves the number of pending withdrawal requests.

    Returns:
        int
            The number of pending withdrawal requests.
    """

    statement = (
        select(func.count())
        .select_from(WithdrawalRequestORM)
        .where(
            WithdrawalRequestORM.status
            == WithdrawalStatus.PENDING
        )
    )

    result = await session.execute(statement)

    return result.scalar_one()
