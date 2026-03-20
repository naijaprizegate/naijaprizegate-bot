# ==============================================================
# tasks/notifier.py
# AUTO-SEND AIRTIME + SMART RETRY + ADMIN ALERTS
# Uses ClubKonnect via services/airtime_providers/service.py
# ==============================================================
from __future__ import annotations

import os
import json
import asyncio

from sqlalchemy import text
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from db import get_async_session
from logger import logger
from services.airtime_providers.service import send_airtime


BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

bot = Bot(token=BOT_TOKEN)

AIRTIME_LOOP_SECONDS = 60
RETRY_NOTIFICATIONS_SECONDS = 60 * 60

MAX_AIRTIME_RETRIES = 3
AIRTIME_RETRY_COOLDOWN_MINUTES = 10
BATCH_SIZE = 10

# Avoid spamming user/admin on every retry
NOTIFY_USER_ON_FAILURE_EVERY_N_ATTEMPTS = 2   # user on 1st, 3rd, 5th...
NOTIFY_ADMIN_ON_FAILURE_EVERY_N_ATTEMPTS = 1  # admin every failure


def _post_airtime_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Continue Playing", callback_data="playtrivia")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
    ])


def _classify_failure(res) -> tuple[str, bool]:
    """
    Returns: (new_status, should_retry)
    """
    raw = (res.raw or {}) if hasattr(res, "raw") else {}
    status = str(raw.get("status") or "").upper().strip()
    msg = str(getattr(res, "message", "") or "").upper().strip()

    if status == "INSUFFICIENT_BALANCE" or msg == "INSUFFICIENT_BALANCE":
        return ("failed_needs_funding", False)

    retryable = raw.get("retryable")
    if isinstance(retryable, bool):
        return ("failed_retryable", retryable)

    http_status = raw.get("http_status")
    try:
        http_status = int(http_status) if http_status is not None else 0
    except Exception:
        http_status = 0

    msg_lower = str(raw.get("message") or "").lower()

    if http_status >= 500 or "non-json response" in msg_lower or "timeout" in msg_lower:
        return ("failed_retryable", True)

    return ("failed_permanent", False)


def _resolve_success_status(res) -> str:
    """
    Distinguish between sent vs completed when provider reports success.
    """
    raw = (res.raw or {}) if hasattr(res, "raw") else {}

    status_code = str(raw.get("statuscode") or raw.get("statusCode") or "").strip()
    status_text = str(raw.get("status") or "").upper().strip()

    if status_code == "200" or status_text in ("ORDER_COMPLETED", "COMPLETED", "SUCCESS"):
        return "completed"

    if status_code in ("100", "300") or status_text in ("ORDER_RECEIVED", "ORDER_PROCESSED"):
        return "sent"

    if getattr(res, "success", False):
        return "sent"

    return "failed"


