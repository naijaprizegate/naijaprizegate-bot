# ================================================================
# tasks/notifier.py
# AUTO-SEND AIRTIME + RETRY FAILED NOTIFICATIONS
# Using ClubKonnect via services/airtime_providers/service.py
# ================================================================
import os
import asyncio
from sqlalchemy import text
from db import get_async_session
from telegram import Bot

from logger import logger

# ✅ Use your provider-agnostic service (currently ClubKonnect)
from services.airtime_providers.service import send_airtime

# -------------------------------------------------------------
# ENVIRONMENT VARIABLES
# -------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

bot = Bot(token=BOT_TOKEN)

# Wait times
AIRTIME_LOOP_SECONDS = 60              # Every minute
RETRY_NOTIFICATIONS_SECONDS = 60 * 60  # Every hour


# ================================================================
# 🔥 PROCESS PENDING AIRTIME PAYOUTS
# ================================================================
async def process_pending_airtime():
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                SELECT id, tg_id, phone_number, amount
                FROM airtime_payouts
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 10;
            """)
        )
        rows = result.fetchall()

        if not rows:
            return

        for row in rows:
            payout_id = row.id
            tg_id = row.tg_id
            phone = row.phone_number
            amount = int(row.amount)

            logger.info(f"📡 Sending airtime (ClubKonnect) → {phone} (₦{amount})")

            try:
                result = await send_airtime(phone=phone, amount=amount)

                # ---------------------------------------------------
                # SUCCESS (or accepted/processing depending on your service.py logic)
                # ---------------------------------------------------
                if result.success:
                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status='sent',
                                sent_at=NOW(),
                                provider=:provider,
                                provider_ref=:ref,
                                provider_response = :response::jsonb,
                                provider_payload=:payload
                            WHERE id = :pid
                        """),
                        {
                            "pid": payout_id,
                            "provider": result.provider,
                            "ref": result.reference,
                            "response": json.dumps(result.raw),
                            "payload": json.dumps(result.raw)[:5000],  # store trimmed payload safely
                        }
                    )
                    await session.commit()

                    # Notify user
                    await bot.send_message(
                        chat_id=tg_id,
                        text=(
                            f"🎉 Your airtime of ₦{amount} has been queued/sent to {phone}!\n"
                            f"Ref: {result.reference or 'N/A'}"
                        )
                    )

                    # Notify admin
                    if ADMIN_USER_ID:
                        await bot.send_message(
                            chat_id=ADMIN_USER_ID,
                            text=(
                                f"✅ Airtime processed (ClubKonnect): ₦{amount} → {phone}\n"
                                f"user: {tg_id}\n"
                                f"ref: {result.reference or 'N/A'}\n"
                                f"msg: {result.message or ''}"
                            )
                        )

                    logger.info(f"✅ Airtime processed → {phone} ref={result.reference}")
                    continue

                # ---------------------------------------------------
                # FAILED AIRTIME
                # ---------------------------------------------------
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status='failed',
                            provider=:provider,
                            provider_ref=:ref,
                            provider_payload=:payload
                        WHERE id = :pid
                    """),
                    {
                        "pid": payout_id,
                        "provider": result.provider,
                        "ref": result.reference or "",
                        "payload": str(result.raw)[:5000],
                    }
                )
                await session.commit()

                logger.error(f"❌ ClubKonnect error: {result.message} | raw={result.raw}")

                await bot.send_message(
                    chat_id=tg_id,
                    text="⚠️ Airtime delivery failed. Please try again later or contact support."
                )

                if ADMIN_USER_ID:
                    await bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=(
                            f"❌ Airtime FAILED (ClubKonnect) → {phone} (₦{amount})\n"
                            f"user: {tg_id}\n"
                            f"msg: {result.message}\n"
                            f"raw: {result.raw}"
                        )
                    )

            except Exception as e:
                logger.exception("❌ Exception during airtime sending")

                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status='failed'
                        WHERE id = :pid
                    """),
                    {"pid": payout_id}
                )
                await session.commit()

                if ADMIN_USER_ID:
                    await bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=f"⚠️ Exception while sending airtime to {phone}: {e}"
                    )


# ================================================================
# 🔁 RETRY FAILED NOTIFICATIONS (placeholder)
# ================================================================
async def retry_failed_notifications():
    logger.debug("🔁 retry_failed_notifications running (implement as needed)")
    await asyncio.sleep(0.1)


# ================================================================
# 🔄 MASTER NOTIFIER LOOP
# ================================================================
async def notifier_loop():
    logger.info("🚀 Notifier started (Airtime + Failed Notifications)...")

    while True:
        try:
            await process_pending_airtime()
            await retry_failed_notifications()
        except Exception as e:
            logger.exception(f"Notifier loop error: {e}")

        await asyncio.sleep(AIRTIME_LOOP_SECONDS)


if __name__ == "__main__":
    asyncio.run(notifier_loop())
