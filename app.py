# =========================
# Step 1 — Imports & Basic Setup
# =========================
import os
import json
import logging
import time
import random
import asyncio
from datetime import datetime, timedelta
from typing import Optional

# Database drivers
import psycopg2
import asyncpg

# Web framework
from fastapi import FastAPI, Request

# Telegram bot libraries
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Helper for MarkdownV2 escaping
from telegram.helpers import escape_markdown

# -------------------------
# Logging
# -------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("naijaprizegate")

# Environment / configuration
# -------------------------
# Note: set these in Render (Environment) or locally before running
BOT_TOKEN = os.getenv("BOT_TOKEN")  # required to connect to Telegram
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./db.sqlite3")
PUBLIC_CHANNEL = os.getenv("PUBLIC_CHANNEL", "@NaijaPrizeGateWinners")
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "14600"))
PAYMENT_EXPIRE_HOURS = int(os.getenv("PAYMENT_EXPIRE_HOURS", "2"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # path secret for webhook
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")  # e.g. https://my-service.onrender.com
ADMIN_ID = int(os.getenv("ADMIN_ID", "6683718665"))  # replace with your Telegram ID

TRANSACTIONS_PER_PAGE = 50

# Use WIN_THRESHOLD everywhere (avoid duplicate THRESHOLD_WIN)
THRESHOLD_WIN = WIN_THRESHOLD

# Friendly warnings (we don't crash here, so you can run linters locally)
if not BOT_TOKEN:
    logger.warning("BOT_TOKEN not set — the bot cannot connect to Telegram until you provide it.")
if not WEBHOOK_SECRET:
    logger.warning("WEBHOOK_SECRET not set — webhook endpoint will be unprotected until you set this.")
if not RENDER_EXTERNAL_URL:
    logger.info("RENDER_EXTERNAL_URL not set. Webhook won't be auto-registered on startup.")

# -------------------------
# FastAPI app (webhook receiver)
# -------------------------
app = FastAPI()

@app.get("/")
async def root():
    """Basic health check (Render friendly)."""
    return {"status": "ok", "message": "NaijaPrizeGate bot is running 🚀"}

# -----------------------------
# Database Setup (SQLAlchemy + asyncpg)
# -----------------------------
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, DateTime, Boolean, Float, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
import asyncpg

# Use DATABASE_URL from environment (set earlier in config)
engine = create_engine(DATABASE_URL)

# ORM session + Base
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# Optional: asyncpg pool for raw async queries
db_pool = None
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

# FastAPI / Telegram imports
from fastapi import Request, HTTPException
from telegram import Bot

# -----------------------------
# Database Setup (Async SQLAlchemy)
# -----------------------------
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, BigInteger, DateTime, Boolean, Float, Text, ForeignKey
from datetime import datetime
from sqlalchemy.orm import relationship

# Async DB URL (Render PostgreSQL must use asyncpg)
DATABASE_URL = "postgresql+asyncpg://naijaprizegate_bot_user:eT3NDsGxXCS7SoHGxr11AMNjofXYsBwq@dpg-d2sq25mr433s73fv1asg-a/naijaprizegate_bot"

# Async SQLAlchemy engine
engine = create_async_engine(DATABASE_URL, echo=False, future=True)

# Session factory (async)
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False
)

# Base class for ORM models
Base = declarative_base()


# -----------------------------
# User table
# -----------------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    tg_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String(255))
    first_seen = Column(DateTime, default=datetime.utcnow)
    tries = Column(Integer, default=0)        # paid tries
    bonus_tries = Column(Integer, default=0)  # bonus/free tries
    welcomed = Column(Boolean, default=False)

# -----------------------------
# Referral table
# -----------------------------
class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, index=True)
    referrer_id = Column(Integer, ForeignKey("users.id"))   # who invited
    new_user_id = Column(Integer, ForeignKey("users.id"))   # the friend who joined
    created_at = Column(DateTime, default=datetime.utcnow)

# -----------------------------
# Payment table
# -----------------------------
class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    amount = Column(Float, nullable=False)
    package = Column(String(50))
    status = Column(String(20), default="pending")  # pending, confirmed, failed
    created_at = Column(DateTime, default=datetime.utcnow)


# -----------------------------
# Transaction Log
# -----------------------------
class TransactionLog(Base):
    __tablename__ = "transaction_logs"
    id = Column(Integer, primary_key=True, index=True)
    tx_ref = Column(String, index=True)
    status = Column(String, index=True)      # successful, failed
    amount = Column(Float)                   # better precision
    raw_data = Column(Text)                  # full JSON payload
    created_at = Column(DateTime, default=datetime.utcnow)

# -----------------------------
# Play (try luck) table
# -----------------------------
class Play(Base):
    __tablename__ = "plays"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    outcome = Column(String(50))              # win/lose
    prize = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Evidence(Base):
    __tablename__ = "evidence"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))  # who submitted
    platform = Column(String, nullable=False)  # facebook / instagram / tiktok / youtube
    file_id = Column(String, nullable=False)  # Telegram photo file_id
    status = Column(String, default="pending")  # pending / approved / rejected
    created_at = Column(DateTime, default=datetime.utcnow)