async def process_pending_airtime():
    async with get_async_session() as session:
        # --------------------------------------------------------
        # Pick only payouts ready to send:
        #
        # 1. Fresh claims:
        #    status = 'pending_claim' AND phone_number IS NOT NULL
        #
        # 2. Retryable failures:
        #    status = 'failed_retryable' and cooldown elapsed
        # --------------------------------------------------------
        pick_sql = text(f"""
            WITH picked AS (
                SELECT id
                FROM airtime_payouts
                WHERE
                    (
                        (
                            status = 'pending_claim'
                            AND phone_number IS NOT NULL
                        )
                        OR (
                            status = 'failed_retryable'
                            AND phone_number IS NOT NULL
                            AND COALESCE(retry_count, 0) < :max_retries
                            AND (
                                last_retry_at IS NULL
                                OR last_retry_at < NOW() - INTERVAL '{AIRTIME_RETRY_COOLDOWN_MINUTES} minutes'
                            )
                        )
                    )
                ORDER BY created_at ASC
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
            )
            SELECT
                a.id,
                a.status,
                a.tg_id,
                a.phone_number,
                a.amount,
                COALESCE(a.retry_count, 0) AS retry_count
            FROM airtime_payouts a
            JOIN picked p ON p.id = a.id
        """)

        try:
            result = await session.execute(
                pick_sql,
                {
                    "limit": BATCH_SIZE,
                    "max_retries": MAX_AIRTIME_RETRIES,
                },
            )
            rows = result.fetchall()
        except Exception:
            logger.exception("❌ Failed to pick airtime payouts batch")
            await session.rollback()
            return

        if not rows:
            logger.debug("ℹ️ No airtime payouts ready for processing")
            return

        logger.info("📦 Picked %s airtime payout(s) for processing", len(rows))

        for row in rows:
            payout_id = row.id
            current_status = row.status
            tg_id = row.tg_id
            phone = (row.phone_number or "").strip()
            amount = int(row.amount or 0)
            prev_retry_count = int(row.retry_count or 0)
            attempt_no = prev_retry_count + 1

            # ----------------------------------------------------
            # Final sanity guard
            # ----------------------------------------------------
            if not phone:
                logger.warning(
                    "⚠️ Skipping payout with empty phone_number | payout_id=%s | tg_id=%s | status=%s",
                    payout_id,
                    tg_id,
                    current_status,
                )
                continue

            if amount <= 0:
                logger.error(
                    "❌ Invalid payout amount | payout_id=%s | tg_id=%s | amount=%s",
                    payout_id,
                    tg_id,
                    amount,
                )
                try:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status = 'failed_permanent',
                                provider_response = CAST(:response AS jsonb),
                                provider_payload = :payload
                            WHERE id = :pid
                        """),
                        {
                            "pid": payout_id,
                            "response": json.dumps(
                                {"status": "error", "message": "Invalid payout amount"},
                                ensure_ascii=False,
                            ),
                            "payload": "Invalid payout amount",
                        },
                    )
                    await session.commit()
                except Exception:
                    logger.exception(
                        "❌ Failed to mark invalid-amount payout as failed | payout_id=%s",
                        payout_id,
                    )
                    await session.rollback()
                continue

            # ----------------------------------------------------
            # Mark as processing before external provider call
            # Also increment retry metadata
            # ----------------------------------------------------
            try:
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET retry_count = COALESCE(retry_count, 0) + 1,
                            last_retry_at = NOW(),
                            status = 'processing'
                        WHERE id = :pid
                    """),
                    {"pid": payout_id},
                )
                await session.commit()
            except Exception:
                logger.exception("❌ Failed to update retry metadata | payout_id=%s", payout_id)
                await session.rollback()
                continue

            logger.info(
                "📡 Airtime attempt #%s | payout_id=%s | from_status=%s | tg_id=%s | phone=%s | amount=₦%s",
                attempt_no,
                payout_id,
                current_status,
                tg_id,
                phone,
                amount,
            )

            # ----------------------------------------------------
            # Send airtime
            # ----------------------------------------------------
            try:
                res = await send_airtime(phone=phone, amount=amount)

                provider = getattr(res, "provider", None) or "clubkonnect"
                ref = str(getattr(res, "reference", None) or "").strip()
                raw = getattr(res, "raw", None) or {}
                raw_json = json.dumps(raw, ensure_ascii=False)
                payload_text = raw_json[:5000]

                if getattr(res, "success", False):
                    new_status = _resolve_success_status(res)
                    should_retry = False
                else:
                    new_status, should_retry = _classify_failure(res)

                    if should_retry and attempt_no >= MAX_AIRTIME_RETRIES:
                        new_status = "failed_permanent"
                        should_retry = False

                try:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status = :status,
                                sent_at = CASE
                                    WHEN :status IN ('sent', 'completed')
                                    THEN COALESCE(sent_at, NOW())
                                    ELSE sent_at
                                END,
                                completed_at = CASE
                                    WHEN :status = 'completed'
                                    THEN COALESCE(completed_at, NOW())
                                    ELSE completed_at
                                END,
                                provider = :provider,
                                provider_ref = CAST(:provider_ref AS text),
                                provider_reference = CAST(:provider_reference AS varchar),
                                provider_response = CAST(:response AS jsonb),
                                provider_payload = :payload
                            WHERE id = :pid
                        """),
                        {
                            "pid": payout_id,
                            "status": new_status,
                            "provider": provider,
                            "provider_ref": ref or None,
                            "provider_reference": ref or None,
                            "response": raw_json,
                            "payload": payload_text,
                        },
                    )
                    await session.commit()
                except Exception:
                    logger.exception(
                        "❌ DB update failed after airtime send | payout_id=%s | status=%s",
                        payout_id,
                        new_status,
                    )
                    await session.rollback()
                    continue

                # ------------------------------------------------
                # Notifications
                # ------------------------------------------------
                if getattr(res, "success", False):
                    try:
                        if new_status == "completed":
                            user_text = (
                                f"🎉 Your airtime of ₦{amount} has been delivered!\n"
                                f"Phone: {phone}\n"
                                f"Ref: {ref or 'N/A'}"
                            )
                        else:
                            user_text = (
                                f"✅ Your airtime request for ₦{amount} has been accepted.\n"
                                f"Phone: {phone}\n"
                                f"Ref: {ref or 'N/A'}\n\n"
                                "It is being delivered now."
                            )

                        await bot.send_message(
                            chat_id=tg_id,
                            text=user_text,
                            reply_markup=_post_airtime_keyboard(),
                        )
                    except Exception:
                        logger.exception(
                            "⚠️ Failed to notify user success | tg_id=%s | payout_id=%s",
                            tg_id,
                            payout_id,
                        )

                    if ADMIN_USER_ID:
                        try:
                            await bot.send_message(
                                chat_id=ADMIN_USER_ID,
                                text=(
                                    f"✅ Airtime processed ({new_status}): ₦{amount} → {phone}\n"
                                    f"user: {tg_id}\n"
                                    f"payout_id: {payout_id}\n"
                                    f"provider: {provider}\n"
                                    f"ref: {ref or 'N/A'}\n"
                                    f"msg: {getattr(res, 'message', '') or ''}"
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "⚠️ Failed to notify admin success | payout_id=%s",
                                payout_id,
                            )

                else:
                    notify_user = (
                        attempt_no % NOTIFY_USER_ON_FAILURE_EVERY_N_ATTEMPTS == 1
                    )

                    if notify_user:
                        user_msg = (
                            "⚠️ Airtime delivery failed.\n"
                            "We’ll retry automatically if possible. If it persists, contact support.\n\n"
                            "Send or click on /contact to report."
                        )

                        if new_status == "failed_needs_funding":
                            user_msg = (
                                "⚠️ Airtime is delayed due to a temporary service issue.\n"
                                "Your reward is safe and will be processed shortly.\n\n"
                                "If after ten minutes you do not get it, contact support via /contact."
                            )

                        if new_status == "failed_permanent":
                            user_msg = (
                                "⚠️ Airtime delivery could not be completed at the moment.\n"
                                "Please contact support so we can resolve it quickly.\n\n"
                                "Send or click on /contact to report."
                            )

                        try:
                            await bot.send_message(
                                chat_id=tg_id,
                                text=user_msg,
                                parse_mode=ParseMode.HTML,
                                reply_markup=_post_airtime_keyboard(),
                            )
                        except Exception:
                            logger.exception(
                                "⚠️ Failed to notify user failure | tg_id=%s | payout_id=%s",
                                tg_id,
                                payout_id,
                            )

                    notify_admin = (
                        new_status == "failed_needs_funding"
                        or (attempt_no % NOTIFY_ADMIN_ON_FAILURE_EVERY_N_ATTEMPTS == 0)
                    )

                    if ADMIN_USER_ID and notify_admin:
                        try:
                            await bot.send_message(
                                chat_id=ADMIN_USER_ID,
                                text=(
                                    f"❌ Airtime FAILED ({new_status}): ₦{amount} → {phone}\n"
                                    f"user: {tg_id}\n"
                                    f"payout_id: {payout_id}\n"
                                    f"attempt: {attempt_no}/{MAX_AIRTIME_RETRIES}\n"
                                    f"msg: {getattr(res, 'message', '') or ''}\n"
                                    f"raw: {raw}"
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "⚠️ Failed to notify admin failure | payout_id=%s",
                                payout_id,
                            )

            except Exception as e:
                logger.exception(
                    "❌ Exception during airtime sending | payout_id=%s | error=%s",
                    payout_id,
                    e,
                )
                await session.rollback()

                fallback_status = "failed_retryable"
                if attempt_no >= MAX_AIRTIME_RETRIES:
                    fallback_status = "failed_permanent"

                try:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status = :status
                            WHERE id = :pid
                        """),
                        {
                            "pid": payout_id,
                            "status": fallback_status,
                        },
                    )
                    await session.commit()
                except Exception:
                    logger.exception(
                        "❌ Failed to mark payout fallback status | payout_id=%s",
                        payout_id,
                    )
                    await session.rollback()


async def retry_failed_notifications():
    logger.debug("🔁 retry_failed_notifications running (implement as needed)")
    await asyncio.sleep(0.1)


async def retry_failed_notifications_loop():
    while True:
        try:
            await retry_failed_notifications()
        except Exception as e:
            logger.exception("Notifier retry_failed_notifications error: %s", e)
        await asyncio.sleep(RETRY_NOTIFICATIONS_SECONDS)


async def notifier_loop():
    logger.info("🚀 Notifier started (Airtime payouts)...")
    while True:
        try:
            await process_pending_airtime()
        except Exception as e:
            logger.exception("Notifier loop error: %s", e)
        await asyncio.sleep(AIRTIME_LOOP_SECONDS)


if __name__ == "__main__":
    asyncio.run(notifier_loop())


