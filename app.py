# =====================================================
# app.py
# =====================================================
import os
import re
import logging
import httpx
import sys
import hmac
import hashlib
import json
import traceback
import builtins

# Force unbuffered output (Render needs this for real-time logs)
os.environ["PYTHONUNBUFFERED"] = "1"

# ------------------------------------------------
# 🧩 Step 1 — Secure Logging & Print Mask (Double-Lock)
# ------------------------------------------------
# Mask any Telegram bot tokens or similar secrets if they ever appear in logs or prints
class SecretFilter(logging.Filter):
    TOKEN_PATTERN = re.compile(r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b")

    def filter(self, record):
        record.msg = self.TOKEN_PATTERN.sub("[SECRET]", str(record.msg))
        if record.args:
            record.args = tuple(self.TOKEN_PATTERN.sub("[SECRET]", str(a)) for a in record.args)
        return True

# Apply this filter globally across all loggers
for name in logging.root.manager.loggerDict:
    logging.getLogger(name).addFilter(SecretFilter())

# Also secure print() to prevent secrets from leaking
_real_print = print
def safe_print(*args, **kwargs):
    safe_args = [re.sub(r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b", "[SECRET]", str(a)) for a in args]
    _real_print(*safe_args, **kwargs)
builtins.print = safe_print

# ------------------------------------------------

from fastapi import FastAPI, Query, Request, HTTPException, Depends, APIRouter, Form
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update
from typing import Dict, Any, Optional

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from datetime import datetime, timezone

# Local imports
from logger import tg_error_handler, logger
from handlers import core, payments, free, admin, tryluck
from tasks import start_background_tasks, stop_background_tasks
from db import init_game_state, get_async_session, get_session
from models import Payment, User, GameState, PrizeWinner
from helpers import get_or_create_user, add_tries
from logging_setup import logger
from utils.signer import generate_signed_token, verify_signed_token
from webhook import router as webhook_router
from bot_instance import bot

# ✅ Import Flutterwave-related functions/constants
from services.payments import (
    FLW_BASE_URL,
    FLW_SECRET_KEY,
    calculate_tries,
    verify_payment,
    validate_webhook,
)

# ------------------------------------------------
# 🔒 Secure Logging Filter (hide Telegram bot token)
# -------------------------------------------------
class TelegramTokenFilter(logging.Filter):
    TOKEN_PATTERN = re.compile(r"(bot[0-9]+:[A-Za-z0-9_-]+)")

    def filter(self, record):
        # Mask bot tokens in log messages
        record.msg = self.TOKEN_PATTERN.sub("bot<REDACTED>", str(record.msg))
        if record.args:
            record.args = tuple(self.TOKEN_PATTERN.sub("bot<REDACTED>", str(a)) for a in record.args)
        return True

# Apply this filter globally
for name in logging.root.manager.loggerDict:
    logging.getLogger(name).addFilter(TelegramTokenFilter())

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

router = APIRouter()


# -------------------------------------------------
# Ensure GameState row exists
# -------------------------------------------------
async def ensure_game_state_exists():
    async with get_async_session() as session:
        gs = await session.get(GameState, 1)
        if not gs:
            gs = GameState(id=1)
            session.add(gs)
            await session.commit()
            logger.info("✅ Default GameState(id=1) created")


# -------------------------------------------------
# Environment setup
# -------------------------------------------------
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN is not set. Please define it in your environment variables.")


# -------------------------------------------------
# Initialize FastAPI 
# -------------------------------------------------
app = FastAPI()
app.include_router(webhook_router)
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
    try:
        logger.info("🚀 Starting up NaijaPrizeGate...")

        # Ensure GameState & GlobalCounter rows exist
        await init_game_state()

        # Ensure GameState(id=1) exists explicitly
        await ensure_game_state_exists()

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

    except Exception as e:
        clean_trace = re.sub(r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b", "[SECRET]", traceback.format_exc())
        logger.error(f"Unhandled exception during startup:\n{clean_trace}")


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
        clean_trace = re.sub(r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b", "[SECRET]", traceback.format_exc())
        logger.warning(f"⚠️ Error while shutting down:\n{clean_trace}")


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
# ---- Flutterwave secure webhook + redirect (copy-and-paste) ----
# Place in app.py (or a routes module). Uses your existing AsyncSession dependency
# and models (Payment, User), plus helper functions like add_tries/get_or_create_user.
# If your AsyncSession dependency is called get_async_session, replace get_session below.


# -----------------------
# Helper: constant-time webhook validation
# -----------------------
def validate_webhook_signature(headers: dict, body_str: str) -> bool:
    """
    Compare Flutterwave verif-hash using constant-time comparison.
    Expects header 'verif-hash' and env var FLW_SECRET_HASH populated.
    """
    signature = headers.get("verif-hash") or headers.get("verif_hash") or ""
    if not signature or not FLW_SECRET_HASH:
        return False
    # Trim whitespace; use compare_digest for constant-time compare
    return hmac.compare_digest(signature.strip(), FLW_SECRET_HASH.strip())

# -----------------------
# Helper: verify payment with Flutterwave API (double-check)
# -----------------------
async def verify_payment(tx_ref: str) -> Dict[str, Any]:
    """
    Calls Flutterwave's API to verify a payment by tx_ref.
    Raises on network / verification errors.
    Returns the parsed JSON response from Flutterwave.
    """
    if not FLW_SECRET_KEY:
        raise RuntimeError("FLW_SECRET_KEY not configured")

    url = f"https://api.flutterwave.com/v3/transactions/verify_by_reference?tx_ref={tx_ref}"
    headers = {"Authorization": f"Bearer {FLW_SECRET_KEY}"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # data structure: {"status": "success", "message": "...", "data": {...}}
    tx = data.get("data")
    if not tx:
        raise ValueError("No transaction data in Flutterwave response")
    return data

# -----------------------
# Fallback calculate_tries if you don't have services.payments.calculate_tries
# (Prefer your canonical function; this is a safe default.)
# -----------------------
def _calculate_tries_from_amount(amount: int) -> int:
    if amount >= 5000:
        return 15
    if amount >= 2000:
        return 5
    if amount >= 500:
        return 1
    return 0

# -----------------------
# Prevent replay: quick DB check
# -----------------------
async def payment_already_processed(session: AsyncSession, tx_ref: str) -> bool:
    q = await session.execute(select(Payment).where(Payment.tx_ref == tx_ref))
    row = q.scalar_one_or_none()
    return bool(row and row.status == "successful")

# -----------------------
# ✅ SECURE FLUTTERWAVE WEBHOOK
# -----------------------

# -----------------------
# 🔁 Helper – Prevent double crediting
# -----------------------
async def payment_already_processed(session: AsyncSession, tx_ref: str) -> bool:
    result = await session.execute(select(Payment).where(Payment.tx_ref == tx_ref))
    p = result.scalar_one_or_none()
    return bool(p and p.status == "successful")


# -----------------------
# 🧩 Main Webhook Route
# -----------------------
@router.post("/flw/webhook")
async def flutterwave_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    """
    Secure Flutterwave webhook handler:
    ✅ Validates verif-hash header
    ✅ Double-verifies with Flutterwave API
    ✅ Prevents replay/double-credit
    ✅ Sanitizes user meta data
    """
    raw_body = await request.body()
    body_str = raw_body.decode("utf-8", errors="ignore")

    # Allow test webhooks from Flutterwave dashboard
    signature_header = request.headers.get("verif-hash")
    if not signature_header:
        logger.info("🧪 Test webhook (no verif-hash). Returning 200 OK.")
        return JSONResponse({"status": "ok", "message": "Test webhook (no signature)"})

    # 🔐 Step 1: Verify signature authenticity
    if not validate_webhook_signature({k.lower(): v for k, v in request.headers.items()}, body_str):
        logger.warning("🚫 Invalid Flutterwave webhook signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # Parse payload safely
    payload = await request.json()
    data = payload.get("data", {}) or {}
    tx_ref = data.get("tx_ref") or data.get("reference") or payload.get("tx_ref")
    status = data.get("status")
    amount = data.get("amount") or 0

    # 🛡️ Step 0: Rate-limit guard
    if tx_ref and is_rate_limited(tx_ref):
        logger.warning(f"🚫 Duplicate webhook within {RATE_LIMIT_SECONDS}s for tx_ref={tx_ref}")
        return JSONResponse({"status": "ignored", "reason": "too frequent"})


    if not tx_ref:
        logger.error("❌ Webhook missing tx_ref; ignoring.")
        raise HTTPException(status_code=400, detail="Missing tx_ref")

    # 🔁 Step 2: Prevent double-processing early
    if await payment_already_processed(session, tx_ref):
        logger.info(f"🔁 Duplicate webhook ignored for tx_ref={tx_ref}")
        return JSONResponse({"status": "duplicate", "tx_ref": tx_ref})

    # Extract & sanitize meta info
    meta = (
        data.get("meta")
        or payload.get("meta")
        or data.get("meta_data")
        or payload.get("meta_data")
        or {}
    ) or {}

    raw_tg_id = meta.get("tg_id") or meta.get("tgId") or meta.get("tgid") or meta.get("customer")
    username = meta.get("username") or meta.get("user") or "Unknown"
    try:
        username = str(username).replace("<", "").replace(">", "")[:64]
    except Exception:
        username = "Unknown"

    tg_id: Optional[int] = None
    if raw_tg_id is not None:
        try:
            tg_id = int(raw_tg_id)
        except Exception:
            logger.warning(f"⚠️ Could not parse tg_id for tx_ref={tx_ref}: {raw_tg_id}")

    # 🔎 Step 3: Verify with Flutterwave API (extra safety)
    fw_data = None
    try:
        fw_resp = await verify_payment(tx_ref)
        fw_data = fw_resp.get("data", {}) or {}
        if fw_data.get("status") != "successful":
            logger.warning(f"⚠️ Flutterwave reports non-success for {tx_ref}: {fw_data.get('status')}")
    except Exception as e:
        logger.exception(f"⚠️ Error verifying transaction {tx_ref} via Flutterwave: {e}")

    # Decide final status and trusted amount
    final_status = fw_data.get("status") if fw_data else status
    try:
        amount = int(float(fw_data.get("amount", amount))) if fw_data else int(float(amount))
    except Exception:
        amount = 0

    # 🔢 Step 4: Calculate tries
    try:
        calc_fn = calculate_tries  # use your main helper
    except NameError:
        calc_fn = _calculate_tries_from_amount
    credited_tries = calc_fn(int(amount or 0))

    # Fetch or create Payment record
    q = await session.execute(select(Payment).where(Payment.tx_ref == tx_ref))
    payment = q.scalar_one_or_none()

    if final_status == "successful":
        # Step 5: Record payment
        if not payment:
            payment = Payment(
                tx_ref=str(tx_ref),
                status="successful",
                credited_tries=credited_tries,
                flw_tx_id=str(fw_data.get("id")) if fw_data else None,
                user_id=None,
                amount=amount,
                tg_id=tg_id,
                username=username,
            )
            session.add(payment)
            await session.flush()
        else:
            payment.status = "successful"
            payment.credited_tries = credited_tries
            payment.flw_tx_id = str(fw_data.get("id")) if fw_data else payment.flw_tx_id
            payment.username = username or payment.username

        # Step 6: Credit user (if Telegram ID known)
        user = None
        if tg_id:
            user = await get_or_create_user(session, tg_id=tg_id, username=username)

        if user:
            await add_tries(session, user, credited_tries, paid=True)
            logger.info(f"🎁 Credited {credited_tries} tries to user {user.tg_id} ({username}) for tx_ref={tx_ref}")
        else:
            logger.warning(f"⚠️ No user linked for tx_ref={tx_ref}; recorded payment only.")

        await session.commit()

        # Step 7: Notify user via Telegram
        if tg_id and BOT_TOKEN:
            try:
                bot = Bot(token=BOT_TOKEN)
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
                    [InlineKeyboardButton("💳 Buy Tries", callback_data="buy")],
                    [InlineKeyboardButton("🎁 Free Tries", callback_data="free")],
                    [InlineKeyboardButton("📊 Available Tries", callback_data="show_tries")]
                ])
                text = (
                    f"✅ *Payment Confirmed!*\n\n"
                    f"You've been credited with *{credited_tries}* spin"
                    f"{'s' if credited_tries > 1 else ''} 🎉\n\n"
                    "Good luck and have fun 🍀"
                )
                await bot.send_message(chat_id=tg_id, text=text, parse_mode="Markdown", reply_markup=keyboard)
            except Exception as e:
                logger.exception(f"⚠️ Failed to notify user tg_id={tg_id} → {e}")

        return JSONResponse({"status": "success", "tx_ref": tx_ref})

    # ❌ Step 8: Failed or pending payments
    if payment:
        payment.status = final_status or (status or "failed")
        await session.commit()

    logger.warning(f"❌ Payment {tx_ref} not successful (status={final_status})")
    return JSONResponse({"status": "failed", "tx_ref": tx_ref, "status_value": final_status})

# -----------------------
# Redirect endpoint for user-friendly browser redirect after checkout
# -----------------------
@router.get("/flw/redirect", response_class=HTMLResponse)
async def flutterwave_redirect(
    tx_ref: str = Query(...),
    status: Optional[str] = None,
    transaction_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """
    User-friendly redirect page. It resolves payment status in DB (or via verify_payment),
    then shows a success/fail HTML page and auto-redirects the user to Telegram.
    """
    # Helper: determine payment state
    async def _resolve_payment(txref: str):
        q = await session.execute(select(Payment).where(Payment.tx_ref == txref))
        p = q.scalar_one_or_none()
        return p

    payment = await _resolve_payment(tx_ref)

    # If not present or still pending and transaction_id provided, try to verify now (credit=True)
    if (not payment or payment.status in (None, "pending")) and transaction_id:
        try:
            # double-verify remotely and credit if needed by calling verify_payment and then the webhook path
            fw_resp = await verify_payment(tx_ref)
            fw_data = fw_resp.get("data", {}) or {}
            if fw_data.get("status") == "successful":
                # call webhook-like logic to credit (we can call the verify flow directly here)
                # For brevity, call webhook handler logic by making an internal call to verify flow:
                # Note: to avoid circular imports, we'll emulate a minimal commit here:
                credited = _calculate_tries_from_amount(int(fw_data.get("amount", 0)))
                # Upsert a payment row & credit user if meta present
                meta = fw_data.get("meta") or {}
                raw_tg = meta.get("tg_id")
                tg = None
                try:
                    tg = int(raw_tg) if raw_tg else None
                except Exception:
                    tg = None
                username = (meta.get("username") or "Unknown")
                # Reuse webhook logic by creating/updating Payment and crediting user
                q2 = await session.execute(select(Payment).where(Payment.tx_ref == tx_ref))
                existing = q2.scalar_one_or_none()
                if not existing:
                    newp = Payment(
                        tx_ref=tx_ref,
                        status="successful",
                        credited_tries=credited,
                        flw_tx_id=str(fw_data.get("id")) if fw_data.get("id") else None,
                        user_id=None,
                        amount=int(fw_data.get("amount", 0)),
                        tg_id=tg,
                        username=username,
                    )
                    session.add(newp)
                    await session.flush()
                    if tg:
                        user = await get_or_create_user(session, tg_id=tg, username=username)
                        await add_tries(session, user, credited, paid=True)
                    await session.commit()
                payment = await _resolve_payment(tx_ref)
        except Exception as e:
            logger.exception(f"❌ Redirect: failed to verify tx_ref={tx_ref}: {e}")

    # Render final pages
    success_url = f"https://t.me/{BOT_USERNAME}?start=payment_success_{tx_ref}"
    failed_url = f"https://t.me/{BOT_USERNAME}?start=payment_failed_{tx_ref}"

    if payment and payment.status == "successful":
        credited_text = f"{payment.credited_tries} spin{'s' if payment.credited_tries > 1 else ''}"
        return HTMLResponse(f"""
            <html><body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
            <h2 style="color:green;">✅ Payment Successful</h2>
            <p>Transaction Reference: <b>{tx_ref}</b></p>
            <p>🎁 You’ve been credited with <b>{credited_text}</b>! 🎉</p>
            <p>This tab will redirect to Telegram in 5 seconds...</p>
            <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
            </body></html>
        """, status_code=200)

    if payment and payment.status in ("failed", "expired"):
        return HTMLResponse(f"""
            <html><body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
            <h2 style="color:red;">❌ Payment Failed</h2>
            <p>Transaction Reference: <b>{tx_ref}</b></p>
            <p>This tab will redirect to Telegram in 5 seconds...</p>
            <script>setTimeout(() => window.location.href="{failed_url}", 5000);</script>
            </body></html>
        """, status_code=200)

    # Otherwise show a "verifying" spinner/poller page that calls the redirect/status endpoint (not implemented here)
    html_content = f"""
    <html><head><meta charset="utf-8"><title>Verifying Payment</title></head>
    <body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
      <h2>⏳ Verifying your payment...</h2>
      <div style="margin:20px auto;height:40px;width:40px;border:5px solid #ccc;border-top-color:#4CAF50;border-radius:50%;animation:spin 1s linear infinite;"></div>
      <p>Please wait — we are checking the payment status. This page will auto-refresh.</p>
      <script>
        setTimeout(() => location.reload(), 4000);
        // simple keyframes for spinner (inline)
      </script>
      <style>
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
      </style>
    </body></html>
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

# --------------------------------------------------------------
# 🏆 WINNER FORM — HTML + API (Web-Based Flow)
# ---------------------------------------------------------------
# ---------------------------------------------------------------
# 📝 WINNER FORM PAGE
# ---------------------------------------------------------------
@app.get("/winner-form", response_class=HTMLResponse)
async def winner_form_page(token: str):
    """
    Secure winner form. Accepts only `token` query param (signed).
    Token contains tgid, choice and expiry.
    """
    ok, payload, err = verify_signed_token(token)
    if not ok:
        # show a friendly error page
        return HTMLResponse(f"""
            <html><body style="font-family: Arial; text-align:center; padding:40px;">
            <h2 style="color:red;">⚠️ Invalid or expired link</h2>
            <p>{err}</p>
            <p>If you believe this is an error, contact the admin.</p>
            </body></html>
        """, status_code=403)

    tgid = payload["tgid"]
    choice = payload["choice"]

    # Render the same form but *do not* expose the token contents in a way that can be attacked.
    # We keep the token (hidden) so the form submission can re-verify it server-side.
    return HTMLResponse(f"""
    <html>
        <head>
            <title>Prize Claim Form</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 420px; margin: 50px auto; padding: 25px; border: 1px solid #ddd; border-radius: 12px; background-color: #fafafa; }}
                h2 {{ text-align: center; color: #333; }}
                label {{ font-weight: bold; display: block; margin-top: 12px; }}
                input, textarea {{ width: 100%; padding: 10px; margin-top: 6px; border-radius: 6px; border: 1px solid #ccc; font-size: 1em; }}
                button {{ width: 100%; margin-top: 20px; padding: 12px; background-color: #28a745; color: white; border: none; border-radius: 6px; font-size: 1em; cursor: pointer; }}
                button:hover {{ background-color: #218838; }}
            </style>
        </head>
        <body>
            <h2>🎉 Congratulations!</h2>
            <p>You’ve won a <b>{choice}</b>!</p>
            <p>Please provide your delivery details below 👇</p>

            <form action="/api/save_winner" method="post">
                <input type="hidden" name="token" value="{token}">
                <label>Full Name:</label>
                <input type="text" name="full_name" placeholder="Enter your full name" required>

                <label>Phone Number:</label>
                <input type="text" name="phone" placeholder="+234..." required>

                <label>Delivery Address:</label>
                <textarea name="address" rows="3" placeholder="Enter full delivery address" required></textarea>

                <button type="submit">Submit Details ✅</button>
            </form>
        </body>
    </html>
    """)



# ---------------------------------------------------------------
# 💾 SAVE WINNER FORM SUBMISSION (FINAL + CORRECT)
# ---------------------------------------------------------------
@app.post("/api/save_winner", response_class=HTMLResponse)
async def save_winner(
    token: str = Form(...),
    full_name: str = Form(...),
    phone: str = Form(...),
    address: str = Form(...),
):
    """
    Save a winner submission — requires a valid token. Token is verified & used to find/ensure the user.
    """
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))
    BOT_TOKEN = os.getenv("BOT_TOKEN")

    ok, payload, err = verify_signed_token(token)
    if not ok:
        return HTMLResponse(f"""
            <html><body style="font-family: Arial; text-align:center; padding:40px;">
            <h2 style="color:red;">⚠️ Invalid or expired submission</h2>
            <p>{err}</p>
            <p>If you believe this is an error, contact the admin.</p>
            </body></html>
        """, status_code=403)

    tgid = payload["tgid"]
    choice = payload["choice"]

    # Now create PrizeWinner record (same as before)
    async with get_async_session() as session:
        user = await get_or_create_user(session, tg_id=tgid)
        pw = PrizeWinner(
            user_id=user.id,
            tg_id=tgid,
            choice=choice,
            delivery_status="Pending",
            submitted_at=datetime.now(timezone.utc),
            pending_at=datetime.now(timezone.utc),
            delivery_data={
                "full_name": full_name,
                "phone": phone,
                "address": address
            }
        )
        session.add(pw)
        await session.commit()
        await session.refresh(pw)

    # Notify admin
    try:
        if ADMIN_USER_ID and BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            msg = (
                f"📢 <b>NEW WINNER ALERT!</b>\n\n"
                f"👤 <b>Name:</b> {full_name}\n"
                f"📱 <b>Phone:</b> {phone}\n"
                f"🏠 <b>Address:</b> {address}\n"
                f"🎁 <b>Prize:</b> {choice}\n"
                f"🆔 <b>Telegram ID:</b> {tgid}\n"
                f"🆔 <b>Record ID:</b> <code>{pw.id}</code>\n"
                f"🕒 <i>Submitted via Winner Form</i>"
            )
            await bot.send_message(chat_id=ADMIN_USER_ID, text=msg, parse_mode="HTML")
    except Exception:
        logger.exception("❌ Failed to notify admin", exc_info=True)

    # Success page
    return HTMLResponse(
        """
        <html>
            <head>
                <title>Form Submitted</title>
                <style>body { font-family: Arial; text-align: center; margin-top: 100px; color: #333; } h2 { color: green; }</style>
            </head>
            <body>
                <h2>✅ Thank You!</h2>
                <p>Your delivery details have been received successfully.</p>
                <p>Our team will contact you soon to arrange your prize delivery 🚚✨</p>
            </body>
        </html>
        """,
        status_code=200
    )


# ✅ Register all Flutterwave routes
app.include_router(router)

