# app.py - NaijaPrizeGate (improved, full version)
# ====================================================================
import os
import re
import uuid
import random
import asyncio
import hmac
import hashlib
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict

import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Header, Query
from fastapi.responses import JSONResponse, HTMLResponse

# initialize FastAPI
api = FastAPI()

# -----------------------------
# Webhook configuration
# -----------------------------
BASE_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "") # must be set in Render env
WEBHOOK_PATH = f"/telegram/webhook/{WEBHOOK_SECRET}" if WEBHOOK_SECRET else "/telegram/webhook"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

from sqlalchemy import (
   create_engine, Column, Integer, String, DateTime, Boolean, BigInteger, Text, select
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import IntegrityError

from telegram import (
   Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.constants import ParseMode
from telegram.ext import (
   Application, ApplicationBuilder, CommandHandler, ContextTypes,
   MessageHandler, filters, CallbackQueryHandler
)

 # 👇 add this pattern (matches hello, hi, hey, good morning, etc.)
greeting_pattern = r'(?i)^(hello|hi|hey|good\s*morning|good\s*afternoon|good\s*evening)$'

# 👇 add this function (reuses your /start welcome message)
async def greet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await start_cmd(update, context)

import ipaddress

# Telegram's published IP ranges (as of 2025)
TELEGRAM_IP_RANGES = [
    "149.154.160.0/20",
    "91.108.4.0/22"
]

def is_telegram_ip(ip: str) -> bool:
    """
    Check if the given IP belongs to Telegram's official ranges.
    Helps secure the fallback webhook against fake requests.
    """
    try:
        ip_addr = ipaddress.ip_address(ip)
        for net in TELEGRAM_IP_RANGES:
            if ip_addr in ipaddress.ip_network(net):
                return True
    except Exception:
        return False
    return False

SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍉", "🍇", "⭐", "🍀", "💎"]

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
PAYMENT_EXPIRE_HOURS = int(os.getenv("PAYMENT_EXPIRE_HOURS", "2"))  # payment link lifetime (hours)

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
  # referral_code = Column(String(64), nullable=True)  # optional for future referral feature

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, index=True, nullable=False)
    tx_ref = Column(String(128), unique=True, index=True, nullable=False)
    amount = Column(Integer, nullable=False)
    tries = Column(Integer, nullable=False, default=0)  # number of tries this payment should credit
    status = Column(String(32), default="pending")  # pending / successful / failed / expired
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)  # new: expiry timestamp for the payment link

    # <-- add this new column
    fw_transaction_id = Column(String, unique=True, nullable=True, index=True)

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
    """Return current try counter as int (0 if missing)."""
    row = db.query(Meta).filter(Meta.key == "try_counter").one_or_none()
    return int(row.value) if row else 0

def set_counter(db, value: int, force: bool = False) -> bool:
    """
    Set try_counter to `value`.

    - By default this WILL NOT lower the counter (prevents accidental resets).
    - Pass force=True to allow lowering (admin use).
    - Returns True when the DB was changed, False when ignored.
    """
    row = db.query(Meta).filter(Meta.key == "try_counter").one_or_none()
    if not row:
        row = Meta(key="try_counter", value=str(value))
        db.add(row)
        db.commit()
        logger.info("try_counter initialized to %s", value)
        return True

    try:
        current = int(row.value)
    except Exception:
        current = 0

    if value < current and not force:
        logger.warning(
            "Attempt to lower try_counter from %s to %s blocked (force=False)", current, value
        )
        return False

    row.value = str(value)
    db.merge(row)
    db.commit()
    logger.info("try_counter set to %s (force=%s)", value, force)
    return True

def increment_counter(db, max_retries: int = 5) -> int:
    """
    Atomically increment and return the new counter value.

    - If the meta row is missing, initialize it to (plays_count + 1) so the counter
      won't unexpectedly reset to 1 after a restore or manual deletion.
    - Uses SELECT ... FOR UPDATE on Postgres for safety; falls back to a retry loop
      for SQLite/MySQL (optimistic update).
    """
    # --- Postgres: strong row lock approach ---
    try:
        if engine.dialect.name == "postgresql":
            new_value = None 
            # start a transaction; commit happens when context exits successfully
            with db.begin():
                res = db.execute(
                    select(Meta).where(Meta.key == "try_counter").with_for_update()
                )
                row = res.scalars().one_or_none()
                if not row:
                    plays_count = db.query(Play).count()
                    new_value = plays_count + 1
                    row = Meta(key="try_counter", value=str(new_value))
                    db.add(row)
                else:
                    new_value = int(row.value) + 1
                    row.value = str(new_value)
                    db.add(row)
            return new_value
    except Exception:
        # If FOR UPDATE branch fails (e.g. not supported), rollback and fall back
        try:
            db.rollback()
        except Exception:
            pass

    # --- Fallback: optimistic CAS-style loop (SQLite, MySQL, other) ---
    for attempt in range(max_retries):
        try:
            row = db.query(Meta).filter(Meta.key == "try_counter").one_or_none()
            if not row:
                plays_count = db.query(Play).count()
                new_value = plays_count + 1
                row = Meta(key="try_counter", value=str(new_value))
                db.add(row)
                db.commit()
                return new_value

            old_value = int(row.value)

            updated_count = db.query(Meta).filter(
                Meta.key == "try_counter", Meta.value == str(old_value)
            ).update({"value": str(old_value + 1)}, synchronize_session=False)

            if updated_count:
                db.commit()
                return old_value + 1

            # If update_count == 0, someone else changed the row — retry
            db.rollback()
            time.sleep(0.01 * (attempt + 1))
            continue

        except Exception:
            # On any DB error, rollback and retry a bit
            try:
                db.rollback()
            except Exception:
                pass
            time.sleep(0.01 * (attempt + 1))
            continue

    # --- Final fallback: best-effort increment (should rarely happen) ---
    try:
        row = db.query(Meta).filter(Meta.key == "try_counter").one_or_none()
        if not row:
            plays_count = db.query(Play).count()
            new_value = plays_count + 1
            row = Meta(key="try_counter", value=str(new_value))
            db.add(row)
            db.commit()
            return new_value
        row.value = str(int(row.value) + 1)
        db.commit()
        return int(row.value)
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("Failed to increment try_counter (final fallback): %s", e)
        raise

