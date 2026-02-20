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
    text,
    func,
)
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
    payments = relationship("Payment", back_populates="user")
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

    paid_tries_target = Column(Integer, nullable=False, server_default=text("50000"))
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

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    tx_ref = Column(Text, nullable=False, unique=True)
    status = Column(Text, nullable=True)

    amount = Column(Integer, nullable=False)
    credited_tries = Column(Integer, nullable=True)

    flw_tx_id = Column(Text, nullable=True)
    tg_id = Column(BigInteger, nullable=True)
    username = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="payments")


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

