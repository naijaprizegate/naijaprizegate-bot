# =================================================================
# models.py — CANONICAL MIRROR OF NEON SCHEMA (SQLAlchemy 2.x SAFE)
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

    tries_paid = Column(Integer, nullable=True)
    tries_bonus = Column(Integer, nullable=True)

    premium_spins = Column(Integer, nullable=False)
    total_premium_spins = Column(Integer, nullable=False)

    created_at = Column(DateTime(timezone=True))

    # Relationships
    plays = relationship("Play", back_populates="user")
    payments = relationship("Payment", back_populates="user")
    proofs = relationship("Proof", back_populates="user")
    prize_wins = relationship("PrizeWinner", back_populates="user")


# ================================================================
# GLOBAL COUNTER
# ================================================================
class GlobalCounter(Base):
    __tablename__ = "global_counter"

    id = Column(Integer, primary_key=True)
    paid_tries_total = Column(Integer, nullable=True)


# ================================================================
# GAME STATE
# ================================================================
class GameState(Base):
    __tablename__ = "game_state"

    id = Column(Integer, primary_key=True)
    current_cycle = Column(Integer, nullable=True)
    paid_tries_this_cycle = Column(Integer, nullable=True)
    lifetime_paid_tries = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


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
    created_at = Column(DateTime(timezone=True, server_default=func.now(), nullable=False,))

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

    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))

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
    created_at = Column(DateTime(timezone=True))

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
    created_at = Column(DateTime(timezone=True))


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

    submitted_at = Column(DateTime(timezone=True))
    pending_at = Column(DateTime(timezone=True))
    in_transit_at = Column(DateTime(timezone=True))
    delivered_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))

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
    created_at = Column(DateTime(timezone=True))


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

    created_at = Column(DateTime(timezone=True))


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
    created_at = Column(DateTime(timezone=True))


# ================================================================
# AIRTIME PAYOUTS
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

    phone_number = Column(Text, nullable=True)
    amount = Column(Integer, nullable=False)

    status = Column(Text, nullable=False)

    flutterwave_tx_ref = Column(Text, nullable=True)

    provider = Column(String, nullable=True)
    provider_reference = Column(String, nullable=True)
    provider_ref = Column(Text, nullable=True)
    provider_payload = Column(Text, nullable=True)

    provider_response = Column(JSONB, nullable=True)

    retry_count = Column(Integer, nullable=False)
    last_retry_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True))
    sent_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))


# ================================================================
# NON-AIRTIME WINNERS
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

    reward_type = Column(Text, nullable=False)
    notified_admin = Column(Boolean, nullable=True)

    created_at = Column(DateTime(timezone=True))


# ================================================================
# PREMIUM REWARD ENTRIES
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

    created_at = Column(DateTime(timezone=True))
