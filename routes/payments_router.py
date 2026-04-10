# ======================================================
# routes/payments_router.py
# =====================================================
import os
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from db import get_session
from services.flutterwave_client import (
    normalize_flw_status,
    validate_flutterwave_webhook,
    verify_payment,
)
from services.trivia_payments import finalize_trivia_payment, get_trivia_payment
from services.jamb_payments import finalize_jamb_payment, get_jamb_payment
from services.mockjamb_payments import finalize_mockjamb_payment, get_mockjamb_payment

logger = logging.getLogger("payments_router")
logger.setLevel(logging.INFO)

router = APIRouter()

BOT_USERNAME = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")
BOT_TOKEN = os.getenv("BOT_TOKEN")


def _product_type_from_tx_ref(tx_ref: str) -> str:
    tx_ref = (tx_ref or "").upper().strip()

    if tx_ref.startswith("JAMBMOCKSUBJECT-"):
        return "JAMBMOCKSUBJECT"
    if tx_ref.startswith("JAMB-"):
        return "JAMB"
    if tx_ref.startswith("MOCKJAMB-"):
        return "MOCKJAMB"
    if tx_ref.startswith("TRIVIA-"):
        return "TRIVIA"

    return "TRIVIA"


def _success_url(tx_ref: str, product_type: str, subject_code: str | None = None) -> str:
    product_type = (product_type or "").upper().strip()
    subject_code = (subject_code or "").strip().lower()

    if product_type == "JAMB":
        return f"https://t.me/{BOT_USERNAME}?start=payok_jamb_{tx_ref}"

    if product_type == "JAMBMOCKSUBJECT":
        if subject_code:
            return f"https://t.me/{BOT_USERNAME}?start=payok_jambmocksubject_{subject_code}_{tx_ref}"
        return f"https://t.me/{BOT_USERNAME}?start=payok_jambmocksubject_{tx_ref}"

    if product_type == "MOCKJAMB":
        return f"https://t.me/{BOT_USERNAME}?start=payok_mockjamb_{tx_ref}"

    return f"https://t.me/{BOT_USERNAME}?start=payok_trivia_{tx_ref}"


def _failed_url(tx_ref: str, product_type: str, subject_code: str | None = None) -> str:
    product_type = (product_type or "").upper().strip()
    subject_code = (subject_code or "").strip().lower()

    if product_type == "JAMB":
        return f"https://t.me/{BOT_USERNAME}?start=payfail_jamb_{tx_ref}"

    if product_type == "JAMBMOCKSUBJECT":
        if subject_code:
            return f"https://t.me/{BOT_USERNAME}?start=payfail_jambmocksubject_{subject_code}_{tx_ref}"
        return f"https://t.me/{BOT_USERNAME}?start=payfail_jambmocksubject_{tx_ref}"

    if product_type == "MOCKJAMB":
        return f"https://t.me/{BOT_USERNAME}?start=payfail_mockjamb_{tx_ref}"

    return f"https://t.me/{BOT_USERNAME}?start=payfail_trivia_{tx_ref}"


