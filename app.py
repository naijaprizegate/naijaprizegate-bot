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
    logger.info("⚡ Flutterwave webhook triggered!")
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
    data = body.get("data", {}) or {}
    tx_ref = data.get("tx_ref")
    status = data.get("status")
    amount = data.get("amount")

    if not tx_ref:
        logger.error("❌ Webhook received without tx_ref")
        return {"status": "error", "message": "No tx_ref in webhook"}

    # ✅ Accept both meta shapes
    meta = data.get("meta") or data.get("meta_data") or {}
    logger.info(f"🌍 Webhook received: {body}")
    logger.info(f"📦 tx_ref={tx_ref}, status={status}, meta_keys={list(meta.keys())}")

    # Defensive extraction of tg_id/username
    raw_tg_id = meta.get("tg_id") or meta.get("tgId") or meta.get("customer") or None
    username = (
        meta.get("username")
        or meta.get("user")
        or meta.get("customer_name")
        or "Unknown"
    )

    # Normalize tg_id if possible (string digits -> int)
    tg_id = None
    if raw_tg_id is not None:
        try:
            tg_id = int(raw_tg_id)
        except Exception:
            tg_id = raw_tg_id  # keep as-is (rare)

    # ⚙️ Fallback: if tg_id is missing, re-verify with Flutterwave to recover meta
    if tg_id is None:
        logger.warning(
            f"⚠️ Webhook missing tg_id for tx_ref={tx_ref} — will verify manually"
        )
        try:
            from services.payments import verify_payment
            verified = await verify_payment(tx_ref)
            meta2 = verified.get("data", {}).get("meta", {}) if verified else {}
            tg_id = meta2.get("tg_id")
            username = meta2.get("username", username)
            if tg_id:
                logger.info(f"✅ Recovered tg_id={tg_id} via verify_payment()")
            else:
                logger.error(f"❌ Could not recover tg_id for {tx_ref}")
                return {"status": "error", "msg": "no tg_id found"}
        except Exception as e:
            logger.exception(f"❌ Error verifying to recover tg_id for {tx_ref}: {e}")
            return {"status": "error", "message": str(e)}

    # 🔍 Verify payment with Flutterwave (extra safety, "credit=False" so we don't double-credit here)
    try:
        from services.payments import verify_payment
        verified = await verify_payment(tx_ref, session, credit=False)
        if not verified:
            logger.warning(f"⚠️ Payment verification failed for tx_ref={tx_ref}")
    except Exception as e:
        logger.exception(f"❌ Error verifying payment {tx_ref}: {e}")
        return {"status": "error", "message": str(e)}

    # 🔎 Fetch existing payment
    stmt = select(Payment).where(Payment.tx_ref == tx_ref)
    result = await session.execute(stmt)
    payment = result.scalar_one_or_none()

    credited_tries = calculate_tries(int(amount or 0))

    if status == "successful":
        # Ensure we have a linked User (if tg_id present) and user_id is User.id (UUID)
        user = None
        if tg_id is not None:
            user = await get_or_create_user(session, tg_id=tg_id, username=username)

        # ✅ Ensure payment exists & is linked correctly
        if not payment:
            payment = Payment(
                tx_ref=str(tx_ref),
                status="successful",
                credited_tries=credited_tries,
                flw_tx_id=str(data.get("id")) if data.get("id") is not None else None,
                user_id=user.id if user else None,
                amount=amount,
                tg_id=int(tg_id) if str(tg_id).isdigit() else None,
                username=username,
            )
            session.add(payment)
            await session.flush()
        else:
            payment.status = "successful"
            payment.credited_tries = credited_tries
            if data.get("id") is not None:
                payment.flw_tx_id = str(data.get("id"))
            if user and not payment.user_id:
                payment.user_id = user.id
            if str(tg_id).isdigit():
                payment.tg_id = int(str(tg_id))
            payment.username = username or payment.username

        # ✅ Credit user tries (if we have user)
        if user:
            await add_tries(session, user, credited_tries, paid=True)
            await session.commit()
            logger.info(
                f"🎁 Credited {credited_tries} tries to user {user.tg_id} ({username})"
            )
        else:
            await session.commit()
            logger.info(
                f"🎁 Payment {tx_ref} recorded but no tg_id/user found — will resolve later"
            )

        # ✅ Notify via Telegram (only if tg_id present)
        if tg_id:
            try:
                bot = Bot(token=BOT_TOKEN)
                await bot.send_message(
                    chat_id=tg_id,
                    text=(
                        f"✅ Payment successful!\n\nYou’ve been credited with {credited_tries} "
                        f"spin{'s' if credited_tries > 1 else ''}! 🎉\n\nUse /spin to try your luck."
                    ),
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


# ------------------------------------------------------
# Redirect: user-friendly "verifying payment" page
# ------------------------------------------------------
@router.get("/flw/redirect", response_class=HTMLResponse)
async def flutterwave_redirect(
    tx_ref: str = Query(...),
    status: str | None = None,
    transaction_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    from services.payments import resolve_payment_status, verify_payment

    success_url = f"https://t.me/NaijaPrizeGateBot?start=payment_success_{tx_ref}"
    failed_url = f"https://t.me/NaijaPrizeGateBot?start=payment_failed_{tx_ref}"

    # 🔎 Always resolve via central helper
    payment = await resolve_payment_status(tx_ref, session)

    # 🛠️ If still pending and Flutterwave sent us transaction_id → verify directly (credit True so the user gets credited)
    if (not payment or payment.status not in ["successful", "failed", "expired"]) and transaction_id:
        try:
            await verify_payment(tx_ref, session, credit=True)
            payment = await resolve_payment_status(tx_ref, session)
        except Exception as e:
            logger.exception(f"❌ Error during redirect verify for {tx_ref}: {e}")

    if payment:
        if payment.status == "successful":
            credited_text = f"{payment.credited_tries} spin{'s' if payment.credited_tries > 1 else ''}"
            return HTMLResponse(f"""
                <h2 style="color:green;">✅ Payment Successful</h2>
                <p>Transaction Reference: <b>{tx_ref}</b></p>
                <p>🎁 You’ve been credited with <b>{credited_text}</b>! 🎉</p>
                <p>This tab will redirect to Telegram in 5 seconds...</p>
                <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
            """, status_code=200)

        if payment.status in ["failed", "expired"]:
            return HTMLResponse(f"""
                <h2 style="color:red;">❌ Payment Failed</h2>
                <p>Transaction Reference: <b>{tx_ref}</b></p>
                <script>setTimeout(() => window.location.href="{failed_url}", 5000);</script>
            """, status_code=200)

    # If still pending → spinner page with polling
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
                async function poll() {{
                    const res = await fetch("/flw/redirect/status?tx_ref={tx_ref}");
                    const data = await res.json();
                    if (data.done) {{
                        document.body.innerHTML = data.html;
                    }} else {{
                        setTimeout(poll, 5000);
                    }}
                }}
                window.onload = poll;
            </script>
        </head>
        <body>
            <h2>⏳ Verifying your payment...</h2>
            <div class="spinner"></div>
            <p>✅ Please wait, we’re checking Flutterwave every 5 seconds.</p>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)


# ------------------------------------------------------
# Redirect status polling with countdown + verify fallback
# ------------------------------------------------------
@router.get("/flw/redirect/status")
async def flutterwave_redirect_status(
    tx_ref: str,
    session: AsyncSession = Depends(get_session),
):
    from services.payments import resolve_payment_status, verify_payment

    success_url = f"https://t.me/NaijaPrizeGateBot?start=payment_success_{tx_ref}"
    failed_url = f"https://t.me/NaijaPrizeGateBot?start=payment_failed_{tx_ref}"

    payment = await resolve_payment_status(tx_ref, session)

    # 🧠 If still pending after several polls, try direct verify again
    if payment and payment.status not in ["successful", "failed", "expired"]:
        try:
            await verify_payment(tx_ref, session, credit=True)
            payment = await resolve_payment_status(tx_ref, session)
        except Exception as e:
            logger.exception(f"❌ Error during polling verify for {tx_ref}: {e}")

    # Case 1: Not found
    if not payment:
        return JSONResponse({
            "done": True,
            "html": f"<h2 style='color:red;'>❌ Payment not found</h2>"
                    f"<p><a href='{failed_url}'>Return to Telegram</a></p>"
        })

    # Case 2: Successful
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

    # Case 3: Failed/Expired
    if payment.status in ["failed", "expired"]:
        return JSONResponse({
            "done": True,
            "html": f"""
            <h2 style="color:red;">❌ Payment Failed</h2>
            <p>Transaction Reference: <b>{tx_ref}</b></p>
            <script>setTimeout(() => window.location.href="{failed_url}", 5000);</script>
            """
        })

    # Case 4: Still pending → keep polling
    return JSONResponse({
        "done": False,
        "html": f"""
        <h2 style="color:orange;">⏳ Payment Pending</h2>
        <p>Transaction Reference: <b>{tx_ref}</b></p>
        <p>⚠️ Your payment is still being processed by Flutterwave.</p>
        <div class="spinner" style="margin:20px auto;height:40px;width:40px;border:5px solid #ccc;border-top-color:#f39c12;border-radius:50%;animation:spin 1s linear infinite;"></div>
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

# ✅ Register all Flutterwave routes
app.include_router(router)
