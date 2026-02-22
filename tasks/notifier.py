# ================================================================
# tasks/notifier.py
# AUTO-SEND AIRTIME + SMART RETRY + ADMIN ALERTS
# Uses ClubKonnect via services/airtime_providers/service.py
# ================================================================
from __future__ import annotations

import os
import json
import asyncio
from sqlalchemy import text
from telegram import Bot
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
NOTIFY_USER_ON_FAILURE_EVERY_N_ATTEMPTS = 2   # notify user on 1st, 3rd, 5th...
NOTIFY_ADMIN_ON_FAILURE_EVERY_N_ATTEMPTS = 1  # admin gets every failure (change to 2 if noisy)


def _classify_failure(res) -> tuple[str, bool]:
    """
    Returns: (new_status, should_retry)
    Relies on improved send_airtime() returning res.raw with retryable hints,
    but still works even if those fields don't exist.
    """
    raw = (res.raw or {}) if hasattr(res, "raw") else {}
    status = str(raw.get("status") or "").upper().strip()
    msg = str(getattr(res, "message", "") or "").upper().strip()

    # Provider explicitly says insufficient balance
    if status == "INSUFFICIENT_BALANCE" or msg == "INSUFFICIENT_BALANCE":
        return ("failed_needs_funding", False)

    # Retryable hint from provider adapter
    retryable = raw.get("retryable")
    if isinstance(retryable, bool):
        return ("failed_retryable", retryable)

    # Fallback heuristic if retryable flag isn't present
    http_status = raw.get("http_status")
    try:
        http_status = int(http_status) if http_status is not None else 0
    except Exception:
        http_status = 0

    msg_lower = (str(raw.get("message") or "")).lower()
    if http_status >= 500 or "non-json response" in msg_lower or "timeout" in msg_lower:
        return ("failed_retryable", True)

    return ("failed_permanent", False)


