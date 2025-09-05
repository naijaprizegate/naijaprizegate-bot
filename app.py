# NaijaPrizeGate Bot (Merged + Clean) with /stats
# ==============================================

import os
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
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")  # must be set to your Telegram numeric ID (string is fine)
PUBLIC_CHANNEL = os.getenv("PUBLIC_CHANNEL", "@NaijaPrizeGateWinners")
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", 14600))
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")  # set this in Flutterwave dashboard (Webhook settings)
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")    # Flutterwave secret key
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./db.sqlite3")
WEBHOOK_SECRET = os.getenv("TG_WEBHOOK_SECRET", "my-secret")  # Telegram webhook secret token
PAY_REDIRECT_URL = os.getenv("PAY_REDIRECT_URL", "https://naijaprizegate-bot-oo2x.onrender.com/payment/verify")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not FLW_SECRET_KEY:
    logger.warning("⚠️ FLW_SECRET_KEY not set — Flutterwave dynamic payments will FAIL!")
if not FLW_SECRET_HASH:
    logger.warning("⚠️ FLW_SECRET_HASH not set — webhook signature verification disabled (not secure)!")

# =========================
# Database (SQLAlchemy)
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
    has_paid = Column(Boolean, default=False)  # marks if user has 1 credit to play
    tries = Column(Integer, default=0)         # total attempts made
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
    await autowelcome(update, context)

# 🔹 Pay command (dynamic Flutterwave link)
async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update)
    user_id = update.effective_user.id
    amount = "500"  # string works fine with Flutterwave API
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
            "email": f"user{user_id}@naijaprizegate.local",
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
    except Exception as e:
        logger.exception("Failed to contact Flutterwave API")
        await update.message.reply_text("⚠️ Could not contact payment provider. Try again later.")
        return

    if data.get("status") == "success" and data.get("data", {}).get("link"):
        payment_link = data["data"]["link"]
        # send link and short instruction
        await update.message.reply_text(
            "💳 Your payment link (valid for a short time):\n\n"
            f"{payment_link}\n\n"
            "👉 After completing payment, return here and type /tryluck 🎰\n"
            "If the link expired, type /pay again to get a new one."
        )
        logger.info(f"Generated payment link for tg={user_id} tx_ref={tx_ref}")
    else:
        logger.warning("Flutterwave response did not contain a usable link: %s", data)
        await update.message.reply_text("⚠️ Sorry, could not create payment link. Try again later.")

