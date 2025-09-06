# app.py - NaijaPrizeGate (improved, full version)
# ====================================================================
import os
import re
import uuid
import hmac
import hashlib
import logging
from datetime import datetime
from typing import Optional, Dict

import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Header, Query
from fastapi.responses import JSONResponse, HTMLResponse

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, BigInteger, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, ContextTypes,
    MessageHandler, filters, CallbackQueryHandler
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
# Environment / Config
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
PUBLIC_CHANNEL = os.getenv("PUBLIC_CHANNEL", "@NaijaPrizeGateWinners")
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", 14600))
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")  # webhook verification secret from Flutterwave
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")    # Flutterwave secret key (for API)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./db.sqlite3")
WEBHOOK_SECRET = os.getenv("TG_WEBHOOK_SECRET", "my-secret")
PAY_REDIRECT_URL = os.getenv("PAY_REDIRECT_URL", "https://yourdomain.com/payment/verify")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not FLW_SECRET_KEY:
    logger.warning("⚠️ FLW_SECRET_KEY not set — creating payment links will fail.")
if not FLW_SECRET_HASH:
    logger.warning("⚠️ FLW_SECRET_HASH not set — incoming Flutterwave webhooks will NOT be verified.")

# Package definitions (amount in NGN -> tries credited)
# You can change or add packages here.
PACKAGES = {
    "500": {"amount": 500, "tries": 1, "label": "₦500 — 1 try"},
    "2000": {"amount": 2000, "tries": 5, "label": "₦2000 — 5 tries"},
    "5000": {"amount": 5000, "tries": 15, "label": "₦5000 — 15 tries"},
}

# =========================
# Database (SQLAlchemy)
# =========================
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String(255))
    first_seen = Column(DateTime, default=datetime.utcnow)
    tries = Column(Integer, default=0)       # how many tries user currently has
    welcomed = Column(Boolean, default=False)
    referral_code = Column(String(64), nullable=True)  # optional for future referral feature

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, index=True, nullable=False)
    tx_ref = Column(String(128), unique=True, index=True, nullable=False)
    amount = Column(Integer, nullable=False)
    tries = Column(Integer, nullable=False, default=0)  # number of tries this payment should credit
    status = Column(String(32), default="pending")  # pending / successful / failed
    created_at = Column(DateTime, default=datetime.utcnow)

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

# Create tables if they don't exist (for simple deployments)
Base.metadata.create_all(engine)

# =========================
# DB helper functions
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

def increment_counter(db) -> int:
    """
    Increment and return the new counter.
    Note: This is fine for low-to-moderate traffic. For very high concurrency,
    consider using DB transactions/locks or a Redis counter.
    """
    current = get_counter(db) + 1
    set_counter(db, current)
    return current

def ensure_user_by_update(update: Update):
    """
    Ensure user exists and return the User object (fresh session required).
    This helper doesn't commit closing manager; caller must handle session.
    """
    db = SessionLocal()
    try:
        uid = update.effective_user.id
        u = db.query(User).filter(User.tg_id == uid).one_or_none()
        if not u:
            u = User(tg_id=uid, username=(update.effective_user.username or ""))
            db.add(u)
            db.commit()
            db.refresh(u)
        return u
    finally:
        db.close()

def ensure_user_return_obj(tg_id: int, username: str = ""):
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.tg_id == tg_id).one_or_none()
        if not u:
            u = User(tg_id=tg_id, username=username)
            db.add(u)
            db.commit()
            db.refresh(u)
        return u
    finally:
        db.close()

# =========================
# Telegram bot setup
# =========================
app_telegram: Optional[Application] = None

WELCOME_TEXT = (
    "🎉 Welcome to *NaijaPrizeGate!*\n\n"
    "Buy tries and stand a chance to win an iPhone 16 Pro Max!\n\n"
    "👉 Tap *Pay Now* to pick a package and get a payment link.\n"
    "👉 After payment is confirmed, tap *Try Luck* to play.\n\n"
    "Good luck! 🍀"
)

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Pay Now", callback_data="pay:start")],
        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck:start")],
        [InlineKeyboardButton("📊 My Tries", callback_data="mytries")]
    ])

