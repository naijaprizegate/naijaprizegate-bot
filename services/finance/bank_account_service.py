# =========================================================
# services/finance/bank_account_service.py
# =========================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from finance_models import UserBankAccountORM


# ==========================================================
# Get Account
# ==========================================================

async def get_bank_account(
    session: AsyncSession,
    user_id: UUID,
    account_id: UUID,
) -> Optional[UserBankAccountORM]:
    """
    Return an active bank account belonging to the user.
    """

    result = await session.execute(
        select(UserBankAccountORM)
        .where(
            UserBankAccountORM.id == account_id,
            UserBankAccountORM.user_id == user_id,
            UserBankAccountORM.is_active.is_(True),
        )
    )

    return result.scalar_one_or_none()


# ==========================================================
# Get User Accounts
# ==========================================================

async def get_user_bank_accounts(
    session: AsyncSession,
    user_id: UUID,
) -> list[UserBankAccountORM]:
    """
    Return all active bank accounts belonging to the user.
    """

    result = await session.execute(
        select(UserBankAccountORM)
        .where(
            UserBankAccountORM.user_id == user_id,
            UserBankAccountORM.is_active.is_(True),
        )
        .order_by(
            UserBankAccountORM.is_default.desc(),
            UserBankAccountORM.created_at.desc(),
        )
    )

    return list(result.scalars().all())


# ==========================================================
# Get Default Account
# ==========================================================

async def get_default_bank_account(
    session: AsyncSession,
    user_id: UUID,
) -> Optional[UserBankAccountORM]:
    """
    Return the user's active default bank account.
    """

    result = await session.execute(
        select(UserBankAccountORM)
        .where(
            UserBankAccountORM.user_id == user_id,
            UserBankAccountORM.is_active.is_(True),
            UserBankAccountORM.is_default.is_(True),
        )
        .order_by(UserBankAccountORM.created_at.desc())
    )

    return result.scalars().first()


# ==========================================================
# Create Account
# ==========================================================

async def create_bank_account(
    session: AsyncSession,
    user_id: UUID,
    *,
    bank_code: str,
    bank_name: str,
    account_number: str,
    account_name: str,
    is_verified: bool = False,
) -> UserBankAccountORM:
    """
    Create a user's bank account.

    The caller owns the transaction.
    This function does not commit.
    """

    bank_code = str(bank_code).strip()
    bank_name = str(bank_name).strip()
    account_number = str(account_number).strip()
    account_name = str(account_name).strip()

    if not bank_code:
        raise ValueError("Bank code is required.")

    if not bank_name:
        raise ValueError("Bank name is required.")

    if not account_number:
        raise ValueError("Account number is required.")

    if not account_name:
        raise ValueError("Account name is required.")

    existing_accounts = await get_user_bank_accounts(
        session=session,
        user_id=user_id,
    )

    make_default = len(existing_accounts) == 0

    account = UserBankAccountORM(
        user_id=user_id,
        bank_code=bank_code,
        bank_name=bank_name,
        account_number=account_number,
        account_name=account_name,
        is_verified=is_verified,
        verified_at=(
            datetime.now(timezone.utc)
            if is_verified
            else None
        ),
        is_default=make_default,
        is_active=True,
    )

    session.add(account)
    await session.flush()

    return account


# ==========================================================
# Set Default Account
# ==========================================================

async def set_default_bank_account(
    session: AsyncSession,
    user_id: UUID,
    account_id: UUID,
) -> UserBankAccountORM:
    """
    Make one active account the user's default account.

    The account must belong to the user.
    """

    account = await get_bank_account(
        session=session,
        user_id=user_id,
        account_id=account_id,
    )

    if account is None:
        raise ValueError("Bank account not found.")

    await session.execute(
        update(UserBankAccountORM)
        .where(
            UserBankAccountORM.user_id == user_id,
            UserBankAccountORM.is_default.is_(True),
        )
        .values(is_default=False)
    )

    account.is_default = True
    await session.flush()

    return account


# ==========================================================
# Deactivate Account
# ==========================================================

async def deactivate_bank_account(
    session: AsyncSession,
    user_id: UUID,
    account_id: UUID,
) -> None:
    """
    Deactivate a bank account.

    The account remains in the database for audit purposes.
    """

    account = await get_bank_account(
        session=session,
        user_id=user_id,
        account_id=account_id,
    )

    if account is None:
        raise ValueError("Bank account not found.")

    account.is_active = False
    account.is_default = False

    await session.flush()
