# ================================================================
# tasks/notifier.py
# AUTO-SEND AIRTIME + RETRY FAILED NOTIFICATIONS
# ================================================================
import os
import asyncio
import aiohttp
from sqlalchemy import text
from db import get_async_session
from telegram import Bot

from logger import logger   # ✅ (Your existing logger)

# -------------------------------------------------------------
# ENVIRONMENT VARIABLES
# -------------------------------------------------------------
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

bot = Bot(token=BOT_TOKEN)

# Wait times
AIRTIME_LOOP_SECONDS = 60        # Every minute
RETRY_NOTIFICATIONS_SECONDS = 60 * 60   # Every hour


# ================================================================
# 🔥 SEND AIRTIME USING FLUTTERWAVE
# ================================================================
async def send_airtime_via_flutterwave(phone: str, amount: int):
    """
    Calls Flutterwave Bills API to send airtime.
    """
    url = "https://api.flutterwave.com/v3/bills"
    payload = {
        "country": "NG",
        "customer": phone,
        "amount": amount,
        "type": "AIRTIME"
    }

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            try:
                return await resp.json()
            except:
                return {"status": "failed", "message": "Invalid JSON from Flutterwave"}


# ================================================================
# 🔥 PROCESS PENDING AIRTIME PAYOUTS
# ================================================================
async def process_pending_airtime():
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                SELECT id, user_id, tg_id, phone_number, amount
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
            amount = row.amount

            logger.info(f"📡 Sending airtime → {phone} (₦{amount})")

            try:
                response = await send_airtime_via_flutterwave(phone, amount)

                # ---------------------------------------------------
                # SUCCESSFUL AIRTIME
                # ---------------------------------------------------
                if response.get("status") == "success":

                    await session.execute(
                        text("""
                            UPDATE airtime_payouts
                            SET status='sent', sent_at=NOW()
                            WHERE id = :pid
                        """),
                        {"pid": payout_id}
                    )
                    await session.commit()

                    # Notify user
                    await bot.send_message(
                        chat_id=tg_id,
                        text=f"🎉 Your airtime of ₦{amount} has been successfully sent to {phone}!"
                    )

                    # Notify admin
                    if ADMIN_USER_ID:
                        await bot.send_message(
                            chat_id=ADMIN_USER_ID,
                            text=f"✅ Airtime sent: ₦{amount} → {phone} (user: {tg_id})"
                        )

                    logger.info(f"✅ Airtime delivered → {phone}")
                    continue

                # ---------------------------------------------------
                # FAILED AIRTIME
                # ---------------------------------------------------
                await session.execute(
                    text("""
                        UPDATE airtime_payouts
                        SET status='failed'
                        WHERE id = :pid
                    """),
                    {"pid": payout_id}
                )
                await session.commit()

                logger.error(f"❌ Flutterwave error: {response}")

                await bot.send_message(
                    chat_id=tg_id,
                    text="⚠️ Airtime delivery failed. Admin will retry soon."
                )

                if ADMIN_USER_ID:
                    await bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=f"❌ Airtime FAILED → {phone} (₦{amount})\nResponse: {response}"
                    )

            except Exception as e:
                logger.exception(f"❌ Exception during airtime sending")

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
# 🔁 RETRY FAILED NOTIFICATIONS (your old logic)
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
# 🔄 MASTER NOTIFIER LOOP (runs both tasks)
# ================================================================
async def notifier_loop():
    logger.info("🚀 Notifier started (Airtime + Failed Notifications)...")

    while True:
        try:
            # Process airtime
            await process_pending_airtime()

            # Retry failed notifications separately
            await retry_failed_notifications()

        except Exception as e:
            logger.exception(f"Notifier loop error: {e}")

        await asyncio.sleep(AIRTIME_LOOP_SECONDS)


# ================================================================
# ENTRYPOINT
# ================================================================
if __name__ == "__main__":
    asyncio.run(notifier_loop())