async def _send_payment_success_message(
    tg_id: int,
    product_type: str,
    amount_or_units: int,
) -> None:
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN missing; cannot send Telegram message")
        return

    try:
        bot = Bot(token=BOT_TOKEN)

        if product_type == "TRIVIA":
            text = (
                "🎉 *Payment Successful!*\n\n"
                f"You received *{amount_or_units}* attempt{'s' if amount_or_units != 1 else ''} 🎁\n\n"
                "You can now proceed to Play Trivia Questions."
            )
        elif product_type == "JAMB":
            text = (
                "🎉 *Payment Successful!*\n\n"
                f"You received *{amount_or_units}* JAMB question credit"
                f"{'s' if amount_or_units != 1 else ''} 📚\n\n"
                "You can now continue your JAMB Practice."
            )
        elif product_type == "JAMBMOCKSUBJECT":
            text = (
                "🎉 *Payment Successful!*\n\n"
                f"You received *{amount_or_units}* mock session"
                f"{'s' if amount_or_units != 1 else ''} 🎟\n\n"
                "You can now continue your Mock UTME \\(By Subject\\)."
            )
        elif product_type == "MOCKJAMB":
            text = (
                "🎉 *Payment Successful!*\n\n"
                "Your *Mock JAMB / UTME* access has been activated 📝\n\n"
                "You can now continue to your mock exam."
            )
        else:
            return

        await bot.send_message(
            chat_id=tg_id,
            text=text,
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.warning("Telegram success message failed for user %s: %s", tg_id, e)


async def _finalize_from_verified_data(
    session: AsyncSession,
    *,
    tx_ref: str,
    verified: dict,
) -> tuple[str, dict]:
    """
    Returns (product_type, info_dict)

    info_dict always contains at least:
      status, credited_now, display_amount
    """
    meta = verified.get("meta") or {}
    amount = int(verified.get("amount") or 0)
    flw_tx_id = str(verified.get("flw_tx_id") or "")

    raw_product_type = meta.get("product_type") or _product_type_from_tx_ref(tx_ref)
    product_type = str(raw_product_type).upper().strip()    
    
    if product_type == "JAMB":
        tg_id_raw = meta.get("tg_id")
        if not tg_id_raw:
            payment = await get_jamb_payment(session, tx_ref)
            tg_id_raw = payment.get("user_id") if payment else None

        if not tg_id_raw:
            return "JAMB", {
                "status": "error",
                "reason": "missing_tg_id",
                "credited_now": False,
            }

        did_credit, payment, credits = await finalize_jamb_payment(
            session,
            payment_reference=tx_ref,
            user_id=int(tg_id_raw),
            amount_paid=amount,
            question_credits_added=None,
        )

        return "JAMB", {
            "status": "successful" if payment else "error",
            "credited_now": did_credit,
            "credits": credits,
            "tg_id": int(tg_id_raw),
            "payment": payment,
            "display_amount": credits,
        }

    
    if product_type == "JAMBMOCKSUBJECT":
        tg_id_raw = meta.get("tg_id")
        mock_sessions_added_raw = meta.get("mock_sessions_added")

        if not tg_id_raw:
            payment = await get_jamb_payment(session, tx_ref)
            tg_id_raw = payment.get("user_id") if payment else None
            mock_sessions_added_raw = (
                mock_sessions_added_raw
                or (payment.get("mock_sessions_added") if payment else None)
            )

        if not tg_id_raw:
            return "JAMBMOCKSUBJECT", {
                "status": "error",
                "reason": "missing_tg_id",
                "credited_now": False,
            }

        did_credit, payment, credits, mock_sessions = await finalize_jamb_payment(
            session,
            payment_reference=tx_ref,
            user_id=int(tg_id_raw),
            amount_paid=amount,
            question_credits_added=0,
            mock_sessions_added=int(mock_sessions_added_raw or 0),
        )

        return "JAMBMOCKSUBJECT", {
            "status": "successful" if payment else "error",
            "credited_now": did_credit,
            "credits": credits,
            "mock_sessions": mock_sessions,
            "tg_id": int(tg_id_raw),
            "payment": payment,
            "display_amount": mock_sessions,
        }
    
    if product_type == "MOCKJAMB":
        tg_id_raw = meta.get("tg_id")
        if not tg_id_raw:
            payment = await get_mockjamb_payment(session, tx_ref)
            tg_id_raw = payment.get("user_id") if payment else None

        if not tg_id_raw:
            return "MOCKJAMB", {
                "status": "error",
                "reason": "missing_tg_id",
                "credited_now": False,
            }

        did_finalize, payment = await finalize_mockjamb_payment(
            session,
            payment_reference=tx_ref,
            user_id=int(tg_id_raw),
        )

        return "MOCKJAMB", {
            "status": "successful" if payment else "error",
            "credited_now": did_finalize,
            "tg_id": int(tg_id_raw),
            "payment": payment,
            "display_amount": amount,
        }

    if product_type == "TRIVIA":
        tg_id_raw = meta.get("tg_id")
        username = (meta.get("username") or "Unknown")[:64]

        if not tg_id_raw:
            existing = await get_trivia_payment(session, tx_ref)
            tg_id_raw = existing.tg_id if existing else None
            username = (existing.username if existing else username) or username

        if not tg_id_raw:
            return "TRIVIA", {
                "status": "error",
                "reason": "missing_tg_id",
                "credited_now": False,
            }

        did_credit, payment, tries = await finalize_trivia_payment(
            session,
            tx_ref=tx_ref,
            amount=amount,
            tg_id=int(tg_id_raw),
            username=username,
            flw_tx_id=flw_tx_id,
        )

        return "TRIVIA", {
            "status": "successful" if payment else "error",
            "credited_now": did_credit,
            "tries": tries,
            "tg_id": int(tg_id_raw),
            "payment": payment,
            "display_amount": tries,
        }

    return product_type or "UNKNOWN", {
        "status": "error",
        "reason": "unknown_product_type",
        "credited_now": False,
    }


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
    tx_ref = str(data.get("tx_ref") or "").strip()
    flw_status = normalize_flw_status(data.get("status"))

    logger.info(
        "🔔 Flutterwave webhook received | event=%s | tx_ref=%s | status=%s",
        event,
        tx_ref,
        flw_status,
    )

    if event != "charge.completed" or not tx_ref:
        return JSONResponse({"status": "ignored"})

    if flw_status != "successful":
        return JSONResponse({"status": "ignored"})

    verified = {
        "status": "successful",
        "amount": int(data.get("amount") or 0),
        "tx_ref": tx_ref,
        "flw_tx_id": data.get("id"),
        "meta": data.get("meta") or {},
    }

    try:
        product_type, info = await _finalize_from_verified_data(
            session,
            tx_ref=tx_ref,
            verified=verified,
        )
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.exception("❌ Webhook finalization failed | tx_ref=%s | err=%s", tx_ref, e)
        return JSONResponse({"status": "error"})

    if info.get("status") != "successful":
        return JSONResponse({"status": "error", "reason": info.get("reason")})

    if info.get("credited_now"):
        if product_type == "JAMB":
            await _send_payment_success_message(
                tg_id=int(info["tg_id"]),
                product_type="JAMB",
                amount_or_units=int(info["credits"]),
            )
        elif product_type == "JAMBMOCKSUBJECT":
            await _send_payment_success_message(
                tg_id=int(info["tg_id"]),
                product_type="JAMBMOCKSUBJECT",
                amount_or_units=int(info["mock_sessions"]),
            )
        elif product_type == "TRIVIA":
            await _send_payment_success_message(
                tg_id=int(info["tg_id"]),
                product_type="TRIVIA",
                amount_or_units=int(info["tries"]),
            )
        elif product_type == "MOCKJAMB":
            await _send_payment_success_message(
                tg_id=int(info["tg_id"]),
                product_type="MOCKJAMB",
                amount_or_units=int(info.get("display_amount") or 0),
            )

    return JSONResponse({"status": "success"})


@router.get("/flw/redirect", response_class=HTMLResponse)
async def flutterwave_redirect(
    tx_ref: str = Query(...),
    status: Optional[str] = None,
    transaction_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    del status, transaction_id

    product_type_hint = _product_type_from_tx_ref(tx_ref)
    success_url = _success_url(tx_ref, product_type_hint)
    failed_url = _failed_url(tx_ref, product_type_hint)

    try:
        verified = await verify_payment(tx_ref)
        verify_status = normalize_flw_status(verified.get("status"))

        logger.info(
            "🌐 Redirect verify | tx_ref=%s | verify_status=%s",
            tx_ref,
            verify_status,
        )

        if verify_status == "successful":
            product_type, info = await _finalize_from_verified_data(
                session,
                tx_ref=tx_ref,
                verified=verified,
            )
            await session.commit()

            subject_code = str((verified.get("meta") or {}).get("subject_code") or "").strip().lower()
            success_url = _success_url(tx_ref, product_type, subject_code)
            failed_url = _failed_url(tx_ref, product_type, subject_code)

            logger.info(
                "↩️ Redirect target chosen | tx_ref=%s | product_type=%s | success_url=%s",
                tx_ref,
                product_type,
                success_url,
            )

            if info.get("status") == "successful":
                if info.get("credited_now"):
                    if product_type == "JAMB":
                        await _send_payment_success_message(
                            tg_id=int(info["tg_id"]),
                            product_type="JAMB",
                            amount_or_units=int(info["credits"]),
                        )
                    elif product_type == "JAMBMOCKSUBJECT":
                        await _send_payment_success_message(
                            tg_id=int(info["tg_id"]),
                            product_type="JAMBMOCKSUBJECT",
                            amount_or_units=int(info["mock_sessions"]),
                        )
                    elif product_type == "TRIVIA":
                        await _send_payment_success_message(
                            tg_id=int(info["tg_id"]),
                            product_type="TRIVIA",
                            amount_or_units=int(info["tries"]),
                        )
                    elif product_type == "MOCKJAMB":
                        await _send_payment_success_message(
                            tg_id=int(info["tg_id"]),
                            product_type="MOCKJAMB",
                            amount_or_units=int(info.get("display_amount") or 0),
                        )

                if product_type == "JAMBMOCKSUBJECT":
                    mock_sessions = int(info.get("mock_sessions") or 0)
                    return HTMLResponse(f"""
                        <html><body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
                        <h2 style="color:green;">✅ Mock UTME \\(By Subject\\) Payment Successful</h2>
                        <p>Transaction Reference: <b>{tx_ref}</b></p>
                        <p>🎟 You’ve been credited with <b>{mock_sessions} mock session{'s' if mock_sessions != 1 else ''}</b>.</p>
                        <p>This tab will redirect to Telegram in 5 seconds...</p>
                        <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
                        </body></html>
                    """, status_code=200)
                
                if product_type == "MOCKJAMB":
                    return HTMLResponse(f"""
                        <html><body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
                        <h2 style="color:green;">✅ Mock JAMB / UTME Payment Successful</h2>
                        <p>Transaction Reference: <b>{tx_ref}</b></p>
                        <p>📝 Your Mock JAMB / UTME access has been activated.</p>
                        <p>This tab will redirect to Telegram in 5 seconds...</p>
                        <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
                        </body></html>
                    """, status_code=200)

                if product_type == "JAMB":
                    credits = int(info.get("credits") or 0)
                    return HTMLResponse(f"""
                        <html><body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
                        <h2 style="color:green;">✅ JAMB Payment Successful</h2>
                        <p>Transaction Reference: <b>{tx_ref}</b></p>
                        <p>📚 You’ve been credited with <b>{credits} JAMB question credits</b>.</p>
                        <p>This tab will redirect to Telegram in 5 seconds...</p>
                        <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
                        </body></html>
                    """, status_code=200)

                tries = int(info.get("tries") or 0)
                credited_text = f"{tries} spin{'s' if tries > 1 else ''}"
                return HTMLResponse(f"""
                    <html><body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
                    <h2 style="color:green;">✅ Payment Successful</h2>
                    <p>Transaction Reference: <b>{tx_ref}</b></p>
                    <p>🎁 You’ve been credited with <b>{credited_text}</b>! 🎉</p>
                    <p>This tab will redirect to Telegram in 5 seconds...</p>
                    <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
                    </body></html>
                """, status_code=200)

            return HTMLResponse(f"""
                <html><body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
                <h2 style="color:red;">❌ Payment Processing Error</h2>
                <p>Transaction Reference: <b>{tx_ref}</b></p>
                <p>We confirmed the payment, but local crediting failed.</p>
                <p>This tab will redirect to Telegram in 5 seconds...</p>
                <script>setTimeout(() => window.location.href="{failed_url}", 5000);</script>
                </body></html>
            """, status_code=200)

        if verify_status in ("failed", "expired"):
            logger.info(
                "↩️ Redirect failed target chosen | tx_ref=%s | product_type=%s | failed_url=%s",
                tx_ref,
                product_type_hint,
                failed_url,
            )

            return HTMLResponse(f"""
                <html><body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
                <h2 style="color:red;">❌ Payment Failed</h2>
                <p>Transaction Reference: <b>{tx_ref}</b></p>
                <p>This tab will redirect to Telegram in 5 seconds...</p>
                <script>setTimeout(() => window.location.href="{failed_url}", 5000);</script>
                </body></html>
            """, status_code=200)

        return HTMLResponse(f"""
            <html><head><meta charset="utf-8"><title>Verifying Payment</title></head>
            <body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
              <h2>⏳ Verifying your payment...</h2>
              <div style="margin:20px auto;height:40px;width:40px;border:5px solid #ccc;border-top-color:#4CAF50;border-radius:50%;animation:spin 1s linear infinite;"></div>
              <p>Please wait — we are checking the payment status. This page will auto-refresh.</p>
              <script>setTimeout(() => location.reload(), 4000);</script>
              <style>@keyframes spin {{ to {{ transform: rotate(360deg); }} }}</style>
            </body></html>
        """, status_code=200)

    except Exception as e:
        await session.rollback()
        logger.exception("❌ Unexpected error in /flw/redirect for %s: %s", tx_ref, e)
        return HTMLResponse(f"""
            <html><body style="font-family: Arial,sans-serif; text-align:center;">
            <h2 style="color:red;">❌ Payment processing error</h2>
            <p>Transaction Reference: <b>{tx_ref}</b></p>
            <p>Something went wrong while processing your payment.</p>
            <p><a href="https://t.me/{BOT_USERNAME}">Return to Telegram</a></p>
            </body></html>
        """, status_code=200)


@router.get("/flw/redirect/status")
async def flutterwave_redirect_status(
    tx_ref: str,
    session: AsyncSession = Depends(get_session),
):
    product_type_hint = _product_type_from_tx_ref(tx_ref)
    success_url = _success_url(tx_ref, product_type_hint)
    failed_url = _failed_url(tx_ref, product_type_hint)

    try:
        verified = await verify_payment(tx_ref)
        verify_status = normalize_flw_status(verified.get("status"))

        if verify_status == "successful":
            product_type, info = await _finalize_from_verified_data(
                session,
                tx_ref=tx_ref,
                verified=verified,
            )
            await session.commit()

            subject_code = str((verified.get("meta") or {}).get("subject_code") or "").strip().lower()
            success_url = _success_url(tx_ref, product_type, subject_code)
            failed_url = _failed_url(tx_ref, product_type, subject_code)

            logger.info(
                "↩️ Redirect status target chosen | tx_ref=%s | product_type=%s | success_url=%s",
                tx_ref,
                product_type,
                success_url,
            )

            if info.get("status") == "successful":
                if product_type == "JAMBMOCKSUBJECT":
                    mock_sessions = int(info.get("mock_sessions") or 0)
                    return JSONResponse({
                        "done": True,
                        "html": f"""
                        <h2 style="color:green;">✅ Mock UTME \\(By Subject\\) Payment Successful</h2>
                        <p>Transaction Reference: <b>{tx_ref}</b></p>
                        <p>🎟 You’ve been credited with <b>{mock_sessions} mock session{'s' if mock_sessions != 1 else ''}</b>.</p>
                        <p>This tab will redirect to Telegram in 5 seconds...</p>
                        <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
                        """
                    })
                
                if product_type == "MOCKJAMB":
                    return JSONResponse({
                        "done": True,
                        "html": f"""
                        <h2 style="color:green;">✅ Mock JAMB / UTME Payment Successful</h2>
                        <p>Transaction Reference: <b>{tx_ref}</b></p>
                        <p>📝 Your Mock JAMB / UTME access has been activated.</p>
                        <p>This tab will redirect to Telegram in 5 seconds...</p>
                        <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
                        """
                    })

                if product_type == "JAMB":
                    credits = int(info.get("credits") or 0)
                    return JSONResponse({
                        "done": True,
                        "html": f"""
                        <h2 style="color:green;">✅ JAMB Payment Successful</h2>
                        <p>Transaction Reference: <b>{tx_ref}</b></p>
                        <p>📚 You’ve been credited with <b>{credits} JAMB question credits</b>.</p>
                        <p>This tab will redirect to Telegram in 5 seconds...</p>
                        <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
                        """
                    })

                tries = int(info.get("tries") or 0)
                return JSONResponse({
                    "done": True,
                    "html": f"""
                    <h2 style="color:green;">✅ Payment Successful</h2>
                    <p>Transaction Reference: <b>{tx_ref}</b></p>
                    <p>🎁 You’ve been credited with <b>{tries} spin{'s' if tries > 1 else ''}</b>! 🎉</p>
                    <p>This tab will redirect to Telegram in 5 seconds...</p>
                    <script>setTimeout(() => window.location.href="{success_url}", 5000);</script>
                    """
                })

            return JSONResponse({
                "done": True,
                "html": f"""
                <h2 style="color:red;">❌ Payment Processing Error</h2>
                <p>Transaction Reference: <b>{tx_ref}</b></p>
                <script>setTimeout(() => window.location.href="{failed_url}", 5000);</script>
                """
            })

        if verify_status in ("failed", "expired"):
            logger.info(
                "↩️ Redirect status failed target chosen | tx_ref=%s | product_type=%s | failed_url=%s",
                tx_ref,
                product_type_hint,
                failed_url,
            )

            return JSONResponse({
                "done": True,
                "html": f"""
                <h2 style="color:red;">❌ Payment Failed</h2>
                <p>Transaction Reference: <b>{tx_ref}</b></p>
                <script>setTimeout(() => window.location.href="{failed_url}", 5000);</script>
                """
            })

        return JSONResponse({
            "done": False,
            "html": f"""
            <h2 style="color:orange;">⏳ Payment Pending</h2>
            <p>Transaction Reference: <b>{tx_ref}</b></p>
            <p>⚠️ Your payment is still being processed.</p>
            <div class="spinner" style="margin:20px auto;height:40px;width:40px;border:5px solid #ccc;border-top-color:#f39c12;border-radius:50%;animation:spin 1s linear infinite;"></div>
            <style>@keyframes spin {{ to {{ transform: rotate(360deg); }} }}</style>
            """
        })

    except Exception as e:
        await session.rollback()
        logger.exception("❌ Unexpected error in /flw/redirect/status for %s: %s", tx_ref, e)
        return JSONResponse({
            "done": True,
            "html": f"""
            <h2 style="color:red;">❌ Payment processing error</h2>
            <p>Transaction Reference: <b>{tx_ref}</b></p>
            <p>Something went wrong while checking your payment.</p>
            <p><a href="https://t.me/{BOT_USERNAME}">Return to Telegram</a></p>
            """
        })

