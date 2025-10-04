# =====================================================
# app.py
# ==============================================================

import os, logging
from fastapi import FastAPI, Query, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update, Bot
from telegram.ext import Application

# local imports
from logger import tg_error_handler, logger
from handlers import core, payments, free, admin, tryluck
from tasks import start_background_tasks, stop_background_tasks
from db import init_game_state, get_async_session, get_session
from services.payments import verify_payment
from models import Payment

# -------------------------------------------------
# Initialize
# -------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")

if not BOT_TOKEN or not RENDER_EXTERNAL_URL or not WEBHOOK_SECRET or not FLW_SECRET_HASH:
    raise RuntimeError("❌ Missing required environment variables")

bot = Bot(token=BOT_TOKEN)
app = FastAPI()
application: Application = None

# -------------------------------------------------
# Routes
# -------------------------------------------------

@app.get("/")
@app.head("/")
async def root():
    return {"status": "ok", "message": "NaijaPrizeGate Bot is running ✅"}


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

@app.post("/flw/webhook")
async def flutterwave_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    """
    Flutterwave webhook endpoint.
    This is called by Flutterwave after a payment.
    It MUST return 200 quickly, so do lightweight work here.
    """
    body = await request.json()
    tx_ref = body.get("data", {}).get("tx_ref")

    if not tx_ref:
        return {"status": "error", "message": "No tx_ref in webhook"}

    # Call your verify_payment with credit=True so user gets credited
    ok = await verify_payment(tx_ref, session, bot=bot, credit=True)

    return {"status": "success" if ok else "failed"}


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

