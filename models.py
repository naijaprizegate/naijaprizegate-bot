# =================================================================
# models.py — CANONICAL MIRROR OF NEON SCHEMA (SQLAlchemy 2.x SAFE)
# Updated: cycle support (cycles + user_cycle_stats + cycle_id cols)
# =================================================================

from sqlalchemy import (
    Column,
    String,
    Integer,
    ForeignKey,
    Text,
    Boolean,
    BigInteger,
    DateTime,
    Date,
    text,
    func,
    Numeric,
    Index,
    CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy import PrimaryKeyConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from base import Base


# ================================================================
# USERS
# ================================================================
class User(Base):
    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    tg_id = Column(BigInteger, nullable=False, unique=True)
    username = Column(Text, nullable=True)
    full_name = Column(Text, nullable=True)

    tries_paid = Column(Integer, nullable=False, server_default=text("0"))
    tries_bonus = Column(Integer, nullable=False, server_default=text("0"))

    # NOTE:
    # premium_spins was previously used as points.
    # After cycle migration, points should move to user_cycle_stats.points.
    # Keep these columns only if they already exist in DB.
    premium_spins = Column(Integer, nullable=False, server_default=text("0"))
    total_premium_spins = Column(Integer, nullable=False, server_default=text("0"))

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Trivia progress per category (sequential question system)
    entertainment_q_index = Column(Integer, default=0)
    history_q_index = Column(Integer, default=0)
    football_q_index = Column(Integer, default=0)
    geography_q_index = Column(Integer, default=0)
    english_q_index = Column(Integer, default=0)
    sciences_q_index = Column(Integer, default=0)
    mathematics_q_index = Column(Integer, default=0)

    # Relationships
    plays = relationship("Play", back_populates="user")
    payments = relationship(
        "Payment",
        back_populates="user",
        foreign_keys="Payment.user_id",
    )
    proofs = relationship("Proof", back_populates="user")
    prize_wins = relationship("PrizeWinner", back_populates="user")

    # New relationship: cycle stats
    cycle_stats = relationship("UserCycleStat", back_populates="user")


# ================================================================
# TRIVIA PROGRESS (per-user, per-category)
# ================================================================
class TriviaProgress(Base):
    __tablename__ = "trivia_progress"

    tg_id = Column(BigInteger, nullable=False)
    category_key = Column(Text, nullable=False)

    next_index = Column(Integer, nullable=False, server_default=text("0"))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("tg_id", "category_key", name="trivia_progress_pkey"),
    )


# ================================================================
# CYCLES (NEW)
# ================================================================
class Cycle(Base):
    __tablename__ = "cycles"

    id = Column(Integer, primary_key=True)  # cycle number: 1,2,3,...

    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)

    paid_tries_target = Column(
        Integer,
        nullable=False,
        server_default=text("2000000"),
    )
    paid_tries_final = Column(Integer, nullable=False, server_default=text("0"))

    winner_user_id = Column(UUID(as_uuid=True), nullable=True)
    winner_tg_id = Column(BigInteger, nullable=True)
    winner_points = Column(Integer, nullable=True)
    winner_decided_at = Column(DateTime(timezone=True), nullable=True)


