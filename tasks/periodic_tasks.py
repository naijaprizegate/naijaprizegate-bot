# ========================================================
# tasks/periodic_tasks.py
# ========================================================
"""
Periodic background task manager for:
- Airtime auto payouts (Flutterwave Bills API)
- Sweeper for pending payments
- Notification retries
- DB cleanup
"""
import asyncio
import os
from logger import logger
from sqlalchemy import text
from telegram import Bot

from . import sweeper, notifier, cleanup
from services.airtime_service import process_single_airtime_payout
from db import AsyncSessionLocal, get_async_session

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

# ================================================================
# 🔁 BACKGROUND LOOP: PROCESS PENDING AIRTIME PAYOUTS + RETRIES
# ================================================================
async def process_pending_airtime_loop() -> None:
    """
    Periodic worker that:
      - Picks airtime_payouts with status 'pending' or 'failed'
      - Retries each one up to 4 times (using retry_count column)
      - Calls process_single_airtime_payout(...) for actual API call
      - Notifies ADMIN_USER_ID when a payout fails 4 times
    """
    logger.info("📲 Airtime payout worker loop started")

    while True:
        try:
            # Same pattern you use elsewhere: get_async_session() as context manager
            async with get_async_session() as session:
                # 1️⃣ Fetch up to 10 payouts that still deserve a retry
                res = await session.execute(
                    text(
                        """
                        SELECT id, retry_count
                        FROM airtime_payouts
                        WHERE status IN ('pending', 'failed')
                          AND retry_count < 4
                        ORDER BY created_at ASC
                        LIMIT 10
                        """
                    )
                )
                rows = res.fetchall()

                if not rows:
                    # Nothing to do for now
                    await asyncio.sleep(20)
                    continue

                for row in rows:
                    payout_id = row.id
                    old_retry_count = row.retry_count or 0

                    logger.info(
                        f"🔄 Processing airtime payout {payout_id} "
                        f"(retry #{old_retry_count + 1})"
                    )

                    # 2️⃣ Increment retry_count *before* attempting
                    await session.execute(
                        text(
                            """
                            UPDATE airtime_payouts
                            SET retry_count = retry_count + 1,
                                last_retry_at = NOW()
                            WHERE id = :pid
                            """
                        ),
                        {"pid": payout_id},
                    )
                    await session.commit()

                    # 3️⃣ Call the single-payout processor (your existing logic)
                    try:
                        await process_single_airtime_payout(
                            session=session,
                            payout_id=str(payout_id),
                            bot=application.bot,
                            admin_id=ADMIN_USER_ID,
                        )
                    except Exception as e:
                        logger.error(
                            f"❌ Exception in process_single_airtime_payout "
                            f"for {payout_id}: {e}"
                        )

                    # 4️⃣ Check final state; if now failed 4+ times → alert admin
                    res2 = await session.execute(
                        text(
                            """
                            SELECT tg_id, phone_number, amount, status, retry_count
                            FROM airtime_payouts
                            WHERE id = :pid
                            """
                        ),
                        {"pid": payout_id},
                    )
                    row2 = res2.first()
                    if not row2:
                        continue

                    tg_id = row2.tg_id
                    phone = row2.phone_number
                    amount = row2.amount
                    status = row2.status
                    retry_count = row2.retry_count or 0

                    # If we've now failed 4 times and it's still not completed
                    if (
                        status == "failed"
                        and retry_count >= 4
                        and ADMIN_USER_ID is not None
                    ):

                        try:
                            masked = phone[:-4].rjust(len(phone), "•") if phone else "Unknown"

                            bot = Bot(token=BOT_TOKEN)
                            await bot.send_message(
                                chat_id=ADMIN_USER_ID,
                                text=(
                                    "🚨 *Airtime payout permanently failed*\n\n"
                                    f"🆔 Payout ID: `{payout_id}`\n"
                                    f"👤 TG ID: `{tg_id}`\n"
                                    f"📱 Phone: `{masked}`\n"
                                    f"💸 Amount: ₦{amount}\n"
                                    f"🔁 Retries: {retry_count}\n\n"
                                    "Please inspect this payout in the dashboard and decide whether "
                                    "to manually credit or adjust the record."
                                ),
                                parse_mode="Markdown",
                            )
                            logger.warning(
                                f"🚨 Airtime payout {payout_id} permanently failed after {retry_count} retries."
                            )
                        except Exception as e:
                            logger.error(
                                f"⚠️ Failed to notify admin about permanent airtime failure "
                                f"for {payout_id}: {e}"
                            )

            # Sleep between batches
            await asyncio.sleep(20)

        except Exception as e:
            logger.error(f"❌ Airtime payout worker crashed: {e}", exc_info=True)
            # brief sleep before trying again to avoid hot-crashing loop
            await asyncio.sleep(20)

# ------------------------------------------
# Star All Tasks
# --------------------------------------------

async def start_all_tasks(loop: asyncio.AbstractEventLoop = None) -> list[asyncio.Task]:
    """
    Boot all repeating service loops (non-blocking)
    """
    if loop is None:
        loop = asyncio.get_running_loop()

    tasks = [
        loop.create_task(sweeper.expire_pending_payments_loop(), name="SweeperLoop"),
        loop.create_task(notifier.retry_failed_notifications_loop(), name="NotifierLoop"),
        loop.create_task(cleanup.cleanup_loop(), name="CleanupLoop"),
        loop.create_task(process_pending_airtime_loop(), name="AirtimePayoutLoop"),
    ]

    logger.info("🚀 All periodic background tasks are now running")
    return tasks