async def process_pending_airtime():
    async with get_async_session() as session:
        # Pick payouts that are eligible for sending/retrying
        pick_sql = text(f"""
            WITH picked AS (
                SELECT id
                FROM airtime_payouts
                WHERE
                    (
                        status IN ('pending', 'pending_claim', 'claim_phone_set', 'queued')
                        OR (
                            status IN ('failed', 'failed_retryable')
                            AND retry_count < :max_retries
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
            SELECT a.id, a.tg_id, a.phone_number, a.amount, COALESCE(a.retry_count, 0) AS retry_count
            FROM airtime_payouts a
            JOIN picked p ON p.id = a.id;
        """)

        try:
            result = await session.execute(
                pick_sql,
                {"limit": BATCH_SIZE, "max_retries": MAX_AIRTIME_RETRIES},
            )
            rows = result.fetchall()
        except Exception:
            logger.exception("❌ Failed to pick airtime payouts batch")
            await session.rollback()
            return

        if not rows:
            return

        for row in rows:
            payout_id = row.id
            tg_id = row.tg_id
            phone = row.phone_number
            amount = int(row.amount)
            prev_retry_count = int(row.retry_count or 0)
            attempt_no = prev_retry_count + 1

            # Basic sanity guard: don't call provider without a phone number
            if not phone:
                logger.error(f"❌ Airtime payout missing phone_number payout_id={payout_id} tg_id={tg_id}")
                try:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status='failed_permanent',
                                provider_response=CAST(:response AS jsonb),
                                provider_payload=:payload
                            WHERE id=:pid
                        """),
                        {
                            "pid": payout_id,
                            "response": json.dumps({"status": "error", "message": "Missing phone_number"}, ensure_ascii=False),
                            "payload": "Missing phone_number",
                        },
                    )
                    await session.commit()
                except Exception:
                    logger.exception(f"❌ Failed to mark missing-phone payout as failed payout_id={payout_id}")
                    await session.rollback()
                continue

            # Mark attempt BEFORE external call (good practice)
            try:
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET retry_count = COALESCE(retry_count, 0) + 1,
                            last_retry_at = NOW(),
                            status = 'queued'
                        WHERE id = :pid
                    """),
                    {"pid": payout_id},
                )
                await session.commit()
            except Exception:
                logger.exception(f"❌ Failed to update retry_count/status for payout_id={payout_id}")
                await session.rollback()
                continue

            logger.info(f"📡 Airtime attempt #{attempt_no} → {phone} (₦{amount}) payout_id={payout_id}")

            try:
                res = await send_airtime(phone=phone, amount=amount)

                provider = getattr(res, "provider", None) or "clubkonnect"
                ref = getattr(res, "reference", None) or ""
                raw = getattr(res, "raw", None) or {}
                raw_json = json.dumps(raw, ensure_ascii=False)
                payload_text = raw_json[:5000]

                if getattr(res, "success", False):
                    new_status = "sent"
                    should_retry = False
                else:
                    new_status, should_retry = _classify_failure(res)

                    # If it's retryable but we've hit max retries -> make it permanent
                    if should_retry and attempt_no >= MAX_AIRTIME_RETRIES:
                        new_status = "failed_permanent"
                        should_retry = False

                # ✅ One unified update with safe CAST
                try:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status=:status,
                                sent_at=CASE WHEN :status='sent' THEN COALESCE(sent_at, NOW()) ELSE sent_at END,
                                provider=:provider,
                                provider_ref=:ref,
                                provider_reference=:ref,
                                provider_response=CAST(:response AS jsonb),
                                provider_payload=:payload
                            WHERE id=:pid
                        """),
                        {
                            "pid": payout_id,
                            "status": new_status,
                            "provider": provider,
                            "ref": ref,
                            "response": raw_json,
                            "payload": payload_text,
                        },
                    )
                    await session.commit()
                except Exception:
                    logger.exception(f"❌ DB update failed payout_id={payout_id} status={new_status}")
                    await session.rollback()
                    continue

                # ------------------------
                # Notifications (safe)
                # ------------------------
                if getattr(res, "success", False):
                    # User success
                    try:
                        await bot.send_message(
                            chat_id=tg_id,
                            text=(
                                f"🎉 Your airtime of ₦{amount} has been processed!\n"
                                f"Phone: {phone}\n"
                                f"Ref: {ref or 'N/A'}"
                            ),
                        )
                    except Exception:
                        logger.exception(f"⚠️ Failed to notify user tg_id={tg_id} payout_id={payout_id}")

                    # Admin success
                    if ADMIN_USER_ID:
                        try:
                            await bot.send_message(
                                chat_id=ADMIN_USER_ID,
                                text=(
                                    f"✅ Airtime processed: ₦{amount} → {phone}\n"
                                    f"user: {tg_id}\n"
                                    f"payout_id: {payout_id}\n"
                                    f"provider: {provider}\n"
                                    f"ref: {ref or 'N/A'}\n"
                                    f"msg: {getattr(res, 'message', '') or ''}"
                                ),
                            )
                        except Exception:
                            logger.exception("⚠️ Failed to notify admin")
                else:
                    # Notify user less frequently to reduce spam
                    notify_user = (attempt_no % NOTIFY_USER_ON_FAILURE_EVERY_N_ATTEMPTS == 1)

                    if notify_user:
                        # Use HTML because your message contains <b>...</b>
                        user_msg = (
                            "⚠️ Airtime delivery failed.<br/>"
                            "We’ll retry automatically if possible. If it persists, contact support.<br/><br/>"
                            "Type or click on /start and then click on <b>CONTACT SUPPORT</b> button."
                        )

                        # If funding issue, be honest + reassuring
                        if new_status == "failed_needs_funding":
                            user_msg = (
                                "⚠️ Airtime is delayed due to a temporary service funding issue.<br/>"
                                "Your reward is safe and will be processed shortly.<br/><br/>"
                                "Type or click on /start and then click on <b>CONTACT SUPPORT</b> button."
                            )

                        # If permanent failure, tell them to contact support
                        if new_status == "failed_permanent":
                            user_msg = (
                                "⚠️ Airtime delivery could not be completed at the moment.<br/>"
                                "Please contact support so we can resolve it quickly.<br/><br/>"
                                "Type or click on /start and then click on <b>CONTACT SUPPORT</b> button."
                            )

                        try:
                            await bot.send_message(
                                chat_id=tg_id,
                                text=user_msg,
                                parse_mode=ParseMode.HTML,
                            )
                        except Exception:
                            logger.exception(f"⚠️ Failed to notify user tg_id={tg_id} payout_id={payout_id}")

                    # Notify admin: ALWAYS for funding issues; otherwise configurable
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
                            logger.exception("⚠️ Failed to notify admin")

            except Exception as e:
                logger.exception(f"❌ Exception during airtime sending payout_id={payout_id}: {e}")
                await session.rollback()

                # Mark as retryable failed (do NOT overwrite to permanent immediately)
                try:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status='failed_retryable'
                            WHERE id=:pid
                        """),
                        {"pid": payout_id},
                    )
                    await session.commit()
                except Exception:
                    logger.exception(f"❌ Failed to mark payout as failed_retryable payout_id={payout_id}")
                    await session.rollback()


async def retry_failed_notifications():
    logger.debug("🔁 retry_failed_notifications running (implement as needed)")
    await asyncio.sleep(0.1)


async def retry_failed_notifications_loop():
    while True:
        try:
            await retry_failed_notifications()
        except Exception as e:
            logger.exception(f"Notifier retry_failed_notifications error: {e}")
        await asyncio.sleep(RETRY_NOTIFICATIONS_SECONDS)


async def notifier_loop():
    logger.info("🚀 Notifier started (Airtime payouts)...")
    while True:
        try:
            await process_pending_airtime()
        except Exception as e:
            logger.exception(f"Notifier loop error: {e}")
        await asyncio.sleep(AIRTIME_LOOP_SECONDS)


if __name__ == "__main__":
    asyncio.run(notifier_loop())
