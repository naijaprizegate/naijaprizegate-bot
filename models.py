#=================================================================
# models.py (cleaned + circular-import-free)
#=================================================================
import uuid
from sqlalchemy import (
    Column, String, Integer, ForeignKey, Text, TIMESTAMP, CheckConstraint,
    Boolean, BigInteger, JSON, DateTime
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from base import Base  # ✅ import from base.py (not db.py)


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

    # 🏆 Winner-related fields
    choice = Column(String, nullable=True)         # e.g. "iPhone 16 Pro Max" or "iPhone 17 Pro Max"
    full_name = Column(String, nullable=True)      # Winner's real name
    phone = Column(String, nullable=True)          # Winner's phone number
    address = Column(String, nullable=True)        # Delivery address
    delivery_status = Column(String, nullable=True, default="Pending")  # "Pending", "In Transit", "Delivered"

    # 💾 Persistent form progress fields
    winner_stage = Column(String, nullable=True)   # "ask_name", "ask_phone", "ask_address"
    winner_data = Column(JSON, nullable=True, default={})  # Partial form data storage

    # 🧩 Relationships
    referrer = relationship("User", remote_side=[id])
    plays = relationship("Play", back_populates="user")
    payments = relationship("Payment", back_populates="user")
    proofs = relationship("Proof", back_populates="user")
    prize_wins = relationship("PrizeWinner", back_populates="user", cascade="all, delete-orphan")

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
    current_cycle = Column(Integer, default=1, nullable=False)  # 🌀 Jackpot cycle number
    paid_tries_this_cycle = Column(Integer, default=0, nullable=False)  # 🎟️ Paid tries in current cycle
    lifetime_paid_tries = Column(Integer, default=0, nullable=False)  # 🌍 Total paid tries since launch

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
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)

    tx_ref = Column(String, unique=True, nullable=False)
    status = Column(String, default="pending")  # pending / successful / failed / expired
    flw_tx_id = Column(String, nullable=True, index=True)

    amount = Column(Integer, nullable=False)
    credited_tries = Column(Integer, nullable=False, default=0)

    # ✅ Added for webhook linking and debugging
    tg_id = Column(BigInteger, nullable=True, index=True)       # Telegram user ID
    username = Column(String, nullable=True)                    # Telegram username

    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','successful','failed','expired')",
            name="check_payment_status"
        ),
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


# ------------------------
# 7. Prize Winner
# ------------------------
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

    # ✅ CSV EXPORT HELPERS (added)
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

