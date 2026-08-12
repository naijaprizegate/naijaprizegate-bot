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
from uuid import UUID, uuid4

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
    JSONB,
    UUID as PG_UUID,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from base import Base


# ======================================================
# Shared Column Types
# ======================================================

MONEY = Numeric(18, 2)

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
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
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
# Referral Relationship
# ==========================================================

class ReferralORM(Base):
    """
    SQLAlchemy ORM model for the referrals table.

    Stores the referral relationship only.
    Commission financial history belongs in wallet_transactions.
    """

    __tablename__ = "referrals"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    referrer_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    referred_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    referral_code_used: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'"),
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=func.now(),
    )

    activated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(
        LONG_TEXT,
        nullable=True,
    )


# ==========================================================
# Withdrawal Request
# ==========================================================

class WithdrawalRequestORM(Base):
    """
    SQLAlchemy ORM model for the referral_withdrawals table.

    This model represents the actual withdrawal request and
    its complete lifecycle through:

        pending
        approved
        rejected
        cancelled
        completed

    Persistence definitions belong here.
    Withdrawal business rules belong in
    services/finance/withdrawal_service.py.
    """

    __tablename__ = "referral_withdrawals"

    # ------------------------------------------------------
    # Identity
    # ------------------------------------------------------

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    wallet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("referral_wallets.id"),
        nullable=False,
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    # ------------------------------------------------------
    # Withdrawal Amount
    # ------------------------------------------------------

    amount: Mapped[Decimal] = mapped_column(
        MONEY,
        nullable=False,
    )

    # ------------------------------------------------------
    # Withdrawal Method
    # ------------------------------------------------------

    withdrawal_method: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # ------------------------------------------------------
    # Bank Account Snapshot
    # ------------------------------------------------------

    account_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    account_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    bank_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    # ------------------------------------------------------
    # Linked Saved Bank Account
    # ------------------------------------------------------

    bank_account_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_bank_accounts.id"),
        nullable=True,
    )

    # ------------------------------------------------------
    # Withdrawal Status
    # ------------------------------------------------------

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'pending'"),
    )

    # ------------------------------------------------------
    # Request Lifecycle
    # ------------------------------------------------------
    requested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=func.now(),
    )

    # ------------------------------------------------------
    # Premium Points
    # ------------------------------------------------------

    points_used: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    # ------------------------------------------------------
    # Wallet Transaction
    # ------------------------------------------------------

    wallet_transaction_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("wallet_transactions.id"),
        nullable=True,
    )

    # ------------------------------------------------------
    # Payment / Provider References
    # ------------------------------------------------------

    payment_reference: Mapped[str | None] = mapped_column(
        LONG_TEXT,
        nullable=True,
    )

    provider_reference: Mapped[str | None] = mapped_column(
        LONG_TEXT,
        nullable=True,
    )

    # ------------------------------------------------------
    # Approval Audit
    # ------------------------------------------------------

    approved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    # ------------------------------------------------------
    # Rejection Audit
    # ------------------------------------------------------

    rejected_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    rejected_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    rejection_reason: Mapped[str | None] = mapped_column(
        LONG_TEXT,
        nullable=True,
    )

    # ------------------------------------------------------
    # Cancellation Audit
    # ------------------------------------------------------

    cancelled_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    cancellation_reason: Mapped[str | None] = mapped_column(
        LONG_TEXT,
        nullable=True,
    )

    # ------------------------------------------------------
    # Completion / Payment Audit
    # ------------------------------------------------------

    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    # ------------------------------------------------------
    # Administrative Note
    # ------------------------------------------------------

    admin_note: Mapped[str | None] = mapped_column(
        LONG_TEXT,
        nullable=True,
    )


    # ------------------------------------------------------
    # Audit Timestamps
    # ------------------------------------------------------

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
# Withdrawal Eligibility Session
# ==========================================================

class WithdrawalEligibilitySessionORM(Base):
    """
    SQLAlchemy ORM model for a withdrawal eligibility session.

    This model represents the period during which a user
    earns the Finance Premium Points required for a specific
    withdrawal amount.

    The eligibility session begins before the actual
    withdrawal request is created.

    Lifecycle:

        ACTIVE
            ↓
        COMPLETED

        ACTIVE
            ↓
        CANCELLED

        ACTIVE
            ↓
        EXPIRED

    Finance withdrawal-qualification rules belong in
    services/finance/premium_points.py.
    """

    __tablename__ = "withdrawal_eligibility_sessions"

    # ------------------------------------------------------
    # Identity
    # ------------------------------------------------------

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # ------------------------------------------------------
    # User / Wallet
    # ------------------------------------------------------

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    wallet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("referral_wallets.id"),
        nullable=False,
    )

    # ------------------------------------------------------
    # Withdrawal Qualification
    # ------------------------------------------------------

    requested_amount: Mapped[Decimal] = mapped_column(
        MONEY,
        nullable=False,
    )

    required_points: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    points_earned: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    # ------------------------------------------------------
    # Session Status
    # ------------------------------------------------------

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # ------------------------------------------------------
    # Session Lifecycle
    # ------------------------------------------------------

    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=func.now(),
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP,
        nullable=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
    )

    # ------------------------------------------------------
    # Actual Withdrawal Request
    # ------------------------------------------------------

    withdrawal_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("referral_withdrawals.id"),
        nullable=True,
    )

    # ------------------------------------------------------
    # Audit Timestamps
    # ------------------------------------------------------

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
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    wallet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("referral_wallets.id"),
        nullable=False,
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    referral_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("referrals.id"),
        nullable=True,
    )

    payment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("payments.id"),
        nullable=True,
    )

    transaction_reference: Mapped[str] = mapped_column(
        LONG_TEXT,
        unique=True,
        nullable=False,
        default=lambda: f"NP-WTX-{uuid4().hex.upper()}",
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
    # --------------------------------------

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
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
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
    # ---------------------

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
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    withdrawal_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("referral_withdrawals.id"),
        nullable=True,
    )

    # ------------------------------------------------------
    # Withdrawal Eligibility Session
    # ------------------------------------------------------

    eligibility_session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("withdrawal_eligibility_sessions.id"),
        nullable=True,
    )

    referral_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("referrals.id"),
        nullable=True,
    )

    reference_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )

    # ----------------------------
    # Ledger Fields
    # ----------------------------

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
        PG_UUID(as_uuid=True),
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

