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
from db import async_sessionmaker

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))


# -------------------------------------------------------
# 🔥 PROCESS PENDING AIRTIME PAYOUTS (BACKGROUND WORKER)
# --------------------------------------------------------
async def process_pending_airtime_loop() -> None:
    """
    Background worker:
      - Polls DB for pending payouts
      - Processes each with its own DB transaction
      - Safe, resilient, async-friendly loop
    """
    from app import application   # lazy import to avoid circulars
    bot = application.bot

    logger.info("📲 Airtime payout worker started (Flutterwave Bills API)")

    while True:
        try:
            # Fetch small batch of pending payouts
            async with async_session_factory() as session:
                res = await session.execute(
                    text("""
                        SELECT id
                        FROM airtime_payouts
                        WHERE status = 'pending'
                        ORDER BY created_at ASC
                        LIMIT 10
                    """)
                )
                payout_ids = [str(row[0]) for row in res.fetchall()]

            if not payout_ids:
                await asyncio.sleep(10)
                continue

            logger.info(f"🔄 Pending airtime payouts: {len(payout_ids)}")

            # Process sequentially to avoid Flutterwave rate-limit issues
            for payout_id in payout_ids:
                try:
                    async with async_session_factory() as payout_session:
                        await process_single_airtime_payout(
                            payout_session, payout_id, bot, ADMIN_USER_ID
                        )
                except Exception as inner_err:
                    logger.error(
                        f"❌ Failed processing payout {payout_id}: {inner_err}",
                        exc_info=True,
                    )
                    await asyncio.sleep(2)  # soft backoff per-item

            await asyncio.sleep(5)  # cooldown before next batch

        except Exception as loop_err:
            logger.error(
                f"🚨 Airtime payout worker crashed: {loop_err}",
                exc_info=True,
            )
            await asyncio.sleep(20)  # global backoff to prevent hammering

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
