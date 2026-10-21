# ==============================================================
# tasks/notifier.py
# AUTO-SEND AIRTIME + SMART RETRY + ADMIN ALERTS
# Uses the configured provider via services.airtime_providers.service
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

from services.airtime_providers.service import (
    send_airtime,
    check_airtime_status,
)



BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

bot = Bot(token=BOT_TOKEN)

AIRTIME_LOOP_SECONDS = 60
RETRY_NOTIFICATIONS_SECONDS = 60 * 60

MAX_AIRTIME_RETRIES = 3
AIRTIME_RETRY_COOLDOWN_MINUTES = 10
BATCH_SIZE = 1

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
    Returns: (new_status, should_retry).

    Ambiguous provider outcomes are held for status checking.
    They must not trigger another purchase automatically.
    """
    raw = getattr(res, "raw", None) or {}
    status = str(raw.get("status") or "").upper().strip()
    msg = str(getattr(res, "message", "") or "").upper().strip()


    if (
        raw.get("provider_state") == "unsupported"
        or raw.get("failure_reason") == "STATUS_CHECK_UNSUPPORTED"
    ):
        return ("needs_review", False)


    if status == "INSUFFICIENT_BALANCE" or msg == "INSUFFICIENT_BALANCE":
        return ("failed_needs_funding", False)

    # These responses do not prove that no purchase occurred.
    uncertain_statuses = {
        "TIMEOUT",
        "NETWORK_ERROR",
        "PROVIDER_ERROR",
        "UNKNOWN_REQUERY_STATUS",
        "REQUERY_HTTP_ERROR",
        "UNKNOWN_NETWORK",
        "UNKNOWN_STATUS",
        "DUPLICATE_REQUEST",
        "DUPLICATE_ORDER",
    }


    if status in uncertain_statuses:
        return ("needs_review", False)

    http_status = raw.get("http_status")
    try:
        http_status = int(http_status) if http_status is not None else 0
    except (TypeError, ValueError):
        http_status = 0

    # A server error may occur after the provider has received the order.
    if http_status >= 500 or "TIMEOUT" in msg:
        return ("needs_review", False)

    retryable = raw.get("retryable")

    # Until requery-before-retry is implemented, pause automatic retries.
    if retryable is True:
        return ("needs_review", False)

    # A definitive non-retryable failure must not enter the retry queue.
    if retryable is False:
        return ("failed_permanent", False)

    return ("failed_permanent", False)


def _resolve_success_status(res) -> str:
    raw = getattr(res, "raw", None) or {}
    status = str(raw.get("status") or "").strip().lower()
    message = str(getattr(res, "message", None) or "").strip().lower()

    # Confirmed completion.
    if status in (
        "completed-api",
        "completed",
        "order_completed",
        "success",
    ) or "order completed" in message:
        return "completed"

    # VTU.ng accepted the request, but completion is not yet confirmed.
    if status in (
        "processing-api",
        "queued-api",
        "initiated-api",
        "pending",
        "processing",
    ):
        return "processing"

    # Only use this fallback for other successful provider results.
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
                            AND NULLIF(TRIM(provider_request_id), '') IS NOT NULL
                            AND COALESCE(retry_count, 0) < :max_retries
                            AND (
                                last_retry_at IS NULL
                                OR last_retry_at < NOW() - INTERVAL '{AIRTIME_RETRY_COOLDOWN_MINUTES} minutes'
                            )
                        )
                        OR (
                            status IN ('needs_review', 'processing')
                            AND phone_number IS NOT NULL
                            AND LOWER(TRIM(COALESCE(network, ''))) IN (
                                'mtn',
                                'airtel',
                                'glo',
                                '9mobile'
                            )
                            AND NULLIF(TRIM(provider_request_id), '') IS NOT NULL
                            AND LOWER(COALESCE(provider, '')) IN (
                                'vtu_ng',
                                'vtu.ng',
                                'vtung',
                                'vtu'
                            )
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
                a.network,
                a.amount,
                a.provider,
                a.provider_request_id,
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
            logger.debug("No airtime payouts ready for processing")
            return

        logger.info("📦 Picked %s airtime payout(s) for processing", len(rows))

        for row in rows:
            payout_id = row.id
            current_status = row.status
            tg_id = row.tg_id
            phone = (row.phone_number or "").strip()
            network = (row.network or "").strip().lower()
            amount = int(row.amount or 0)
            prev_retry_count = int(row.retry_count or 0)
            provider_request_id = (
                str(row.provider_request_id or "").strip()
            )
            had_existing_request_id = bool(provider_request_id)



            allowed_networks = {"mtn", "airtel", "glo", "9mobile"}


            if network not in allowed_networks:
                logger.error(
                    "Invalid or missing airtime network | payout_id=%s | tg_id=%s | network=%s",
                    payout_id,
                    tg_id,
                    network,
                )
                try:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status = 'needs_review',
                                provider_response = CAST(:response AS jsonb),
                                provider_payload = :payload
                            WHERE id = :pid
                            AND status IN (
                                'pending_claim',
                                'failed_retryable',
                                'needs_review',
                                'processing'
                            )
                        """),
                        {
                            "pid": payout_id,
                            "response": json.dumps(
                                {
                                    "status": "INVALID_NETWORK",
                                    "message": (
                                        "Missing or invalid network selection; "
                                        "manual review required"
                                    ),
                                    "network": network,
                                },
                                ensure_ascii=False,
                            ),
                            "payload": "Missing or invalid network selection",
                        },
                    )
                    await session.commit()
                except Exception:
                    logger.exception(
                        "Failed to mark invalid-network payout for review | payout_id=%s",
                        payout_id,
                    )
                    await session.rollback()
                continue



            attempt_no = prev_retry_count + 1

            # ----------------------------------------------------
            # Final sanity guard
            # ----------------------------------------------------
            if not phone:
                logger.warning(
                    "Skipping payout with empty phone_number | "
                    "payout_id=%s | tg_id=%s | status=%s",
                    payout_id,
                    tg_id,
                    current_status,
                )

                # Preserve an existing provider request for manual review.
                validation_status = (
                    "needs_review"
                    if had_existing_request_id
                    else "failed_permanent"
                )

                try:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status = :status,
                                provider_response = CAST(:response AS jsonb),
                                provider_payload = :payload
                            WHERE id = :pid
                              AND status IN (
                                  'pending_claim',
                                  'failed_retryable',
                                  'needs_review',
                                  'processing'
                              )
                        """),
                        {
                            "pid": payout_id,
                            "status": validation_status,
                            "response": json.dumps(
                                {
                                    "status": "INVALID_PHONE",
                                    "message": "Phone number is missing",
                                    "request_id": provider_request_id or None,
                                    "outcome": (
                                        "uncertain"
                                        if had_existing_request_id
                                        else "not_submitted_by_this_attempt"
                                    ),
                                },
                                ensure_ascii=False,
                            ),
                            "payload": "Phone number is missing",
                        },
                    )
                    await session.commit()
                except Exception:
                    logger.exception(
                        "Failed to record empty-phone payout | payout_id=%s",
                        payout_id,
                    )
                    await session.rollback()

                continue

            if amount <= 0:
                logger.error(
                    "Invalid payout amount | payout_id=%s | tg_id=%s | amount=%s",
                    payout_id,
                    tg_id,
                    amount,
                )

                # If a provider request already exists, its outcome
                # may be uncertain. Preserve it for manual review.
                validation_status = (
                    "needs_review"
                    if had_existing_request_id
                    else "failed_permanent"
                )

                try:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status = :status,
                                provider_response = CAST(:response AS jsonb),
                                provider_payload = :payload
                            WHERE id = :pid
                              AND status IN (
                                  'pending_claim',
                                  'failed_retryable',
                                  'needs_review',
                                  'processing'
                              )
                        """),
                        {
                            "pid": payout_id,
                            "status": validation_status,
                            "response": json.dumps(
                                {
                                    "status": "INVALID_AMOUNT",
                                    "message": "Invalid payout amount",
                                    "request_id": provider_request_id or None,
                                    "outcome": (
                                        "uncertain"
                                        if had_existing_request_id
                                        else "not_submitted_by_this_attempt"
                                    ),
                                },
                                ensure_ascii=False,
                            ),
                            "payload": "Invalid payout amount",
                        },
                    )
                    await session.commit()
                except Exception:
                    logger.exception(
                        "Failed to record invalid-amount payout | payout_id=%s",
                        payout_id,
                    )
                    await session.rollback()

                continue

            # ----------------------------------------------------
            # Save a stable request ID before the provider call
            # Reuse the same ID for every retry of this payout
            # ----------------------------------------------------
            provider_request_id = (
                provider_request_id or f"NPG-{payout_id}"
            )

            try:
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET provider_request_id = COALESCE(
                                NULLIF(TRIM(provider_request_id), ''),
                                :request_id
                            ),
                            provider = CASE
                                WHEN NULLIF(TRIM(provider_request_id), '') IS NULL
                                THEN :provider
                                ELSE provider
                            END,
                            retry_count = CASE
                                WHEN NULLIF(TRIM(provider_request_id), '') IS NULL
                                THEN COALESCE(retry_count, 0) + 1
                                ELSE COALESCE(retry_count, 0)
                            END,
                            last_retry_at = NOW(),
                            status = 'processing'
                        WHERE id = :pid
                    """),
                    {
                        "pid": payout_id,
                        "request_id": provider_request_id,
                        "provider": os.getenv("AIRTIME_PROVIDER", "vtu_ng").strip().lower(),
                    },
                )
                await session.commit()
            except Exception:
                logger.exception(
                    "❌ Failed to save request ID and retry metadata | payout_id=%s",
                    payout_id,
                )
                await session.rollback()
                continue

            logger.info(
                "📡 Airtime attempt #%s | payout_id=%s | request_id=%s | from_status=%s | tg_id=%s | phone=%s | amount=₦%s",
                attempt_no,
                payout_id,
                provider_request_id,
                current_status,
                tg_id,
                phone,
                amount,
            )


            # ----------------------------------------------------
            # Send airtime
            # ----------------------------------------------------
            try:
                if had_existing_request_id:
                    # An earlier attempt may have reached the provider.
                    # Never purchase again until we check its original request.
                    original_provider = str(row.provider or "").strip()

                    if not original_provider:
                        raise RuntimeError(
                            "Existing request ID has no recorded provider; "
                            "manual review is required."
                        )

                    logger.info(
                        "Checking existing airtime transaction before retry | "
                        "payout_id=%s | provider=%s | request_id=%s",
                        payout_id,
                        original_provider,
                        provider_request_id,
                    )

                    res = await check_airtime_status(
                        reference=provider_request_id,
                        provider_name=original_provider,
                    )

                else:
                    # This is a new payout with no previous provider request.
                    res = await send_airtime(
                        phone=phone,
                        amount=amount,
                        network=network,
                        request_id=provider_request_id,
                    )


                provider = getattr(res, "provider", None) or "unknown"
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
                    update_result = await session.execute(
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
                              AND status = 'processing'
                              AND provider_request_id = :request_id
                        """),
                        {
                            "pid": payout_id,
                            "request_id": provider_request_id,
                            "status": new_status,
                            "provider": provider,
                            "provider_ref": ref or None,
                            "provider_reference": ref or None,
                            "response": raw_json,
                            "payload": payload_text,
                        },
                    )
                    await session.commit()

                    if update_result.rowcount == 0:
                        logger.warning(
                            "Skipping notifications because the payout "
                            "status changed before the final update | "
                            "payout_id=%s | request_id=%s",
                            payout_id,
                            provider_request_id,
                        )
                        continue

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
                if new_status == "processing":
                    logger.info(
                        "Airtime transaction still processing; awaiting another status check | "
                        "payout_id=%s | request_id=%s",
                        payout_id,
                        provider_request_id,
                    )
                elif getattr(res, "success", False):
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
                            "We’ll retry automatically if possible. If the issue persists, please contact our support team.\n\n"
                            "📩 Type /contact or tap it below to submit a report."
                        )

                        if new_status == "failed_needs_funding":
                            user_msg = (
                                "⚠️ Airtime is delayed due to a temporary service issue.\n"
                                "Your reward is safe and will be processed shortly.\n\n"
                                "If you do not receive it within 10 minutes, type /contact to submit a report."
                            )

                        if new_status == "failed_permanent":
                            user_msg = (
                                "⚠️ Airtime delivery could not be completed at the moment.\n"
                                "Please contact our support team so we can help resolve the issue.\n\n"
                                "📩 Type /contact or tap it below to submit a report."
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
                                    f"⚠️ Airtime FAILED ({new_status}): ₦{amount} → {phone}\n"
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
                    "Exception during airtime processing | payout_id=%s | error=%s",
                    payout_id,
                    e,
                )
                await session.rollback()

                # The provider may have received the request before
                # the exception occurred. Do not automatically retry.
                fallback_status = "needs_review"

                exception_payload = json.dumps(
                    {
                        "status": "UNEXPECTED_EXCEPTION",
                        "message": str(e)[:1000],
                        "request_id": provider_request_id,
                        "outcome": "uncertain",
                    },
                    ensure_ascii=False,
                )

                try:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status = :status,
                                provider_request_id = COALESCE(
                                    NULLIF(TRIM(provider_request_id), ''),
                                    :request_id
                                ),
                                provider_response = CAST(:response AS jsonb),
                                provider_payload = :payload
                            WHERE id = :pid
                              AND status = 'processing'
                        """),
                        {
                            "pid": payout_id,
                            "status": fallback_status,
                            "request_id": provider_request_id,
                            "response": exception_payload,
                            "payload": exception_payload[:5000],
                        },
                    )
                    await session.commit()

                    logger.warning(
                        "Airtime outcome requires review | payout_id=%s | request_id=%s",
                        payout_id,
                        provider_request_id,
                    )

                except Exception:
                    logger.exception(
                        "Failed to save airtime review status | payout_id=%s",
                        payout_id,
                    )
                    await session.rollback()

async def retry_failed_notifications():
    logger.debug("retry_failed_notifications running (implement as needed)")
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
