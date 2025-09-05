# NaijaPrizeGate Bot Full Version
# ====================================================================
import os
import re
import uuid
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse, HTMLResponse
import uvicorn
import httpx

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, BigInteger, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, ContextTypes,
    MessageHandler, filters
)

# =========================
# Logging
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("naijaprizegate")

# =========================
# Environment
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
PUBLIC_CHANNEL = os.getenv("PUBLIC_CHANNEL", "@NaijaPrizeGateWinners")
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", 14600))
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./db.sqlite3")
WEBHOOK_SECRET = os.getenv("TG_WEBHOOK_SECRET", "my-secret")
PAY_REDIRECT_URL = os.getenv(
    "PAY_REDIRECT_URL",
    "https://naijaprizegate-bot-oo2x.onrender.com/payment/verify"
)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not FLW_SECRET_KEY:
    logger.warning("⚠️ FLW_SECRET_KEY not set — Flutterwave dynamic payments will FAIL!")
if not FLW_SECRET_HASH:
    logger.warning("⚠️ FLW_SECRET_HASH not set — webhook signature verification disabled!")

# =========================
# Database
# =========================
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String(255))
    first_seen = Column(DateTime, default=datetime.utcnow)
    has_paid = Column(Boolean, default=False)
    tries = Column(Integer, default=0)
    welcomed = Column(Boolean, default=False)

class Play(Base):
    __tablename__ = "plays"
    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, index=True, nullable=False)
    ts = Column(DateTime, default=datetime.utcnow)
    result = Column(String(16), default="lose")

class Meta(Base):
    __tablename__ = "meta"
    key = Column(String(64), primary_key=True)
    value = Column(Text)

class Winner(Base):
    __tablename__ = "winners"
    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, index=True, nullable=False)
    username = Column(String(255))
    code = Column(String(32), unique=True)
    ts = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)

# =========================
# DB Helpers
# =========================
def get_counter(db) -> int:
    row = db.query(Meta).filter(Meta.key == "try_counter").one_or_none()
    return int(row.value) if row else 0

def set_counter(db, value: int):
    row = db.query(Meta).filter(Meta.key == "try_counter").one_or_none()
    if not row:
        row = Meta(key="try_counter", value=str(value))
        db.add(row)
    else:
        row.value = str(value)
    db.commit()

def mark_user_paid(tg_id: int):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_id == tg_id).one_or_none()
        if not user:
            user = User(tg_id=tg_id, username="")
            db.add(user)
        user.has_paid = True
        db.merge(user)
        db.commit()
    finally:
        db.close()

def has_paid(tg_id: int) -> bool:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_id == tg_id).one_or_none()
        return bool(user and user.has_paid)
    finally:
        db.close()

# =========================
# Telegram Bot
# =========================
app_telegram: Optional[Application] = None

WELCOME_TEXT = (
    "🎉 Welcome to *NaijaPrizeGate!*\n\n"
    "Pay ₦500 to try your luck for an iPhone 16 Pro Max!\n\n"
    "👉 Use /pay to get your unique payment link\n"
    "👉 After payment is confirmed, use /tryluck\n\n"
    "Good luck! 🍀"
)

# ---------- Helpers ----------
def is_valid_email(email: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", email))

async def ensure_user(update: Update) -> User:
    db = SessionLocal()
    try:
        uid = update.effective_user.id
        u = db.query(User).filter(User.tg_id == uid).one_or_none()
        if not u:
            u = User(tg_id=uid, username=(update.effective_user.username or ""))
            db.add(u)
            db.commit()
        return u
    finally:
        db.close()

# ---------- Handlers ----------
async def autowelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    db = SessionLocal()
    try:
        u = await ensure_user(update)
        if not u.welcomed:
            await update.message.reply_text(WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN)
            u.welcomed = True
            db.merge(u)
            db.commit()
    finally:
        db.close()

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await autowelcome(update, context)

# 🔹 Step 1: Ask for email
async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update)
    await update.message.reply_text("📧 Please reply with your email address for payment receipt.")
    context.user_data["awaiting_email"] = True

