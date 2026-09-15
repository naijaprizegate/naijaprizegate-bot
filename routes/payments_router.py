# ======================================================
# routes/payments_router.py
# =====================================================
import json
import os
import logging
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from db import get_session
from models import User
from finance_models import WithdrawalRequestORM

from services.flutterwave_client import (
    normalize_flw_status,
    validate_flutterwave_webhook,
    verify_payment,
)
from services.trivia_payments import finalize_trivia_payment, get_trivia_payment
from services.jamb_payments import finalize_jamb_payment, get_jamb_payment
from services.mockjamb_payments import finalize_mockjamb_payment, get_mockjamb_payment
from services.mockwaec_payments import finalize_mockwaec_payment, get_mockwaec_payment
from services.waec_payment_finalizer import finalize_waec_payment, get_waec_payment
from services.finance.enums import WithdrawalStatus
from services.finance.flutterwave_payout import get_withdrawal_payout_status
from services.finance.commission_service import (
    process_referral_commission,
)

logger = logging.getLogger("payments_router")
logger.setLevel(logging.INFO)

router = APIRouter()

BOT_USERNAME = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")
BOT_TOKEN = os.getenv("BOT_TOKEN")


def _product_type_from_tx_ref(tx_ref: str) -> str:
    tx_ref = (tx_ref or "").upper().strip()

    if tx_ref.startswith("WAECMOCKSUBJECT-"):
        return "WAECMOCKSUBJECT"
    if tx_ref.startswith("JAMBMOCKSUBJECT-"):
        return "JAMBMOCKSUBJECT"
    if tx_ref.startswith("JAMB-"):
        return "JAMB"
    if tx_ref.startswith("WAEC-"):
        return "WAEC"
    if tx_ref.startswith("MOCKJAMB-"):
        return "MOCKJAMB"
    if tx_ref.startswith("MOCKWAEC-"):
        return "MOCKWAEC"
    if tx_ref.startswith("TRIVIA-"):
        return "TRIVIA"

    return "TRIVIA"


def _success_url(tx_ref: str, product_type: str, subject_code: str | None = None) -> str:
    product_type = (product_type or "").upper().strip()
    subject_code = (subject_code or "").strip().lower()

    if product_type == "JAMB":
        return f"https://t.me/{BOT_USERNAME}?start=payok_jamb_{tx_ref}"

    if product_type == "WAEC":
        return f"https://t.me/{BOT_USERNAME}?start=payok_waec_{tx_ref}"

    if product_type == "WAECMOCKSUBJECT":
        if subject_code:
            return f"https://t.me/{BOT_USERNAME}?start=payok_waecmocksubject_{subject_code}_{tx_ref}"
        return f"https://t.me/{BOT_USERNAME}?start=payok_waecmocksubject_{tx_ref}"

    if product_type == "JAMBMOCKSUBJECT":
        if subject_code:
            return f"https://t.me/{BOT_USERNAME}?start=payok_jambmocksubject_{subject_code}_{tx_ref}"
        return f"https://t.me/{BOT_USERNAME}?start=payok_jambmocksubject_{tx_ref}"

    if product_type == "MOCKJAMB":
        return f"https://t.me/{BOT_USERNAME}?start=payok_mockjamb_{tx_ref}"

    if product_type == "MOCKWAEC":
        return f"https://t.me/{BOT_USERNAME}?start=payok_mockwaec_{tx_ref}"
    
    return f"https://t.me/{BOT_USERNAME}?start=payok_trivia_{tx_ref}"