# --------------------
# Proof model (for social follow screenshots)
# --------------------
class Proof(Base):
    __tablename__ = "proofs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    platform = Column(String, nullable=False)  # e.g. "facebook", "instagram", "tiktok", "youtube"
    photo_file_id = Column(String, nullable=False)  # Telegram photo_file_id (not storing big files)
    status = Column(String, default="pending")  # "pending", "approved", "rejected"
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", backref="proofs")

# -----------------------------
# Create tables (on startup)
# -----------------------------
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ------------------------
# Try-luck DB helpers (Async)
# Place AFTER your async Base + init_db
# ------------------------

from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError

# ----- Async helpers (using AsyncSessionLocal) -----

# ----- Async: Get or create user with SQLAlchemy async -----
async def get_or_create_user(tg_id: int, username: str = ""):
    async with AsyncSessionLocal() as session:

        async with session.begin():
            user = await session.get(User, tg_id)
            if user:
                user.is_new = False
                return user

            # Create new user
            user = User(id=tg_id, username=username, tries=0, created_at=datetime.utcnow())
            session.add(user)
            await session.flush()
            user.is_new = True
            return user

async def get_user_tries(tg_id: int) -> int:
    """Return total available tries (paid + bonus) for the user."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalars().first()
        if not user:
            return 0
        return (user.tries or 0) + (user.bonus_tries or 0)


async def add_tries(tg_id: int, n: int):
    """Credit n paid tries to the user (create account if missing)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalars().first()
        if not user:
            user = User(tg_id=tg_id, username="", tries=n)
            session.add(user)
        else:
            user.tries = (user.tries or 0) + n
        await session.commit()


# -----------------------------
# Global Counter table
# -----------------------------
class GlobalCounter(Base):
    __tablename__ = "global_counter"
    id = Column(Integer, primary_key=True)
    paid_tries_total = Column(Integer, default=0)


# -----------------------------
# Consume try (paid or bonus)
# -----------------------------
async def consume_try(tg_id: int, use_bonus_first: bool = True) -> bool:
    """
    Deduct 1 try from user. Paid tries increment global counter, bonus tries do not.
    Logs the play as 'pending'.
    
    Args:
        tg_id: Telegram ID of user
        use_bonus_first: If True, consume bonus tries before paid tries
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalars().first()
        if not user:
            return False

        used_paid = False

        # --- Decide which try to use ---
        if use_bonus_first and (user.bonus_tries or 0) > 0:
            user.bonus_tries -= 1
        elif (user.tries or 0) > 0:
            user.tries -= 1
            used_paid = True
        elif (user.bonus_tries or 0) > 0:  # fallback: only bonus left
            user.bonus_tries -= 1
        else:
            return False  # No tries at all

        # --- Log play immediately ---
        play = Play(user_id=user.id, outcome="pending", created_at=datetime.utcnow())
        session.add_all([user, play])

        # --- If paid try → increment global counter ---
        if used_paid:
            counter = await session.get(GlobalCounter, 1, with_for_update=True)
            if not counter:
                counter = GlobalCounter(id=1, paid_tries_total=0)
                session.add(counter)

            counter.paid_tries_total += 1

        await session.commit()
        return True

# -----------------------------
# Reset global counter after a win
# -----------------------------
async def reset_global_counter():
    async with AsyncSessionLocal() as session:
        counter = await session.get(GlobalCounter, 1)
        if counter:
            counter.paid_tries_total = 0
            session.add(counter)
            await session.commit()


async def record_play(tg_id: int, outcome: str, prize: str | None = None):
    """Update the last pending play for the user with outcome + prize."""
    async with AsyncSessionLocal() as session:
        # Get user id
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalars().first()
        if not user:
            return

        result = await session.execute(
            select(Play)
            .where(Play.user_id == user.id, Play.outcome == "pending")
            .order_by(Play.created_at.desc())
            .limit(1)
        )
        play = result.scalars().first()
        if play:
            play.outcome = outcome
            play.prize = prize
            session.add(play)
            await session.commit()


async def get_total_plays() -> int:
    """Return total number of recorded plays."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Play))
        return len(result.scalars().all())