# ================================================================
# USER CYCLE STATS (NEW) — Points per cycle per user
# ================================================================
class UserCycleStat(Base):
    __tablename__ = "user_cycle_stats"

    cycle_id = Column(Integer, ForeignKey("cycles.id"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)

    tg_id = Column(BigInteger, nullable=False)
    points = Column(Integer, nullable=False, server_default=text("0"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="cycle_stats")
    cycle = relationship("Cycle")


# ================================================================
# GLOBAL COUNTER (legacy; prefer game_state.paid_tries_this_cycle)
# ================================================================
class GlobalCounter(Base):
    __tablename__ = "global_counter"

    id = Column(Integer, primary_key=True)
    paid_tries_total = Column(Integer, nullable=False, server_default=text("0"))


# ================================================================
# GAME STATE
# ================================================================
class GameState(Base):
    __tablename__ = "game_state"

    id = Column(Integer, primary_key=True)

    current_cycle = Column(Integer, nullable=False, server_default=text("1"))
    paid_tries_this_cycle = Column(Integer, nullable=False, server_default=text("0"))
    lifetime_paid_tries = Column(Integer, nullable=False, server_default=text("0"))

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


# ================================================================
# PLAYS
# ================================================================
class Play(Base):
    __tablename__ = "plays"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    result = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="plays")


# ================================================================
# PAYMENTS
# ================================================================
class Payment(Base):
    __tablename__ = "payments"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    tg_id = Column(
        BigInteger,
        nullable=False,
    )

    payment_provider = Column(
        String,
        nullable=False,
    )

    tx_ref = Column(
        Text,
        nullable=False,
        unique=True,
    )

    gateway_transaction_id = Column(
        Text,
        nullable=True,
    )

    gateway_status = Column(
        String,
        nullable=True,
    )

    gateway_response = Column(
        JSONB,
        nullable=True,
    )

    amount = Column(
        Numeric(18, 2),
        nullable=False,
    )

    currency = Column(
        String(3),
        nullable=False,
        server_default=text("'NGN'"),
    )

    payment_type_code = Column(
        String,
        nullable=False,
    )

    status = Column(
        String,
        nullable=False,
        server_default=text("'PENDING'"),
    )

    verification_attempts = Column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    referral_commission_processed = Column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    verified_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    credited_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    payment_metadata = Column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    verified_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    processed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    failure_reason = Column(
        Text,
        nullable=True,
    )

    idempotency_key = Column(
        Text,
        nullable=True,
    )

    user = relationship(
        "User",
        back_populates="payments",
        foreign_keys=[user_id],
    )


# ================================================================
# PROOFS
# ================================================================
class Proof(Base):
    __tablename__ = "proofs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    file_id = Column(Text, nullable=False)
    status = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="proofs")


# ================================================================
# TRANSACTION LOGS
# ================================================================
class TransactionLog(Base):
    __tablename__ = "transaction_logs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    provider = Column(Text, nullable=False)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ================================================================
# PRIZE WINNERS
# ================================================================
class PrizeWinner(Base):
    __tablename__ = "prize_winners"

    id = Column(Integer, primary_key=True)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    tg_id = Column(BigInteger, nullable=False)
    choice = Column(Text, nullable=False)

    delivery_status = Column(Text, nullable=True)

    submitted_at = Column(DateTime(timezone=True), server_default=func.now())
    pending_at = Column(DateTime(timezone=True), nullable=True)
    in_transit_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    delivery_data = Column(JSONB, nullable=True)

    user = relationship("User", back_populates="prize_wins")


# ================================================================
# TRIVIA QUESTIONS
# ================================================================
class TriviaQuestion(Base):
    __tablename__ = "trivia_questions"

    id = Column(Integer, primary_key=True)
    category = Column(Text, nullable=False)
    question = Column(Text, nullable=False)
    options = Column(JSONB, nullable=False)
    answer = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ================================================================
