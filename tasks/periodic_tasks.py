# ========================================================
# tasks/periodic_tasks.py
# ========================================================
"""
Wrapper for periodic background tasks.
Central place to start sweeper, notifier, cleanup, and airtime payout loops.
"""

import asyncio
import os
from sqlalchemy import text
from logger import logger

from . import sweeper, notifier, cleanup
from services.airtime_service import process_single_airtime_payout

# Import correct async session factory from db.py
from sqlalchemy.ext.asyncio import async_sessionmaker

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))


async def process_pending_airtime_loop() -> None:
    """
    Background worker that auto-processes pending airtime payouts.
    Uses Flutterwave via airtime_service.
    """
    from app import application
    bot = application.bot

    logger.info("📲 Airtime payout worker started (Flutterwave)")

    while True:
        try:
            async with async_sessionmaker() as session:
                async with session.begin():  # auto-commit
                    res = await session.execute(
                        text("""
                            SELECT id
                            FROM airtime_payouts
                            WHERE status = 'pending'
                            ORDER BY created_at ASC
                            LIMIT 10
                        """)
                    )
                    rows = res.fetchall()
                    payout_ids = [str(r[0]) for r in rows]

                if not payout_ids:
                    await asyncio.sleep(10)
                    continue  # <-- never exit loop!

                logger.info(f"🔄 Pending airtime payouts: {len(payout_ids)}")

                # Process each payout (will update DB inside service)
                for payout_id in payout_ids:
                    await process_single_airtime_payout(
                        session, payout_id, bot, ADMIN_USER_ID
                    )

        except Exception as e:
            logger.error(f"❌ Error in airtime payout loop: {e}", exc_info=True)
            await asyncio.sleep(15)


async def start_all_tasks(loop: asyncio.AbstractEventLoop = None) -> list[asyncio.Task]:
    """
    Start all background task loops:
    - Sweeper of stale pending payments
    - Retry failed notifications
    - DB cleanup
    - Airtime auto-credit worker (Flutterwave)
    """
    if loop is None:
        loop = asyncio.get_running_loop()

    tasks = [
        loop.create_task(sweeper.expire_pending_payments_loop(), name="SweeperLoop"),
        loop.create_task(notifier.retry_failed_notifications_loop(), name="NotifierLoop"),
        loop.create_task(cleanup.cleanup_loop(), name="CleanupLoop"),
        loop.create_task(process_pending_airtime_loop(), name="AirtimePayoutLoop"),
    ]

    logger.info("🚀 All periodic tasks started from periodic_tasks.py")
    return tasks