def ensure_counter_initialized():
    """
    Ensure the try_counter meta row exists and is at least the number of recorded plays.
    Call this once at startup so the counter cannot be accidentally lower than plays count.
    """
    db = SessionLocal()
    try:
        plays = db.query(Play).count()
        row = db.query(Meta).filter(Meta.key == "try_counter").one_or_none()
        if not row:
            set_counter(db, plays, force=True)
            logger.info("Initialized try_counter to existing plays_count=%s", plays)
        else:
            try:
                current = int(row.value)
            except Exception:
                current = 0
            if current < plays:
                set_counter(db, plays, force=True)
                logger.info(
                    "Bumped try_counter from %s to plays_count=%s to keep consistency", current, plays
                )
    finally:
        db.close()


async def reset_counter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin-only command to force-reset try_counter.
    Usage: /resetcounter <new_value>
    Only users in ADMIN_USER_ID env (comma-separated) are allowed.
    """
    admin_env = os.getenv("ADMIN_USER_ID", "")
    try:
        admin_ids = [int(x.strip()) for x in admin_env.split(",") if x.strip()]
    except Exception:
        admin_ids = []

    caller_id = update.effective_user.id if update.effective_user else None
    if caller_id not in admin_ids:
        try:
            await update.message.reply_text("❌ You are not authorized to use this command.")
        except Exception:
            pass
        return

    args = context.args if hasattr(context, "args") else []
    if not args:
        await update.message.reply_text("Usage: /resetcounter <new_integer_value> (admin only)")
        return

    try:
        new_value = int(args[0])
    except Exception:
        await update.message.reply_text("Please provide a valid integer value.")
        return

    db = SessionLocal()
    try:
        ok = set_counter(db, new_value, force=True)
        if ok:
            await update.message.reply_text(f"✅ try_counter force-set to {new_value}")
        else:
            await update.message.reply_text("⚠️ Failed to set try_counter.")
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

def format_display_name(user_obj) -> str:
    """
    Return a safe display name for announcements.
    - If the Telegram user has a username, return '@username'
    - Otherwise return 'User<tg_id>' so we never show '@None'
    """
    # Try common attributes the telegram User object has
    uname = getattr(user_obj, "username", None)
    uid = getattr(user_obj, "id", None) or getattr(user_obj, "tg_id", None)

    if uname:
        return f"@{uname}"
    # fallback to numeric id with a readable prefix
    return f"User{uid if uid is not None else 'unknown'}"


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

            # Provide a Cancel inline button so user can abort easily
            cancel_kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data="pay:cancel")]]
            )

            await query.edit_message_text(
                f"You selected *{pkg['label']}*.\n\n"
                "Please reply with your email address for the payment receipt.\n\n"
                "If you want to cancel, press the Cancel button or type 'cancel'.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=cancel_kb
            )
            return

    # User pressed the Cancel button for the payment flow
    if data == "pay:cancel":
        # clear any awaiting flags for this user
        context.user_data.pop("awaiting_email", None)
        context.user_data.pop("selected_package", None)
        await query.edit_message_text("Payment flow cancelled. Back to menu:", reply_markup=main_menu_keyboard())
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
            await query.edit_message_text(
                f"You have *{tries}* tries remaining.",
                parse_mode=ParseMode.MARKDOWN
            )
        finally:
            db.close()
        return

    # Unhandled callback
    await query.edit_message_text("Unknown action. Use /start to show the menu.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Single text handler used for:
    - accepting emails when awaiting_email is True (from inline package flow)
    - allowing the user to type 'cancel' to abort the pay flow
    - fallback welcome/help message
    """
    if update.message is None:
        return

    text = update.message.text.strip()
    uid = update.effective_user.id
    uname = update.effective_user.username or ""

    # If awaiting_email is set for this user, treat this text as email or cancellation
    if context.user_data.get("awaiting_email"):
        # Support cancel by typing 'cancel' or '/cancel'
        if text.lower() in ("cancel", "/cancel", "❌"):
            context.user_data.pop("awaiting_email", None)
            context.user_data.pop("selected_package", None)
            await update.message.reply_text("❌ Payment cancelled. Back to the menu:", reply_markup=main_menu_keyboard())
            return

        email = text
        if not is_valid_email(email):
            # give a clear instruction and remind about cancel option
            await update.message.reply_text(
                "⚠️ That doesn’t look like a valid email. Reply with a valid email or type 'cancel' to stop."
            )
            return

        # clear awaiting flag (we got a valid email)
        context.user_data["awaiting_email"] = False
        selected_key = context.user_data.get("selected_package", "500")
        pkg = PACKAGES.get(selected_key, PACKAGES["500"])
        amount = pkg["amount"]
        tries_to_credit = pkg["tries"]

        # generate tx_ref and save Payment row
        tx_ref = f"TG-{uid}-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            # Defensive check: ensure selected package is valid (defense-in-depth)
            selected_key = context.user_data.get("selected_package", "500")
            pkg = PACKAGES.get(selected_key)
            if not pkg:
                logger.warning("Invalid selected_package for user %s", uid, selected_key)
                await update.message.reply_text(
                    "⚠️ The package you selected is invalid. Please press 💳 Pay Now and choose a package again."
                )
                context.user_data.pop("selected_package", None)
                return
            
            # use canonical package values (don't trust client input)
            amount = pkg["amount"]
            tries_to_credit = pkg["tries"]

            # ensure user exists
            u = db.query(User).filter(User.tg_id == uid).one_or_none()
            if not u:
                u = User(tg_id=uid, username=uname)
                db.add(u)
                db.commit()
                db.refresh(u)

            # setexpiry for this payment (default from PAYMENT_EXPIRE_HOURS)
            expires = datetime.utcnow() + timedelta(hours=PAYMENT_EXPIRE_HOURS)
            
            payment = Payment(
                tg_id=uid,
                tx_ref=tx_ref,
                amount=amount,
                tries=tries_to_credit,
                status="pending",
                expires_at=expires 
            )
            db.add(payment)
            db.commit()
            db.refresh(payment)

            logger.info(
                "Created payment record tx_ref=%s, tg_id=%s, amount=%s, tries=%s, expires_at=%s",
                tx_ref, uid, amount, tries_to_credit, expires.isoformat()
            )

        except IntegrityError:
            db.rollback()
            logger.exception("TX_REF collision creating payment record for tg_id=%s tx_ref=%s", uid, tx_ref)
            await update.message.reply_text("⚠️ An internal error occurred creating your payment. Please try again.")
            return

        except Exception:
            db.rollback()
            logger.exception("Failed to create payment record for tg_id=%s", uid)
            await update.message.reply_text("⚠️ An unexpected error occurred. Please try again later.")
            return

        finally:
            db.close()

        # create flutterwave link
        try:
            link = await create_flutterwave_payment_link(
                tx_ref=tx_ref,
                amount=amount,
                email=email,
                name=(update.effective_user.full_name or str(uid))
            )

            if link:
                # Provide a clear button so Telegram shows the link as a clickable URL button.
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Open payment link", url=link)]
                ])
                await update.message.reply_text(
                    "💳 Tap the button below to open the payment page.\n\n"
                    "If the payment page fails to load inside Telegram's in-app browser, "
                    "use your phone's browser (choose 'Open in browser' from the menu) "
                    "or copy the link below and paste it into your browser:\n\n"
                    f"{link}\n\n"
                    "👉 After completing payment, return to Telegram and press Try Luck 🎰 "
                    "or wait a few moments for automatic confirmation.",
                    reply_markup=kb,
                    disable_web_page_preview=True
                )
            else:
                await update.message.reply_text(
                    "⚠️ Could not create payment link. Try again later."
                )

        except Exception:
            logger.exception("Error while creating Flutterwave payment link for tx_ref=%s", tx_ref)
            await update.message.reply_text("⚠️ An unexpected error occurred. Try again later.")

        # clear selected_package
        context.user_data.pop("selected_package", None)
        return

    # fallback (not awaiting email)
    # show welcome + menu and quick hint
    await autowelcome_fallback(update, context)


