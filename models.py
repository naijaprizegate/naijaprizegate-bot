#=================================================================
# models.py (cleaned + expanded with trivia + spin reward models)
#=================================================================
import uuid
from uuid import uuid4
from sqlalchemy import (
    Column, String, Integer, ForeignKey, Text, TIMESTAMP, CheckConstraint,
    Boolean, BigInteger, JSON, DateTime, text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from base import Base  # from base.py


# ================================================================
# 1. USERS
# ================================================================
class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tg_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String, nullable=True)

    tries_paid = Column(Integer, default=0)
    tries_bonus = Column(Integer, default=0)

    referred_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP, server_default=func.now())
    is_admin = Column(Boolean, default=False, nullable=False)

    # Winner data (unchanged)
    choice = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    # phone = Column(String, nullable=True)
    phone_number = Column("phone", String(20), nullable=True)
    address = Column(String, nullable=True)
    delivery_status = Column(String, nullable=True, default="Pending")

    winner_stage = Column(String, nullable=True)
    winner_data = Column(JSON, nullable=True, default={})

    # Relationships
    referrer = relationship("User", remote_side=[id])
    plays = relationship("Play", back_populates="user")
    payments = relationship("Payment", back_populates="user")
    proofs = relationship("Proof", back_populates="user")
    prize_wins = relationship("PrizeWinner", back_populates="user", cascade="all, delete-orphan")


# ================================================================
# 2. GLOBAL COUNTER
# ================================================================
class GlobalCounter(Base):
    __tablename__ = "global_counter"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paid_tries_total = Column(Integer, default=0)


# ================================================================
# 2b. GAME STATE
# ================================================================
class GameState(Base):
    __tablename__ = "game_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    current_cycle = Column(Integer, default=1, nullable=False)
    paid_tries_this_cycle = Column(Integer, default=0, nullable=False)
    lifetime_paid_tries = Column(Integer, default=0, nullable=False)

    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


# ================================================================
# 3. PLAYS
# ================================================================
class Play(Base):
    __tablename__ = "plays"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    result = Column(String, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    __table_args__ = (
        CheckConstraint("result IN ('win','lose','pending')", name="check_play_result"),
    )

    user = relationship("User", back_populates="plays")


# ================================================================
# 4. PAYMENTS
# ================================================================
class Payment(Base):
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)

    tx_ref = Column(String, unique=True, nullable=False)
    status = Column(String, default="pending")
    flw_tx_id = Column(String, nullable=True, index=True)

    amount = Column(Integer, nullable=False)
    credited_tries = Column(Integer, nullable=False, default=0)

    tg_id = Column(BigInteger, nullable=True, index=True)
    username = Column(String, nullable=True)

    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','successful','failed','expired')",
            name="check_payment_status"
        ),
    )

    user = relationship("User", back_populates="payments")


# ================================================================
# 5. PROOFS
# ================================================================
class Proof(Base):
    __tablename__ = "proofs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    file_id = Column(Text, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(TIMESTAMP, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected')", name="check_proof_status"),
    )

    user = relationship("User", back_populates="proofs")


# ================================================================
# 6. TRANSACTION LOG
# ================================================================
class TransactionLog(Base):
    __tablename__ = "transaction_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String, nullable=False)
    payload = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())


# ================================================================
# 7. PRIZE WINNERS (existing Top-Tier Campaign Reward users)
# ================================================================
class PrizeWinner(Base):
    __tablename__ = "prize_winners"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tg_id = Column(BigInteger, nullable=False, index=True)
    choice = Column(String, nullable=False)
    delivery_status = Column(String, nullable=True)

    submitted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    pending_at = Column(DateTime(timezone=True), nullable=True)
    in_transit_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    delivery_data = Column(JSON, nullable=True, default={})

    user = relationship("User", back_populates="prize_wins", lazy="joined")

    def to_csv_row(self):
        return [
            self.user.full_name or "",
            self.user.username or "",
            self.user.phone or "",
            self.choice,
            self.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if self.submitted_at else "",
            self.delivery_status or "",
        ]

    @staticmethod
    def csv_headers():
        return [
            "Full Name",
            "Telegram Username",
            "Phone",
            "Prize",
            "Date Won",
            "Delivery Status",
        ]


# =================================================================
# NEW TABLE 1 — Trivia Questions
# =================================================================
class TriviaQuestion(Base):
    __tablename__ = "trivia_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String, nullable=False)
    question = Column(Text, nullable=False)
    options = Column(JSON, nullable=False)
    answer = Column(String, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())


# =================================================================
# NEW TABLE 2 — User Trivia Answers
# =================================================================
class UserAnswer(Base):
    __tablename__ = "user_answers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    question_id = Column(Integer, ForeignKey("trivia_questions.id", ondelete="CASCADE"))
    selected = Column(String, nullable=False)
    correct = Column(Boolean, default=False)

    created_at = Column(TIMESTAMP, server_default=func.now())


# =================================================================
# NEW TABLE 3 — Spin Results (Every Spin Recorded)
# =================================================================
class SpinResult(Base):
    __tablename__ = "spin_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    tg_id = Column(BigInteger, nullable=True)

    spin_type = Column(String, nullable=False)       # basic / premium
    outcome = Column(String, nullable=False)         # lose / Top-Tier Campaign Reward / airtime / speaker / earpod
    created_at = Column(TIMESTAMP, server_default=func.now())

    extra_data = Column(JSON, nullable=True, default={})  # optional metadata


# =================================================================
# NEW TABLE 4 — Airtime Payouts
# =================================================================
class AirtimePayout(Base):
    __tablename__ = "airtime_payouts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    tg_id = Column(BigInteger, nullable=False)

    phone_number = Column(String, nullable=False)
    amount = Column(Integer, default=100)  # default airtime reward

    status = Column(String, default="pending")  # pending / sent
    created_at = Column(TIMESTAMP, server_default=func.now())
    sent_at = Column(TIMESTAMP, nullable=True)


# =================================================================
# NEW TABLE 5 — Non-Airtime Winners (earpods / speakers)
# =================================================================
class NonAirtimeWinner(Base):
    __tablename__ = "non_airtime_winners"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    tg_id = Column(BigInteger, nullable=False)

    reward_type = Column(String, nullable=False)  # "earpod", "speaker"
    notified_admin = Column(Boolean, default=False)

    created_at = Column(TIMESTAMP, server_default=func.now())


# ============================================================
# premium reward tier ENTRIES  (FIXED — proper UUID types)
# ============================================================
class PremiumRewardEntry(Base):
    __tablename__ = "premium_reward_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # IMPORTANT: match the User.id type exactly
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    tg_id = Column(BigInteger, nullable=False)

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=text("NOW()"),
        nullable=False
    )
