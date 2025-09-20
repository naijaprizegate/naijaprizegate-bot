# =========================
# Step 1 — Imports & basic setup
# =========================
import os
import json
import httpx
import logging
import time
import ipaddress
import uuid
import random
import asyncio
import uvicorn 
from datetime import datetime, timedelta
from typing import Optional

# FastAPI (used for webhook endpoint)
from fastapi import FastAPI
from fastapi import Request

# telegram helper for MarkdownV2 escaping (we'll use this to keep messages safe)
from telegram.helpers import escape_markdown

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown as md_escape
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram import Update
from telegram.ext import CallbackContext

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
BOT_TOKEN = os.getenv("BOT_TOKEN")                        # required to connect to Telegram
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./db.sqlite3")
PUBLIC_CHANNEL = os.getenv("PUBLIC_CHANNEL", "@NaijaPrizeGateWinners")
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "14600"))
PAYMENT_EXPIRE_HOURS = int(os.getenv("PAYMENT_EXPIRE_HOURS", "2"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")         # path secret for webhook
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")  # e.g. https://my-service.onrender.com
ADMIN_ID = int(os.getenv("ADMIN_ID", "6683718665"))  # replace with your Telegram ID

# Friendly warnings (we don't crash here, so you can run linters locally)
if not BOT_TOKEN:
    logger.warning("BOT_TOKEN not set — the bot cannot connect to Telegram until you provide it.")
if not WEBHOOK_SECRET:
    logger.warning("WEBHOOK_SECRET not set — webhook endpoint will be unprotected until you set this.")
if not RENDER_EXTERNAL_URL:
    logger.info("RENDER_EXTERNAL_URL not set. Webhook won't be auto-registered on startup.")

application = Application.builder().token(BOT_TOKEN).build()

# -------------------------
# FastAPI app (webhook receiver)
# -------------------------
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "NaijaPrizeGate bot is running 🚀"}

# -----------------------------
# Database Setup (SQLAlchemy)
# -----------------------------
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, DateTime, Boolean, Float
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, ForeignKey, Text


# Use SQLite for now (Render supports Postgres later if needed)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bot.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

from fastapi import Request, HTTPException
from telegram import Bot

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
# Payment table
# -----------------------------
class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)       # link to User.id
    amount = Column(Float, nullable=False)      # payment amount
    package = Column(String(50))                # e.g., "N2000 = 5 tries"
    status = Column(String(20), default="pending")  # pending, confirmed, failed
    created_at = Column(DateTime, default=datetime.utcnow)

class TransactionLog(Base):
    __tablename__ = "transaction_logs"

    id = Column(Integer, primary_key=True, index=True)
    tx_ref = Column(String, index=True)          # reference from Flutterwave
    status = Column(String, index=True)          # e.g. "successful", "failed"
    amount = Column(Integer)                     # amount paid
    raw_data = Column(Text)                      # full JSON payload
    created_at = Column(DateTime, default=datetime.utcnow)

# -----------------------------
# Play (try luck) table
# -----------------------------
class Play(Base):
    __tablename__ = "plays"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)        # link to User.id
    outcome = Column(String(50))                 # e.g., "win", "lose"
    prize = Column(String(255), nullable=True)   # what they won (if any)
    created_at = Column(DateTime, default=datetime.utcnow)

# -----------------------------
# Create tables if not exist
# -----------------------------
Base.metadata.create_all(bind=engine)

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

HELP_MSG = (
    "ℹ️ *How it works:*\n\n"
    "1️⃣ Pick a package (₦500, ₦2000, or ₦5000).\n"
    "2️⃣ Get your tries credited.\n"
    "3️⃣ Each try is a chance to win the iPhone!\n\n"
    f"Winner unboxing videos will be posted in {PUBLIC_CHANNEL}."
)



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

# -----------------------------
# Step 4: Start & Help Handlers
# -----------------------------

WELCOME_TEXT = (
    "👋 Welcome to *NaijaPrizeGate!* 🎉\n\n"
    "🔥 Try your luck to win an *iPhone 16 Pro Max* 📱🔥\n\n"
    "Here’s how it works:\n"
    "1️⃣ Buy tries (₦500 = 1 try, ₦2000 = 5 tries, ₦5000 = 15 tries)\n"
    "2️⃣ Press *🎰 Try Luck* to spin the slot machine\n"
    "3️⃣ Each spin is a chance to win the iPhone!\n\n"
    "🎥 Winner unboxing videos will be posted in "
    f"{PUBLIC_CHANNEL} — don’t miss them!\n\n"
    "👉 Tap a button below to get started!"
)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command"""
    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode="MarkdownV2",
        reply_markup=main_menu_keyboard()
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /help command"""
    help_text = (
        "ℹ️ *How to use this bot:*\n\n"
        "• /start → Show welcome message + menu\n"
        "• 💳 Buy Tries → Select a package & pay\n"
        "• 🎰 Try Luck → Spin the slot machine\n"
        "• 📊 My Balance → Check how many tries you have left\n\n"
    )
    await update.message.reply_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=main_menu_keyboard()
    )

