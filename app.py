# ====================================================
# app.py
# =====================================================
# 1️⃣ Import & initialize secure logging first
# -------------------------------------------------
from logging_setup import logger, tg_error_handler  # must be first to protect secrets

import os
import re
import logging
import builtins
import traceback

# Force unbuffered output (Render needs this for real-time logs)
os.environ["PYTHONUNBUFFERED"] = "1"

# ------------------------------------------------
# 🧩 Step 1 — Secure Logging & Print Mask (Double-Lock)
# ------------------------------------------------
class SecretFilter(logging.Filter):
    TOKEN_PATTERN = re.compile(r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b")

    def filter(self, record):
        record.msg = self.TOKEN_PATTERN.sub("[SECRET]", str(record.msg))
        if record.args:
            record.args = tuple(self.TOKEN_PATTERN.sub("[SECRET]", str(a)) for a in record.args)
        return True


for name in logging.root.manager.loggerDict:
    logging.getLogger(name).addFilter(SecretFilter())


_real_print = print


def safe_print(*args, **kwargs):
    safe_args = [re.sub(r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b", "[SECRET]", str(a)) for a in args]
    _real_print(*safe_args, **kwargs)


builtins.print = safe_print

# ------------------------------------------------

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse
from telegram import Update, Bot
from datetime import datetime, timezone
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# Local imports
from handlers import core, payments, free, admin, playtrivia, battle, jambpractice
from db import init_game_state, get_async_session
from models import GameState, PrizeWinner
from helpers import get_or_create_user
from utils.signer import verify_signed_token
from webhook import router as webhook_router
from routes.payments_router import router as payments_router
from services.airtime_service import handle_claim_airtime_button, handle_airtime_claim_phone
from handlers.support import support_conv, admin_reply
from handlers.challenge import register_handlers as register_challenge_handlers
from tasks import start_background_tasks, stop_background_tasks
from handlers.mockjamb import register_handlers as register_mockjamb_handlers

logging.getLogger("httpx").setLevel(logging.WARNING)

# ------------------------------------
# Simple anti-spam for webhook calls
# ------------------------------------
_rate_limit_cache = {}
RATE_LIMIT_SECONDS = 10


def is_rate_limited(key: str, seconds: int = 30) -> bool:
    import time
    now = time.time()
    last_call = _rate_limit_cache.get(key)
    if last_call and now - last_call < seconds:
        return True
    _rate_limit_cache[key] = now
    return False


# ------------------------------------------------
# 🔒 Secure Logging Filter (hide Telegram bot token)
# -------------------------------------------------
class TelegramTokenFilter(logging.Filter):
    TOKEN_PATTERN = re.compile(r"(bot[0-9]+:[A-Za-z0-9_-]+)")

    def filter(self, record):
        record.msg = self.TOKEN_PATTERN.sub("bot<REDACTED>", str(record.msg))
        if record.args:
            record.args = tuple(self.TOKEN_PATTERN.sub("bot<REDACTED>", str(a)) for a in record.args)
        return True


for name in logging.root.manager.loggerDict:
    logging.getLogger(name).addFilter(TelegramTokenFilter())


# -------------------------------------------------
# Ensure GameState row exists
# -------------------------------------------------
async def ensure_game_state_exists():
    async with get_async_session() as session:
        gs = await session.get(GameState, 1)
        if not gs:
            gs = GameState(id=1)
            session.add(gs)
            await session.commit()
            logger.info("✅ Default GameState(id=1) created")


# -------------------------------------------------
# Environment setup
# -------------------------------------------------
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN is not set. Please define it in your environment variables.")

if not WEBHOOK_SECRET:
    raise ValueError("❌ WEBHOOK_SECRET is not set. Please define it in your environment variables.")

BOT_USERNAME = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")

# -------------------------------------------------
# Initialize FastAPI
# -------------------------------------------------
app = FastAPI()
app.include_router(webhook_router)
app.include_router(payments_router)

application: Application = None  # Telegram Application (global)
BOT_READY: bool = False          # prevent early webhook processing


# -------------------------------------------------
# Root route
# -------------------------------------------------
@app.get("/")
@app.head("/")
async def root():
    return {
        "status": "ok",
        "message": "NaijaPrizeGate Bot is running ✅",
        "health": "Check /health for bot status",
    }


# -------------------------------------------------
# Startup event
# -------------------------------------------------
@app.on_event("startup")
async def on_startup():
    global application, BOT_READY

    BOT_READY = False

    try:
        logger.info("🚀 Starting up NaijaPrizeGate...")

        # -------------------------------------------------
        # Ensure required DB rows exist
        # -------------------------------------------------
        await init_game_state()
        await ensure_game_state_exists()

        # -------------------------------------------------
        # Build Telegram Application
        # -------------------------------------------------
        application = Application.builder().token(BOT_TOKEN).build()

        # -------------------------------------------------
        # High Priority Handlers FIRST
        # -------------------------------------------------
        application.add_handler(
            CallbackQueryHandler(
                handle_claim_airtime_button,
                pattern=r"^claim_airtime:"
            ),
            group=-10,
        )

        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                handle_airtime_claim_phone,
            ),
            group=-9,
        )

        application.add_handler(support_conv, group=-8)

        # Admin reply command
        application.add_handler(CommandHandler("reply", admin_reply), group=-7)

        # -------------------------------------------------
        # Register All Other Handlers
        # -------------------------------------------------
        core.register_handlers(application)
        playtrivia.register_handlers(application)
        jambpractice.register_handlers(application)
        register_mockjamb_handlers(application)
        battle.register_handlers(application)
        register_challenge_handlers(application)
        free.register_handlers(application)
        payments.register_handlers(application)
        admin.register_handlers(application)

        # -------------------------------------------------
        # Global Error Handler
        # -------------------------------------------------
        application.add_error_handler(tg_error_handler)

        # -------------------------------------------------
        # Initialize Application
        # -------------------------------------------------
        await application.initialize()

        # -------------------------------------------------
        # Webhook Setup
        # -------------------------------------------------
        base_url = os.getenv("BASE_URL")
        if not base_url:
            raise ValueError("BASE_URL is not set")

        webhook_url = f"{base_url}/telegram/webhook/{WEBHOOK_SECRET}"

        await application.bot.set_webhook(webhook_url)
        logger.info("✅ Webhook set to %s", webhook_url)

        # -------------------------------------------------
        # Start Application
        # -------------------------------------------------
        await application.start()
        logger.info("🚀 Telegram bot via Webhook is LIVE")

        BOT_READY = True
        logger.info("✅ BOT_READY=True (safe to process updates)")

        # -------------------------------------------------
        # Background Tasks
        # -------------------------------------------------
        await start_background_tasks()
        logger.info("✅ Background tasks started")

    except Exception:
        BOT_READY = False

        clean_trace = re.sub(
            r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b",
            "[SECRET]",
            traceback.format_exc()
        )

        logger.error("❌ Unhandled exception during startup:\n%s", clean_trace)


# -------------------------------------------------
# Shutdown event
# -------------------------------------------------
@app.on_event("shutdown")
async def on_shutdown():
    global application, BOT_READY

    BOT_READY = False
    logger.info("🔻 BOT_READY=False (shutting down)")

    # Stop background tasks first
    try:
        await stop_background_tasks()
    except Exception:
        clean_trace = re.sub(
            r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b",
            "[SECRET]",
            traceback.format_exc()
        )
        logger.warning("⚠️ Error stopping background tasks:\n%s", clean_trace)

    # Then stop Telegram app
    if not application:
        return

    try:
        await application.stop()
    except RuntimeError as e:
        logger.info("ℹ️ application.stop() skipped: %s", e)
    except Exception:
        clean_trace = re.sub(
            r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b",
            "[SECRET]",
            traceback.format_exc()
        )
        logger.warning("⚠️ Unexpected error in application.stop():\n%s", clean_trace)

    try:
        await application.shutdown()
        logger.info("🛑 Telegram bot shutdown complete.")
    except Exception:
        clean_trace = re.sub(
            r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b",
            "[SECRET]",
            traceback.format_exc()
        )
        logger.warning("⚠️ Error in application.shutdown():\n%s", clean_trace)


# -------------------------------------------------
# Telegram webhook endpoint
# -------------------------------------------------
@app.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # ✅ Prevent race condition (Telegram hitting webhook before startup finishes)
    if application is None or not BOT_READY:
        # Return 200 so Telegram doesn't keep retrying aggressively during startup
        return {"ok": True, "status": "starting"}

    payload = await request.json()

    try:
        update = Update.de_json(payload, application.bot)
        await application.process_update(update)
    except Exception:
        clean_trace = re.sub(
            r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b", "[SECRET]", traceback.format_exc()
        )
        logger.error(f"❌ Error while processing update:\n{clean_trace}")

    return {"ok": True}


# -------------------------------------------------
# Health check endpoint
# -------------------------------------------------
@app.get("/health")
@app.head("/health")
async def health_check():
    return {"status": "ok", "bot_initialized": application is not None}


# --------------------------------------------------------------
# 🏆 WINNER FORM — HTML + API (Web-Based Flow)
# ---------------------------------------------------------------
# ---------------------------------------------------------------
# 📝 WINNER FORM PAGE
# ---------------------------------------------------------------
@app.get("/winner-form", response_class=HTMLResponse)
async def winner_form_page(token: str):
    """
    Secure winner form. Accepts only `token` query param (signed).
    Token contains tgid, choice and expiry.
    """
    ok, payload, err = verify_signed_token(token)
    if not ok:
        return HTMLResponse(f"""
            <html><body style="font-family: Arial; text-align:center; padding:40px;">
            <h2 style="color:red;">⚠️ Invalid or expired link</h2>
            <p>{err}</p>
            <p>If you believe this is an error, contact the admin.</p>
            </body></html>
        """, status_code=403)

    choice = payload["choice"]

    return HTMLResponse(f"""
    <html>
        <head>
            <title>Prize Claim Form</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 420px; margin: 50px auto; padding: 25px; border: 1px solid #ddd; border-radius: 12px; background-color: #fafafa; }}
                h2 {{ text-align: center; color: #333; }}
                label {{ font-weight: bold; display: block; margin-top: 12px; }}
                input, textarea {{ width: 100%; padding: 10px; margin-top: 6px; border-radius: 6px; border: 1px solid #ccc; font-size: 1em; }}
                button {{ width: 100%; margin-top: 20px; padding: 12px; background-color: #28a745; color: white; border: none; border-radius: 6px; font-size: 1em; cursor: pointer; }}
                button:hover {{ background-color: #218838; }}
            </style>
        </head>
        <body>
            <h2>🎉 Congratulations!</h2>
            <p>You’ve won a <b>{choice}</b>!</p>
            <p>Please provide your delivery details below 👇</p>

            <form action="/api/save_winner" method="post">
                <input type="hidden" name="token" value="{token}">
                <label>Full Name:</label>
                <input type="text" name="full_name" placeholder="Enter your full name" required>

                <label>Phone Number:</label>
                <input type="text" name="phone" placeholder="+234..." required>

                <label>Delivery Address:</label>
                <textarea name="address" rows="3" placeholder="Enter full delivery address" required></textarea>

                <button type="submit">Submit Details ✅</button>
            </form>
        </body>
    </html>
    """)


# ---------------------------------------------------------------
# 💾 SAVE WINNER FORM SUBMISSION
# ---------------------------------------------------------------
@app.post("/api/save_winner", response_class=HTMLResponse)
async def save_winner(
    token: str = Form(...),
    full_name: str = Form(...),
    phone: str = Form(...),
    address: str = Form(...),
):
    """
    Save a winner submission — requires a valid token. Token is verified & used to find/ensure the user.
    """
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))
    BOT_TOKEN = os.getenv("BOT_TOKEN")

    ok, payload, err = verify_signed_token(token)
    if not ok:
        return HTMLResponse(f"""
            <html><body style="font-family: Arial; text-align:center; padding:40px;">
            <h2 style="color:red;">⚠️ Invalid or expired submission</h2>
            <p>{err}</p>
            <p>If you believe this is an error, contact the admin.</p>
            </body></html>
        """, status_code=403)

    tgid = payload["tgid"]
    choice = payload["choice"]

    async with get_async_session() as session:
        user = await get_or_create_user(session, tg_id=tgid)
        pw = PrizeWinner(
            user_id=user.id,
            tg_id=tgid,
            choice=choice,
            delivery_status="Pending",
            submitted_at=datetime.now(timezone.utc),
            pending_at=datetime.now(timezone.utc),
            delivery_data={
                "full_name": full_name,
                "phone": phone,
                "address": address
            }
        )
        session.add(pw)
        await session.commit()
        await session.refresh(pw)

    try:
        if ADMIN_USER_ID and BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            msg = (
                f"📢 <b>NEW WINNER ALERT!</b>\n\n"
                f"👤 <b>Name:</b> {full_name}\n"
                f"📱 <b>Phone:</b> {phone}\n"
                f"🏠 <b>Address:</b> {address}\n"
                f"🎁 <b>Prize:</b> {choice}\n"
                f"🆔 <b>Telegram ID:</b> {tgid}\n"
                f"🆔 <b>Record ID:</b> <code>{pw.id}</code>\n"
                f"🕒 <i>Submitted via Winner Form</i>"
            )
            await bot.send_message(chat_id=ADMIN_USER_ID, text=msg, parse_mode="HTML")
    except Exception:
        logger.exception("❌ Failed to notify admin", exc_info=True)

    return HTMLResponse(
        """
        <html>
            <head>
                <title>Form Submitted</title>
                <style>body { font-family: Arial; text-align: center; margin-top: 100px; color: #333; } h2 { color: green; }</style>
            </head>
            <body>
                <h2>✅ Thank You!</h2>
                <p>Your delivery details have been received successfully.</p>
                <p>Our team will contact you soon to arrange your prize delivery 🚚✨</p>
            </body>
        </html>
        """,
        status_code=200
    )