async def autowelcome_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # reply with welcome message and main menu keyboard
    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard()
    )


async def tryluck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE): 
    """
    Try luck command with slot machine style spinning animation + database integration.
    Handles both callback_query and direct /tryluck command.
    """
    # Determine chat context (callback_query vs message)
    if update.callback_query:
        user = update.callback_query.from_user
        answer_target = update.callback_query.message
    else:
        user = update.effective_user
        answer_target = update.message

    uid = user.id

    db = SessionLocal()
    code = None  # will hold the winner code if this play wins
    msg = None

    try:
        # Find user
        u = db.query(User).filter(User.tg_id == uid).one_or_none()
        if not u or (u.tries or 0) <= 0:
            await answer_target.reply_text(
                "⚠️ You have no tries left. Please buy tries using Pay Now 💳"
            )
            return

        # Consume a try and record the play as a 'lose' by default
        u.tries = (u.tries or 0) - 1
        play = Play(tg_id=uid, result="lose")
        db.add(play)
        db.merge(u)
        db.commit()

        # refresh objects if you plan to use them further
        try:
            db.refresh(u)
        except Exception:
            pass  # ignore refresh errors on simple setups

        # Increment global counter (stored in Meta table)
        counter = increment_counter(db)
        logger.info(f"User {uid} played. Counter={counter}, remaining_tries={u.tries}")

        # Initial spinning message
        msg = await answer_target.reply_text("🎰 Spinning...")

        # Animate slot reels (randomized frames & delays to reduce Telegram edit queuing)
        frames = random.randint(3, 6)  # play between 3 and 6 frames (tweak as desired)
        for i in range(frames):
            reel = " | ".join(random.choices(SLOT_SYMBOLS, k=3))

            # Use a short delay at the start and a slightly longer one towards the end
            # to simulate the reels slowing down and reduce heavy edit load on Telegram.
            min_delay = 0.5   # shortest pause between edits (seconds)
            max_delay = 1.3   # longest pause at the end (seconds)
            progress = i / (frames - 1) if frames > 1 else 1.0
            # easing makes the slowdown feel more natural (progress**1.5)
            delay = min_delay + (max_delay - min_delay) * (progress ** 1.5)

            try:
                # Try to edit the same message with the new reel
                await msg.edit_text(f"🎰 {reel}")
            except Exception:
                # On slow/unstable networks the edit may fail (message deleted, rate-limited).
                # We catch the error and continue so the bot doesn't crash.
                logger.debug("Could not edit spin message; continuing animation.")
            await asyncio.sleep(delay)

        # Determine win
        is_win = (counter % WIN_THRESHOLD == 0)

        if is_win:
            try:
                # Step 1: create winner row without code (get unique id from DB)
                winner = Winner(tg_id=uid, username=(user.username or ""), code=None)
                db.add(winner)

                play.result = "win"
                db.merge(play)

                db.commit()
                db.refresh(winner)  # ensure we have winner.id

            except Exception:
                db.rollback()
                logger.exception("Failed to create winner record for user %s", uid)
                await msg.edit_text(
                    "⚠️ An error occurred while recording your win. Please contact support.",
                    reply_markup=main_menu_keyboard()
                )
                return

            # Step 2: build code using DB id + random suffix (guaranteed unique)
            try:
                code = f"WIN-{winner.id}-{uuid.uuid4().hex[:6].upper()}"
                winner.code = code
                db.merge(winner)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Failed to assign code for winner id=%s", winner.id)
                await msg.edit_text(
                    "⚠️ An error occurred while finalizing your win. Please contact support.",
                    reply_markup=main_menu_keyboard()
                )
                return

            # Step 3: now safely announce
            final_reel = "💎 | 💎 | 💎"
            display_name = format_display_name(user)
            await msg.edit_text(
                f"🎉 JACKPOT!!!\n\n{final_reel}\n\n"
                f"🥳 Congratulations {display_name}, You WON!\n"
                f"Your Winner Code: `{code}`\n\n"
                f"📢 You’ll be featured in {PUBLIC_CHANNEL}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu_keyboard()
            )

        else:
            # Losing outcome: show a random final reel
            final_reel = " | ".join(random.choices(SLOT_SYMBOLS, k=3))
            await msg.edit_text(
                f"{final_reel}\n\n🙁 Not a win this time. Try again!",
                reply_markup=main_menu_keyboard()
            )


    except Exception as e:
        logger.exception("Error during play: %s", e)
        try:
            if msg:
                await msg.edit_text("⚠️ An error occurred. Please try again later.")
            else:
                await answer_target.reply_text("⚠️ An error occurred. Please try again later.")
        except Exception:
            pass  # avoid raising inside error handler

    finally:
        db.close()

    # Announce in public channel — ONLY if winner was created
    if code:
        display_name = format_display_name(user)
        try:
            await context.bot.send_message(
                chat_id=PUBLIC_CHANNEL,
                text=f"🎊 Winner Alert! {display_name} just won an iPhone 16 Pro Max! Code: {code}"
            )
        except Exception:
            logger.exception("Failed to announce winner in public channel.")