async def free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📘 Follow on Facebook", url="https://web.facebook.com/Naijaprizegate")],
        [InlineKeyboardButton("📸 Follow on Instagram", url="https://www.instagram.com/naijaprizegate/")],
        [InlineKeyboardButton("🎵 Follow on TikTok", url="https://www.tiktok.com/@naijaprizegate")],
        [InlineKeyboardButton("▶️ Subscribe on YouTube", url="https://www.youtube.com/@Naijaprizegate")],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "🎉🔥 **WIN AN iPhone 16 Pro Max!** 🔥🎉\n\n"
        "💎 Here’s your chance to grab **FREE TRIES** in our Lucky Draw Campaign!\n\n"
        "✅ All you need to do is support us by following/subscribing on our platforms:\n\n"
        "👉 Facebook\n"
        "👉 Instagram\n"
        "👉 TikTok\n"
        "👉 YouTube\n\n"
        "📲 Just click the buttons below ⬇️ and show love ❤️\n\n"
        "🎁 Every support = **extra free tries** towards winning your dream iPhone 📱✨"
    )

    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

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
                f"Hello {uname}, you don’t have an account yet.\n\n"
                "👉 Tap *Pay Now* to buy tries and start playing 🎰"
            )
        else:
            paid = u.tries or 0
            bonus = u.bonus_tries or 0
            total = paid + bonus
            text = (
                f"📊 *Your Balance:*\n\n"
                f"• Paid tries: *{paid}*\n"
                f"• Bonus tries: *{bonus}*\n"
                f"• Total: *{total}*\n\n"
                "👉 Use *Try Luck 🎰* to spin!"
            )

        await update.message.reply_text(
            text,
            parse_mode="MarkdownV2",
            reply_markup=main_menu_keyboard()
        )
    finally:
        db.close()

# Step 6: Payment Handler

# /pay command - show package options
async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Select a package below to proceed with payment:",
        reply_markup=packages_keyboard()
    )

# Callback when a package is chosen
async def handle_package_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data  # "package_500", "package_2000", etc.

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
            "redirect_url": "https://yourdomain.com/payment/verify",  # adjust this
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
                    f"✅ You selected *₦{amount} package*.\n\n"
                    f"Click below to complete your payment securely:\n\n"
                    f"{checkout_link}"
                ),
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                "❌ Sorry, something went wrong creating your payment link. Please try again."
            )

    elif choice == "cancel":
        await query.edit_message_text("❌ Payment cancelled. Returning to main menu.")


# Callback when user presses "Back" during payment flow
async def handle_payment_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Return to package selection menu
    await query.edit_message_text(
        "🔙 Select a package below to proceed with payment:",
        reply_markup=packages_keyboard()
    )


# Callback when user presses "Cancel" during payment flow
async def handle_payment_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Cancel payment and return to main menu
    await query.edit_message_text(
        "❌ Payment cancelled. Returning to main menu."
    )

from telegram import InlineKeyboardMarkup, InlineKeyboardButton

TRANSACTIONS_PER_PAGE = 5

from datetime import datetime, timedelta
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

TRANSACTIONS_PER_PAGE = 5  # how many logs per page

# ---------------------------
# /transactions command
# ---------------------------
async def transactions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ You are not authorized to use this command.")
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
        await update.message.reply_text("📂 No transactions logged yet.")
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

    msg = f"📑 *Transactions ({period.capitalize()} — Page {page+1})*\n\n"
    msg += f"✅ Success: {success_pct}% {make_bar(success_pct, '🟩')}\n"
    msg += f"❌ Failed: {fail_pct}% {make_bar(fail_pct, '🟥')}\n\n"

    # transaction list
    for log in logs:
        msg += (
            f"🆔 `{log.tx_ref}`\n"
            f"💰 Amount: ₦{log.amount}\n"
            f"📌 Status: {'✅ Success' if log.status == 'success' else '❌ ' + log.status}\n"
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
        await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=keyboard)

# ---------------------------
# Pagination handler
# ---------------------------
async def transactions_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

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

async def stat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ You are not authorized to use this command.")
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
        await update.message.reply_text(f"📊 No transactions found for *{arg}* period.", parse_mode="Markdown")
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
        f"📊 *Bot Stats* ({arg})\n\n"
        f"✅ Successful: {success} ({success_pct}%)\n{bar(success_pct, '🟩')}\n\n"
        f"❌ Failed: {failed} ({failed_pct}%)\n{bar(failed_pct, '🟥')}\n\n"
        f"⏳ Pending: {pending} ({pending_pct}%)\n{bar(pending_pct, '🟨')}\n\n"
        f"📂 Total Transactions: {total}"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")

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
                    tries = PACKAGES.get(payment.amount, 0)
                    user.balance += tries

                    # Confirmation message
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=(
                            f"🎉 *Payment confirmed!*\n\n"
                            f"✅ Amount: ₦{payment.amount}\n"
                            f"🎰 You have been credited with *{tries} tries*.\n"
                            f"📊 New balance: {user.balance} tries."
                        ),
                        parse_mode="Markdown"
                    )

            else:
                # ❌ Payment failed or abandoned
                payment.status = "failed"

                if user:
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=(
                            f"⚠️ Payment *failed* or was not completed.\n\n"
                            f"❌ Amount: ₦{payment.amount}\n"
                            "Please try again from the /pay menu."
                        ),
                        parse_mode="Markdown"
                    )

        session.commit()
        session.close()

        return {"status": "ok"}

    except Exception as e:
        print("Webhook error:", str(e))
        raise HTTPException(status_code=500, detail="Webhook processing failed")


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