# 🔹 Tryluck command: consumes 1 paid credit (has_paid), so user must pay again for another try
async def tryluck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not has_paid(user_id):
        await update.message.reply_text("⚠️ You haven’t paid yet. Please pay ₦500 first using /pay 💳")
        return

    db = SessionLocal()
    try:
        u = await ensure_user(update)

        # consume the paid credit
        u.has_paid = False

        counter = get_counter(db) + 1
        set_counter(db, counter)

        await update.message.reply_text("🎰 Spinning…")
        play = Play(tg_id=u.tg_id)
        db.add(play)

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
                f"🎉 CONGRATULATIONS! You WON!\nWinner Code: *{code}*\n\nSend your Name, Phone & Address to the admin.",
                parse_mode=ParseMode.MARKDOWN,
            )

            # announce publicly
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

            # notify admin
            if ADMIN_USER_ID:
                try:
                    await context.bot.send_message(
                        chat_id=int(ADMIN_USER_ID),
                        text=f"✅ WINNER ALERT: @{u.username}, ID: {u.tg_id}, Code: {code}"
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
            play.result = "lose"
            await update.message.reply_text("❌ Not a winner this time. Try again!")
    finally:
        db.close()

async def echo_autowelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # welcome first-time users on any message or /start
    await autowelcome(update, context)
    # short guidance if message is plain text (not a command)
    if update.message and update.message.text and not update.message.text.startswith("/"):
        await update.message.reply_text("Use /pay to get your link, then /tryluck after payment ✨")

# =========================
# Admin: /stats command
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only admin can run this
    if not ADMIN_USER_ID or str(update.effective_user.id) != str(ADMIN_USER_ID):
        await update.message.reply_text("⛔ You are not authorized to view stats.")
        return

    db = SessionLocal()
    try:
        counter = get_counter(db)
        paid = db.query(User).filter(User.has_paid == True).count()
        total_users = db.query(User).count()
        wins = db.query(Winner).count()
        plays = db.query(Play).count()
        await update.message.reply_text(
            (
                f"📊 Counter: {counter}/{WIN_THRESHOLD}\n"
                f"👥 Users: {total_users} (paid: {paid})\n"
                f"🎮 Plays logged: {plays}\n"
                f"🏆 Winners: {wins}"
            )
        )
    finally:
        db.close()

# =========================
# FastAPI (Webhook + simple pages)
# =========================
api = FastAPI()

@api.get("/")
async def root():
    return {"status": "ok", "service": "NaijaPrizeGate"}

@api.get("/payment/verify")
async def payment_verify():
    # simple page shown after a user finishes payment (redirect from Flutterwave)
    html = """
    <html><body>
      <h2>Payment received (or in process)</h2>
      <p>✅ Thank you. Please return to Telegram and type <strong>/tryluck</strong> to use your attempt.</p>
      <p>If your payment was successful but /tryluck says you haven't paid, wait a few seconds for webhook processing.</p>
    </body></html>
    """
    return HTMLResponse(content=html, status_code=200)

# Telegram webhook (if you use Telegram webhook to deliver updates to this service)
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

# Flutterwave webhook
@api.post("/webhooks/flutterwave")
async def flutterwave_webhook(request: Request):
    # verify signature header if configured
    signature = request.headers.get("verif-hash") or request.headers.get("Verif-Hash")
    if FLW_SECRET_HASH:
        if not signature or signature != FLW_SECRET_HASH:
            logger.warning("Invalid webhook signature: %s", signature)
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        # not recommended for production
        logger.warning("FLW_SECRET_HASH not set — webhook not signature-verified")

    payload = await request.json()
    data = payload.get("data", {}) or {}
    status = (data.get("status") or "").lower()
    tx_ref = data.get("tx_ref")

    if status not in {"successful", "success"}:
        # ignore non-success events
        return JSONResponse({"received": True, "ignored": True})

    if tx_ref and str(tx_ref).startswith("TG-"):
        try:
            # our tx_ref format: TG-<tg_id>-<random>
            parts = str(tx_ref).split("-")
            tg_id = int(parts[1])
            mark_user_paid(tg_id)
            logger.info("✅ Payment confirmed for Telegram user %s (tx_ref=%s)", tg_id, tx_ref)

            # notify user in Telegram (best-effort)
            try:
                if app_telegram:
                    await app_telegram.bot.send_message(
                        chat_id=int(tg_id),
                        text="✅ Payment confirmed! You can now use /tryluck 🎰"
                    )
            except Exception as e:
                logger.warning("Could not DM user after payment: %s", e)
        except Exception as e:
            logger.exception("Failed to process webhook tx_ref=%s: %s", tx_ref, e)
            return JSONResponse({"received": True, "error": "processing_failed"})
    else:
        logger.warning("Webhook without tg_id mapping. tx_ref=%s", tx_ref)
        return JSONResponse({"received": True, "mapped": False})

    return {"received": True}

# =========================
# Bootstrapping (Telegram bot in webhook mode)
# =========================
async def on_startup():
    global app_telegram
    app_telegram = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # register handlers
    app_telegram.add_handler(CommandHandler("start", start_cmd))
    app_telegram.add_handler(CommandHandler("pay", pay_cmd))
    app_telegram.add_handler(CommandHandler("tryluck", tryluck_cmd))
    app_telegram.add_handler(CommandHandler("stats", stats_cmd))
    app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_autowelcome))

    await app_telegram.initialize()
    await app_telegram.start()
    logger.info("✅ Telegram bot started (webhook mode).")

async def on_shutdown():
    if app_telegram:
        try:
            await app_telegram.stop()
            await app_telegram.shutdown()
        except Exception as e:
            logger.warning("Bot shutdown warning: %s", e)
    logger.info("✅ Telegram bot stopped.")

@api.on_event("startup")
async def _startup_event():
    await on_startup()

@api.on_event("shutdown")
async def _shutdown_event():
    await on_shutdown()

if __name__ == "__main__":
    uvicorn.run("app:api", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