# 🔹 Step 2: Handle email + generate Flutterwave link
async def handle_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_email"):
        return

    email = update.message.text.strip()
    if not is_valid_email(email):
        await update.message.reply_text("⚠️ That doesn’t look like a valid email. Try again.")
        return

    context.user_data["awaiting_email"] = False
    user_id = update.effective_user.id
    amount = "500"
    tx_ref = f"TG-{user_id}-{uuid.uuid4().hex[:8]}"

    url = "https://api.flutterwave.com/v3/payments"
    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "tx_ref": tx_ref,
        "amount": amount,
        "currency": "NGN",
        "redirect_url": PAY_REDIRECT_URL,
        "customer": {
            "email": email,
            "phonenumber": "0000000000",
            "name": (update.effective_user.full_name or str(user_id)),
        },
        "customizations": {
            "title": "NaijaPrizeGate",
            "description": "Pay ₦500 to try your luck!",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            data = resp.json()
    except Exception:
        logger.exception("Failed to contact Flutterwave API")
        await update.message.reply_text("⚠️ Could not contact payment provider. Try again later.")
        return

    if data.get("status") == "success" and data.get("data", {}).get("link"):
        payment_link = data["data"]["link"]
        await update.message.reply_text(
            "💳 Your payment link (valid for a short time):\n\n"
            f"{payment_link}\n\n"
            "👉 After completing payment, return here and type /tryluck 🎰"
        )
        logger.info(f"Generated payment link for tg={user_id} tx_ref={tx_ref} email={email}")
    else:
        logger.warning("Flutterwave response did not contain a usable link: %s", data)
        await update.message.reply_text("⚠️ Sorry, could not create payment link. Try again later.")

# 🔹 Tryluck command
async def tryluck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_paid(user_id):
        await update.message.reply_text("⚠️ You haven’t paid yet. Please pay ₦500 first using /pay 💳")
        return

    db = SessionLocal()
    try:
        counter = get_counter(db) + 1
        set_counter(db, counter)

        play = Play(tg_id=user_id, result="lose")
        db.add(play)

        if counter % WIN_THRESHOLD == 0:
            code = f"WIN-{uuid.uuid4().hex[:6].upper()}"
            winner = Winner(
                tg_id=user_id,
                username=update.effective_user.username or "",
                code=code,
            )
            db.add(winner)
            play.result = "win"
            db.commit()

            await update.message.reply_text(
                f"🎉 Congratulations! You just WON!\n\nYour Winner Code: `{code}`\n\n"
                f"📢 You’ll be featured in {PUBLIC_CHANNEL}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        else:
            db.commit()
            await update.message.reply_text("🙁 Sorry, not a win this time. Try again!")
    finally:
        db.close()

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_USER_ID):
        return
    db = SessionLocal()
    try:
        total_users = db.query(User).count()
        total_paid = db.query(User).filter(User.has_paid == True).count()
        total_plays = db.query(Play).count()
        winners = db.query(Winner).count()
        counter = get_counter(db)

        await update.message.reply_text(
            f"📊 Stats:\n"
            f"Users: {total_users}\n"
            f"Paid: {total_paid}\n"
            f"Plays: {total_plays}\n"
            f"Winners: {winners}\n"
            f"Counter: {counter}"
        )
    finally:
        db.close()

async def echo_autowelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await autowelcome(update, context)
    if update.message and update.message.text and not update.message.text.startswith("/"):
        await update.message.reply_text("Use /pay to get your link, then /tryluck after payment ✨")

# =========================
# FastAPI + Webhooks
# =========================
api = FastAPI()

@api.get("/")
async def root():
    return HTMLResponse("<h3>✅ NaijaPrizeGate Bot is running.</h3>")

@api.get("/payment/verify")
async def verify_payment(request: Request):
    return HTMLResponse(
        "<h3>Payment verification not yet implemented here.</h3>"
    )

@api.post("/telegram/webhook")
async def telegram_webhook(update: dict, x_webhook_secret: str = Header(None)):
    if x_webhook_secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if app_telegram:
        await app_telegram.process_update(Update.de_json(update, app_telegram.bot))
    return JSONResponse({"ok": True})

@api.post("/webhooks/flutterwave")
async def webhook_flutterwave(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = body.get("event")
    data = body.get("data", {})

    if event == "charge.completed" and data.get("status") == "successful":
        tx_ref = data.get("tx_ref")
        if tx_ref and tx_ref.startswith("TG-"):
            tg_id = int(tx_ref.split("-")[1])
            mark_user_paid(tg_id)
            logger.info(f"✅ Payment confirmed for tg_id={tg_id}")
    return JSONResponse({"ok": True})

# =========================
# Bootstrapping
# =========================
async def on_startup():
    global app_telegram
    app_telegram = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    app_telegram.add_handler(CommandHandler("start", start_cmd))
    app_telegram.add_handler(CommandHandler("pay", pay_cmd))
    app_telegram.add_handler(CommandHandler("tryluck", tryluck_cmd))
    app_telegram.add_handler(CommandHandler("stats", stats_cmd))
    app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_email))
    app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_autowelcome))

    await app_telegram.initialize()
    await app_telegram.start()
    logger.info("✅ Telegram bot started (webhook mode).")

async def on_shutdown():
    if app_telegram:
        await app_telegram.stop()
        await app_telegram.shutdown()

if __name__ == "__main__":
    uvicorn.run(
        "app:api",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True,
    )
