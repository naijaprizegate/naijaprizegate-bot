# ================================================================
# tasks/notifier.py
# AUTO-SEND AIRTIME + RETRY FAILED NOTIFICATIONS
# Uses ClubKonnect via services/airtime_providers/service.py
# ================================================================
from __future__ import annotations

import os
import json
import asyncio
from sqlalchemy import text
from telegram import Bot

from db import get_async_session
from logger import logger
from services.airtime_providers.service import send_airtime

# -------------------------------------------------------------
# ENVIRONMENT VARIABLES
# -------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

bot = Bot(token=BOT_TOKEN)

# Loop intervals
AIRTIME_LOOP_SECONDS = 60              # every minute
RETRY_NOTIFICATIONS_SECONDS = 60 * 60  # every hour

# Airtime retry policy
MAX_AIRTIME_RETRIES = 3
AIRTIME_RETRY_COOLDOWN_MINUTES = 10
BATCH_SIZE = 10


# ================================================================
# 🔥 PROCESS PENDING/RETRYABLE AIRTIME PAYOUTS (safe + idempotent-ish)
# ================================================================
async def process_pending_airtime():
    """
    - Picks rows with status 'pending', OR 'failed' that are eligible for retry.
    - Uses FOR UPDATE SKIP LOCKED to avoid double-processing across workers.
    - Increments retry_count and sets last_retry_at before calling provider.
    """
    async with get_async_session() as session:
        # 1) Pick a batch safely (prevents double-payout if multiple workers run)
        pick_sql = text(f"""
            WITH picked AS (
                SELECT id
                FROM airtime_payouts
                WHERE
                    (
                        status = 'pending'
                        OR (
                            status = 'failed'
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
            SELECT a.id, a.tg_id, a.phone_number, a.amount, a.retry_count
            FROM airtime_payouts a
            JOIN picked p ON p.id = a.id;
        """)

        result = await session.execute(
            pick_sql,
            {"limit": BATCH_SIZE, "max_retries": MAX_AIRTIME_RETRIES},
        )
        rows = result.fetchall()
        if not rows:
            return

        for row in rows:
            payout_id = row.id
            tg_id = row.tg_id
            phone = row.phone_number
            amount = int(row.amount)
            retry_count = int(row.retry_count or 0)

            # 2) Mark attempt time + increment retry count BEFORE external call
            await session.execute(
                text("""
                    UPDATE airtime_payouts
                    SET retry_count = COALESCE(retry_count, 0) + 1,
                        last_retry_at = NOW()
                    WHERE id = :pid
                """),
                {"pid": payout_id},
            )
            await session.commit()

            logger.info(f"📡 Airtime attempt #{retry_count+1} → {phone} (₦{amount}) payout_id={payout_id}")

            try:
                res = await send_airtime(phone=phone, amount=amount)

                provider = res.provider or "clubkonnect"
                ref = res.reference or ""
                raw = res.raw or {}
                raw_json = json.dumps(raw, ensure_ascii=False)
                payload_text = raw_json[:5000]

                # SUCCESS / ACCEPTED
                if res.success:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status='sent',
                                sent_at=COALESCE(sent_at, NOW()),
                                provider=:provider,
                                provider_ref=:ref,
                                provider_response=:response::jsonb,
                                provider_payload=:payload
                            WHERE id=:pid
                        """),
                        {
                            "pid": payout_id,
                            "provider": provider,
                            "ref": ref,
                            "response": raw_json,
                            "payload": payload_text,
                        },
                    )
                    await session.commit()

                    # Notify user
                    await bot.send_message(
                        chat_id=tg_id,
                        text=(
                            f"🎉 Your airtime of ₦{amount} has been processed!\n"
                            f"Phone: {phone}\n"
                            f"Ref: {ref or 'N/A'}"
                        ),
                    )

                    # Notify admin
                    if ADMIN_USER_ID:
                        await bot.send_message(
                            chat_id=ADMIN_USER_ID,
                            text=(
                                f"✅ Airtime processed: ₦{amount} → {phone}\n"
                                f"user: {tg_id}\n"
                                f"payout_id: {payout_id}\n"
                                f"provider: {provider}\n"
                                f"ref: {ref or 'N/A'}\n"
                                f"msg: {res.message or ''}"
                            ),
                        )

                    logger.info(f"✅ Airtime processed payout_id={payout_id} ref={ref}")
                    continue

                # HARD FAILURE
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status='failed',
                            provider=:provider,
                            provider_ref=:ref,
                            provider_response=:response::jsonb,
                            provider_payload=:payload
                        WHERE id=:pid
                    """),
                    {
                        "pid": payout_id,
                        "provider": provider,
                        "ref": ref,
                        "response": raw_json,
                        "payload": payload_text,
                    },
                )
                await session.commit()

                logger.error(f"❌ Airtime FAILED payout_id={payout_id} msg={res.message} raw={raw}")

                await bot.send_message(
                    chat_id=tg_id,
                    text=(
                        "⚠️ Airtime delivery failed.\n"
                        "We’ll retry automatically if possible. If it persists, contact support."
                    ),
                )

                if ADMIN_USER_ID:
                    await bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=(
                            f"❌ Airtime FAILED: ₦{amount} → {phone}\n"
                            f"user: {tg_id}\n"
                            f"payout_id: {payout_id}\n"
                            f"msg: {res.message}\n"
                            f"raw: {raw}"
                        ),
                    )

            except Exception as e:
                logger.exception(f"❌ Exception during airtime sending payout_id={payout_id}: {e}")

                # Mark failed (will retry later if eligible)
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status='failed'
                        WHERE id=:pid
                    """),
                    {"pid": payout_id},
                )
                await session.commit()

                if ADMIN_USER_ID:
                    await bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=f"⚠️ Exception while sending airtime to {phone} (payout_id={payout_id}): {e}",
                    )


# ================================================================
# 🔁 RETRY FAILED NOTIFICATIONS (placeholder)
# ================================================================
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


# ================================================================
# 🔄 AIRTIME LOOP (runs every minute)
# ================================================================
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
