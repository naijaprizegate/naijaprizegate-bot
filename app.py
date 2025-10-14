# =====================================================
# app.py
# =====================================================
import os
import logging
import httpx
import sys

# Force unbuffered output (Render needs this for real-time logs)
os.environ["PYTHONUNBUFFERED"] = "1"

from fastapi import FastAPI, Query, Request, HTTPException, Depends, APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from telegram import Update, Bot
from telegram.ext import Application

# Local imports
from logger import tg_error_handler, logger
from handlers import core, payments, free, admin, tryluck
from tasks import start_background_tasks, stop_background_tasks
from db import init_game_state, get_async_session, get_session
from models import Payment, User
from helpers import get_or_create_user, add_tries
from logging_setup import logger

# ✅ Import Flutterwave-related functions/constants
from services.payments import (
    FLW_BASE_URL,
    FLW_SECRET_KEY,
    calculate_tries,
    verify_payment,
    validate_webhook,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# -------------------------------------------------
# Environment setup
# -------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")

if not BOT_TOKEN or not RENDER_EXTERNAL_URL or not WEBHOOK_SECRET or not FLW_SECRET_HASH:
    raise RuntimeError("❌ Missing required environment variables")


# -------------------------------------------------
# Initialize FastAPI + Telegram bot
# -------------------------------------------------
bot = Bot(token=BOT_TOKEN)
app = FastAPI()
application: Application = None  # Telegram Application (global)

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
    global application
    logger.info("🚀 Starting up NaijaPrizeGate...")

    # Ensure GameState & GlobalCounter rows exist
    await init_game_state()

    # Telegram Bot Application
    application = Application.builder().token(BOT_TOKEN).build()

    # ✅ Register handlers
    core.register_handlers(application)
    free.register_handlers(application)
    payments.register_handlers(application)
    admin.register_handlers(application)
    tryluck.register_handlers(application)

    # Initialize & start bot
    await application.initialize()
    await application.start()
    logger.info("Telegram Application initialized & started ✅")

    # Add error handler
    application.add_error_handler(tg_error_handler)

    # Webhook setup
    webhook_url = f"{RENDER_EXTERNAL_URL}/telegram/webhook/{WEBHOOK_SECRET}"
    await application.bot.set_webhook(webhook_url)
    logger.info(f"Webhook set to {webhook_url} ✅")

    # ✅ Start background tasks
    await start_background_tasks()
    logger.info("✅ Background tasks started.")

# -------------------------------------------------
# Shutdown event
# -------------------------------------------------
@app.on_event("shutdown")
async def on_shutdown():
    global application
    try:
        # Stop background tasks first
        await stop_background_tasks()

        # Then stop Telegram app
        if application:
            await application.stop()
            await application.shutdown()
            logger.info("🛑 Telegram bot stopped cleanly.")
    except Exception as e:
        logger.warning(f"⚠️ Error while shutting down: {e}")

# -------------------------------------------------
# Telegram webhook endpoint
# -------------------------------------------------
@app.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    if application is None:
        raise HTTPException(status_code=500, detail="Bot not initialized")

    payload = await request.json()
    update = Update.de_json(payload, application.bot)
    await application.process_update(update)
    return {"ok": True}
    
# ------------------------------------------------------
# Webhook: called by Flutterwave after payment
# ------------------------------------------------------
@router.post("/flw/webhook")
async def flutterwave_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    raw_body = await request.body()
    body_str = raw_body.decode("utf-8")

    # ✅ Allow Flutterwave dashboard test pings (no signature)
    signature = request.headers.get("verif-hash")
    if not signature:
        logger.info("🧪 Flutterwave test webhook received — allowing 200 OK response.")
        return {"status": "ok", "message": "Test webhook received"}

    # ✅ Validate real payment webhooks
    if not validate_webhook(request.headers, body_str):
        logger.warning("⚠️ Invalid Flutterwave webhook signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    body = await request.json()
    data = body.get("data", {})
    tx_ref = data.get("tx_ref")
    status = data.get("status")

    if not tx_ref:
        logger.error("❌ Webhook received without tx_ref")
        return {"status": "error", "message": "No tx_ref in webhook"}

    # ✅ Log full payload for debugging
    logger.info(f"🌍 Webhook received: {body}")
    logger.info(f"📦 tx_ref={tx_ref}, status={status}")

    # ✅ Extract meta info safely
    meta = data.get("meta", {}) or {}
    tg_id = meta.get("tg_id")
    username = meta.get("username") or "Unknown"
    amount = data.get("amount")

    if not tg_id:
        logger.warning(f"⚠️ Webhook missing tg_id for tx_ref={tx_ref}")
        return {"status": "error", "message": "Missing tg_id in meta"}

    # 🔍 Verify payment with Flutterwave (extra safety)
    try:
        from services.payments import verify_payment
        verified = await verify_payment(tx_ref, session, credit=False)
        if not verified:
            logger.warning(f"⚠️ Payment verification failed for tx_ref={tx_ref}")
            return {"status": "error", "message": "Verification failed"}
    except Exception as e:
        logger.exception(f"❌ Error verifying payment {tx_ref}: {e}")
        return {"status": "error", "message": str(e)}

    # 🔎 Fetch existing payment
    stmt = select(Payment).where(Payment.tx_ref == tx_ref)
    result = await session.execute(stmt)
    payment = result.scalar_one_or_none()

    credited_tries = calculate_tries(int(amount or 0))

    if status == "successful":
        # ✅ Ensure payment exists
        if not payment:
            user = await get_or_create_user(session, tg_id=tg_id, username=username)
            payment = Payment(
                tx_ref=tx_ref,
                status="successful",
                credited_tries=credited_tries,
                flw_tx_id=data.get("id"),
                user_id=user.id,
                amount=amount,
            )
            session.add(payment)
        else:
            payment.status = "successful"
            payment.credited_tries = credited_tries
            payment.flw_tx_id = data.get("id")

        # ✅ Credit user tries
        user = await get_or_create_user(session, tg_id=tg_id, username=username)
        await add_tries(session, user, credited_tries, paid=True)
        await session.commit()

        logger.info(f"🎁 Credited {credited_tries} tries to user {user.tg_id} ({username})")

        # ✅ Notify via Telegram
        try:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                chat_id=tg_id,
                text=f"✅ Payment successful!\n\nYou’ve been credited with {credited_tries} spin{'s' if credited_tries > 1 else ''}! 🎉\n\nUse /spin to try your luck."
            )
        except Exception as e:
            logger.error(f"⚠️ Failed to send Telegram DM to {tg_id}: {e}")

        return {"status": "success", "tx_ref": tx_ref}

    # ❌ Handle failed or incomplete payments
    if payment:
        payment.status = status or "failed"
        await session.commit()
        logger.info(f"❌ Payment {tx_ref} marked as {payment.status}")

    return {"status": "failed", "tx_ref": tx_ref}
