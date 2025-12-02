# ========================================================
# tasks/periodic_tasks.py
# ========================================================
"""
Wrapper for periodic background tasks.
Central place to start sweeper, notifier, cleanup, and airtime payout loops.
"""

import asyncio
from sqlalchemy import text
from logger import logger

from . import sweeper, notifier, cleanup
from services.airtime_service import process_single_airtime_payout
from config import ADMIN_USER_ID
from app import application
from db import async_session_maker


async def process_pending_airtime_loop() -> None:
    """
    Background worker that auto-processes pending airtime payouts.
    Uses Flutterwave via airtime_service.
    """
    bot = application.bot
    admin_id = ADMIN_USER_ID

    logger.info("📲 Airtime payout worker started (Flutterwave)")

    while True:
        try:
            async with async_session_maker() as session:
                res = await session.execute(
                    text("""
                        SELECT id
                        FROM airtime_payouts
                        WHERE status = 'pending'
                        ORDER BY created_at ASC
                        LIMIT 10
                    """)
                )
                row_ids = [str(r[0]) for r in res.fetchall()]

                if not row_ids:
                    await asyncio.sleep(10)
                    continue

                for payout_id in row_ids:
                    await process_single_airtime_payout(
                        session, payout_id, bot, admin_id
                    )

                await session.commit()

        except Exception as e:
            logger.error(f"❌ Error in airtime payout loop: {e}")
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

    logger.info("✅ All periodic tasks started from periodic_tasks.py")
    return tasks
