import os
import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

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
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")  # numeric telegram user id as string
PAY_LINK = os.getenv("PAY_LINK")  # fallback/link hub visible to users
PUBLIC_CHANNEL = os.getenv("PUBLIC_CHANNEL", "@NaijaPrizeGateWinners")  # channel username or chat_id
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", 14600))
SECRET_HASH = os.getenv("FLW_SECRET_HASH")  # Flutterwave webhook verification (Dashboard > Settings)
FLW_PUBLIC_KEY = os.getenv("FLW_PUBLIC_KEY")
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./db.sqlite3")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not SECRET_HASH:
    logger.warning("FLW_SECRET_HASH not set — webhook verification will FAIL in production!")

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
    result = Column(String(16), default="lose")  # "win" or "lose"

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

# Helpers for meta counter
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

# =========================
# Telegram Bot (python-telegram-bot v20+)
# =========================
app_telegram: Application

WELCOME_TEXT = (
    "🎉 Welcome to *NaijaPrizeGate!*\n\n"
    "Pay ₦500 to try your luck for an iPhone 16 Pro Max!\n\n"
    "👉 Use /pay to get your unique payment link\n"
    "👉 After payment is confirmed, use /tryluck\n\n"
    "Good luck! 🍀"
)

async def ensure_user(update: Update) -> User:
    db = SessionLocal()
    try:
        uid = update.effective_user.id
        u = db.query(User).filter(User.tg_id == uid).one_or_none()
        if not u:
            u = User(
                tg_id=uid,
                username=(update.effective_user.username or ""),
            )
            db.add(u)
            db.commit()
        return u
    finally:
        db.close()

async def autowelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:  # ignore non-message updates
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
    await ensure_user(update)
    await update.message.reply_text(WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN)

async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        u = await ensure_user(update)
        # Provide static pay link OR instruct to DM proof; ideally generate tx_ref & dynamic link.
        # You can also construct a querystring to carry tg_id/tx_ref if your link supports it.
        tx_ref = f"TG{u.tg_id}-{int(datetime.utcnow().timestamp())}"
        await update.message.reply_text(
            (
                "💳 Pay ₦500 using your personal link below. After payment, wait a few seconds for confirmation.\n\n"
                f"Payment link: {PAY_LINK}\n"
                f"Reference (save this): {tx_ref}"
            )
        )
    finally:
        db.close()