async def get_total_tries() -> int:
    """Return total number of tries (all users) recorded in users table."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()
        return sum((u.tries or 0) + (u.bonus_tries or 0) for u in users)

# When user clicks proof button
async def ask_for_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    platform = query.data.replace("proof_", "")  # e.g. facebook
    context.user_data["awaiting_proof"] = platform

    await query.message.reply_text(
        f"📸 Please upload a screenshot showing you followed us on *{platform.title()}*.\n"
        "Once approved, you’ll get +1 bonus try 🎁",
        parse_mode="MarkdownV2"
    )

async def proof_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    platform = query.data.split("_")[1]  # facebook / instagram / tiktok / youtube

    context.user_data["awaiting_proof"] = platform

    await query.message.reply_text(
        f"📸 Please send a screenshot as proof that you followed on *{platform.capitalize()}*.",
        parse_mode="Markdown"
    )

async def review_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action, proof_id = query.data.split("_")
    proof_id = int(proof_id)

    async with AsyncSessionLocal() as session:
        proof = await session.get(Proof, proof_id)
        if not proof or proof.status != "pending":
            await query.answer("⚠️ Already reviewed or not found.", show_alert=True)
            return

        if action == "approve":
            proof.status = "approved"

            # Give bonus try
            user = await session.get(User, proof.user_id)
            user.bonus_tries = (user.bonus_tries or 0) + 1
            session.add(user)

            await session.commit()

            # Notify user
            await context.bot.send_message(
                chat_id=user.tg_id,
                text=f"🎉 Your proof for *{proof.platform.capitalize()}* was approved! A bonus try has been added 🎁",
                parse_mode="Markdown"
            )
            await query.edit_message_caption(
                caption=f"✅ Approved {proof.platform} proof for user {user.tg_id}"
            )

        elif action == "reject":
            proof.status = "rejected"
            await session.commit()

            user = await session.get(User, proof.user_id)
            await context.bot.send_message(
                chat_id=user.tg_id,
                text=f"❌ Your proof for *{proof.platform.capitalize()}* was rejected. Please try again."
            )
            await query.edit_message_caption(
                caption=f"❌ Rejected {proof.platform} proof for user {user.tg_id}"
            )

# When user sends a photo
async def handle_proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "awaiting_proof" not in context.user_data:
        return

    platform = context.user_data.pop("awaiting_proof")
    photo = update.message.photo[-1]  # best quality
    file_id = photo.file_id
    tg_id = update.effective_user.id

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalars().first()

        if user:
            evidence = Evidence(user_id=user.id, platform=platform, file_id=file_id, status="pending")
            session.add(evidence)
            await session.commit()

    await update.message.reply_text(
        f"🙏 Thanks! Your {platform.title()} proof has been submitted and is awaiting admin approval."
    )

ADMIN_IDS = [6683718665]  # replace with your Telegram user ID(s)

async def pending_proofs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMIN_IDS:
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Proof).where(Proof.status == "pending").limit(10)
        )
        proofs = result.scalars().all()

    if not proofs:
        await update.message.reply_text("🎉 No pending proofs right now.")
        return

    for proof in proofs:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{proof.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{proof.id}")
            ]
        ])
        await context.bot.send_photo(
            chat_id=update.message.chat_id,
            photo=proof.file_id,
            caption=f"👤 User ID: {proof.user_id}\n📱 Platform: {proof.platform}",
            reply_markup=keyboard
        )

async def approve_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    evidence_id = int(query.data.replace("approve_", ""))

    async with AsyncSessionLocal() as session:
        evidence = await session.get(Evidence, evidence_id)
        if not evidence or evidence.status != "pending":
            await query.answer("⚠️ Already handled.", show_alert=True)
            return

        # Approve
        evidence.status = "approved"
        user = await session.get(User, evidence.user_id)
        user.bonus_tries += 1
        await session.commit()

    # Notify user
    await context.bot.send_message(
        chat_id=user.tg_id,
        text=f"✅ Your {evidence.platform.title()} proof has been approved!\n"
             "🎁 You’ve received +1 bonus try. Check /mytries to confirm."
    )

    await query.answer("✅ Approved & bonus try added!")

# -------------------
# Telegram Application
# -------------------
application = Application.builder().token(BOT_TOKEN).build()

# -------------------------
# MarkdownV2 escaping helper
# -------------------------
def md_escape(value: Optional[str]) -> str:
    """
    Return the given value escaped for MarkdownV2 using telegram.helpers.escape_markdown.
    Accepts None and returns an empty string in that case.
    Use this for any dynamic text inserted into parse_mode=MARKDOWN_V2 messages.
    """
    s = "" if value is None else str(value)
    # escape_markdown handles the heavy lifting; ensure we explicitly pass version=2 where used later
    return escape_markdown(s, version=2)

# -----------------------
# Helper to Check admin
# -----------------------
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


# -----------------------------
# Sync helper: Get or create user
# -----------------------------
# -----------------------------
# Async helper: Get or create user
# -----------------------------
async def get_or_create_user(tg_id: int, username: str = ""):
    """Fully async: Get an existing user or create a new one."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalars().first()

        if user:
            return user

        # Create new user if not found
        user = User(
            tg_id=tg_id,
            username=username,
            tries=0,
            bonus_tries=0,
            created_at=datetime.utcnow(),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

# =========================
# Step 2 — Global constants
# =========================

# Payment packages
PACKAGES = {
    "500": {
        "label": "₦500 (1 try)",
        "amount": 500,
        "tries": 1,
    },
    "2000": {
        "label": "₦2000 (5 tries)",
        "amount": 2000,
        "tries": 5,
    },
    "5000": {
        "label": "₦5000 (15 tries)",
        "amount": 5000,
        "tries": 15,
    },
}

# =========================
# Step 3 — Keyboards
# =========================

def main_menu_keyboard():
    """Main menu buttons"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Pay Now", callback_data="pay:start")],
        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck:start")],
        [InlineKeyboardButton("📊 My Tries", callback_data="mytries")],
        [InlineKeyboardButton("🎁 Get Free Tries", callback_data="free_tries")]
    ])

def packages_keyboard():
    """Show available packages with Cancel + Back buttons"""
    buttons = []
    for key, p in PACKAGES.items():
        # Escape dynamic labels (Markdown-safe)
        label = md_escape(p["label"])
        buttons.append([InlineKeyboardButton(label, callback_data=f"pay:package:{key}")])

    # Back + Cancel row
    buttons.append([
        InlineKeyboardButton("⬅️ Back", callback_data="pay:back"),
        InlineKeyboardButton("❌ Cancel", callback_data="pay:cancel")
    ])
    return InlineKeyboardMarkup(buttons)

def tryluck_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck:start")],
        [InlineKeyboardButton("⬅️ Back", callback_data="main:menu")]
    ])

# -----------------------------
# Step 4: Start & Help Handlers
# -----------------------------

# ⚠️ Needs MarkdownV2 escaping
WELCOME_TEXT = (
    "👋 Welcome to *NaijaPrizeGate\\!* 🎉\n\n"
    "🔥 Try your luck to win an *iPhone 16 Pro Max* 📱🔥\n\n"
    "Here’s how it works:\n"
    "1️⃣ Buy tries (₦500 = 1 try, ₦2000 = 5 tries, ₦5000 = 15 tries)\n"
    "2️⃣ Press *🎰 Try Luck* to spin the slot machine\n"
    "3️⃣ Each spin is a chance to win the iPhone\\!\n\n"
    "🎥 Winner unboxing videos will be posted in "
    f"{md_escape(PUBLIC_CHANNEL)} — don’t miss them\\!\n\n"
    "👉 Tap a button below to get started\\!"
)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username or ""

    user = await get_or_create_user(user_id, username)

    if getattr(user, "is_new", False):  # brand new user
        await update.message.reply_text(
            WELCOME_TEXT,
            parse_mode="MarkdownV2",
            reply_markup=main_menu_keyboard()
        )
    else:  # returning
        tries = await get_user_tries(user_id)
        safe_username = md_escape(username)

        welcome_back_text = (
            f"🎉 Welcome back, {safe_username}! 🎉\n\n"
            f"You currently have 🎯 *{tries} tries* available\\.\n\n"
            "💡 Each try brings you closer to becoming the next *LUCKY WINNER* of the "
            "*iPhone 16 Pro Max* 📱🔥\n\n"
            "✨ Don’t wait — tap *Try Luck* now and see if fortune is on your side\\!"
        )
        await update.message.reply_text(
            welcome_back_text,
            parse_mode="MarkdownV2",
            reply_markup=main_menu_keyboard()
        )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /help command"""
    # ⚠️ Escaped for MarkdownV2
    help_text = (
        "ℹ️ *How to use this bot:*\n\n"
        "• /start → Show welcome message \\+ menu\n"
        "• 💳 Buy Tries → Select a package \\& pay\n"
        "• 🎰 Try Luck → Spin the slot machine\n"
        "• 📊 My Balance → Check how many tries you have left\n\n"
    )
    await update.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=main_menu_keyboard()
    )

