# =====================================================
# app.py
# =====================================================
import os
import logging
import httpx
import sys

# Force unbuffered output (Render needs this for real-time logs)
os.environ["PYTHONUNBUFFERED"] = "1"

from fastapi import FastAPI, Query, Request, HTTPException, Depends, APIRouter, Form
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
from models import Payment, User, GameState, PrizeWinner
from helpers import get_or_create_user, add_tries
from logging_setup import logger
from datetime import datetime, timezone
from utils.signer import generate_signed_token, verify_signed_token
from models.prize_winners import PrizeWinner


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
    """Handles real-time payment confirmation from Flutterwave."""
    raw_body = await request.body()
    body_str = raw_body.decode("utf-8")
    logger.info("⚡ Flutterwave webhook triggered!")

    # ✅ Allow test webhook calls (Flutterwave dashboard)
    signature = request.headers.get("verif-hash")
    if not signature:
        logger.info("🧪 Test webhook received (no signature) → returning 200 OK")
        return {"status": "ok", "message": "Test webhook received"}

    # ✅ Validate the signature
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

    # ✅ Extract meta (Flutterwave sometimes nests it differently)
    meta = (
        data.get("meta")
        or body.get("meta")
        or data.get("meta_data")
        or body.get("meta_data")
        or {}
    )

    logger.info(f"🌍 Webhook payload: {body}")
    logger.info(f"📦 tx_ref={tx_ref}, status={status}, amount={amount}, meta_keys={list(meta.keys())}")

    # Extract tg_id and username
    raw_tg_id = meta.get("tg_id") or meta.get("tgId") or meta.get("customer")
    username = meta.get("username") or meta.get("user") or "Unknown"

    tg_id = None
    if raw_tg_id:
        try:
            tg_id = int(raw_tg_id)
        except ValueError:
            tg_id = None

    # Fallback: verify manually if tg_id missing
    if tg_id is None:
        logger.warning(f"⚠️ Missing tg_id for tx_ref={tx_ref}, re-verifying payment...")
        try:
            verified = await verify_payment(tx_ref, session)
            meta2 = verified.get("data", {}).get("meta") or {}
            tg_id = meta2.get("tg_id")
            username = meta2.get("username", username)
            if tg_id:
                logger.info(f"✅ Recovered tg_id={tg_id} from verify_payment()")
        except Exception as e:
            logger.exception(f"❌ Could not recover tg_id for {tx_ref}: {e}")

    # Verify again with Flutterwave to confirm status
    verified = None
    try:
        verified = await verify_payment(tx_ref, session, credit=False)
    except Exception as e:
        logger.warning(f"⚠️ Payment verification failed for {tx_ref}: {e}")

    # Lookup payment record
    result = await session.execute(select(Payment).where(Payment.tx_ref == tx_ref))
    payment = result.scalar_one_or_none()

    credited_tries = calculate_tries(int(amount or 0))

    # ✅ Process successful payment
    if status == "successful":
        user = None
        if tg_id:
            user = await get_or_create_user(session, tg_id=tg_id, username=username)

        if not payment:
            payment = Payment(
                tx_ref=str(tx_ref),
                status="successful",
                credited_tries=credited_tries,
                flw_tx_id=str(data.get("id")) if data.get("id") else None,
                user_id=user.id if user else None,
                amount=amount,
                tg_id=tg_id,
                username=username,
            )
            session.add(payment)
        else:
            payment.status = "successful"
            payment.credited_tries = credited_tries
            payment.flw_tx_id = str(data.get("id")) if data.get("id") else payment.flw_tx_id
            payment.username = username
            if user and not payment.user_id:
                payment.user_id = user.id

        # 💰 Credit user tries
        if user:
            await add_tries(session, user, credited_tries, paid=True)
            logger.info(f"🎁 Credited {credited_tries} tries to user {user.tg_id} ({username})")
        else:
            logger.warning(f"⚠️ No linked user found for tx_ref={tx_ref}")

        await session.commit()

        # 🎉 Send Telegram confirmation
        if tg_id:
            try:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                bot = Bot(token=BOT_TOKEN)
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
                    [InlineKeyboardButton("💳 Buy Tries", callback_data="buy")],
                    [InlineKeyboardButton("🎁 Free Tries", callback_data="free")],
                    [InlineKeyboardButton("📊 Available Tries", callback_data="show_tries")]
                ])
                await bot.send_message(
                    chat_id=tg_id,
                    text=(
                        f"✅ Payment confirmed!\n\n"
                        f"You’ve been credited with *{credited_tries}* spin"
                        f"{'s' if credited_tries > 1 else ''}. 🎉\n\n"
                        f"Good luck and have fun 🍀"
                    ),
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"⚠️ Failed to send Telegram DM to {tg_id}: {e}")

        return {"status": "success", "tx_ref": tx_ref}

    # ❌ Handle failed payments
    else:
        if payment:
            payment.status = status or "failed"
            await session.commit()
        logger.warning(f"❌ Payment {tx_ref} failed with status={status}")
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
