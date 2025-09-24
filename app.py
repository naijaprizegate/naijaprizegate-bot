# ==============================================================
# app.py
# ==============================================================

import os
import logging
from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import Application

from logger import tg_error_handler
from handlers import core, payments, free, admin, tryluck  # ensure handlers register
from tasks import start_background_tasks  # unified entrypoint

# --------------------------------------------------------------
# Load environment variables
# --------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# --------------------------------------------------------------
# Logging setup
# --------------------------------------------------------------
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# --------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------
app = FastAPI()
application: Application = None  # Telegram Application (global)

# Added root route
@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "NaijaPrizeGate Bot is running ✅",
        "health": f"Check /health for bot status",
    }

# --------------------------------------------------------------
# Startup event
# --------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    global application
    if not BOT_TOKEN or not RENDER_EXTERNAL_URL or not WEBHOOK_SECRET:
        logger.error("❌ Missing one or more required env vars: BOT_TOKEN, RENDER_EXTERNAL_URL, WEBHOOK_SECRET")
        raise RuntimeError("Missing required environment variables.")

    # Telegram Bot Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Initialize bot
    await application.initialize()
    logger.info("Telegram Application initialized ✅")

    # Add error handler
    application.add_error_handler(tg_error_handler)

    # Webhook setup
    webhook_url = f"{RENDER_EXTERNAL_URL}/telegram/webhook/{WEBHOOK_SECRET}"
    await application.bot.set_webhook(webhook_url)
    logger.info(f"Webhook set to {webhook_url} ✅")

    # ✅ Start background tasks (unified entrypoint)
    await start_background_tasks()


# --------------------------------------------------------------
# Shutdown event
# --------------------------------------------------------------
@app.on_event("shutdown")
async def on_shutdown():
    global application
    try:
        # Stop background tasks first
        from tasks import stop_background_tasks
        await stop_background_tasks()

        # Then stop Telegram app
        if application:
            await application.stop()
            await application.shutdown()
            logger.info("🛑 Telegram bot stopped cleanly.")
    except Exception as e:
        logger.warning(f"⚠️ Error while shutting down: {e}")


# --------------------------------------------------------------
# Telegram webhook endpoint
# --------------------------------------------------------------
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


# --------------------------------------------------------------
# Flutterwave webhook placeholder
# --------------------------------------------------------------
@app.post("/flw/webhook/{secret}")
async def flutterwave_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    body = await request.json()
    # TODO: implement verify_webhook() in services/payments.py
    return {"ok": True}


# --------------------------------------------------------------
# Health check endpoint
# --------------------------------------------------------------
@app.get("/health")
async def health_check():
    return {"status": "ok", "bot_initialized": application is not None}

