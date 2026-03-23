# ====================================================
# app.py 
# =====================================================
# 1️⃣ Import & initialize secure logging first
# -------------------------------------------------
from logging_setup import logger, tg_error_handler  # must be first to protect secrets

import os
import re
import logging
import builtins
import traceback

# Force unbuffered output (Render needs this for real-time logs)
os.environ["PYTHONUNBUFFERED"] = "1"

# ------------------------------------------------
# 🧩 Step 1 — Secure Logging & Print Mask (Double-Lock)
# ------------------------------------------------
class SecretFilter(logging.Filter):
    TOKEN_PATTERN = re.compile(r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b")

    def filter(self, record):
        record.msg = self.TOKEN_PATTERN.sub("[SECRET]", str(record.msg))
        if record.args:
            record.args = tuple(self.TOKEN_PATTERN.sub("[SECRET]", str(a)) for a in record.args)
        return True


for name in logging.root.manager.loggerDict:
    logging.getLogger(name).addFilter(SecretFilter())


_real_print = print

def safe_print(*args, **kwargs):
    safe_args = [re.sub(r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b", "[SECRET]", str(a)) for a in args]
    _real_print(*safe_args, **kwargs)


builtins.print = safe_print

# ------------------------------------------------

from fastapi import FastAPI, Query, Request, HTTPException, Depends, APIRouter, Form
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Dict, Any, Optional
from telegram import Update, Bot
from datetime import datetime, timezone
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

# Local imports
from handlers import core, payments, free, admin, playtrivia, battle, jambpractice
from db import init_game_state, get_async_session, get_session
from models import Payment, GameState, PrizeWinner
from helpers import get_or_create_user, add_tries
from utils.signer import generate_signed_token, verify_signed_token
from webhook import router as webhook_router
from services.airtime_service import handle_claim_airtime_button, handle_airtime_claim_phone
from utils.conversation_states import AIRTIME_PHONE
from handlers.support import support_conv, admin_reply
from handlers.challenge import register_handlers as register_challenge_handlers
from tasks import start_background_tasks, stop_background_tasks

# ✅ Import Flutterwave-related functions/constants
from services.payments import (
    FLW_BASE_URL,
    FLW_SECRET_KEY,
    calculate_tries,
    verify_payment,
    resolve_payment_status,
    validate_flutterwave_webhook,
)

logging.getLogger("httpx").setLevel(logging.WARNING)

router = APIRouter()

# ------------------------------------
# Simple anti-spam for webhook calls
# ------------------------------------
_rate_limit_cache = {}
RATE_LIMIT_SECONDS = 10

def is_rate_limited(key: str, seconds: int = 30) -> bool:
    import time
    now = time.time()
    last_call = _rate_limit_cache.get(key)
    if last_call and now - last_call < seconds:
        return True
    _rate_limit_cache[key] = now
    return False


# ------------------------------------------------
# 🔒 Secure Logging Filter (hide Telegram bot token)
# -------------------------------------------------
class TelegramTokenFilter(logging.Filter):
    TOKEN_PATTERN = re.compile(r"(bot[0-9]+:[A-Za-z0-9_-]+)")

    def filter(self, record):
        record.msg = self.TOKEN_PATTERN.sub("bot<REDACTED>", str(record.msg))
        if record.args:
            record.args = tuple(self.TOKEN_PATTERN.sub("bot<REDACTED>", str(a)) for a in record.args)
        return True


for name in logging.root.manager.loggerDict:
    logging.getLogger(name).addFilter(TelegramTokenFilter())


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
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN is not set. Please define it in your environment variables.")

if not WEBHOOK_SECRET:
    raise ValueError("❌ WEBHOOK_SECRET is not set. Please define it in your environment variables.")

BOT_USERNAME = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")

# -------------------------------------------------
# Initialize FastAPI
# -------------------------------------------------
app = FastAPI()
app.include_router(webhook_router)

application: Application = None  # Telegram Application (global)
BOT_READY: bool = False          # prevent early webhook processing


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
    global application, BOT_READY

    BOT_READY = False

    try:
        logger.info("🚀 Starting up NaijaPrizeGate...")

        # -------------------------------------------------
        # Ensure required DB rows exist
        # -------------------------------------------------
        await init_game_state()
        await ensure_game_state_exists()

        # -------------------------------------------------
        # Build Telegram Application
        # -------------------------------------------------
        application = Application.builder().token(BOT_TOKEN).build()

        # -------------------------------------------------
        # High Priority Handlers FIRST
        # -------------------------------------------------
        application.add_handler(
            CallbackQueryHandler(
                handle_claim_airtime_button,
                pattern=r"^claim_airtime:"
            ),
            group=-10,
        )

        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                handle_airtime_claim_phone,
            ),
            group=-9,
        )

        application.add_handler(support_conv, group=-8)

        # Admin reply command
        application.add_handler(CommandHandler("reply", admin_reply), group=-7)

        # -------------------------------------------------
        # Register All Other Handlers
        # -------------------------------------------------
        core.register_handlers(application)
        playtrivia.register_handlers(application)
        jambpractice.register_handlers(application)
        battle.register_handlers(application)
        register_challenge_handlers(application)
        free.register_handlers(application)
        payments.register_handlers(application)
        admin.register_handlers(application)

        # -------------------------------------------------
        # Global Error Handler
        # -------------------------------------------------
        application.add_error_handler(tg_error_handler)

        # -------------------------------------------------
        # Initialize Application
        # -------------------------------------------------
        await application.initialize()

        # -------------------------------------------------
        # Webhook Setup
        # -------------------------------------------------
        base_url = os.getenv("BASE_URL")
        if not base_url:
            raise ValueError("BASE_URL is not set")

        webhook_url = f"{base_url}/telegram/webhook/{WEBHOOK_SECRET}"

        await application.bot.set_webhook(webhook_url)
        logger.info("✅ Webhook set to %s", webhook_url)

        # -------------------------------------------------
        # Start Application
        # -------------------------------------------------
        await application.start()
        logger.info("🚀 Telegram bot via Webhook is LIVE")

        BOT_READY = True
        logger.info("✅ BOT_READY=True (safe to process updates)")

        # -------------------------------------------------
        # Background Tasks
        # -------------------------------------------------
        await start_background_tasks()
        logger.info("✅ Background tasks started")

    except Exception:
        BOT_READY = False

        clean_trace = re.sub(
            r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b",
            "[SECRET]",
            traceback.format_exc()
        )

        logger.error("❌ Unhandled exception during startup:\n%s", clean_trace)


# -------------------------------------------------
# Shutdown event
# -------------------------------------------------
@app.on_event("shutdown")
async def on_shutdown():
    global application, BOT_READY

    BOT_READY = False
    logger.info("🔻 BOT_READY=False (shutting down)")

    # Stop background tasks first
    try:
        await stop_background_tasks()
    except Exception:
        clean_trace = re.sub(
            r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b",
            "[SECRET]",
            traceback.format_exc()
        )
        logger.warning("⚠️ Error stopping background tasks:\n%s", clean_trace)

    # Then stop Telegram app
    if not application:
        return

    try:
        await application.stop()
    except RuntimeError as e:
        logger.info("ℹ️ application.stop() skipped: %s", e)
    except Exception:
        clean_trace = re.sub(
            r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b",
            "[SECRET]",
            traceback.format_exc()
        )
        logger.warning("⚠️ Unexpected error in application.stop():\n%s", clean_trace)

    try:
        await application.shutdown()
        logger.info("🛑 Telegram bot shutdown complete.")
    except Exception:
        clean_trace = re.sub(
            r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b",
            "[SECRET]",
            traceback.format_exc()
        )
        logger.warning("⚠️ Error in application.shutdown():\n%s", clean_trace)

# -------------------------------------------------
# Telegram webhook endpoint
# -------------------------------------------------
@app.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # ✅ NEW: Prevent race condition (Telegram hitting webhook before startup finishes)
    if application is None or not BOT_READY:
        # Return 200 so Telegram doesn't keep retrying aggressively during startup
        return {"ok": True, "status": "starting"}

    payload = await request.json()

    try:
        update = Update.de_json(payload, application.bot)
        await application.process_update(update)
    except Exception:
        clean_trace = re.sub(
            r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b", "[SECRET]", traceback.format_exc()
        )
        logger.error(f"❌ Error while processing update:\n{clean_trace}")

    return {"ok": True}
    

# -----------------------
# 🔁 Helper – Prevent double crediting
# -----------------------
async def payment_already_processed(session: AsyncSession, tx_ref: str) -> bool:
    result = await session.execute(select(Payment).where(Payment.tx_ref == tx_ref))
    p = result.scalar_one_or_none()
    return bool(p and p.status == "successful")

# -----------------------
# Helpers – Payment Idempotency / JAMB Credits
# -----------------------
async def get_jamb_payment_record(session: AsyncSession, payment_reference: str) -> dict | None:
    result = await session.execute(
        text("""
            select
                user_id,
                amount_paid,
                question_credits_added,
                payment_status
            from jamb_payments
            where payment_reference = :payment_reference
            limit 1
        """),
        {"payment_reference": payment_reference},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def jamb_payment_already_processed(session: AsyncSession, payment_reference: str) -> bool:
    result = await session.execute(
        text("""
            select 1
            from jamb_payments
            where payment_reference = :payment_reference
              and lower(payment_status) = 'successful'
            limit 1
        """),
        {"payment_reference": payment_reference},
    )
    return result.first() is not None


async def credit_jamb_question_credits(
    session: AsyncSession,
    user_id: int,
    payment_reference: str,
    question_credits_added: int,
) -> bool:
    """
    Credits JAMB question credits and marks jamb_payments row successful.
    Returns True if the jamb_payments row was found and updated.
    """
    await session.execute(
        text("""
            insert into jamb_user_access (user_id)
            values (:user_id)
            on conflict (user_id) do nothing
        """),
        {"user_id": user_id},
    )

    await session.execute(
        text("""
            update jamb_user_access
            set
                paid_question_credits = paid_question_credits + :question_credits_added,
                updated_at = now()
            where user_id = :user_id
        """),
        {
            "user_id": user_id,
            "question_credits_added": question_credits_added,
        },
    )

    result = await session.execute(
        text("""
            update jamb_payments
            set
                payment_status = 'successful',
                updated_at = now()
            where payment_reference = :payment_reference
              and lower(payment_status) <> 'successful'
            returning payment_reference
        """),
        {"payment_reference": payment_reference},
    )

    return result.first() is not None

# ------------------------------------------------------
# Webhook: called by Flutterwave after payment
# ------------------------------------------------------
@router.post("/flw/webhook")
async def flutterwave_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    raw_body = await request.body()
    body_str = raw_body.decode("utf-8", errors="ignore")

    if not validate_flutterwave_webhook(
        {k.lower(): v for k, v in request.headers.items()},
        body_str,
    ):
        raise HTTPException(status_code=403)

    payload = await request.json()
    data = payload.get("data") or {}

    event = (payload.get("event") or "").lower().strip()
    status = (data.get("status") or "").lower().strip()
    tx_ref = str(data.get("tx_ref") or "").strip()

    # Only process successful completed charges
    if event != "charge.completed" or status != "successful" or not tx_ref:
        return JSONResponse({"status": "ignored"})

    # ------------------------------------------------------
    # JAMB PAYMENT BRANCH (STRICT)
    # ------------------------------------------------------
    if tx_ref.startswith("JAMB-"):
        jamb_payment = await get_jamb_payment_record(session, tx_ref)

        # STRICT MODE: DB row must already exist
        if not jamb_payment:
            logger.error("❌ No jamb_payments row found for tx_ref=%s", tx_ref)
            return JSONResponse({"status": "ignored"})

        payment_status = str(jamb_payment.get("payment_status") or "").lower().strip()
        if payment_status == "successful":
            return JSONResponse({"status": "duplicate"})

        tg_id = int(jamb_payment["user_id"])
        question_credits = int(jamb_payment["question_credits_added"] or 0)

        if question_credits <= 0:
            logger.error("❌ Invalid JAMB credit amount in DB for tx_ref=%s", tx_ref)
            return JSONResponse({"status": "ignored"})

        if await jamb_payment_already_processed(session, tx_ref):
            return JSONResponse({"status": "duplicate"})

        credited = await credit_jamb_question_credits(
            session=session,
            user_id=tg_id,
            payment_reference=tx_ref,
            question_credits_added=question_credits,
        )

        if not credited:
            logger.error("❌ Failed to update jamb_payments row for tx_ref=%s", tx_ref)
            await session.rollback()
            return JSONResponse({"status": "error", "message": "jamb payment update failed"})

        await session.commit()

        try:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                tg_id,
                f"🎉 *JAMB Payment Successful!*\n\n"
                f"You received *{question_credits} JAMB question credits* 📚\n\n"
                "You can now continue your JAMB Practice.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Telegram JAMB notify failed: %s", e)

        return JSONResponse({"status": "success"})

    # ------------------------------------------------------
    # TRIVIA PAYMENT BRANCH
    # ------------------------------------------------------
    if not tx_ref.startswith("TRIVIA-"):
        return JSONResponse({"status": "ignored"})

    payment = await session.scalar(
        select(Payment).where(Payment.tx_ref == tx_ref)
    )

    # Idempotency: already credited
    if payment and payment.status == "successful" and payment.credited_tries > 0:
        return JSONResponse({"status": "duplicate"})

    meta = data.get("meta") or {}
    tg_id_raw = meta.get("tg_id")

    if not tg_id_raw and payment:
        tg_id_raw = payment.tg_id

    if not tg_id_raw:
        logger.error("❌ Missing tg_id for tx_ref=%s", tx_ref)
        return JSONResponse({"status": "ignored"})

    tg_id = int(tg_id_raw)
    username = (
        (meta.get("username"))
        or (payment.username if payment else None)
        or "Unknown"
    )[:64]
    amount = int(data.get("amount") or 0)
    flw_tx_id = str(data.get("id") or "")

    payment = payment or Payment(tx_ref=tx_ref)
    payment.status = "successful"
    payment.amount = amount
    payment.flw_tx_id = flw_tx_id
    payment.tg_id = tg_id
    payment.username = username
    payment.credited_tries = 0

    session.add(payment)
    await session.flush()

    tries = calculate_tries(amount)
    user = await get_or_create_user(session, tg_id, username)
    await add_tries(session, user, tries, paid=True)

    payment.credited_tries = tries
    await session.commit()

    try:
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(
            tg_id,
            f"🎉 *Payment Successful!*\n\nYou received *{tries}* attempts 🎁",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning("Telegram notify failed: %s", e)

    return JSONResponse({"status": "success"})

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
    User-friendly redirect page. Resolves payment status in DB (or via verify_payment),
    then shows a success/fail HTML page and auto-redirects the user to Telegram.
    """
    async def _resolve_payment(txref: str):
        try:
            q = await session.execute(select(Payment).where(Payment.tx_ref == txref))
            return q.scalar_one_or_none()
        except Exception as e:
            logger.exception(f"❌ DB error resolving payment {txref}: {e}")
            return None

    try:
        payment = await _resolve_payment(tx_ref)

        # Attempt verification if payment not found or still pending
        if (not payment or payment.status in (None, "pending")) and transaction_id:
            try:
                
                fw_resp = await verify_payment(tx_ref, session)
                fw_data = fw_resp.get("data") or {}
                # ✅ Normalize status before comparing or saving
                raw_status = (fw_data.get("status") or "").lower().strip()
                if raw_status == "success":
                    raw_status = "successful"

                if raw_status == "successful":

                    credited = calculate_tries(int(fw_data.get("amount", 0)))
                    meta = fw_data.get("meta") or {}
                    raw_tg = meta.get("tg_id")
                    tg = None
                    try:
                        tg = int(raw_tg) if raw_tg else None
                    except Exception:
                        tg = None
                    username = meta.get("username") or "Unknown"
                    
                    # Upsert payment
                    existing = await _resolve_payment(tx_ref)

                    # ✅ Normalize status before saving
                    raw_status = (fw_data.get("status") or "").lower().strip()
                    if raw_status == "success":
                        raw_status = "successful"
                    elif raw_status == "completed":
                        raw_status = "successful"
                    elif raw_status == "canceled":
                        raw_status = "expired"

                    # Then create or update payment
                    if not existing:
                        newp = Payment(
                            tx_ref=tx_ref,
                            status=raw_status,  # ✅ use normalized version here
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

        # URLs for redirect
        success_url = f"https://t.me/{BOT_USERNAME}?start=payment_success_{tx_ref}"
        failed_url = f"https://t.me/{BOT_USERNAME}?start=payment_failed_{tx_ref}"

        # Render final page based on payment status
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

        # Fallback: still verifying
        html_content = f"""
        <html><head><meta charset="utf-8"><title>Verifying Payment</title></head>
        <body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
          <h2>⏳ Verifying your payment...</h2>
          <div style="margin:20px auto;height:40px;width:40px;border:5px solid #ccc;border-top-color:#4CAF50;border-radius:50%;animation:spin 1s linear infinite;"></div>
          <p>Please wait — we are checking the payment status. This page will auto-refresh.</p>
          <script>
            setTimeout(() => location.reload(), 4000);
          </script>
          <style>
            @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
          </style>
        </body></html>
        """
        return HTMLResponse(content=html_content, status_code=200)

    except Exception as e:
        # Catch-all to prevent 500
        logger.exception(f"❌ Unexpected error in /flw/redirect for {tx_ref}: {e}")
        return HTMLResponse(f"""
            <html><body style="font-family: Arial,sans-serif; text-align:center;">
            <h2 style="color:red;">❌ Payment processing error</h2>
            <p>Transaction Reference: <b>{tx_ref}</b></p>
            <p>Something went wrong while processing your payment.</p>
            <p><a href="https://t.me/{BOT_USERNAME}">Return to Telegram</a></p>
            </body></html>
        """, status_code=200)


# ------------------------------------------------------
# Redirect status polling with countdown + verify fallback
# ------------------------------------------------------
@router.get("/flw/redirect/status")
async def flutterwave_redirect_status(
    tx_ref: str,
    session: AsyncSession = Depends(get_session),
):

    success_url = f"https://t.me/{BOT_USERNAME}?start=payment_success_{tx_ref}"
    failed_url = f"https://t.me/{BOT_USERNAME}?start=payment_failed_{tx_ref}"

    try:
        payment = await resolve_payment_status(tx_ref, session)

        # Attempt direct verify if still pending
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

        # Case 3: Failed / Expired
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

    except Exception as e:
        # Catch-all to prevent 500
        logger.exception(f"❌ Unexpected error in /flw/redirect/status for {tx_ref}: {e}")
        return JSONResponse({
            "done": True,
            "html": f"""
            <h2 style="color:red;">❌ Payment processing error</h2>
            <p>Transaction Reference: <b>{tx_ref}</b></p>
            <p>Something went wrong while checking your payment.</p>
            <p><a href="https://t.me/{BOT_USERNAME}">Return to Telegram</a></p>
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