from telegram.ext import MessageHandler, filters

# Handles greetings or any text message
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    username = update.effective_user.username or ""

    user = await get_or_create_user(tg_id, username)

    if getattr(user, "is_new", False):
        text = (
            "👋 *Welcome to NaijaPrizeGate!* 🎁 \n\n"
            "Spin, play, and stand a chance to win amazing prizes. "
            "Your first step to becoming a winner starts now 🚀"
        )
    else:
        text = (
            "👋 *Welcome back!* \n\n"
            "Your luck might just shine today 🌟. "
            "Who knows? You could be the *next proud owner of an iPhone 16 Pro Max* 📱🎉"
        )

    await update.message.reply_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=main_menu_keyboard()
    )

# =========================
# Try Luck Flow
# =========================

from telegram import InlineKeyboardMarkup, InlineKeyboardButton

async def handle_tryluck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when user clicks Try Luck"""
    query = update.callback_query
    user_id = query.from_user.id

    # Fetch user tries from DB (paid + bonus combined, for availability check)
    user_tries = await get_user_tries(user_id)
    if user_tries <= 0:
        await query.answer("😔 No tries left. Please buy a package 💳", show_alert=True)
        return

    # Deduct 1 try (consume_try should internally know whether it was bonus or paid)
    success = await consume_try(user_id)
    if not success:
        await query.answer("⚠️ Error: Couldn’t deduct try. Please try again.", show_alert=True)
        return
    
    # Show spinning effect
    frames = [
        "🎰 *Spinning*\\.",
        "🎰 *Spinning* \\.\\.",
        "🎰 *Spinning* \\.\\.\\.",
    ]
    for _ in range(3):  # run 3 cycles (~9s)
        for frame in frames:
            await query.edit_message_text(
                f"{frame}\n\n⏳ Please wait for the result\\.",
                parse_mode="MarkdownV2"
            )
            await asyncio.sleep(1)

    # ✅ Check global counter (only paid tries, ignores bonus)
    async with AsyncSessionLocal() as session:
        counter = await session.get(GlobalCounter, 1)
        total_paid_tries = counter.paid_tries_total if counter else 0

    if total_paid_tries == THRESHOLD_WIN:
        # 🎉 WINNER
        await query.edit_message_text(
            "🎉 *CONGRATULATIONS* 🎉\n\n"
            "You are the *LUCKY WINNER* of the *iPhone 16 Pro Max* 📱🔥\n\n"
            "Our team will contact you shortly for delivery 🛵📦",
            parse_mode="MarkdownV2"
        )
        await record_play(user_id, "win", "iPhone 16 Pro Max")

        # 🔄 Reset counter for next cycle
        await reset_global_counter()

    else:
        # ❌ Not a winner
        await query.edit_message_text(
            "😔 You are *not a winner* this time\\. Try again 🎰\n\n"
            "👉 The more you try, the higher your chances of winning\\!",
            parse_mode="MarkdownV2",
            reply_markup=tryluck_keyboard()
        )
        await record_play(user_id, "lose")

async def mytries_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.message.from_user.id

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalars().first()

    if not user:
        await update.message.reply_text("⚠️ You’re not registered yet. Please start with /start.")
        return

    paid = user.tries or 0
    bonus = user.bonus_tries or 0
    total = paid + bonus

    text = (
        "🎰 *Your Current Tries*\n\n"
        f"💳 Paid Tries: *{paid}*\n"
        f"🎁 Bonus Tries: *{bonus}*\n"
        "────────────────────\n"
        f"🔢 Total Available: *{total}*\n\n"
        "👉 Paid tries count toward the jackpot counter.\n"
        "👉 Bonus tries are free extras but don’t move the global jackpot closer."
    )

    await update.message.reply_text(text, parse_mode="Markdown")

async def free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📘 Follow on Facebook", url="https://web.facebook.com/Naijaprizegate")],
        [InlineKeyboardButton("📸 Follow on Instagram", url="https://www.instagram.com/naijaprizegate/")],
        [InlineKeyboardButton("🎵 Follow on TikTok", url="https://www.tiktok.com/@naijaprizegate")],
        [InlineKeyboardButton("▶️ Subscribe on YouTube", url="https://www.youtube.com/@Naijaprizegate")],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🎉🔥 *WIN AN iPhone 16 Pro Max\\!* 🔥🎉\n\n"
        "💎 Here’s your chance to grab *FREE TRIES* in our Lucky Draw Campaign\\!\n\n"
        "✅ All you need to do is support us by following\\/subscribing on our platforms:\n\n"
        "👉 Facebook\n"
        "👉 Instagram\n"
        "👉 TikTok\n"
        "👉 YouTube\n\n"
        "📲 Just click the buttons below ⬇️ and show love ❤️\n\n"
        "🎁 Every support = *extra free tries* towards winning your dream iPhone 📱✨"
    )

    await update.message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode="MarkdownV2"
    )

# -----------------------------
# Step 5: Balance Check Handler
# -----------------------------

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the user how many tries they have left"""

    uid = update.effective_user.id
    uname = update.effective_user.username or ""

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.tg_id == uid).one_or_none()
        if not u:
            text = (
                f"Hello {md_escape(uname)}, you don’t have an account yet\.\n\n"
                "👉 Tap *Pay Now* to buy tries and start playing 🎰"
            )
        else:
            paid = u.tries or 0
            bonus = u.bonus_tries or 0
            total = paid + bonus
            text = (
                "📊 *Your Balance:*\n\n"
                f"• Paid tries: *{paid}*\n"
                f"• Bonus tries: *{bonus}*\n"
                f"• Total: *{total}*\n\n"
                "👉 Use *Try Luck 🎰* to spin\!"
            )

        await update.message.reply_text(
            text,
            parse_mode="MarkdownV2",
            reply_markup=main_menu_keyboard()
        )
    finally:
        db.close()

