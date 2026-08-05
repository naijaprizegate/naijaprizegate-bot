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

from sqlalchemy.dialects.postgresql import (
    UUID,
    JSONB,
)

from sqlalchemy.orm import Mapped, mapped_column

from sqlalchemy.sql import func

from base import Base


MONEY = Numeric(12, 2)

LONG_TEXT = Text

TIMESTAMP = DateTime(timezone=True)

JSON_DOCUMENT = JSONB

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
        LONG_TEXT,
        nullable=True,
    )

    last_transaction_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
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
        LONG_TEXT,
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

    amount: Mapped[Decimal] = mapped_column(
        MONEY,
        nullable=False,
    )

    balance_before: Mapped[Decimal] = mapped_column(
        MONEY,
        nullable=False,
    )

    balance_after: Mapped[Decimal] = mapped_column(
        MONEY,
        nullable=False,
    )

    # --------------------------------------
    # Status & Audit Layer
    # ---------------------------------------
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        LONG_TEXT,
        nullable=True,
    )

    remarks: Mapped[str | None] = mapped_column(
        LONG_TEXT,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=func.now(),
    )

    processed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )


# ==========================================================
# User Premium Points
# ==========================================================

class UserPremiumPointsORM(Base):
    """
    SQLAlchemy ORM model for the user_premium_points table.
    """

    __tablename__ = "user_premium_points"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
    )

    lifetime_points: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    eligible_points: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    reserved_points: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    total_points_used: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    # -------------------------------------------------
    # Point History
    # -------------------------------------------------

    last_point_earned_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    last_withdrawal_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    reserved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    points_reset_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    # ---------------------
    # Audit fields
    # --------------------
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=func.now(),
        nullable=False,
    )
    
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ==========================================================
# Premium Point Transaction
# ==========================================================

class PremiumPointTransactionORM(Base):
    """
    SQLAlchemy ORM model for the premium_point_transactions table.
    """

    __tablename__ = "premium_point_transactions"

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

    withdrawal_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("referral_withdrawals.id"),
        nullable=True,
    )

    referral_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("referrals.id"),
        nullable=True,
    )

    reference_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # ----------------------------
    # Ledger Fields
    # ---------------------------
    points: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    transaction_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'COMPLETED'"),
    )

    source: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'SYSTEM'"),
    )

    # ------------------------------------
    # Processing Metadata & Audit Fields
    # ------------------------------------
    
    idempotency_key: Mapped[str | None] = mapped_column(
        LONG_TEXT,
        nullable=True,
    )

    created_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        LONG_TEXT,
        nullable=True,
    )

    remarks: Mapped[str | None] = mapped_column(
        LONG_TEXT,
        nullable=True,
    )

    metadata: Mapped[dict] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=func.now(),
    )
    
