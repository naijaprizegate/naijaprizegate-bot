# =====================================================
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

from db import init_game_state

# --------------------------------------------------------------
# Load environment variables
# --------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")
if not FLW_SECRET_HASH:
    raise RuntimeError("❌ FLW_SECRET_HASH not set in environment")

# --------------------------------------------------------------
# Logging setup
# --------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger("app")

# --------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------
app = FastAPI()
application: Application = None  # Telegram Application (global)


# --------------------------------------------------------------
# Root route
# --------------------------------------------------------------
@app.get("/")
@app.head("/")
async def root():
    return {
        "status": "ok",
        "message": "NaijaPrizeGate Bot is running ✅",
        "health": "Check /health for bot status",
    }


# --------------------------------------------------------------
# Startup event
# --------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    global application
    if not BOT_TOKEN or not RENDER_EXTERNAL_URL or not WEBHOOK_SECRET:
        logger.error(
            "❌ Missing one or more required env vars: BOT_TOKEN, RENDER_EXTERNAL_URL, WEBHOOK_SECRET"
        )
        raise RuntimeError("Missing required environment variables.")
    
    #  👈 Ensure GameState & GlobalCounter rows exist
    await init_game_state()

    # Telegram Bot Application
    application = Application.builder().token(BOT_TOKEN).build()

    # ✅ Register all handlers here
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
# Flutterwave webhook (real handler)
# --------------------------------------------------------------
from fastapi import Request, HTTPException
from db import AsyncSessionLocal
from models import Payment
from sqlalchemy import select, update
from logger import logger
from services.payments import FLW_SECRET_HASH


@app.post("/flw/webhook/{secret}")
async def flutterwave_webhook(secret: str, request: Request):
    # 1️⃣ Check URL secret
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # 2️⃣ Parse body
    data = await request.json()
    logger.info(f"💳 Flutterwave webhook received: {data}")

    # 3️⃣ Verify Flutterwave signature
    signature = request.headers.get("verif-hash")
    if not signature or signature != FLW_SECRET_HASH:
        raise HTTPException(status_code=403, detail="Invalid signature")

    # 4️⃣ Extract values
    tx_status = data.get("status")   # "successful", "failed"
    tx_id = data.get("id")           # Flutterwave tx id
    ref = data.get("tx_ref")         # your own payment reference

    # 5️⃣ Update DB
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Payment).where(Payment.tx_ref == ref))
        payment = result.scalars().first()

        if not payment:
            logger.warning(f"No Payment record found for ref {ref}")
        else:
            stmt = (
                update(Payment)
                .where(Payment.id == payment.id)
                .values(status=tx_status, flw_tx_id=tx_id)
            )
            await session.execute(stmt)
            await session.commit()
            logger.info(f"✅ Payment {ref} updated to {tx_status}")

            # ✅ Notify user on Telegram if payment is successful
            if tx_status == "successful":
                try:
                    keyboard = [
                        [InlineKeyboardButton("🎰 TryLuck", callback_data="tryluck")],
                        [InlineKeyboardButton("🎟️ MyTries", callback_data="mytries")],
                        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await bot_app.bot.send_message(
                        chat_id=payment.user_id,  # assumes you stored user_id in Payment table
                        text=f"✅ Payment received! You’ve been credited.\n\nRef: {ref}",
                        reply_markup=reply_markup
                    )
                    logger.info(f"🎉 Notified user {payment.user_id} about successful payment.")
                except Exception as e:
                    logger.exception(f"❌ Failed to notify user {payment.user_id}: {e}")

    return {"status": "success"}

# --------------------------------------------------------------
# Flutterwave Redirect (after checkout)
# --------------------------------------------------------------
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from db import get_async_session
from services.payments import verify_payment

@app.get("/flw/redirect", response_class=HTMLResponse)
async def flutterwave_redirect(tx_ref: str, status: str, session: AsyncSession = Depends(get_async_session)):
    """
    Redirect endpoint for users after Flutterwave checkout.
    Verifies payment and shows success/failure message.
    """

    verified = await verify_payment(tx_ref, session)

    if status.lower() == "successful" and verified:
        html_content = f"""
        <html>
            <head>
                <title>Payment Success</title>
                <script>
                    // Auto-close after 5 seconds
                    setTimeout(function() {{
                        window.open('', '_self').close();
                    }}, 5000);
                </script>
            </head>
            <body style="font-family: Arial; text-align:center; padding:40px;">
                <h2 style="color:green;">✅ Payment Successful</h2>
                <p>Transaction Reference: <b>{tx_ref}</b></p>
                <p>Thank you for your payment! 🎉</p>
                <p>This tab will close automatically in 5 seconds.</p>
                <p><a href="https://t.me/NaijaPrizeGateBot" style="color:blue;">Return to Telegram Bot now</a></p>
            </body>
        </html>
        """
    else:
        html_content = f"""
        <html>
            <head>
                <title>Payment Failed</title>
                <script>
                    // Auto-close after 8 seconds
                    setTimeout(function() {{
                        window.open('', '_self').close();
                    }}, 8000);
                </script>
            </head>
            <body style="font-family: Arial; text-align:center; padding:40px;">
                <h2 style="color:red;">❌ Payment Failed</h2>
                <p>Transaction Reference: <b>{tx_ref}</b></p>
                <p>If money was deducted, please contact support.</p>
                <p>This tab will close automatically in 8 seconds.</p>
                <p><a href="https://t.me/NaijaPrizeGateBot" style="color:blue;">Return to Telegram Bot now</a></p>
            </body>
        </html>
        """

    return HTMLResponse(content=html_content, status_code=200)

# --------------------------------------------------------------
# Health check endpoint
# --------------------------------------------------------------
@app.get("/health")
@app.head("/health")
async def health_check():
    return {"status": "ok", "bot_initialized": application is not None}

