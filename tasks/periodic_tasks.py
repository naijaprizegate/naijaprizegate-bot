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

from . import sweeper, notifier, cleanup
from services.airtime_service import process_single_airtime_payout
from db import AsyncSessionLocal

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))


# -------------------------------------------------------
# 🔥 PROCESS PENDING AIRTIME PAYOUTS (BACKGROUND WORKER)
# --------------------------------------------------------
async def process_pending_airtime_loop() -> None:
    """
    Background worker that auto-processes pending airtime payouts.
    Uses Flutterwave Bills API.
    Retries failed payouts up to 4 times.
    """
    from app import application
    bot = application.bot

    logger.info("📲 Airtime payout worker started (Flutterwave Bills API)")

    while True:
        try:
            # FIXED 🔥 correct session init
            async with AsyncSessionLocal() as session:

                async with session.begin():
                    res = await session.execute(
                        text("""
                            SELECT id
                            FROM airtime_payouts
                            WHERE status IN ('pending', 'failed')
                            AND retry_count < 4
                            ORDER BY created_at ASC
                            LIMIT 10
                        """)
                    )
                    rows = res.fetchall()
                    payout_ids = [str(r[0]) for r in rows]

                if not payout_ids:
                    await asyncio.sleep(10)
                    continue

                logger.info(f"🔄 Pending airtime payouts: {len(payout_ids)}")

                for payout_id in payout_ids:
                    try:
                        await process_single_airtime_payout(
                            session,
                            payout_id,
                            bot,
                            ADMIN_USER_ID
                        )
                    except Exception as e:
                        logger.error(f"⚠️ Error processing payout {payout_id}: {e}")

        except Exception as e:
            logger.error(f"❌ Airtime payout worker crashed: {e}", exc_info=True)

        await asyncio.sleep(15)

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
