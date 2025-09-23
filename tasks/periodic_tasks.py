# ========================================================
# tasks/periodic_tasks.py
# ========================================================
"""
Wrapper for periodic background tasks.
Central place to start sweeper, notifier, and cleanup loops.
"""

import asyncio
from logger import logger
from . import sweeper, notifier, cleanup

async def start_all_tasks(loop: asyncio.AbstractEventLoop = None) -> list[asyncio.Task]:
    """
    Start all background task loops: sweeper, notifier, cleanup.
    Returns a list of created tasks.
    """
    if loop is None:
        loop = asyncio.get_running_loop()  # safer in Python 3.13+

    tasks = [
        loop.create_task(sweeper.expire_pending_payments_loop(), name="SweeperLoop"),
        loop.create_task(notifier.retry_failed_notifications_loop(), name="NotifierLoop"),
        loop.create_task(cleanup.cleanup_loop(), name="CleanupLoop"),
    ]

    logger.info("✅ All periodic tasks started from periodic_tasks.py")
    return tasks