# USER ANSWERS
# ================================================================
class UserAnswer(Base):
    __tablename__ = "user_answers"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("trivia_questions.id"), nullable=True)

    selected = Column(Text, nullable=False)
    correct = Column(Boolean, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ================================================================
# SPIN RESULTS
# ================================================================
class SpinResult(Base):
    __tablename__ = "spin_results"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    tg_id = Column(BigInteger, nullable=True)

    spin_type = Column(Text, nullable=False)
    outcome = Column(Text, nullable=False)

    extra_data = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ================================================================
# AIRTIME PAYOUTS (add cycle_id)
# ================================================================
class AirtimePayout(Base):
    __tablename__ = "airtime_payouts"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    tg_id = Column(BigInteger, nullable=False)

    # ✅ NEW
    cycle_id = Column(Integer, ForeignKey("cycles.id"), nullable=True)

    phone_number = Column(Text, nullable=True)
    amount = Column(Integer, nullable=False)

    status = Column(Text, nullable=False)

    flutterwave_tx_ref = Column(Text, nullable=True)

    provider = Column(String, nullable=True)
    provider_reference = Column(String, nullable=True)
    provider_ref = Column(Text, nullable=True)
    provider_payload = Column(Text, nullable=True)

    provider_response = Column(JSONB, nullable=True)

    retry_count = Column(Integer, nullable=False, server_default=text("0"))
    last_retry_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


# ================================================================
# NON-AIRTIME WINNERS (add cycle_id)
# ================================================================
class NonAirtimeWinner(Base):
    __tablename__ = "non_airtime_winners"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    tg_id = Column(BigInteger, nullable=False)

    # ✅ NEW
    cycle_id = Column(Integer, ForeignKey("cycles.id"), nullable=True)

    reward_type = Column(Text, nullable=False)
    notified_admin = Column(Boolean, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ================================================================
# PREMIUM REWARD ENTRIES (add cycle_id)
# ================================================================
class PremiumRewardEntry(Base):
    __tablename__ = "premium_reward_entries"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    tg_id = Column(BigInteger, nullable=False)

    # ✅ NEW
    cycle_id = Column(Integer, ForeignKey("cycles.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ================================================================
# PRIZE CATALOGUE (public prize listings)
# Separate from PrizeWinner, which records actual winners.
# ================================================================
class PrizeCatalogue(Base):
    __tablename__ = "prize_catalogue"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False, server_default=text("''"))
    conditions = Column(Text, nullable=False, server_default=text("''"))

    # Telegram photo file_id; nullable so an entry can be created
    # before its picture is uploaded.
    photo_file_id = Column(Text, nullable=True)

    is_active = Column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ================================================================
# FLUTTERWAVE SETTLEMENT RECONCILIATION
# Independent merchant collection and settlement tracking
# ================================================================


class FlutterwaveSettlement(Base):
    __tablename__ = "flutterwave_settlements"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    provider = Column(Text, nullable=False, server_default=text("'FLUTTERWAVE'"))
    flutterwave_settlement_id = Column(Text, nullable=False)

    currency = Column(Text, nullable=False, server_default=text("'NGN'"))

    transaction_date = Column(DateTime(timezone=True), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    processed_date = Column(DateTime(timezone=True), nullable=True)

    settlement_status = Column(Text, nullable=True)

    gross_amount = Column(Numeric(18, 2), nullable=True)
    app_fee = Column(Numeric(18, 2), nullable=True)
    merchant_fee = Column(Numeric(18, 2), nullable=True)
    stamp_duty = Column(Numeric(18, 2), nullable=True)
    chargeback_amount = Column(Numeric(18, 2), nullable=True)
    refund_amount = Column(Numeric(18, 2), nullable=True)
    vat_amount = Column(Numeric(18, 2), nullable=True)
    net_amount = Column(Numeric(18, 2), nullable=True)

    transaction_count = Column(Integer, nullable=True)

    processor_ref = Column(Text, nullable=True)
    disburse_ref = Column(Text, nullable=True)
    destination = Column(JSONB, nullable=True)

    flag_message = Column(Text, nullable=True)

    raw_payload = Column(JSONB, nullable=True)

    first_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_synced_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "flutterwave_settlement_id",
            name="uq_flutterwave_settlement",
        ),
    )


class FlutterwaveCollectionTransaction(Base):
    __tablename__ = "flutterwave_collection_transactions"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    provider = Column(Text, nullable=False, server_default=text("'FLUTTERWAVE'"))

    flutterwave_transaction_id = Column(Text, nullable=True)
    tx_ref = Column(Text, nullable=False)

    local_payment_source = Column(Text, nullable=True)
    local_payment_id = Column(Text, nullable=True)

    settlement_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "flutterwave_settlements.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    currency = Column(Text, nullable=False, server_default=text("'NGN'"))

    transaction_status = Column(Text, nullable=True)
    payment_entity = Column(Text, nullable=True)
    customer_email = Column(Text, nullable=True)

    transaction_date = Column(DateTime(timezone=True), nullable=True)
    settlement_date = Column(DateTime(timezone=True), nullable=True)

    charged_amount = Column(Numeric(18, 2), nullable=True)
    amount_settled = Column(Numeric(18, 2), nullable=True)

    app_fee = Column(Numeric(18, 2), nullable=True)
    merchant_fee = Column(Numeric(18, 2), nullable=True)
    stamp_duty = Column(Numeric(18, 2), nullable=True)
    vat_amount = Column(Numeric(18, 2), nullable=True)

    is_local_payment_matched = Column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    reconciliation_status = Column(
        Text,
        nullable=False,
        server_default=text("'UNMATCHED'"),
    )

    raw_payload = Column(JSONB, nullable=True)

    first_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_synced_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            """
            local_payment_source IS NULL
            OR local_payment_source IN (
                'payments',
                'jamb_payments',
                'waec_payments',
                'mockjamb_payments',
                'mockwaec_payments',
                'university_payments'
            )
            """,
            name="chk_flutterwave_local_payment_source",
        ),
        Index(
        "idx_flutterwave_collection_provider_tx_ref",
        "provider",
        "tx_ref",
    ),
        Index(
            "uq_flutterwave_collection_provider_transaction_id",
            "provider",
            "flutterwave_transaction_id",
            unique=True,
            postgresql_where=text(
                "flutterwave_transaction_id IS NOT NULL"
            ),
        ),
        Index(
            "idx_flutterwave_collection_local_payment",
            "local_payment_source",
            "local_payment_id",
            postgresql_where=text(
                """
                local_payment_source IS NOT NULL
                AND local_payment_id IS NOT NULL
                """
            ),
        ),
        Index(
            "idx_flutterwave_collection_transaction_date",
            "transaction_date",
        ),
        Index(
            "idx_flutterwave_collection_settlement_id",
            "settlement_id",
        ),
        Index(
            "idx_flutterwave_collection_reconciliation_status",
            "reconciliation_status",
        ),
    )


