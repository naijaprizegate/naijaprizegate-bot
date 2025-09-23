# ========================================================
# tasks/periodic_tasks.py
# ========================================================
"""
Wrapper for periodic background tasks.
This file exists so app.py can safely import `periodic_tasks`
without causing ImportError.
"""

import asyncio
from helpers import logger
from . import sweeper, notifier, cleanup

async def start_all_tasks(loop: asyncio.AbstractEventLoop = None) -> None:
    """
    Start all background task loops: sweeper, notifier, cleanup.
    """
    if loop is None:
        loop = asyncio.get_event_loop()

    loop.create_task(sweeper.expire_pending_payments_loop())
    loop.create_task(notifier.retry_failed_notifications_loop())
    loop.create_task(cleanup.cleanup_loop())

    logger.info("✅ All periodic tasks started from periodic_tasks.py")

