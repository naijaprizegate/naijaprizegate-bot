# ======================================================
# finance_models.py
# ======================================================

"""
SQLAlchemy ORM models for the NaijaPrize Finance subsystem.

These models map directly to PostgreSQL tables.

They are responsible ONLY for database persistence.

Business logic belongs in services/finance/.

Business dataclasses belong in services/finance/models.py.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    text,
)

from sqlalchemy.dialects.postgresql import UUID

from sqlalchemy.orm import Mapped, mapped_column

from sqlalchemy.sql import func

from base import Base


MONEY = Numeric(12, 2)

# ==========================================================
# Referral Wallet
# ==========================================================

class ReferralWalletORM(Base):
    """
    SQLAlchemy ORM model for the referral_wallets table.
    """

    __tablename__ = "referral_wallets"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    wallet_code: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
    )

    balance: Mapped[Decimal] = mapped_column(
        MONEY,
        nullable=False,
        server_default=text("0.00"),
    )

    total_earned: Mapped[Decimal] = mapped_column(
        MONEY,
        nullable=False,
        server_default=text("0.00"),
    )

    total_withdrawn: Mapped[Decimal] = mapped_column(
        MONEY,
        nullable=False,
        server_default=text("0.00"),
    )

    total_pending_withdrawals: Mapped[Decimal] = mapped_column(
        MONEY,
        nullable=False,
        server_default=text("0.00"),
    )

    total_reversed: Mapped[Decimal] = mapped_column(
        MONEY,
        nullable=False,
        server_default=text("0.00"),
    )

    is_locked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    locked_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    last_transaction_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ==========================================================
# Wallet Transaction
# ==========================================================

class WalletTransactionORM(Base):
    """
    SQLAlchemy ORM model for the wallet_transactions table.
    """

    __tablename__ = "wallet_transactions"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    wallet_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("referral_wallets.id"),
        nullable=False,
    )

    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    referral_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("referrals.id"),
        nullable=True,
    )

    payment_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id"),
        nullable=True,
    )

    transaction_reference: Mapped[str] = mapped_column(
        Text,
        unique=True,
        nullable=False,
    )

    transaction_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    transaction_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
