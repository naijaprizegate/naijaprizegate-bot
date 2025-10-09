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
    if not validate_webhook(request.headers, raw_body.decode("utf-8")):
        logger.warning("⚠️ Invalid Flutterwave webhook signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    body = await request.json()
    data = body.get("data", {})
    tx_ref = data.get("tx_ref")
    status = data.get("status")

    if not tx_ref:
        logger.error("❌ Webhook received without tx_ref")
        return {"status": "error", "message": "No tx_ref in webhook"}

    logger.info(f"🌍 Webhook received for tx_ref={tx_ref}, status={status}")

    stmt = select(Payment).where(Payment.tx_ref == tx_ref)
    result = await session.execute(stmt)
    payment = result.scalar_one_or_none()

    if status == "successful":
        amount = data.get("amount")
        credited_tries = calculate_tries(amount)

        # Ensure payment row exists
        if not payment:
            payment = Payment(
                tx_ref=tx_ref,
                status="successful",
                credited_tries=credited_tries,
                flw_tx_id=data.get("id"),
                tg_id=data.get("meta", {}).get("tg_id"),
                username=data.get("meta", {}).get("username"),
            )
            session.add(payment)
        else:
            payment.status = "successful"
            payment.credited_tries = credited_tries
            payment.flw_tx_id = data.get("id")

        # Credit user
        user = await get_or_create_user(
            session,
            tg_id=payment.tg_id,
            username=payment.username
        )
        await add_tries(session, user, credited_tries, paid=True)
        await session.commit()

        logger.info(f"🎁 User {user.id} credited with {credited_tries} tries")

        # ✅ Send Telegram DM immediately
        try:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                chat_id=user.tg_id,
                text=f"🎁 You’ve been credited with {credited_tries} spin{'s' if credited_tries > 1 else ''}! 🎉\n\nUse /spin to try your luck."
            )
        except Exception as e:
            logger.error(f"⚠️ Failed to send Telegram DM: {e}")

        return {"status": "success", "tx_ref": tx_ref}

    # Non-success cases
    if payment:
        payment.status = status or "failed"
        await session.commit()
    return {"status": "failed", "tx_ref": tx_ref}

# ------------------------------------------------------
# Redirect: user-friendly "verifying payment" page
# ------------------------------------------------------
@router.get("/flw/redirect", response_class=HTMLResponse)
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
                .countdown {{
                    font-weight: bold;
                    color: #f39c12;
                }}
            </style>
            <script>
                let countdown = 5;
                let totalWait = 0;
                const fallbackLimit = 120; // 120s = 2 minutes

                function startCountdown() {{
                    const cdElem = document.getElementById("countdown");
                    countdown = 5;
                    cdElem.textContent = countdown;
                    const interval = setInterval(() => {{
                        countdown -= 1;
                        if (countdown <= 0) {{
                            clearInterval(interval);
                        }}
                        cdElem.textContent = countdown;
                    }}, 1000);
                }}

                async function checkStatus() {{
                    try {{
                        const response = await fetch("/flw/redirect/status?tx_ref={tx_ref}");
                        const data = await response.json();
                        if (data.done) {{
                            document.body.innerHTML = data.html;
                        }} else {{
                            totalWait += 5;
                            if (totalWait >= fallbackLimit) {{
                                // Fallback redirect: send user to Telegram with tx_ref
                                window.location.href = "https://t.me/NaijaPrizeGateBot?start=payment_pending_{tx_ref}";
                                return;
                            }}
                            startCountdown();
                            setTimeout(checkStatus, 5000);
                        }}
                    }} catch (err) {{
                        console.error("Polling error:", err);
                        totalWait += 5;
                        if (totalWait >= fallbackLimit) {{
                            window.location.href = "https://t.me/NaijaPrizeGateBot?start=payment_pending_{tx_ref}";
                            return;
                        }}
                        setTimeout(checkStatus, 5000);
                    }}
                }}

                window.onload = () => {{
                    startCountdown();
                    checkStatus();
                }};
            </script>
        </head>
        <body>
            <h2>⏳ Verifying your payment...</h2>
            <div class="spinner"></div>
            <p>✅ Please wait, we’re checking Flutterwave every <span id="countdown">5</span> seconds.</p>
            <p>(Auto-redirecting if nothing happens after 2 minutes)</p>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

# ------------------------------------------------------
# Redirect status polling with countdown refresh
# ------------------------------------------------------
@router.get("/flw/redirect/status")
async def flutterwave_redirect_status(
    tx_ref: str,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Payment).where(Payment.tx_ref == tx_ref)
    result = await session.execute(stmt)
    payment = result.scalar_one_or_none()

    success_url = f"https://t.me/NaijaPrizeGateBot?start=payment_success_{tx_ref}"
    failed_url = f"https://t.me/NaijaPrizeGateBot?start=payment_failed_{tx_ref}"

    if not payment:
        return JSONResponse({
            "done": True,
            "html": f"<h2 style='color:red;'>❌ Payment not found</h2>"
                    f"<p><a href='{failed_url}'>Return to Telegram</a></p>"
        })

    if payment.status == "successful":
        credited_text = f"{payment.credited_tries} spin{'s' if payment.credited_tries > 1 else ''}"
        return JSONResponse({
            "done": True,
            "html": f"""
            <h2 style="color:green;">✅ Payment Successful</h2>
            <p>Transaction Reference: <b>{tx_ref}</b></p>
            <p>🎁 You’ve been credited with <b>{credited_text}</b>! 🎉</p>
            <p>This tab will redirect to Telegram in 5 seconds...</p>
            <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
            """
        })

    if payment.status in ["failed", "expired"]:
        return JSONResponse({
            "done": True,
            "html": f"""
            <h2 style="color:red;">❌ Payment Failed</h2>
            <p>Transaction Reference: <b>{tx_ref}</b></p>
            <script>setTimeout(() => window.location.href="{failed_url}", 5000);</script>
            """
        })

    # Still pending → spinner + countdown
    return JSONResponse({
        "done": False,
        "html": f"""
        <h2 style="color:orange;">⏳ Payment Pending</h2>
        <p>Transaction Reference: <b>{tx_ref}</b></p>
        <p>⚠️ Your payment is still being processed by Flutterwave.</p>
        <p>✅ Don’t close this tab — once confirmed, your spins will be credited automatically 🎁</p>
        <div class="spinner" style="margin:20px auto;height:40px;width:40px;border:5px solid #ccc;border-top-color:#f39c12;border-radius:50%;animation:spin 1s linear infinite;"></div>
        <p>🔄 Checking again in <span id="countdown">5</span> seconds...</p>
        <script>
            let countdown = 5;
            const cdElem = document.getElementById("countdown");
            setInterval(() => {{
                countdown -= 1;
                if (countdown <= 0) {{
                    countdown = 5;
                }}
                cdElem.textContent = countdown;
            }}, 1000);
        </script>
        <style>@keyframes spin {{ to {{ transform: rotate(360deg); }} }}</style>
        """
    })


# -------------------------------------------------
# Health check endpoint
# -------------------------------------------------
@app.get("/health")
@app.head("/health")
async def health_check():
    return {"status": "ok", "bot_initialized": application is not None}