# =========================
# Step 6: Payment Handler
# =========================

# /pay command - show package options
async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 Select a package below to proceed with payment:",
        parse_mode="MarkdownV2",
        reply_markup=packages_keyboard()
    )


# Callback when a package is chosen
async def handle_package_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data  # e.g. "package_500", "package_2000"

    if choice.startswith("package_"):
        amount = int(choice.split("_")[1])

        # DB: ensure user exists
        session = SessionLocal()
        user = session.query(User).filter(User.telegram_id == query.from_user.id).first()
        if not user:
            user = User(telegram_id=query.from_user.id, balance=0)
            session.add(user)
            session.commit()

        # Create a payment record
        payment = Payment(
            user_id=user.id,
            amount=amount,
            status="pending"
        )
        session.add(payment)
        session.commit()

        # Call Flutterwave API to create a checkout link
        payload = {
            "tx_ref": f"tx_{payment.id}",
            "amount": str(amount),
            "currency": "NGN",
            "redirect_url": "https://yourdomain.com/payment/verify",  # TODO: adjust this
            "customer": {
                "email": f"user_{user.id}@naijaprizegate.com",
                "phonenumber": "08000000000",
                "name": f"User {user.id}"
            },
            "customizations": {
                "title": "NaijaPrizeGate",
                "description": f"Payment for ₦{amount} package"
            }
        }

        headers = {
            "Authorization": f"Bearer {FLW_SECRET_KEY}",  # from your env
            "Content-Type": "application/json"
        }

        checkout_link = None
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.flutterwave.com/v3/payments",
                json=payload,
                headers=headers
            )
            resp.raise_for_status()  # raises error if non-200
            data = resp.json()
            if data.get("status") == "success":
                checkout_link = data["data"]["link"]

        session.close()

        if checkout_link:
            await query.edit_message_text(
                text=(
                    f"✅ You selected *₦{amount} package*\\.\n\n"
                    f"Click below to complete your payment securely:\n\n"
                    f"{md_escape(checkout_link)}"
                ),
                parse_mode="MarkdownV2"
            )
        else:
            await query.edit_message_text(
                "❌ Sorry, something went wrong creating your payment link\\. Please try again later\\.",
                parse_mode="MarkdownV2"
            )

    elif choice == "cancel":
        await query.edit_message_text(
            "❌ Payment cancelled\\. Returning to main menu\\.",
            parse_mode="MarkdownV2"
        )


