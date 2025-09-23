# ==============================================================
# app.py
# ==============================================================
import os
import logging
import asyncio
from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import Application

from logger import tg_error_handler
from handlers import core, payments, free, admin, tryluck  # ensure handlers register
from tasks import register_background_tasks
from tasks import periodic_tasks  # module with start_all_tasks()

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

    # Background tasks
    register_background_tasks(asyncio.get_event_loop())   # from tasks/__init__.py
    await periodic_tasks.start_all_tasks()               # from tasks/periodic_tasks.py


# --------------------------------------------------------------
# Shutdown event
# --------------------------------------------------------------
@app.on_event("shutdown")
async def on_shutdown():
    global application
    if application:
        try:
            await application.stop()
            await application.shutdown()
            logger.info("🛑 Telegram bot stopped cleanly.")
        except Exception as e:
            logger.warning(f"⚠️ Error while shutting down Telegram bot: {e}")


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

