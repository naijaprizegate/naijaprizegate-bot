#=================================================================
# models.py (cleaned + aligned)
#=================================================================
import uuid
from sqlalchemy import (
    Column, String, Integer, ForeignKey, Text, TIMESTAMP, CheckConstraint, Boolean, BigInteger
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


# -----------------------
# 1. Users
# -----------------------
class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tg_id = Column(BigInteger, unique=True, nullable=False)   # ✅ Telegram user ID
    username = Column(String, nullable=True)
    tries_paid = Column(Integer, default=0)
    tries_bonus = Column(Integer, default=0)
    referred_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP, server_default=func.now())
    is_admin = Column(Boolean, default=False, nullable=False)

    # relationships
    referrer = relationship("User", remote_side=[id])
    plays = relationship("Play", back_populates="user")
    payments = relationship("Payment", back_populates="user")
    proofs = relationship("Proof", back_populates="user")


# ----------------------
# 2. Global Counter
# ----------------------
class GlobalCounter(Base):
    __tablename__ = "global_counter"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paid_tries_total = Column(Integer, default=0)


# ----------------------
# 2b. Game State
# ----------------------
class GameState(Base):
    __tablename__ = "game_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    current_cycle = Column(Integer, default=1, nullable=False)  # jackpot cycle #
    paid_tries_this_cycle = Column(Integer, default=0, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


# ----------------------
# 3. Plays
# ----------------------
class Play(Base):
    __tablename__ = "plays"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    result = Column(String, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    __table_args__ = (
        CheckConstraint("result IN ('win','lose','pending')", name="check_play_result"),
    )

    # relationships
    user = relationship("User", back_populates="plays")


# ----------------------
# 4. Payments
# ----------------------
class Payment(Base):
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tx_ref = Column(String, unique=True, nullable=False)
    status = Column(String, default="pending")  # pending / successful / failed / expired
    flw_tx_id = Column(String, nullable=True, index=True)
    amount = Column(Integer, nullable=False)
    credited_tries = Column(Integer, nullable=False, default=0)  # ✅ renamed for consistency
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('pending','successful','failed','expired')", name="check_payment_status"),
    )

    # relationships
    user = relationship("User", back_populates="payments")


# ----------------------
# 5. Proofs
# ----------------------
class Proof(Base):
    __tablename__ = "proofs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    file_id = Column(Text, nullable=False)
    status = Column(String, default="pending")  # pending / approved / rejected
    created_at = Column(TIMESTAMP, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected')", name="check_proof_status"),
    )

    # relationships
    user = relationship("User", back_populates="proofs")


# ----------------------
# 6. Transaction Logs
# ----------------------
class TransactionLog(Base):
    __tablename__ = "transaction_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String, nullable=False)   # e.g. "flutterwave"
    payload = Column(Text, nullable=False)      # raw JSON payload
    created_at = Column(TIMESTAMP, server_default=func.now())