# Callback when user presses "Back" during payment flow
async def handle_payment_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🔙 Select a package below to proceed with payment:",
        parse_mode="MarkdownV2",
        reply_markup=packages_keyboard()
    )


# Callback when user presses "Cancel" during payment flow
async def handle_payment_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "❌ Payment cancelled\\. Returning to main menu\\.",
        parse_mode="MarkdownV2"
    )

async def handle_invite_friend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    # Build referral link
    link = f"https://t.me/{context.bot.username}?start={user_id}"

    await query.message.reply_text(
        "👥 *Invite Your Friends!*\n\n"
        "Share this personal link with your friends:\n\n"
        f"{link}\n\n"
        "🎁 For each friend that joins, you’ll earn +1 bonus try!",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="free_tries")]])
    )

# ---------------------------
# /transactions command
# ---------------------------
async def transactions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "⛔ You are not authorized to use this command\.",
            parse_mode="MarkdownV2"
        )
        return

    # detect filters
    args = context.args
    period = "all"
    if args:
        if args[0].lower() in ["today", "week", "month"]:
            period = args[0].lower()

    await send_transactions_page(update, context, page=0, period=period)


# ---------------------------
# Send page of transactions
# ---------------------------
async def send_transactions_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, period: str = "all"):
    session = SessionLocal()

    # Filter by period
    now = datetime.utcnow()
    if period == "today":
        start = datetime(now.year, now.month, now.day)
    elif period == "week":
        start = now - timedelta(days=7)
    elif period == "month":
        start = now - timedelta(days=30)
    else:
        start = None

    query = session.query(TransactionLog)
    if start:
        query = query.filter(TransactionLog.created_at >= start)

    total_logs = query.count()

    logs = (
        query.order_by(TransactionLog.id.desc())
        .offset(page * TRANSACTIONS_PER_PAGE)
        .limit(TRANSACTIONS_PER_PAGE)
        .all()
    )

    # summary stats (success vs fail)
    success_count = query.filter(TransactionLog.status == "success").count()
    failed_count = query.filter(TransactionLog.status != "success").count()
    session.close()

    if not logs:
        await update.message.reply_text(
            "📂 No transactions logged yet\.",
            parse_mode="MarkdownV2"
        )
        return

    total = success_count + failed_count
    if total > 0:
        success_pct = int((success_count / total) * 100)
        fail_pct = 100 - success_pct
    else:
        success_pct = fail_pct = 0

    # make emoji bar (10 blocks)
    def make_bar(pct, symbol):
        blocks = int(pct / 10)
        return symbol * blocks + "▫️" * (10 - blocks)

    # Header
    msg = (
        f"📑 *Transactions \\({period.capitalize()} — Page {page+1}\\)*\n\n"
        f"✅ Success: {success_pct}\\% {make_bar(success_pct, '🟩')}\n"
        f"❌ Failed: {fail_pct}\\% {make_bar(fail_pct, '🟥')}\n\n"
    )

    # Transaction list
    for log in logs:
        msg += (
            f"🆔 `{md_escape(log.tx_ref)}`\n"
            f"💰 Amount: ₦{log.amount}\n"
            f"📌 Status: {'✅ Success' if log.status == 'success' else '❌ ' + md_escape(log.status)}\n"
            f"📅 {log.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        )

    # Buttons
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅ Prev", callback_data=f"txn_{period}_prev_{page-1}"))
    if (page + 1) * TRANSACTIONS_PER_PAGE < total_logs:
        buttons.append(InlineKeyboardButton("Next ➡", callback_data=f"txn_{period}_next_{page+1}"))

    keyboard = InlineKeyboardMarkup([buttons]) if buttons else None

    if update.callback_query:
        await update.callback_query.edit_message_text(
            msg,
            parse_mode="MarkdownV2",
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            msg,
            parse_mode="MarkdownV2",
            reply_markup=keyboard
        )

# ---------------------------
# Pagination handler
# ---------------------------
async def transactions_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    # ⚠️ Static text: Escape for MarkdownV2 if using parse_mode later.
    # Here we're only answering a callback, so no parse_mode is applied.
    if not is_admin(user_id):
        await query.answer("⛔ Not authorized", show_alert=True)
        return

    await query.answer()

    # callback data looks like: txn_week_next_1
    data = query.data.split("_")  # ["txn", "week", "next", "1"]
    if len(data) == 4:
        _, period, _, page_str = data
        page = int(page_str)
        await send_transactions_page(update, context, page, period)


from datetime import datetime, timedelta

# ---------------------------
# stat_cmd
# ---------------------------
async def stat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "⛔ You are not authorized to use this command\.",
            parse_mode="MarkdownV2"
        )
        return

    # Determine time filter
    arg = context.args[0].lower() if context.args else "all"
    now = datetime.utcnow()

    session = SessionLocal()
    query = session.query(TransactionLog)

    if arg == "today":
        start = datetime(now.year, now.month, now.day)  # midnight UTC today
        query = query.filter(TransactionLog.timestamp >= start)
    elif arg == "week":
        start = now - timedelta(days=7)
        query = query.filter(TransactionLog.timestamp >= start)
    elif arg == "month":
        start = now - timedelta(days=30)
        query = query.filter(TransactionLog.timestamp >= start)
    else:
        arg = "all"  # fallback to all-time stats

    total = query.count()
    success = query.filter(TransactionLog.status == "successful").count()
    failed = query.filter(TransactionLog.status == "failed").count()
    pending = query.filter(TransactionLog.status == "pending").count()
    session.close()

    if total == 0:
        await update.message.reply_text(
            f"📊 No transactions found for *{md_escape(arg)}* period\.",
            parse_mode="MarkdownV2"
        )
        return

    # Calculate percentages
    success_pct = int((success / total) * 100)
    failed_pct = int((failed / total) * 100)
    pending_pct = int((pending / total) * 100)

    # Emoji bars
    def bar(pct, emoji):
        blocks = pct // 10
        return emoji * blocks + "▫️" * (10 - blocks)

    msg = (
        f"📊 *Bot Stats* \\({md_escape(arg)}\\)\n\n"
        f"✅ *Successful*: {success} \\({success_pct}\\%\\)\n{bar(success_pct, '🟩')}\n\n"
        f"❌ *Failed*: {failed} \\({failed_pct}\\%\\)\n{bar(failed_pct, '🟥')}\n\n"
        f"⏳ *Pending*: {pending} \\({pending_pct}\\%\\)\n{bar(pending_pct, '🟨')}\n\n"
        f"📂 *Total Transactions*: {total}"
    )

    await update.message.reply_text(msg, parse_mode="MarkdownV2")

