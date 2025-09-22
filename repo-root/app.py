# ==============================================================
# app.py
# ==============================================================
import os
import logging
import asyncio
from tasks import periodic_tasks
from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import Application
from logger import tg_error_handler
from handlers import core, payments, free, admin, tryluck # Ensure these import registers handlers

# Load environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# Logging setup
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI()
application: Application = None  # Telegram Application (global)


@app.on_event("startup")
async def on_startup():
    global application
    if not BOT_TOKEN or not RENDER_EXTERNAL_URL or not WEBHOOK_SECRET:
        logger.error("Missing one or more required environment variables: BOT_TOKEN, RENDER_EXTERNAL_URL, WEBHOOK_SECRET")
        raise RuntimeError("Missing required environment variables.")

    # Initialize Telegram Bot Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers are imported at top; they register themselves
    await application.initialize()
    logger.info("Telegram Application initialized ✅")

    # Add error handler here
    application. add_error_handler(tg_error_handler)

    # 4️⃣ Set Telegram webhook
    webhook_url = f"{RENDER_EXTERNAL_URL}/telegram/webhook{WEBHOOK_SECRET}"
    await application.bot.set_webhook(webhook_url)
    logger.info(f"Webhook set to {webhook_url} ✅")

    # start backgrounf tasks
    asyncio.create_task(periodic_tasks())

# =========================
# Shutdown (cleanup)
# =========================
@app.on_event("shutdown")
async def on_shutdown():
    global application
    if application:
        try:
            # Stop background tasks if needed (you can keep a reference to tasks to cancel them)
            # await background_task.cancel()  # optional, if you store tasks
            
            await application.stop()
            await application.shutdown()
            logger.info("🛑 Telegram bot stopped cleanly.")
        except Exception as e:
            logger.warning(f"⚠️ Error while shutting down Telegram bot: {e}")

# ----------------------------
# Telegram webhook endpoint
# ----------------------------
@app.post(f"/telegram/webhook{WEBHOOK_SECRET}")
async def telegram_webhook(request: Request):
    if application is None:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    payload = await request.json()
    update = Update.de_json(payload, application.bot)
    await application.process_update(update)
    return {"ok": True}

# Flutterwave webhook placeholder (adjust in handlers/payments.py)
@app.post(f"/flw/webhook{WEBHOOK_SECRET}")
async def flutterwave_webhook(request: Request):
    body = await request.json()
    # Implement verify_wehook() logic in services/payments.py
    # Example: await services.payments.verify_webhook(body)
    return {"ok": True}

# Health check endpoint for Render
@app.get("/health")
async def health_check():
    return {"status": "ok", "bot_initialized": application is not None}