def _failed_url(tx_ref: str, product_type: str, subject_code: str | None = None) -> str:
    product_type = (product_type or "").upper().strip()
    subject_code = (subject_code or "").strip().lower()

    if product_type == "JAMB":
        return f"https://t.me/{BOT_USERNAME}?start=payfail_jamb_{tx_ref}"

    if product_type == "WAEC":
        return f"https://t.me/{BOT_USERNAME}?start=payfail_waec_{tx_ref}"
    
    if product_type == "WAECMOCKSUBJECT":
        if subject_code:
            return f"https://t.me/{BOT_USERNAME}?start=payfail_waecmocksubject_{subject_code}_{tx_ref}"
        return f"https://t.me/{BOT_USERNAME}?start=payfail_waecmocksubject_{tx_ref}"
    
    if product_type == "JAMBMOCKSUBJECT":
        if subject_code:
            return f"https://t.me/{BOT_USERNAME}?start=payfail_jambmocksubject_{subject_code}_{tx_ref}"
        return f"https://t.me/{BOT_USERNAME}?start=payfail_jambmocksubject_{tx_ref}"

    if product_type == "MOCKJAMB":
        return f"https://t.me/{BOT_USERNAME}?start=payfail_mockjamb_{tx_ref}"

    if product_type == "MOCKWAEC":
        return f"https://t.me/{BOT_USERNAME}?start=payfail_mockwaec_{tx_ref}"
    
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
        
        elif product_type == "WAEC":
            text = (
                "🎉 *Payment Successful!*\n\n"
                f"You received *{amount_or_units}* WAEC question credit"
                f"{'s' if amount_or_units != 1 else ''} 📚\n\n"
                "You can now continue your WAEC / NECO Practice."
            )
        
        elif product_type == "JAMBMOCKSUBJECT":
            text = (
                "🎉 *Payment Successful!*\n\n"
                f"You received *{amount_or_units}* mock session"
                f"{'s' if amount_or_units != 1 else ''} 🎟\n\n"
                "You can now continue your Mock UTME \\(By Subject\\)."
            )
        
        elif product_type == "WAECMOCKSUBJECT":
            text = (
                "🎉 *Payment Successful!*\n\n"
                "Your *Mock WAEC / NECO (By Subject)* session(s) have been added 📝\n\n"
                "You can now continue to your mock subject screen."
            )
        
        elif product_type == "MOCKJAMB":
            text = (
                "🎉 *Payment Successful!*\n\n"
                "Your *Mock JAMB / UTME* access has been activated 📝\n\n"
                "You can now continue to your mock exam."
            )
        
        elif product_type == "MOCKWAEC":
            text = (
                "🎉 *Payment Successful!*\n\n"
                "Your *Mock WAEC / NECO* access has been activated 📝\n\n"
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


async def _process_referral_commission_if_needed(
    session: AsyncSession,
    payment,
) -> None:
    """
    Process referral commission for a successfully finalized payment.

    The commission service is transaction-aware and does not commit.
    The caller owns the surrounding transaction.
    """

    if payment is None:
        return

    result = await process_referral_commission(
        session,
        payment,
    )

    logger.info(
        "💰 Referral commission processed | "
        "payment_id=%s | status=%s | "
        "referral_id=%s | referrer_user_id=%s | "
        "commission_amount=%s",
        payment.id,
        result.status,
        result.referral_id,
        result.referrer_user_id,
        result.commission_amount,
    )


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

        did_credit, payment, credits, mock_sessions = await finalize_jamb_payment(
            session,
            payment_reference=tx_ref,
            user_id=int(tg_id_raw),
            amount_paid=amount,
            question_credits_added=None,
            mock_sessions_added=None,
        )

        if payment:
            await _process_referral_commission_if_needed(
                session,
                payment,
            )

        return "JAMB", {
            "status": "successful" if payment else "error",
            "credited_now": did_credit,
            "credits": credits,
            "tg_id": int(tg_id_raw),
            "payment": payment,
            "display_amount": credits,
        }

    if product_type == "WAEC":
        tg_id_raw = meta.get("tg_id")
        if not tg_id_raw:
            payment = await get_waec_payment(session, tx_ref)
            tg_id_raw = payment.get("user_id") if payment else None

        if not tg_id_raw:
            return "WAEC", {
                "status": "error",
                "reason": "missing_tg_id",
                "credited_now": False,
            }

        did_credit, payment, credits, mock_sessions = await finalize_waec_payment(
            session,
            payment_reference=tx_ref,
            user_id=int(tg_id_raw),
            amount_paid=amount,
            question_credits_added=None,
            mock_sessions_added=None,
        )

        if payment:
            await _process_referral_commission_if_needed(
                session,
                payment,
            )

        return "WAEC", {
            "status": "successful" if payment else "error",
            "credited_now": did_credit,
            "credits": credits,
            "mock_sessions": mock_sessions,
            "tg_id": int(tg_id_raw),
            "payment": payment,
            "display_amount": credits,
        }

    if product_type == "WAECMOCKSUBJECT":
        tg_id_raw = meta.get("tg_id")
        mock_sessions_added_raw = meta.get("mock_sessions_added")

        if not tg_id_raw:
            payment = await get_waec_payment(session, tx_ref)
            tg_id_raw = payment.get("user_id") if payment else None
            mock_sessions_added_raw = (
                mock_sessions_added_raw
                or (payment.get("mock_sessions_added") if payment else None)
            )

        if not tg_id_raw:
            return "WAECMOCKSUBJECT", {
                "status": "error",
                "reason": "missing_tg_id",
                "credited_now": False,
            }

        did_credit, payment, credits, mock_sessions = await finalize_waec_payment(
            session,
            payment_reference=tx_ref,
            user_id=int(tg_id_raw),
            amount_paid=amount,
            question_credits_added=0,
            mock_sessions_added=int(mock_sessions_added_raw or 0),
        )

        if payment:
            await _process_referral_commission_if_needed(
                session,
                payment,
            )

        return "WAECMOCKSUBJECT", {
            "status": "successful" if payment else "error",
            "credited_now": did_credit,
            "credits": credits,
            "mock_sessions": mock_sessions,
            "tg_id": int(tg_id_raw),
            "payment": payment,
            "display_amount": mock_sessions,
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

        if payment:
            await _process_referral_commission_if_needed(
                session,
                payment,
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

        if payment:
            await _process_referral_commission_if_needed(
                session,
                payment,
            )

        return "MOCKJAMB", {
            "status": "successful" if payment else "error",
            "credited_now": did_finalize,
            "tg_id": int(tg_id_raw),
            "payment": payment,
            "display_amount": amount,
        }

    if product_type == "MOCKWAEC":
        tg_id_raw = meta.get("tg_id")
        if not tg_id_raw:
            payment = await get_mockwaec_payment(session, tx_ref)
            tg_id_raw = payment.get("user_id") if payment else None

        if not tg_id_raw:
            return "MOCKWAEC", {
                "status": "error",
                "reason": "missing_tg_id",
                "credited_now": False,
            }

        did_finalize, payment = await finalize_mockwaec_payment(
            session,
            payment_reference=tx_ref,
            user_id=int(tg_id_raw),
        )

        if payment:
            await _process_referral_commission_if_needed(
                session,
                payment,
            )

        return "MOCKWAEC", {
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

        if payment:
            await _process_referral_commission_if_needed(
                session,
                payment,
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

    event = (
        payload.get("event")
        or payload.get("type")
        or ""
    ).lower().strip()

    tx_ref = str(data.get("tx_ref") or "").strip()
    flw_status = normalize_flw_status(data.get("status"))

    logger.info(
        "🔔 Flutterwave webhook received | event=%s | tx_ref=%s | status=%s",
        event,
        tx_ref,
        flw_status,
    )

    # ============================================================
    # FLUTTERWAVE WITHDRAWAL / PAYOUT WEBHOOK
    # ============================================================
    if event == "transfer.disburse":
        transfer_id = str(data.get("id") or "").strip()

        provider_reference = str(
            data.get("reference") or ""
        ).strip()

        webhook_status = str(
            data.get("status") or ""
        ).upper().strip()

        try:
            webhook_amount = Decimal(
                str(data.get("amount"))
            )
        except Exception:
            webhook_amount = None

        webhook_currency = str(
            data.get("currency")
            or data.get("source_currency")
            or ""
        ).upper().strip()

        logger.info(
            "💸 Flutterwave payout webhook | "
            "transfer_id=%s | reference=%s | status=%s | amount=%s | currency=%s",
            transfer_id,
            provider_reference,
            webhook_status,
            webhook_amount,
            webhook_currency,
        )

        # --------------------------------------------------------
        # FIND OUR WITHDRAWAL
        # --------------------------------------------------------
        withdrawal = None

        # Primary match:
        # Flutterwave webhook data.id is the transfer ID,
        # which we store as provider_reference.
        if transfer_id:
            result = await session.execute(
                select(WithdrawalRequestORM).where(
                    WithdrawalRequestORM.provider_reference
                    == transfer_id
                )
            )
            withdrawal = result.scalar_one_or_none()

        # Fallback match:
        # Flutterwave webhook data.reference is our merchant
        # reference, which we store as payment_reference.
        if withdrawal is None and provider_reference:
            result = await session.execute(
                select(WithdrawalRequestORM).where(
                    WithdrawalRequestORM.payment_reference
                    == provider_reference
                )
            )
            withdrawal = result.scalar_one_or_none()

        if withdrawal is None:
            logger.warning(
                "⚠️ Flutterwave payout webhook does not match "
                "any withdrawal | transfer_id=%s | reference=%s",
                transfer_id,
                provider_reference,
            )

            return JSONResponse(
                {
                    "status": "ok",
                    "message": "Withdrawal not found",
                }
            )

        logger.info(
            "💸 Flutterwave payout webhook matched withdrawal %s",
            withdrawal.id,
        )

        # --------------------------------------------------------
        # ALREADY COMPLETED
        # --------------------------------------------------------
        if withdrawal.status == WithdrawalStatus.COMPLETED:
            return JSONResponse(
                {
                    "status": "ok",
                    "message": "Withdrawal already completed",
                }
            )

        # --------------------------------------------------------
        # ONLY PROCESS WITHDRAWALS CURRENTLY PROCESSING
        # --------------------------------------------------------
        if withdrawal.status != WithdrawalStatus.PROCESSING:
            logger.info(
                "Ignoring payout webhook for withdrawal %s "
                "because internal status is %s",
                withdrawal.id,
                withdrawal.status,
            )

            return JSONResponse(
                {
                    "status": "ok",
                    "message": "Withdrawal not processing",
                }
            )

        # --------------------------------------------------------
        # INDEPENDENTLY VERIFY THE PAYOUT WITH FLUTTERWAVE
        # --------------------------------------------------------
        provider_result = await get_withdrawal_payout_status(
            withdrawal.provider_reference
        )

        if not provider_result.get("ok"):
            logger.error(
                "❌ Could not independently verify Flutterwave "
                "payout | withdrawal=%s | result=%s",
                withdrawal.id,
                provider_result,
            )

            return JSONResponse(
                {
                    "status": "error",
                    "message": "Could not verify payout",
                },
                status_code=500,
            )

        provider = provider_result.get("data") or {}

        verified_transfer_id = str(
            provider.get("transfer_id") or ""
        ).strip()

        verified_reference = str(
            provider.get("reference") or ""
        ).strip()

        verified_status = str(
            provider.get("status") or ""
        ).upper().strip()

        try:
            verified_amount = Decimal(
                str(provider.get("amount"))
            )
        except Exception:
            verified_amount = None

        verified_currency = str(
            provider.get("currency") or ""
        ).upper().strip()

        logger.info(
            "🔎 Flutterwave payout independently verified | "
            "withdrawal=%s | transfer_id=%s | reference=%s | "
            "status=%s | amount=%s | currency=%s",
            withdrawal.id,
            verified_transfer_id,
            verified_reference,
            verified_status,
            verified_amount,
            verified_currency,
        )

        # --------------------------------------------------------
        # VERIFY TRANSFER ID
        # --------------------------------------------------------
        if (
            transfer_id
            and verified_transfer_id
            and transfer_id != verified_transfer_id
        ):
            logger.error(
                "❌ Flutterwave transfer ID mismatch | "
                "withdrawal=%s | webhook=%s | verified=%s",
                withdrawal.id,
                transfer_id,
                verified_transfer_id,
            )

            return JSONResponse(
                {
                    "status": "error",
                    "message": "Transfer ID mismatch",
                },
                status_code=500,
            )

        # --------------------------------------------------------
        # VERIFY AMOUNT
        # --------------------------------------------------------
        if (
            verified_amount is None
            or verified_amount
            != Decimal(str(withdrawal.amount))
        ):
            logger.error(
                "❌ Flutterwave payout amount mismatch | "
                "withdrawal=%s | expected=%s | verified=%s",
                withdrawal.id,
                withdrawal.amount,
                verified_amount,
            )

            return JSONResponse(
                {
                    "status": "error",
                    "message": "Amount mismatch",
                },
                status_code=500,
            )

        # --------------------------------------------------------
        # VERIFY CURRENCY
        # --------------------------------------------------------
        if verified_currency != "NGN":
            logger.error(
                "❌ Flutterwave payout currency mismatch | "
                "withdrawal=%s | currency=%s",
                withdrawal.id,
                verified_currency,
            )

            return JSONResponse(
                {
                    "status": "error",
                    "message": "Currency mismatch",
                },
                status_code=500,
            )

        # --------------------------------------------------------
        # VERIFY REFERENCE
        # --------------------------------------------------------
        if (
            withdrawal.payment_reference
            and verified_reference
            and withdrawal.payment_reference
            != verified_reference
        ):
            logger.error(
                "❌ Flutterwave payout reference mismatch | "
                "withdrawal=%s | expected=%s | verified=%s",
                withdrawal.id,
                withdrawal.payment_reference,
                verified_reference,
            )

            return JSONResponse(
                {
                    "status": "error",
                    "message": "Reference mismatch",
                },
                status_code=500,
            )

        # --------------------------------------------------------
        # FAILED PAYOUT
        #
        # DO NOT AUTOMATICALLY RETRY.
        # Admin's existing Retry button handles retrying.
        # --------------------------------------------------------
        if verified_status in {"FAILED", "EXPIRED"}:
            logger.warning(
                "❌ Flutterwave payout failed | withdrawal=%s | status=%s",
                withdrawal.id,
                verified_status,
            )

            return JSONResponse(
                {
                    "status": "ok",
                    "message": (
                        "Payout failed; awaiting admin retry"
                    ),
                }
            )

        # --------------------------------------------------------
        # STILL PENDING / PROCESSING / UNKNOWN
        # --------------------------------------------------------
        if verified_status not in {
            "SUCCESSFUL",
            "SUCCESS",
        }:
            logger.info(
                "⏳ Flutterwave payout not yet successful | "
                "withdrawal=%s | status=%s",
                withdrawal.id,
                verified_status,
            )

            return JSONResponse(
                {
                    "status": "ok",
                    "message": "Payout not yet successful",
                }
            )

        # ========================================================
        # PAYOUT IS SUCCESSFUL
        #
        # VERY IMPORTANT:
        #
        # DO NOT COMPLETE THE WITHDRAWAL HERE.
        #
        # We only notify Admin.
        # Admin must press Confirm Payout / Complete.
        # ========================================================

        existing_note = withdrawal.admin_note or ""

        notification_marker = (
            f"[FLW_WEBHOOK_NOTIFIED:{verified_reference}]"
        )

        # Prevent duplicate Admin notifications.
        if notification_marker in existing_note:
            logger.info(
                "ℹ️ Admin already notified for withdrawal %s",
                withdrawal.id,
            )

            return JSONResponse(
                {
                    "status": "ok",
                    "message": "Admin already notified",
                }
            )

        admin_user_id = os.getenv("ADMIN_USER_ID")
        bot_token = os.getenv("BOT_TOKEN")

        if not admin_user_id or not bot_token:
            logger.error(
                "❌ ADMIN_USER_ID or BOT_TOKEN is missing; "
                "cannot notify Admin | withdrawal=%s",
                withdrawal.id,
            )

            return JSONResponse(
                {
                    "status": "error",
                    "message": "Admin notification not configured",
                },
                status_code=500,
            )

        admin_message = (
            "💸 <b>Flutterwave Payout Verified</b>\n\n"
            f"💰 Amount: ₦"
            f"{Decimal(str(withdrawal.amount)):,.2f}\n"
            f"🏦 Bank: {withdrawal.bank_name}\n"
            f"👤 Account Name: {withdrawal.account_name}\n"
            f"🔢 Account Number: {withdrawal.account_number}\n\n"
            f"🆔 Withdrawal ID: "
            f"<code>{withdrawal.id}</code>\n"
            f"🔗 Provider Transfer ID: "
            f"<code>{verified_transfer_id}</code>\n"
            f"📌 Reference: "
            f"<code>{verified_reference}</code>\n\n"
            "✅ Flutterwave independently confirmed this payout "
            "as <b>SUCCESSFUL</b>.\n\n"
            "⚠️ The withdrawal is STILL "
            "<b>PROCESSING</b>.\n"
            "⚠️ Wallet funds have NOT been consumed yet.\n\n"
            "Please verify and use "
            "<b>💰 Confirm Payout / Complete</b> "
            "to finalize it."
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "💰 Confirm Payout / Complete",
                        callback_data=(
                            "admin_withdrawal:complete:"
                            f"{withdrawal.id}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔄 Retry Failed Payout",
                        callback_data=(
                            "admin_withdrawal:retry:"
                            f"{withdrawal.id}"
                        ),
                    )
                ],
            ]
        )

        # --------------------------------------------------------
        # SEND ADMIN NOTIFICATION
        # --------------------------------------------------------
        try:
            bot = Bot(token=bot_token)

            await bot.send_message(
                chat_id=int(admin_user_id),
                text=admin_message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

        except Exception:
            logger.exception(
                "❌ Failed to notify Admin about payout "
                "withdrawal=%s",
                withdrawal.id,
            )

            # Returning 500 allows Flutterwave to retry the webhook.
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Admin notification failed",
                },
                status_code=500,
            )

        # --------------------------------------------------------
        # RECORD THAT ADMIN WAS NOTIFIED
        #
        # IMPORTANT:
        # We STILL DO NOT mark the withdrawal COMPLETED.
        # --------------------------------------------------------
        if existing_note:
            withdrawal.admin_note = (
                f"{existing_note}\n{notification_marker}"
            )
        else:
            withdrawal.admin_note = notification_marker

        await session.commit()

        logger.info(
            "✅ Admin notified of verified Flutterwave payout | "
            "withdrawal=%s | status remains PROCESSING",
            withdrawal.id,
        )

        return JSONResponse(
            {
                "status": "ok",
                "message": "Payout verified and Admin notified",
            }
        )

    # ============================================================
    # EXISTING COLLECTION PAYMENT WEBHOOK
    # ============================================================

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

        logger.exception(
            "❌ Webhook finalization failed | tx_ref=%s | err=%s",
            tx_ref,
            e,
        )

        return JSONResponse({"status": "error"})

    if info.get("status") != "successful":
        return JSONResponse(
            {
                "status": "error",
                "reason": info.get("reason"),
            }
        )

    if info.get("credited_now"):
        if product_type == "JAMB":
            await _send_payment_success_message(
                tg_id=int(info["tg_id"]),
                product_type="JAMB",
                amount_or_units=int(info["credits"]),
            )

        elif product_type == "WAEC":
            await _send_payment_success_message(
                tg_id=int(info["tg_id"]),
                product_type="WAEC",
                amount_or_units=int(info["credits"]),
            )

        elif product_type == "JAMBMOCKSUBJECT":
            await _send_payment_success_message(
                tg_id=int(info["tg_id"]),
                product_type="JAMBMOCKSUBJECT",
                amount_or_units=int(info["mock_sessions"]),
            )

        elif product_type == "WAECMOCKSUBJECT":
            await _send_payment_success_message(
                tg_id=int(info["tg_id"]),
                product_type="WAECMOCKSUBJECT",
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
                amount_or_units=int(
                    info.get("display_amount") or 0
                ),
            )

        elif product_type == "MOCKWAEC":
            await _send_payment_success_message(
                tg_id=int(info["tg_id"]),
                product_type="MOCKWAEC",
                amount_or_units=int(
                    info.get("display_amount") or 0
                ),
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
                    elif product_type == "WAEC":
                        await _send_payment_success_message(
                            tg_id=int(info["tg_id"]),
                            product_type="WAEC",
                            amount_or_units=int(info["credits"]),
                        )
                    elif product_type == "JAMBMOCKSUBJECT":
                        await _send_payment_success_message(
                            tg_id=int(info["tg_id"]),
                            product_type="JAMBMOCKSUBJECT",
                            amount_or_units=int(info["mock_sessions"]),
                        )
                    elif product_type == "WAECMOCKSUBJECT":
                        await _send_payment_success_message(
                            tg_id=int(info["tg_id"]),
                            product_type="WAECMOCKSUBJECT",
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

                    elif product_type == "MOCKWAEC":
                        await _send_payment_success_message(
                            tg_id=int(info["tg_id"]),
                            product_type="MOCKWAEC",
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
                
                if product_type == "WAECMOCKSUBJECT":
                    mock_sessions = int(info.get("mock_sessions") or 0)
                    return HTMLResponse(f"""
                        <html><body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
                        <h2 style="color:green;">✅ Mock WAEC / NECO (By Subject) Payment Successful</h2>
                        <p>Transaction Reference: <b>{tx_ref}</b></p>
                        <p>🎟 You’ve been credited with <b>{mock_sessions} mock session{'s' if mock_sessions != 1 else ''}</b>.</p>
                        <p>This tab will redirect to Telegram in 5 seconds.</p>
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

                if product_type == "MOCKWAEC":
                    return HTMLResponse(f"""
                        <html><body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
                        <h2 style="color:green;">✅ Mock WAEC / NECO Payment Successful</h2>
                        <p>Transaction Reference: <b>{tx_ref}</b></p>
                        <p>📝 Your Mock WAEC / NECO access has been activated.</p>
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

                if product_type == "WAEC":
                    credits = int(info.get("credits") or 0)
                    return HTMLResponse(f"""
                        <html><body style="font-family: Arial, sans-serif; text-align:center; padding:40px;">
                        <h2 style="color:green;">✅ WAEC Payment Successful</h2>
                        <p>Transaction Reference: <b>{tx_ref}</b></p>
                        <p>📚 You’ve been credited with <b>{credits} WAEC question credits</b>.</p>
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

                if product_type == "MOCKWAEC":
                    return JSONResponse({
                        "done": True,
                        "html": f"""
                        <h2 style="color:green;">✅ Mock WAEC / NECO Payment Successful</h2>
                        <p>Transaction Reference: <b>{tx_ref}</b></p>
                        <p>📝 Your Mock WAEC / NECO access has been activated.</p>
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
                
                if product_type == "WAEC":
                    credits = int(info.get("credits") or 0)
                    return JSONResponse({
                        "done": True,
                        "html": f"""
                        <h2 style="color:green;">✅ WAEC Payment Successful</h2>
                        <p>Transaction Reference: <b>{tx_ref}</b></p>
                        <p>📚 You’ve been credited with <b>{credits} WAEC question credits</b>.</p>
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