# ---------------------------
# =============
# Flutterwave
# =============
@app.post("/flutterwave/webhook")
async def flutterwave_webhook(request: Request):
    try:
        payload = await request.json()

        # ✅ Verify the webhook signature (security check)
        signature = request.headers.get("verif-hash")
        if signature != FLW_SECRET_HASH:  # set FLW_SECRET_HASH in .env
            raise HTTPException(status_code=403, detail="Invalid signature")

        data = payload.get("data", {})
        tx_ref = data.get("tx_ref")

        if not tx_ref:
            raise HTTPException(status_code=400, detail="Missing tx_ref")

        # 🔹 Double-check with Flutterwave API
        try:
            verification = await verify_payment(tx_ref)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Verification failed: {str(e)}")

        # ✅ Extract confirmed values
        status = verification.get("data", {}).get("status")
        amount = verification.get("data", {}).get("amount")
        currency = verification.get("data", {}).get("currency")

        # Extract payment ID from tx_ref (remember we used tx_{payment.id})
        payment_id = int(tx_ref.split("_")[1])

        session = SessionLocal()

        # 🔹 Always log this transaction
        log_entry = TransactionLog(
            tx_ref=tx_ref,
            status=status,
            amount=amount,
            raw_data=json.dumps(payload)  # still store raw webhook JSON
        )
        session.add(log_entry)

        payment = session.query(Payment).filter(Payment.id == payment_id).first()
        bot = Bot(token=BOT_TOKEN)

        if payment:
            user = session.query(User).filter(User.id == payment.user_id).first()

            if status == "successful":
                # ✅ Mark as completed
                payment.status = "completed"

                # Credit user with tries
                if user:
                    tries = PACKAGES.get(str(payment.amount), {}).get("tries", 0)
                    user.balance += tries

                    # Confirmation message (MarkdownV2 safe)
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=(
                            "🎉 *Payment confirmed\\!*\\n\\n"
                            f"✅ Amount: ₦{payment.amount}\\n"
                            f"🎰 You have been credited with *{tries} tries*\\.\\n"
                            f"📊 New balance: {user.balance} tries\\."
                        ),
                        parse_mode="MarkdownV2"
                    )

            else:
                # ❌ Payment failed or abandoned
                payment.status = "failed"

                if user:
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=(
                            "⚠️ Payment *failed* or was not completed\\.\\n\\n"
                            f"❌ Amount: ₦{payment.amount}\\n"
                            "Please try again from the /pay menu\\."
                        ),
                        parse_mode="MarkdownV2"
                    )

        session.commit()
        session.close()

        return {"status": "ok"}

    except Exception as e:
        print("Webhook error:", str(e))
        raise HTTPException(status_code=500, detail="Webhook processing failed")