def packages_keyboard():
    # show package buttons with amounts
    buttons = []
    for key, p in PACKAGES.items():
        buttons.append([InlineKeyboardButton(p["label"], callback_data=f"pay:package:{key}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="pay:back")])
    return InlineKeyboardMarkup(buttons)

# ---------- Helpers ----------
def is_valid_email(email: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", email))

async def create_flutterwave_payment_link(tx_ref: str, amount: int, email: str, name: str) -> Optional[str]:
    """
    Calls Flutterwave /v3/payments to create a checkout link.
    Returns link string or None on failure.
    """
    if not FLW_SECRET_KEY:
        logger.error("FLW_SECRET_KEY not configured.")
        return None

    url = "https://api.flutterwave.com/v3/payments"
    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "tx_ref": tx_ref,
        "amount": str(amount),
        "currency": "NGN",
        "redirect_url": PAY_REDIRECT_URL,
        "customer": {
            "email": email,
            "phonenumber": "0000000000",
            "name": name,
        },
        "customizations": {
            "title": "NaijaPrizeGate",
            "description": f"Pay ₦{amount} to get your tries"
        },
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            data = resp.json()
            if data.get("status") == "success" and data.get("data", {}).get("link"):
                return data["data"]["link"]
            else:
                logger.warning("Flutterwave create payment failed: %s", data)
                return None
    except Exception:
        logger.exception("Failed to contact Flutterwave API")
        return None

# =========================
# Telegram Handlers
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # welcome and show main menu
    db = SessionLocal()
    try:
        uid = update.effective_user.id
        u = db.query(User).filter(User.tg_id == uid).one_or_none()
        if not u:
            u = User(tg_id=uid, username=(update.effective_user.username or ""))
            db.add(u)
            db.commit()
        if not u.welcomed:
            await update.message.reply_text(WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())
            u.welcomed = True
            db.merge(u)
            db.commit()
        else:
            await update.message.reply_text("Welcome back! Use the buttons below:", reply_markup=main_menu_keyboard())
    finally:
        db.close()

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle inline button presses:
    - pay:start -> show packages
    - pay:package:<key> -> prompt for email and set awaiting state
    - pay:back -> back to main
    - tryluck:start -> call tryluck_cmd
    - mytries -> show user's tries
    """
    query = update.callback_query
    await query.answer()  # acknowledge callback quickly
    data = query.data or ""
    user = query.from_user

    if data == "pay:start":
        await query.edit_message_text("Choose a package to buy:", reply_markup=packages_keyboard())
        return

    if data.startswith("pay:package:"):
        parts = data.split(":")
        if len(parts) == 3:
            key = parts[2]
            pkg = PACKAGES.get(key)
            if not pkg:
                await query.edit_message_text("Invalid package selected.")
                return
            # store chosen package in user_data and ask for email
            context.user_data["awaiting_email"] = True
            context.user_data["selected_package"] = key
            await query.edit_message_text(f"You selected *{pkg['label']}*.\n\nPlease reply with your email address for the payment receipt.", parse_mode=ParseMode.MARKDOWN)
            return

    if data == "pay:back":
        await query.edit_message_text("Back to menu:", reply_markup=main_menu_keyboard())
        return

    if data == "tryluck:start":
        # call tryluck logic using same context
        await tryluck_cmd(update, context)
        return

    if data == "mytries":
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.tg_id == user.id).one_or_none()
            tries = u.tries if u else 0
            await query.edit_message_text(f"You have *{tries}* tries remaining.", parse_mode=ParseMode.MARKDOWN)
        finally:
            db.close()
        return

    # Unhandled callback
    await query.edit_message_text("Unknown action. Use /start to show the menu.")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Single text handler used for:
    - accepting emails when awaiting_email is True (from inline package flow)
    - fallback welcome/help message
    """
    if update.message is None:
        return

    text = update.message.text.strip()
    uid = update.effective_user.id
    uname = update.effective_user.username or ""
    # If awaiting_email is set for this user, treat this text as email
    if context.user_data.get("awaiting_email"):
        email = text
        if not is_valid_email(email):
            await update.message.reply_text("⚠️ That doesn’t look like a valid email. Try again.")
            return

        # clear awaiting flag
        context.user_data["awaiting_email"] = False
        selected_key = context.user_data.get("selected_package", "500")
        pkg = PACKAGES.get(selected_key, PACKAGES["500"])
        amount = pkg["amount"]
        tries_to_credit = pkg["tries"]

        # generate tx_ref and save Payment row
        tx_ref = f"TG-{uid}-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            # ensure user
            u = db.query(User).filter(User.tg_id == uid).one_or_none()
            if not u:
                u = User(tg_id=uid, username=uname)
                db.add(u)
                db.commit()
                db.refresh(u)

            payment = Payment(
                tg_id=uid,
                tx_ref=tx_ref,
                amount=amount,
                tries=tries_to_credit,
                status="pending"
            )
            db.add(payment)
            db.commit()
            logger.info(f"Created payment record tx_ref={tx_ref}, tg_id={uid}, amount={amount}, tries={tries_to_credit}")

        finally:
            db.close()

        # create flutterwave link
        link = await create_flutterwave_payment_link(tx_ref=tx_ref, amount=amount, email=email, name=(update.effective_user.full_name or str(uid)))
        if link:
            await update.message.reply_text(
                f"💳 Your payment link (valid for a short time):\n\n{link}\n\n"
                "👉 After completing payment, return here and press Try Luck 🎰 or wait for webhook confirmation.",
                disable_web_page_preview=True
            )
        else:
            await update.message.reply_text("⚠️ Could not create payment link. Try again later.")
        # clear selected_package
        context.user_data.pop("selected_package", None)
        return

    # fallback (not awaiting email)
    # show welcome + menu and quick hint
    await autowelcome_fallback(update, context)

async def autowelcome_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # reply with welcome message and main menu keyboard
    await update.message.reply_text(WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())

async def tryluck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Try luck command: consumes one try and records play.
    Handles both callback query and direct /tryluck command.
    """
    # Determine chat context (callback_query vs message)
    if update.callback_query:
        user = update.callback_query.from_user
        answer_target = update.callback_query
        # close query quick ack is already done in callback handler
    else:
        user = update.effective_user
        answer_target = update.message

    uid = user.id
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.tg_id == uid).one_or_none()
        if not u or u.tries <= 0:
            # no tries
            await answer_target.reply_text("⚠️ You have no tries left. Please buy tries using Pay Now 💳")
            return

        # consume a try
        u.tries -= 1
        # record play (default lose)
        play = Play(tg_id=uid, result="lose")
        db.add(play)
        db.merge(u)
        db.commit()

        # increment global counter
        counter = increment_counter(db)
        logger.info(f"User {uid} played. Counter={counter}, remaining_tries={u.tries}")

        # check win
        if counter % WIN_THRESHOLD == 0:
            # winner!
            code = f"WIN-{uuid.uuid4().hex[:6].upper()}"
            winner = Winner(tg_id=uid, username=(user.username or ""), code=code)
            play.result = "win"
            db.add(winner)
            db.merge(play)
            db.commit()

            await answer_target.reply_text(
                f"🎉 Congratulations! You WON!\n\nYour Winner Code: `{code}`\n\n"
                f"📢 You’ll be featured in {PUBLIC_CHANNEL}",
                parse_mode=ParseMode.MARKDOWN
            )

            # announce in public channel (best effort)
            try:
                await context.bot.send_message(
                    chat_id=PUBLIC_CHANNEL,
                    text=f"🎊 Winner Alert! @{user.username or uid} just won an iPhone 16 Pro Max! Code: {code}"
                )
            except Exception:
                logger.exception("Failed to announce winner in public channel.")
            return
        else:
            await answer_target.reply_text("🙁 Not a win this time. Try again!")
    finally:
        db.close()

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_USER_ID):
        return
    db = SessionLocal()
    try:
        total_users = db.query(User).count()
        total_tries_allocated = sum([u.tries for u in db.query(User).all()])  # small data ok
        total_plays = db.query(Play).count()
        winners = db.query(Winner).count()
        counter = get_counter(db)
        await update.message.reply_text(
            f"📊 Stats:\n"
            f"Users: {total_users}\n"
            f"Tries (remaining sum): {total_tries_allocated}\n"
            f"Plays: {total_plays}\n"
            f"Winners: {winners}\n"
            f"Counter: {counter}"
        )
    finally:
        db.close()

# =========================
# FastAPI app + webhook endpoints
# =========================
api = FastAPI()

@api.get("/")
async def root():
    return HTMLResponse("<h3>✅ NaijaPrizeGate Bot is running.</h3>")

@api.get("/payment/verify")
async def verify_payment(tx_ref: Optional[str] = Query(None)):
    """
    Simple page to show basic verification info when user is redirected back from Flutterwave.
    Note: we still rely on webhook to credit tries. This page can optionally call Flutterwave verify API.
    """
    if not tx_ref:
        return HTMLResponse("<h3>❌ Invalid payment verification request.</h3>")
    # We simply show tx_ref and instruct user to return to Telegram.
    return HTMLResponse(
        f"<h3>✅ Payment finished (tx_ref={tx_ref}).</h3>"
        "<p>If your tries are not credited automatically, return to Telegram and wait a few moments.</p>"
    )

@api.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, update: dict):
    """
    Telegram webhook entrypoint. 
    We secure this by including a secret token in the URL.
    Example webhook URL: https://<your-app>.onrender.com/telegram/webhook/my-secret
    """
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    if app_telegram:
        await app_telegram.process_update(Update.de_json(update, app_telegram.bot))

    return JSONResponse({"ok": True})

# Fallback: accept Telegram webhook without secret
@api.post("/telegram/webhook")
async def telegram_webhook_fallback(update: dict):
    """
    Fallback route in case Telegram calls /telegram/webhook without the secret.
    Less secure, but prevents 404 errors if Telegram ignores the secret.
    """
    if app_telegram:
        await app_telegram.process_update(Update.de_json(update, app_telegram.bot))
    return JSONResponse({"ok": True})

@api.post("/payment/webhook")
async def flutterwave_webhook(request: Request, verif_hash: str = Header(None, convert_underscores=False)):
    """
    Flutterwave will POST payment events to this endpoint.
    We verify header `verif-hash` (mapped to 'verif_hash' param here) against FLW_SECRET_HASH.
    Then we optionally call Flutterwave verify API for extra safety, and finally update DB.
    """
    # Read raw body bytes for HMAC if needed
    raw_body = await request.body()

    # verify header
    header_value = request.headers.get("verif-hash") or verif_hash
    if FLW_SECRET_HASH:
        if not header_value:
            logger.warning("No verif-hash header present in webhook.")
            raise HTTPException(status_code=403, detail="Missing signature header")
        # compare using simple equality; Flutterwave expects exact match of the SHA-256 (string)
        if header_value != FLW_SECRET_HASH:
            logger.warning("Invalid verif-hash in webhook. Provided: %s", header_value)
            raise HTTPException(status_code=403, detail="Invalid webhook signature")
    else:
        logger.warning("FLW_SECRET_HASH not set; skipping webhook header verification (not recommended).")

    # parse JSON
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event")
    data = payload.get("data", {}) or {}

    # We only care about completed charges
    if event == "charge.completed" and data.get("status") == "successful":
        tx_ref = data.get("tx_ref")
        if not tx_ref:
            logger.warning("Webhook with successful charge missing tx_ref: %s", data)
            return JSONResponse({"ok": False, "reason": "missing tx_ref"}, status_code=200)

        # Optional: verify payment via Flutterwave API using transaction id in payload (safer)
        verify_ok = True
        if FLW_SECRET_KEY:
            try:
                transaction_id = data.get("id")
                if transaction_id:
                    verify_url = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify"
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp = await client.get(verify_url, headers={"Authorization": f"Bearer {FLW_SECRET_KEY}"})
                        verify_data = resp.json()
                        verify_status = verify_data.get("data", {}).get("status")
                        if verify_status != "successful":
                            logger.warning("Flutterwave verify API disagrees: %s", verify_data)
                            verify_ok = False
                # else: if id not provided, proceed but recommended to have id
            except Exception:
                logger.exception("Error calling Flutterwave verify API")
                verify_ok = False
        else:
            logger.warning("FLW_SECRET_KEY not set — skipping API verify step")

        if not verify_ok:
            logger.warning("Payment verification failed for tx_ref=%s", tx_ref)
            return JSONResponse({"ok": False, "reason": "verify_failed"}, status_code=200)

        # update payment row and credit user tries
        db = SessionLocal()
        try:
            payment = db.query(Payment).filter(Payment.tx_ref == tx_ref).one_or_none()
            if not payment:
                logger.warning("No payment row found for tx_ref=%s", tx_ref)
                return JSONResponse({"ok": False, "reason": "no_payment_record"}, status_code=200)

            if payment.status == "successful":
                logger.info("Payment already processed tx_ref=%s", tx_ref)
                return JSONResponse({"ok": True})

            # mark successful and credit user tries
            payment.status = "successful"
            payment.amount = payment.amount  # keep amount
            db.merge(payment)

            user = db.query(User).filter(User.tg_id == payment.tg_id).one_or_none()
            if not user:
                # create user record if missing (shouldn't usually happen)
                user = User(tg_id=payment.tg_id, username="")
                db.add(user)
                db.commit()
                db.refresh(user)

            user.tries = (user.tries or 0) + (payment.tries or 0)
            db.merge(user)
            db.commit()
            logger.info("✅ Payment confirmed and tries credited: tx_ref=%s, tg_id=%s, tries=%s", tx_ref, payment.tg_id, payment.tries)
        finally:
            db.close()

    # respond success to Flutterwave
    return JSONResponse({"ok": True})

# =========================
# Bootstrapping bot (startup/shutdown)
# =========================
async def on_startup():
    global app_telegram
    app_telegram = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # Command handlers
    app_telegram.add_handler(CommandHandler("start", start_cmd))
    app_telegram.add_handler(CommandHandler("tryluck", tryluck_cmd))
    app_telegram.add_handler(CommandHandler("stats", stats_cmd))

    # Callback query (inline buttons)
    app_telegram.add_handler(CallbackQueryHandler(callback_query_handler))

    # Single text handler (handles awaited email & fallback)
    app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    await app_telegram.initialize()
    await app_telegram.start()
    logger.info("✅ Telegram bot started (webhook mode).")

async def on_shutdown():
    if app_telegram:
        await app_telegram.stop()
        await app_telegram.shutdown()

# Let FastAPI call these on startup/shutdown
api.add_event_handler("startup", on_startup)
api.add_event_handler("shutdown", on_shutdown)

# =========================
# Run with uvicorn if executed directly
# =========================
if __name__ == "__main__":
    uvicorn.run(
        "app:api",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True,
    )