async def tryluck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        u = await ensure_user(update)
        if not u.has_paid:
            await update.message.reply_text(
                "⚠️ You haven’t paid yet. Please pay ₦500 first using /pay 💳"
            )
            return

        # increment global counter stored in DB
        counter = get_counter(db) + 1
        set_counter(db, counter)

        await update.message.reply_text("🎰 Spinning…")

        play = Play(tg_id=u.tg_id)
        db.add(play)

        if counter >= WIN_THRESHOLD:
            # reset counter & mark winner
            set_counter(db, 0)
            from random import randint
            code = f"{randint(1000, 9999)}-{randint(1000, 9999)}"

            w = Winner(tg_id=u.tg_id, username=u.username, code=code)
            db.add(w)

            u.tries += 1
            db.merge(u)
            db.commit()

            await update.message.reply_text(
                (
                    "🎉 CONGRATULATIONS! You WON the iPhone 16 Pro Max!\n\n"
                    f"Your Winner Code: *{code}*\n\n"
                    "📦 Please send your *Name, Phone, and Address* to the admin."
                ),
                parse_mode=ParseMode.MARKDOWN,
            )

            # Publish to winners channel
            try:
                await context.bot.send_message(
                    chat_id=PUBLIC_CHANNEL,
                    text=(
                        "🏆 *WINNER ANNOUNCEMENT*\n"
                        f"User: @{(u.username or 'unknown')} (ID: {u.tg_id})\n"
                        f"Code: {code}\n"
                        f"Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.error(f"Failed to publish winner: {e}")

            # Notify admin
            if ADMIN_USER_ID:
                await context.bot.send_message(
                    chat_id=int(ADMIN_USER_ID),
                    text=f"✅ WINNER ALERT!\nUser: @{u.username}\nID: {u.tg_id}\nCode: {code}",
                )

            play.result = "win"
            db.merge(play)
            db.commit()
        else:
            u.tries += 1
            db.merge(u)
            db.commit()
            await update.message.reply_text(
                "❌ Sorry, not a winner this time. Try again or share our page for a bonus! 🎁"
            )
    finally:
        db.close()

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_USER_ID):
        await update.message.reply_text("⛔ You are not authorized.")
        return
    db = SessionLocal()
    try:
        counter = get_counter(db)
        paid = db.query(User).filter(User.has_paid == True).count()
        total_users = db.query(User).count()
        wins = db.query(Winner).count()
        await update.message.reply_text(
            (
                f"📊 Counter: {counter}/{WIN_THRESHOLD}\n"
                f"👥 Users: {total_users} (paid: {paid})\n"
                f"🏆 Winners: {wins}"
            )
        )
    finally:
        db.close()

# A catch-all to auto-welcome new users who send any text without /start
async def echo_autowelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await autowelcome(update, context)
    # Optionally guide them
    if update.message and update.message.text and update.message.text.startswith("/") is False:
        await update.message.reply_text("Use /pay to begin, then /tryluck after confirmation ✨")

# =========================
# FastAPI for Flutterwave Webhook
# =========================
api = FastAPI()

@api.get("/")
async def root():
    return {"status": "ok", "service": "NaijaPrizeGate"}

@api.post("/webhooks/flutterwave")
async def flutterwave_webhook(request: Request):
    # Verify signature using secret hash per Flutterwave docs
    signature = request.headers.get("verif-hash")
    if not SECRET_HASH or signature != SECRET_HASH:
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event = payload.get("event") or payload.get("event.type") or ""

    data = payload.get("data", {})
    status = data.get("status") or payload.get("status")
    tx_ref = data.get("tx_ref") or payload.get("tx_ref")
    amount = data.get("amount") or (data.get("amount_settled") if isinstance(data, dict) else None)

    # Only act on successful charge events
    if str(status).lower() not in {"successful", "success"}:
        return JSONResponse({"received": True, "ignored": True})

    # Expect tx_ref to contain TG<tg_id>-<timestamp>
    tg_id: Optional[int] = None
    if tx_ref and str(tx_ref).startswith("TG") and "-" in str(tx_ref):
        try:
            tg_id = int(str(tx_ref).split("-", 1)[0].replace("TG", ""))
        except Exception:  # fall back to meta
            tg_id = None

    if tg_id is None:
        # try to pull from meta/customer fields if you attached it when generating the payment
        meta = data.get("meta") or {}
        tg_id = meta.get("tg_id") if isinstance(meta, dict) else None

    if not tg_id:
        logger.warning(f"Webhook without tg_id mapping. tx_ref={tx_ref}")
        return JSONResponse({"received": True, "mapped": False})

    # Mark user as paid
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.tg_id == int(tg_id)).one_or_none()
        if not u:
            u = User(tg_id=int(tg_id), username="")
            db.add(u)
            db.commit()
        u.has_paid = True
        db.merge(u)
        db.commit()
    finally:
        db.close()

    # Optionally notify user in Telegram (best-effort)
    try:
        await app_telegram.bot.send_message(
            chat_id=int(tg_id),
            text=(
                "✅ Payment confirmed! You can now use /tryluck to spin for the iPhone 16 Pro Max.\n"
                "Good luck! 🍀"
            ),
        )
    except Exception as e:
        logger.warning(f"Could not DM user after payment: {e}")

    return {"received": True}

# =========================
# Bootstrapping both FastAPI (for webhooks) and Telegram bot
# =========================
async def on_startup():
    global app_telegram
    app_telegram = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # Handlers
    app_telegram.add_handler(CommandHandler("start", start_cmd))
    app_telegram.add_handler(CommandHandler("pay", pay_cmd))
    app_telegram.add_handler(CommandHandler("tryluck", tryluck_cmd))
    app_telegram.add_handler(CommandHandler("stats", stats_cmd))

    # Auto-welcome on any text (first-time users don't need to type /start)
    app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_autowelcome))

    # Run bot as a background task (long polling)
    asyncio.create_task(app_telegram.initialize())
    asyncio.create_task(app_telegram.start())
    asyncio.create_task(app_telegram.updater.start_polling(drop_pending_updates=True))

async def on_shutdown():
    await app_telegram.updater.stop()
    await app_telegram.stop()
    await app_telegram.shutdown()

# FastAPI lifespan hooks
@api.on_event("startup")
async def _startup_event():
    await on_startup()

@api.on_event("shutdown")
async def _shutdown_event():
    await on_shutdown()

if __name__ == "__main__":
    # Local dev: uvicorn app:api --reload
    uvicorn.run("app:api", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