# -----------------
# Verify Payment
# -----------------
async def verify_payment(tx_ref: str):
    """
    Verify a payment on Flutterwave by transaction reference.
    Returns JSON response with payment details.
    """
    url = f"https://api.flutterwave.com/v3/transactions/verify_by_reference?tx_ref={tx_ref}"
    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

# =========================
# Dispatcher / Handler Registration
# =========================

# Register command handlers
application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("balance", balance_cmd))
application.add_handler(CommandHandler("pay", pay_cmd))

# Register callback query handlers
application.add_handler(CallbackQueryHandler(handle_package_selection, pattern="^package:"))
application.add_handler(CallbackQueryHandler(handle_payment_back, pattern="^pay:back$"))
application.add_handler(CallbackQueryHandler(handle_payment_cancel, pattern="^pay:cancel$"))
application.add_handler(CallbackQueryHandler(transactions_pagination, pattern="^txn_"))
application.add_handler(CallbackQueryHandler(handle_tryluck, pattern="^tryluck:start$"))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
application.add_handler(CallbackQueryHandler(ask_for_proof, pattern="^proof_"))
application.add_handler(CallbackQueryHandler(handle_invite_friend, pattern="^invite_friend$"))
application.add_handler(CommandHandler("pending_proofs", pending_proofs))
application.add_handler(CallbackQueryHandler(proof_request, pattern=r"^proof_"))
application.add_handler(CallbackQueryHandler(review_proof, pattern=r"^(approve|reject)_"))
application.add_handler(MessageHandler(filters.PHOTO, handle_proof_photo))
application.add_handler(CommandHandler("mytries", mytries_cmd))

# Add more handlers as we build other features (tryluck, free_tries, etc.)
      
application.add_handler(CommandHandler("transactions", transactions_cmd))
    
application.add_handler(CommandHandler("stat", stat_cmd))
    
# =========================
# Entrypoint (Production-ready: FastAPI + Webhooks)
# =========================
import os
import uvicorn
import httpx
from fastapi import Request
from telegram import Update

# ⚡ FastAPI app is already defined above as `app`
# ⚡ `application` (telegram.ext.Application) is also defined above

# =========================
# Startup / Webhook setup
# =========================
@app.on_event("startup")
async def startup_event():
    """
    Initialize Telegram bot and set webhook automatically.
    """
    # Initialize the Application (important!)
    await application.initialize()

    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if render_url:
        webhook_url = f"{render_url}/telegram/webhook"
        # Set webhook with Telegram
        await application.bot.set_webhook(webhook_url)
        print(f"✅ Telegram webhook set to: {webhook_url}")
    else:
        print("⚠️ RENDER_EXTERNAL_URL not set. Webhook cannot be registered automatically.")

# =========================
# Telegram webhook endpoint
# =========================
@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Receives updates from Telegram and passes them to the bot.
    """
    # Ensure Application is initialized (safe)
    await application.initialize()

    update_data = await request.json()
    update = Update.de_json(update_data, application.bot)
    await application.process_update(update)

    return {"status": "ok"}

# =========================
# Entrypoint for Render
# =========================
if __name__ == "__main__":
    import uvicorn

    PORT = int(os.getenv("PORT", 8080))  # Render automatically sets $PORT
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
