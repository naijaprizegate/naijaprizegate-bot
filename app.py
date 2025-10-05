# =====================================================
# app.py
# =====================================================

import os
import logging
import httpx

from fastapi import FastAPI, Query, Request, HTTPException, Depends
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
from models import Payment
from services.payments import FLW_BASE_URL, FLW_SECRET_KEY  # ✅ Keep these two only

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
# Logging setup
# -------------------------------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger("app")

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

# -------------------------------------------------
# Flutterwave webhook (source of truth)
# -------------------------------------------------
@app.post("/flw/webhook")
async def flutterwave_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session)
):
    body = await request.json()
    tx_ref = body.get("data", {}).get("tx_ref")

    if not tx_ref:
        return {"status": "error", "message": "No tx_ref in webhook"}

    ok = await verify_payment(tx_ref, session, bot=bot, credit=True)
    return {"status": "success" if ok else "failed"}

# -------------------------------------------------
# Flutterwave Redirect (user lands here after checkout)
# -------------------------------------------------
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
                    try {{
                        const response = await fetch("/flw/redirect/status?tx_ref={tx_ref}");
                        const data = await response.json();
                        if (data.done) {{
                            document.body.innerHTML = data.html;
                        }} else {{
                            setTimeout(checkStatus, 2500);
                        }}
                    }} catch (err) {{
                        console.error("Polling error:", err);
                        setTimeout(checkStatus, 3000);
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


# -------------------------------------------------
# Flutterwave Redirect Status (verifies + auto credits)
# -------------------------------------------------
@app.get("/flw/redirect/status")
async def flutterwave_redirect_status(
    tx_ref: str,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Payment).where(Payment.tx_ref == tx_ref)
    result = await session.execute(stmt)
    payment = result.scalar_one_or_none()

    success_url = f"https://t.me/NaijaPrizeGateBot?start=payment_success_{tx_ref}"
    failed_url = f"https://t.me/NaijaPrizeGateBot?start=payment_failed_{tx_ref}"
    notfound_url = "https://t.me/NaijaPrizeGateBot?start=payment_notfound"

    # --- Payment not found ---
    if not payment:
        html = f"""
        <h2 style="color:red;">❌ Payment not found</h2>
        <p><a href="{notfound_url}" style="color:blue; font-weight:bold;">Return to Telegram Bot</a></p>
        <script>setTimeout(() => window.open('', '_self').close(), 5000);</script>
        """
        return JSONResponse({"done": True, "html": html})

    # --- Check local DB first ---
    if payment.status == "successful":
        html = f"""
        <h2 style="color:green;">✅ Payment Successful</h2>
        <p>Transaction Reference: <b>{tx_ref}</b></p>
        <p>You’ve been credited with <b>{payment.credited_tries}</b> tries 🎉</p>
        <p>This tab will redirect to Telegram in 5 seconds...</p>
        <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
        """
        return JSONResponse({"done": True, "html": html})

    if payment.status in ["failed", "expired"]:
        html = f"""
        <h2 style="color:red;">❌ Payment Failed</h2>
        <p>Transaction Reference: <b>{tx_ref}</b></p>
        <p>If money was deducted, please contact support.</p>
        <script>setTimeout(() => window.location.href="{failed_url}", 8000);</script>
        """
        return JSONResponse({"done": True, "html": html})

    # --- Still pending → fallback verify directly from Flutterwave ---
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{FLW_BASE_URL}/transactions/verify_by_reference?tx_ref={tx_ref}",
                headers={"Authorization": f"Bearer {FLW_SECRET_KEY}"},
            )
        data = resp.json()
        logger.info(f"Verification for {tx_ref}: {data}")

        if data.get("status") == "success" and data["data"]["status"] == "successful":
            payment.status = "successful"
            payment.flw_tx_id = data["data"]["id"]
            payment.credited_tries = 1  # or set dynamically based on plan
            await session.commit()

            html = f"""
            <h2 style="color:green;">✅ Payment Verified Successfully</h2>
            <p>Transaction Reference: <b>{tx_ref}</b></p>
            <p>You’ve been credited with <b>{payment.credited_tries}</b> tries 🎉</p>
            <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
            """
            return JSONResponse({"done": True, "html": html})

    except Exception as e:
        logger.error(f"Verification error for {tx_ref}: {e}")

    # --- Pending still ---
    html_template = f"""
    <h2 style="color:orange;">⏳ Payment Pending</h2>
    <p>Transaction Reference: <b>{tx_ref}</b></p>
    <p>You’ve been credited with <b>{payment.credited_tries or 0}</b> tries 🎯</p>
    <p>This tab will automatically update once confirmed.</p>
    """
    return JSONResponse({"done": False, "html": html_template})


# -------------------------------------------------
# Health check endpoint
# -------------------------------------------------
@app.get("/health")
@app.head("/health")
async def health_check():
    return {"status": "ok", "bot_initialized": application is not None}