class FlutterwaveReconciliationRun(Base):
    __tablename__ = "flutterwave_reconciliation_runs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    run_type = Column(Text, nullable=False)
    run_status = Column(
        Text,
        nullable=False,
        server_default=text("'RUNNING'"),
    )

    reporting_date = Column(Date, nullable=True)

    period_start = Column(DateTime(timezone=True), nullable=True)
    period_end = Column(DateTime(timezone=True), nullable=True)

    currency = Column(Text, nullable=False, server_default=text("'NGN'"))

    total_transactions = Column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    matched_transactions = Column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    unmatched_transactions = Column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    unresolved_discrepancies = Column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    gross_collections = Column(Numeric(18, 2), nullable=True)
    confirmed_fees = Column(Numeric(18, 2), nullable=True)
    estimated_fees = Column(Numeric(18, 2), nullable=True)
    confirmed_vat = Column(Numeric(18, 2), nullable=True)
    estimated_vat = Column(Numeric(18, 2), nullable=True)

    expected_net_amount = Column(Numeric(18, 2), nullable=True)
    confirmed_net_amount = Column(Numeric(18, 2), nullable=True)
    net_difference = Column(Numeric(18, 2), nullable=True)

    summary = Column(JSONB, nullable=True)

    error_message = Column(Text, nullable=True)

    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "idx_flutterwave_reconciliation_reporting_date",
            "reporting_date",
        ),
        Index(
            "idx_flutterwave_reconciliation_run_status",
            "run_status",
        ),
        Index(
            "idx_flutterwave_reconciliation_run_started_at",
            "started_at",
        ),
    )
