import os
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Response, Header
from fastapi.responses import JSONResponse
import uvicorn
import httpx  # 🔹 added for Flutterwave requests

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
SECRET_HASH = os.getenv("FLW_SECRET_HASH")
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")  # 🔹 Flutterwave secret key
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./db.sqlite3")

# Telegram webhook secret (protects against fake POSTs)
WEBHOOK_SECRET = os.getenv("TG_WEBHOOK_SECRET", "my-secret")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not SECRET_HASH:
    logger.warning("⚠️ FLW_SECRET_HASH not set — webhook verification will FAIL in production!")
if not FLW_SECRET_KEY:
    logger.warning("⚠️ FLW_SECRET_KEY not set — Flutterwave dynamic payments will FAIL!")

# =========================
# Database
# =========================
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# ---------- Tables ----------
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
# Helpers
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

# ---------- Handlers ----------
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
    await ensure_user(update)
    await update.message.reply_text(WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN)

# 🔹 NEW pay_cmd (dynamic Flutterwave payment)
async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        u = await ensure_user(update)
        tx_ref = f"TG{u.tg_id}-{int(datetime.utcnow().timestamp())}"

        url = "https://api.flutterwave.com/v3/payments"
        headers = {
            "Authorization": f"Bearer {FLW_SECRET_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "tx_ref": tx_ref,
            "amount": "500",
            "currency": "NGN",
            "redirect_url": "https://naijaprizegate-bot-oo2x.onrender.com/payment/thanks",
            "customer": {
                "email": f"user{u.tg_id}@naijaprizegate.com",
                "phonenumber": "0000000000",
                "name": u.username or str(u.tg_id),
            },
            "customizations": {
                "title": "NaijaPrizeGate",
                "description": "Try your luck for iPhone 16 Pro Max 🎁",
                "logo": "https://your-logo-url.com/logo.png",
            },
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload)
            data = resp.json()

        if data.get("status") == "success":
            link = data["data"]["link"]
            await update.message.reply_text(
                f"💳 Pay ₦500 using this secure link:\n\n{link}\n\n"
                f"Reference (save this): {tx_ref}"
            )
        else:
            await update.message.reply_text("⚠️ Payment link could not be generated. Try again later.")
    finally:
        db.close()

async def tryluck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        u = await ensure_user(update)
        if not u.has_paid:
            await update.message.reply_text("⚠️ You haven’t paid yet. Please pay ₦500 first using /pay 💳")
            return
        counter = get_counter(db) + 1
        set_counter(db, counter)
        await update.message.reply_text("🎰 Spinning…")
        play = Play(tg_id=u.tg_id)
        db.add(play)
        # --- Win condition
        if counter >= WIN_THRESHOLD:
            set_counter(db, 0)
            from random import randint
            code = f"{randint(1000,9999)}-{randint(1000,9999)}"
            w = Winner(tg_id=u.tg_id, username=u.username, code=code)
            db.add(w)
            u.tries += 1
            db.merge(u)
            db.commit()
            await update.message.reply_text(
                f"🎉 CONGRATULATIONS! You WON!\nWinner Code: *{code}*",
                parse_mode=ParseMode.MARKDOWN,
            )
            # Announce in channel
            try:
                await context.bot.send_message(
                    chat_id=PUBLIC_CHANNEL,
                    text=f"🏆 WINNER: @{(u.username or 'unknown')} Code: {code}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.error(f"Publish winner error: {e}")
            # Notify admin
            if ADMIN_USER_ID:
                try:
                    await context.bot.send_message(
                        chat_id=int(ADMIN_USER_ID),
                        text=f"✅ WINNER ALERT: @{u.username}, ID: {u.tg_id}, Code: {code}",
                    )
                except Exception as e:
                    logger.warning(f"Admin notify failed: {e}")
            play.result = "win"
            db.merge(play)
            db.commit()
        else:
            u.tries += 1
            db.merge(u)
            db.commit()
            await update.message.reply_text("❌ Not a winner this time. Try again!")
    finally:
        db.close()

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_USER_ID or str(update.effective_user.id) != str(ADMIN_USER_ID):
        await update.message.reply_text("⛔ You are not authorized.")
        return
    db = SessionLocal()
    try:
        counter = get_counter(db)
        paid = db.query(User).filter(User.has_paid == True).count()
        total_users = db.query(User).count()
        wins = db.query(Winner).count()
        await update.message.reply_text(
            f"📊 Counter: {counter}/{WIN_THRESHOLD}\n👥 Users: {total_users} (paid: {paid})\n🏆 Winners: {wins}"
        )
    finally:
        db.close()

async def echo_autowelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await autowelcome(update, context)
    if update.message and update.message.text and not update.message.text.startswith("/"):
        await update.message.reply_text("Use /pay to begin, then /tryluck after confirmation ✨")

# =========================
# FastAPI (Webhook)
# =========================
api = FastAPI()

@api.get("/")
async def root():
    return {"status": "ok", "service": "NaijaPrizeGate"}

@api.head("/")
async def head_root():
    return Response(status_code=200)

@api.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_secret: str = Header(None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    if WEBHOOK_SECRET and x_telegram_secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret token")
    data = await request.json()
    update = Update.de_json(data, app_telegram.bot)
    await app_telegram.process_update(update)
    return {"ok": True}

@api.post("/webhooks/flutterwave")
async def flutterwave_webhook(request: Request):
    signature = request.headers.get("verif-hash") or request.headers.get("Verif-Hash")
    if not SECRET_HASH or signature != SECRET_HASH:
        raise HTTPException(status_code=401, detail="Invalid signature")
    payload = await request.json()
    data = payload.get("data", {}) or {}
    status = (data.get("status") or "").lower()
    tx_ref = data.get("tx_ref")
    if status not in {"successful", "success"}:
        return JSONResponse({"received": True, "ignored": True})
    tg_id: Optional[int] = None
    if tx_ref and str(tx_ref).startswith("TG") and "-" in str(tx_ref):
        try:
            tg_id = int(str(tx_ref).split("-", 1)[0].replace("TG", ""))
        except Exception:
            tg_id = None
    if not tg_id:
        logger.warning(f"Webhook without tg_id mapping. tx_ref={tx_ref}")
        return JSONResponse({"received": True, "mapped": False})
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
    try:
        if app_telegram:
            await app_telegram.bot.send_message(
                chat_id=int(tg_id),
                text="✅ Payment confirmed! You can now use /tryluck 🎰",
            )
    except Exception as e:
        logger.warning(f"Could not DM user after payment: {e}")
    return {"received": True}

# =========================
# Bootstrapping (Webhook only, no polling)
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
    app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_autowelcome))

    await app_telegram.initialize()
    await app_telegram.start()
    logger.info("✅ Telegram bot started (webhook mode, no polling).")

async def on_shutdown():
    if app_telegram:
        try:
            await app_telegram.stop()
            await app_telegram.shutdown()
        except Exception as e:
            logger.warning(f"Bot shutdown warning: {e}")
    logger.info("✅ Telegram bot stopped.")

@api.on_event("startup")
async def _startup_event():
    await on_startup()

@api.on_event("shutdown")
async def _shutdown_event():
    await on_shutdown()

if __name__ == "__main__":
    uvicorn.run("app:api", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
