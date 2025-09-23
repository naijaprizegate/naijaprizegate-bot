# =======================================================
# tasks/__init__.py
# =======================================================
"""
Background tasks package for NaijaPrizeGate Bot.
"""

import asyncio
from typing import List

from helpers import logger
from . import sweeper, notifier, cleanup, periodic_tasks

__all__ = ["start_background_tasks", "stop_background_tasks"]

# Keep track of all running background tasks
_running_tasks: List[asyncio.Task] = []


async def start_background_tasks() -> None:
    """
    Start all background tasks. Call this from FastAPI startup.
    """
    global _running_tasks
    loop = asyncio.get_event_loop()

    _running_tasks = [
        loop.create_task(sweeper.expire_pending_payments_loop(), name="SweeperLoop"),
        loop.create_task(notifier.retry_failed_notifications_loop(), name="NotifierLoop"),
        loop.create_task(cleanup.cleanup_loop(), name="CleanupLoop"),
        loop.create_task(periodic_tasks.start_all_tasks(), name="PeriodicTasks"),
    ]

    logger.info("✅ Background tasks started.")


async def stop_background_tasks() -> None:
    """
    Cancel all running background tasks. Call this from FastAPI shutdown.
    """
    global _running_tasks
    logger.info("🛑 Stopping background tasks...")

    for task in _running_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            logger.debug(f"✅ Task '{task.get_name()}' cancelled cleanly.")
        except Exception as e:
            logger.error(f"⚠️ Error while cancelling task '{task.get_name()}': {e}")

    _running_tasks.clear()
    logger.info("✅ All background tasks stopped.")