async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin-only /stats command.

    - ADMIN_USER_ID may be a single integer or comma-separated integers in the env.
    - This function sends the stats as a private message (DM) to the admin to avoid leaking
      information in public chats. If DM fails (e.g. admin has not started the bot),
      it falls back to replying in the current chat.
    """
    # Read admin IDs from env (use the global ADMIN_USER_ID if set)
    admin_env = ADMIN_USER_ID or os.getenv("ADMIN_USER_ID")
    if not admin_env:
        logger.warning("ADMIN_USER_ID not configured; rejecting /stats call.")
        # Safe user-facing reply (do not reveal stats)
        try:
            if update.callback_query:
                await update.callback_query.answer("❌ Admins not configured. This command is unavailable.", show_alert=True)
            elif update.message:
                await update.message.reply_text("❌ Admins not configured. This command is unavailable.")
        except Exception:
            logger.exception("Failed to send admin-missing reply.")
        return

    # Parse admin IDs (support comma-separated list)
    try:
        admin_ids = [int(x.strip()) for x in admin_env.split(",") if x.strip()]
        if not admin_ids:
            raise ValueError("No valid admin IDs found")
    except Exception:
        logger.exception("Invalid ADMIN_USER_ID format. Expected integer or comma-separated integers.")
        try:
            if update.callback_query:
                await update.callback_query.answer("❌ Admin configuration error. Contact the developer.", show_alert=True)
            elif update.message:
                await update.message.reply_text("❌ Admin configuration error. Contact the developer.")
        except Exception:
            logger.exception("Failed to send admin-config-error reply.")
        return

    # Identify caller
    caller_id = update.effective_user.id if update.effective_user else None
    if caller_id not in admin_ids:
        logger.info("Unauthorized /stats attempt by %s", caller_id)
        try:
            if update.callback_query:
                await update.callback_query.answer("❌ You are not authorized to use this command.", show_alert=True)
            elif update.message:
                await update.message.reply_text("❌ You are not authorized to use this command.")
        except Exception:
            logger.debug("Failed to send unauthorized-reply (maybe missing message object).")
        return

    # Authorized: collect stats and send privately to the admin
    db = SessionLocal()
    try:
        total_users = db.query(User).count()
        # For small DBs this is fine; for large DBs change to a single SUM query.
        total_tries_allocated = sum([u.tries or 0 for u in db.query(User).all()])
        total_plays = db.query(Play).count()
        winners = db.query(Winner).count()
        counter = get_counter(db)

        stats_text = (
            f"📊 NaijaPrizeGate Stats\n\n"
            f"Users: {total_users}\n"
            f"Tries (remaining sum): {total_tries_allocated}\n"
            f"Plays: {total_plays}\n"
            f"Winners: {winners}\n"
            f"Counter: {counter}\n"
        )

        # Try to DM the admin (private message). This avoids leaking stats in a group chat.
        try:
            await context.bot.send_message(chat_id=caller_id, text=stats_text)
            # Acknowledge in the invoking chat that the stats were sent privately
            try:
                if update.callback_query:
                    await update.callback_query.answer("✅ Stats sent to your private chat.", show_alert=False)
                elif update.message:
                    # If invoked in a chat, gently tell the admin to check their DM
                    await update.message.reply_text("✅ Stats sent to your private chat.")
            except Exception:
                # Acknowledge failure to reply inline is non-fatal
                logger.debug("Could not send inline acknowledgement after DMing admin.")
        except Exception:
            # Bot could not DM (likely admin hasn't started a private chat with the bot).
            # Fallback: reply in-place (less ideal, but ensures admin sees stats).
            logger.exception("Failed to DM admin. Falling back to replying in chat.")
            try:
                if update.message:
                    await update.message.reply_text(stats_text)
                elif update.callback_query and update.callback_query.message:
                    await update.callback_query.message.reply_text(stats_text)
                else:
                    # As a last resort, try to send directly to caller_id (may fail)
                    await context.bot.send_message(chat_id=caller_id, text=stats_text)
            except Exception:
                logger.exception("Failed to deliver stats to admin.")
    finally:
        db.close()



# =========================
# FastAPI app + webhook endpoints
# =========================
from fastapi import Request, Header
import ipaddress

# Telegram official IP ranges (update if Telegram expands)
TELEGRAM_IP_RANGES = [
    "149.154.160.0/20",
    "91.108.4.0/22",
]

def is_telegram_ip(ip: str) -> bool:
    """Check if incoming IP belongs to Telegram."""
    try:
        ip_obj = ipaddress.ip_address(ip)
        return any(ip_obj in ipaddress.ip_network(r) for r in TELEGRAM_IP_RANGES)
    except Exception:
        return False

# =========================
# Main secured webhook (secret in URL)
# =========================
@api.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        logger.warning("❌ Invalid secret in webhook URL attempt.")
        return JSONResponse({"ok": False, "error": "Invalid webhook secret"})  # ✅ Always 200

    if not app_telegram:
        logger.error("⚠️ Telegram app not initialized yet.")
        return JSONResponse({"ok": False, "error": "Bot not ready"})  # ✅ Always 200

    try:
        body = await request.json()
        update = Update.de_json(body, app_telegram.bot)
        await app_telegram.process_update(update)
    except Exception as e:
        logger.exception("Error processing Telegram update (secured webhook): %s", e)
        return JSONResponse({"ok": False, "error": "Processing error"})  # ✅ Always 200

    return JSONResponse({"ok": True})


# =========================
# Fallback webhook (IP + optional header token)
# =========================
@api.post("/telegram/webhook")
async def telegram_webhook_fallback(
    request: Request,
    x_fallback_token: str = Header(None, alias="X-Fallback-Token")
):
    client_ip = request.client.host

    # 1) Verify request is from Telegram IP range
    if not is_telegram_ip(client_ip):
        logger.warning("❌ Blocked non-Telegram IP %s on fallback webhook", client_ip)
        return JSONResponse({"ok": False, "error": "Not allowed"})  # ✅ Always 200

    # 2) Optional: check fallback token if configured
    fallback_token = os.getenv("TELEGRAM_FALLBACK_TOKEN")
    if fallback_token and x_fallback_token != fallback_token:
        logger.warning("❌ Invalid fallback token from IP %s", client_ip)
        return JSONResponse({"ok": False, "error": "Invalid token"})  # ✅ Always 200

    if not app_telegram:
        logger.error("⚠️ Telegram app not initialized yet (fallback).")
        return JSONResponse({"ok": False, "error": "Bot not ready"})  # ✅ Always 200

    try:
        body = await request.json()
        update = Update.de_json(body, app_telegram.bot)
        await app_telegram.process_update(update)
    except Exception as e:
        logger.exception("Error processing Telegram update (fallback webhook): %s", e)
        return JSONResponse({"ok": False, "error": "Processing error"})  # ✅ Always 200

    return JSONResponse({"ok": True})

# ========================= 
# Flutterwave Webhook
# =========================
@api.post("/payment/webhook")
async def flutterwave_webhook(
    request: Request,
    verif_hash: str = Header(None, convert_underscores=False)
):
    """
    Secure Flutterwave webhook handler with strict server-side verification.
    Steps:
      1) Verify 'verif-hash' header matches FLW_SECRET_HASH (if set)
      2) Parse payload; only handle charge.completed with status 'successful'
      3) Look up our Payment row by tx_ref and ensure it exists and is not expired
      4) Call Flutterwave verify API (requires FLW_SECRET_KEY) and confirm:
           - API response status is success
           - payment status is 'successful'
           - tx_ref matches
           - amount and currency match what we expect
      5) Only then mark payment successful and credit tries
    """
    raw_body = await request.body()
    header_value = (request.headers.get("verif-hash") or verif_hash or "").strip()

    # 1) Verify webhook header (if configured)
    if FLW_SECRET_HASH:
        if not header_value:
            logger.warning("No verif-hash header present in webhook.")
            raise HTTPException(status_code=403, detail="Missing signature header")
        if not hmac.compare_digest(header_value, FLW_SECRET_HASH):
            logger.warning("Invalid verif-hash in webhook. Provided: %s", header_value)
            raise HTTPException(status_code=403, detail="Invalid webhook signature")
    else:
        logger.warning("FLW_SECRET_HASH not set; skipping webhook header verification (NOT recommended).")

    # 2) Parse JSON payload
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("❌ Failed to parse webhook JSON. Body=%s Error=%s",
                     raw_body.decode("utf-8", errors="ignore"), str(e))
        return JSONResponse({"ok": False, "reason": "invalid_json"}, status_code=400)

    event = payload.get("event")
    data = payload.get("data", {}) or {}

    # Only handle completed successful charges
    if event == "charge.completed" and data.get("status") == "successful":
        tx_ref = data.get("tx_ref") or data.get("txref")
        transaction_id = data.get("id") or data.get("transaction_id")

        if not tx_ref:
            logger.warning("Webhook with successful charge missing tx_ref: %s", data)
            return JSONResponse({"ok": False, "reason": "missing_tx_ref"}, status_code=200)

        # We require the FLW API key to perform server-side verification
        if not FLW_SECRET_KEY:
            logger.error("FLW_SECRET_KEY not set — cannot verify transaction via API. Aborting credit.")
            return JSONResponse({"ok": False, "reason": "no_api_key"}, status_code=200)

        if not transaction_id:
            logger.warning("No transaction id in webhook payload; cannot verify via API. Payload: %s", data)
            return JSONResponse({"ok": False, "reason": "missing_transaction_id"}, status_code=200)

        db = SessionLocal()
        try:
            # Find matching payment row
            payment = db.query(Payment).filter(Payment.tx_ref == tx_ref).one_or_none()
            if not payment:
                logger.warning("No payment row found for tx_ref=%s", tx_ref)
                return JSONResponse({"ok": False, "reason": "no_payment_record"}, status_code=200)

            # Reject if already processed
            if payment.status == "successful":
                logger.info("Payment already processed tx_ref=%s", tx_ref)
                return JSONResponse({"ok": True}, status_code=200)

            # Reject expired payment links
            if payment.expires_at and datetime.utcnow() > payment.expires_at:
                logger.info("Payment link expired for tx_ref=%s (expires_at=%s)", tx_ref, payment.expires_at)
                payment.status = "expired"
                db.merge(payment)
                db.commit()
                return JSONResponse({"ok": False, "reason": "payment_expired"}, status_code=200)

            # Call Flutterwave verify API
            verify_ok = False
            verify_data = {}
            try:
                verify_url = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify"
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        verify_url,
                        headers={"Authorization": f"Bearer {FLW_SECRET_KEY}"}
                    )
                    # (1): log HTTP status if not 200
                    if resp.status_code != 200:
                        logger.warning("Verify API returned HTTP %s for tx_ref=%s, body=%s",
                                       resp.status_code, tx_ref, resp.text)    
                    
                    verify_data = resp.json()
                    status_ok = verify_data.get("status") == "success"
                    tx_status = verify_data.get("data", {}).get("status")
                    verify_tx_ref = verify_data.get("data", {}).get("tx_ref") or verify_data.get("data", {}).get("txref")
                    verify_amount_raw = verify_data.get("data", {}).get("amount")
                    verify_currency = verify_data.get("data", {}).get("currency")

                    # normalize amount to integer Naira when possible
                    try:
                        verify_amount_int = int(float(verify_amount_raw)) if verify_amount_raw is not None else None
                    except Exception:
                        verify_amount_int = None

                    # Strict checks: API agrees it's successful, tx_ref matches, amount matches, currency is NGN (if provided)
                    if (
                        status_ok
                        and tx_status == "successful"
                        and verify_tx_ref == tx_ref
                        and verify_amount_int == payment.amount
                        and (verify_currency is None or str(verify_currency).upper() == "NGN")
                    ):
                        verify_ok = True
                    else:
                        logger.warning("Flutterwave verify mismatch for tx_ref=%s: %s", tx_ref, verify_data)

            except Exception:
                logger.exception("Error calling Flutterwave verify API")
                verify_ok = False

            if not verify_ok:
                logger.warning("Payment verification failed for tx_ref=%s", tx_ref)
                return JSONResponse({"ok": False, "reason": "verify_failed"}, status_code=200)

            # All checks passed -> mark payment successful and credit tries
            # <-- change here (3a): add replay protection
            if payment.fw_transaction_id and payment.fw_transaction_id != transaction_id:
                logger.warning("Replay attack? tx_ref=%s already linked to fw_txn_id=%s, got different id=%s",
                               tx_ref, payment.fw_transaction_id, transaction_id)
                return JSONResponse({"ok": False, "reason": "txn_id_mismatch"}, status_code=200)

            payment.status = "successful"
            payment.fw_transaction_id = transaction_id  # <-- change here (3b): save verified FW transaction ID
            db.merge(payment)
            

            user = db.query(User).filter(User.tg_id == payment.tg_id).one_or_none()
            if not user:
                user = User(tg_id=payment.tg_id, username="")
                db.add(user)
                db.commit()
                db.refresh(user)

            user.tries = (user.tries or 0) + (payment.tries or 0)
            db.merge(user)
            db.commit()

            logger.info(
                # <-- change here (4): log tx_ref + fw txn id
                "✅ Payment confirmed and tries credited: tx_ref=%s, fw_txn_id=%s, tg_id=%s, tries=%s",
                tx_ref, transaction_id, payment.tg_id, payment.tries
            )

            # Notify user via Telegram (best-effort)
            try:
                if app_telegram:
                    await app_telegram.bot.send_message(
                        chat_id=payment.tg_id,
                        text=(
                            f"✅ Payment confirmed! {payment.tries} "
                            f"{'try' if payment.tries == 1 else 'tries'} "
                            "have been credited to your account.\n\n"
                            "Press *Try Luck* 🎰 to play now."
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=main_menu_keyboard()
                    )
            except Exception:
                logger.exception("Failed to notify user (tries still saved in DB).")

        finally:
            db.close()

    # Always return 200 so Flutterwave doesn’t retry endlessly
    return JSONResponse({"ok": True})


# =========================
# Payment Verification Redirect
# =========================
@api.api_route("/payment/verify", methods=["GET", "POST"])
async def verify_payment(request: Request):
    """
    Robust payment redirect handler.
    Accepts GET or POST from Flutterwave, extracts tx_ref, optionally verifies,
    updates DB, and shows a friendly HTML page.
    """
    # 1) Extract tx_ref
    tx_ref = (
        request.query_params.get("tx_ref")
        or request.query_params.get("txref")
        or None
    )
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        try:
            form = await request.form()
            payload = dict(form)
        except Exception:
            payload = {}

    if not tx_ref:
        tx_ref = (
            payload.get("tx_ref")
            or payload.get("txref")
            or payload.get("reference")
            or payload.get("transaction_id")
        )

    if not tx_ref:
        html = (
            "<h3>❌ Payment verification failed (no tx_ref received).</h3>"
            "<p>If you were redirected here after payment, return to Telegram and wait a few moments; "
            "the bot should be credited automatically once the webhook is processed.</p>"
            "<p>If your tries are not credited after a few minutes, contact support.</p>"
        )
        try:
            logger.warning("Payment redirect with no tx_ref. payload keys=%s", list(payload.keys()))
        except Exception:
            pass
        return HTMLResponse(html, status_code=400)

    # 2) Verify with Flutterwave if possible
    verified = False
    verify_details = {}
    if FLW_SECRET_KEY:
        try:
            verify_url = f"https://api.flutterwave.com/v3/transactions/verify_by_reference?tx_ref={tx_ref}"
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    verify_url,
                    headers={"Authorization": f"Bearer {FLW_SECRET_KEY}"}
                )
                verify_data = resp.json()
                status_ok = verify_data.get("status") == "success"
                tx_status = verify_data.get("data", {}).get("status")
                verify_details = verify_data
                if status_ok and tx_status == "successful":
                    verified = True
        except Exception:
            logger.exception("Error while calling Flutterwave verify_by_reference API")
    else:
        logger.warning("FLW_SECRET_KEY not set — skipping API verify step for tx_ref=%s", tx_ref)

    # 3) Update DB if verified
    db = SessionLocal()
    try:
        payment = db.query(Payment).filter(Payment.tx_ref == tx_ref).one_or_none()
        if not payment:
            logger.warning("No payment row found for tx_ref=%s", tx_ref)
            return JSONResponse({"ok": False, "reason": "no_payment_record"}, status_code=200)

        # 🕒 Expiry check first
        if payment.expires_at and datetime.utcnow() > payment.expires_at:
            logger.warning("Redirect verify: payment expired for tx_ref=%s (expires_at=%s)", tx_ref, payment.expires_at)
            payment.status = "expired"
            db.merge(payment)
            db.commit()
            return JSONResponse({"ok": False, "reason": "payment_expired"}, status_code=200)

        # --- VALIDATION: only accept amounts defined in PACKAGES ---
        allowed_amounts = {p["amount"] for p in PACKAGES.values()}
        if payment.amount not in allowed_amounts:
            logger.warning(
                "Invalid package amount for tx_ref=%s: %s not in allowed %s",
                tx_ref, payment.amount, allowed_amounts
            )
            payment.status = "failed"
            db.merge(payment)
            db.commit()
            return JSONResponse({"ok": False, "reason": "invalid_package_amount"}, status_code=200)

        # Additional defence: verify Flutterwave amount & currency matches our DB row
        try:
            fw_amount_raw = verify_data.get("data", {}).get("amount")
            fw_currency = verify_data.get("data", {}).get("currency") or verify_data.get("data", {}).get("currency_code")
            fw_amount = None
            if fw_amount_raw is not None:
                fw_amount = int(float(fw_amount_raw))
            if fw_amount is None or fw_amount != int(payment.amount) or (fw_currency and fw_currency.upper() != "NGN"):
                logger.warning(
                    "Flutterwave amount/currency mismatch for tx_ref=%s: fw=%s %s, db=%s",
                    tx_ref, fw_amount_raw, fw_currency, payment.amount
                )
                payment.status = "failed"
                db.merge(payment)
                db.commit()
                return JSONResponse({"ok": False, "reason": "amount_mismatch"}, status_code=200)
        except Exception:
            logger.exception("Error validating Flutterwave amount for tx_ref=%s", tx_ref)
            payment.status = "failed"
            db.merge(payment)
            db.commit()
            return JSONResponse({"ok": False, "reason": "amount_validation_error"}, status_code=200)

        if payment.status == "successful":
            logger.info("Payment already processed tx_ref=%s", tx_ref)
            return JSONResponse({"ok": True})

        # ✅ Mark successful + credit tries
        payment.status = "successful"
        db.merge(payment)

        user = db.query(User).filter(User.tg_id == payment.tg_id).one_or_none()
        if not user:
            user = User(tg_id=payment.tg_id, username="")
            db.add(user)
            db.commit()
            db.refresh(user)

        user.tries = (user.tries or 0) + (payment.tries or 0)
        db.merge(user)
        db.commit()

        logger.info(
            "✅ Redirect verification credited tries: tx_ref=%s, tg_id=%s, tries=%s",
            tx_ref, payment.tg_id, payment.tries
        )

        # Notify user via Telegram
        try:
            if app_telegram:
                await app_telegram.bot.send_message(
                    chat_id=payment.tg_id,
                    text=(
                        f"✅ Payment confirmed! {payment.tries} "
                        f"{'try' if payment.tries == 1 else 'tries'} "
                        "have been credited to your account.\n\n"
                        "Press *Try Luck* 🎰 to play now."
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=main_menu_keyboard()
                )
        except Exception:
            logger.exception("Failed to notify user (tries still saved in DB).")

    finally:
        db.close()


# =========================
# Bootstrapping bot (startup/shutdown)
# =========================
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# Global ref
app_telegram: Application | None = None


@api.on_event("startup")
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
    app_telegram.add_handler(CommandHandler("stat", stats_cmd))  # alias

    # Admin-only command to force-reset the try counter (register it here)
    app_telegram.add_handler(CommandHandler("resetcounter", reset_counter_cmd))
    
    # Callback queries
    app_telegram.add_handler(CallbackQueryHandler(callback_query_handler))

    # 👇 Greeting trigger (hello, hi, hey, good morning, etc.)
    app_telegram.add_handler(MessageHandler(filters.Regex(greeting_pattern), greet))
    
    # Text handler
    app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Ensure try_counter is initialized (prevents accidental low resets)
    ensure_counter_initialized()
    
    # 🚀 Build secure webhook URL with secret
    secret = os.getenv("WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("WEBHOOK_SECRET not set!")

    webhook_url = f"{BASE_URL}/telegram/webhook/{secret}"
    if not webhook_url.startswith("https://"):
        raise RuntimeError(f"Invalid webhook URL: {webhook_url}")

    #  ✅ Instead, run bot startup in background so FastAPI won't crash if Telegram is slow
    async def init_bot():
        try:
            await app_telegram.initialize()

            secret = os.getenv("WEBHOOK_SECRET")
            if not secret:
                raise RuntimeError("WEBHOOK_SECRET not set!")

            webhook_url = f"{BASE_URL}/telegram/webhook/{secret}"
            
            # 🔹 Check current webhook first
            current = await app_telegram.bot.get_webhook_info()
            if current.url != webhook_url:
                await app_telegram.bot.set_webhook(webhook_url, drop_pending_updates=True)
                logger.info(f"✅ Telegram bot started (webhook set to {webhook_url}).")
            else:
                logger.info(f"ℹ️ Webhook already set to {webhook_url}, skipping reset.")
        
        except Exception as e:
            logger.warning(f"⚠️ Bot init failed in background: {e}")

    # 👇 This makes it run in the background after FastAPI starts
    import asyncio
    asyncio.create_task(init_bot())  
# =========================
# Shutdown (cleanup)
# =========================
async def on_shutdown():
    global app_telegram
    if app_telegram:
        try:
            await app_telegram.stop()
            await app_telegram.shutdown()
            logger.info("🛑 Telegram bot stopped cleanly.")
        except Exception as e:
            logger.warning(f"⚠️ Error while shutting down bot: {e}")


# Register lifecycle hooks with FastAPI
api.add_event_handler("startup", on_startup)
api.add_event_handler("shutdown", on_shutdown)

# =========================
# Run API server only
# =========================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:api",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        log_level="info"
        )
        
