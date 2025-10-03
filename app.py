# =====================================================
# app.py
# ==============================================================

import os
import logging
from fastapi import FastAPI, Query, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
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
# Flutterwave webhook (SINGLE SOURCE OF TRUTH)
# --------------------------------------------------------------
from fastapi import Request, HTTPException
from db import AsyncSessionLocal
from logger import logger

@app.post("/flw/webhook/{secret}")
async def flutterwave_webhook(secret: str, request: Request):
    # 1️⃣ Validate URL secret
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # 2️⃣ Parse payload
    payload = await request.json()
    logger.info(f"💳 Flutterwave webhook received: {payload}")

    # 3️⃣ Verify Flutterwave signature
    signature = request.headers.get("verif-hash")
    if signature != FLW_SECRET_HASH:
        raise HTTPException(status_code=403, detail="Invalid signature")

    # 4️⃣ Extract tx_ref
    flw_data = payload.get("data", {})
    ref = flw_data.get("tx_ref")
    if not ref:
        logger.warning("⚠️ Webhook payload missing tx_ref")
        return {"status": "ignored"}

    # 5️⃣ Call verify_payment with credit=True → CREDIT HAPPENS ONLY HERE
    async with AsyncSessionLocal() as session:
        success = await verify_payment(ref, session, bot=bot_app.bot, credit=True)

    logger.info(f"✅ Webhook processed for {ref}, success={success}")
    return {"status": "success" if success else "ignored"}


# --------------------------------------------------------------
# Flutterwave Redirect (user lands here after checkout)
# --------------------------------------------------------------
@app.get("/flw/redirect", response_class=HTMLResponse)
async def flutterwave_redirect(tx_ref: str = Query(...)):
    html_content = f"""
    <html>
        <head>
            <title>Verifying Payment</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    text-align: center;
                    padding: 50px;
                }}
                .spinner {{
                    margin: 30px auto;
                    height: 40px;
                    width: 40px;
                    border: 5px solid #ccc;
                    border-top-color: #4CAF50;
                    border-radius: 50%;
                    animation: spin 1s linear infinite;
                }}
                @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
            </style>
            <script>
                async function checkStatus() {{
                    let response = await fetch("/flw/redirect/status?tx_ref={tx_ref}");
                    let data = await response.json();
                    if (data.done) {{
                        document.body.innerHTML = data.html;
                    }} else {{
                        setTimeout(checkStatus, 2000);
                    }}
                }}
                window.onload = checkStatus;
            </script>
        </head>
        <body>
            <h2>⏳ Verifying your payment...</h2>
            <div class="spinner"></div>
            <p>Please wait a few seconds.</p>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

# --------------------------------------------------------------
# Flw Redirect Status (polls DB until webhook credits user)
# --------------------------------------------------------------
@app.get("/flw/redirect/status")
async def flutterwave_redirect_status(
    tx_ref: str,
    session: AsyncSession = Depends(get_async_session),
):
    """
    Polled by /flw/redirect until webhook updates payment in DB.
    This does NOT credit tries — webhook is the source of truth.
    """
    # ✅ Do NOT credit here, only check DB
    stmt = select(Payment).where(Payment.tx_ref == tx_ref)
    result = await session.execute(stmt)
    payment = result.scalar_one_or_none()

    if not payment:
        return JSONResponse({"done": True, "html": "<h2 style='color:red;'>❌ Payment not found</h2>"})

    if payment.status == "successful":
        html = f"""
        <h2 style="color:green;">✅ Payment Successful</h2>
        <p>Transaction Reference: <b>{tx_ref}</b></p>
        <p>You’ve been credited with <b>{payment.credited_tries}</b> tries 🎉</p>
        <p>This tab will close automatically in 5 seconds.</p>
        <p><a href="https://t.me/NaijaPrizeGateBot" style="color:blue; font-weight:bold;">Return to Telegram Bot</a></p>
        <script>
            setTimeout(function() {{ window.open('', '_self').close(); }}, 5000);
        </script>
        """
        return JSONResponse({"done": True, "html": html})

    elif payment.status in ["failed", "expired"]:
        html = f"""
        <h2 style="color:red;">❌ Payment Failed</h2>
        <p>Transaction Reference: <b>{tx_ref}</b></p>
        <p>If money was deducted, please contact support.</p>
        <p>This tab will close automatically in 8 seconds.</p>
        <p><a href="https://t.me/NaijaPrizeGateBot" style="color:blue; font-weight:bold;">Return to Telegram Bot</a></p>
        <script>
            setTimeout(function() {{ window.open('', '_self').close(); }}, 8000);
        </script>
        """
        return JSONResponse({"done": True, "html": html})

    # Still pending → keep polling
    return JSONResponse({"done": False})


# --------------------------------------------------------------
# Health check endpoint
# --------------------------------------------------------------
@app.get("/health")
@app.head("/health")
async def health_check():
    return {"status": "ok", "bot_initialized": application is not None}
