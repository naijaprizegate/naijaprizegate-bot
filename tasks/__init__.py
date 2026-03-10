# ====================================================================
# tasks/__init__.py
# ====================================================================
import asyncio
from typing import List
from logger import logger
from . import periodic_tasks

__all__ = ["start_background_tasks", "stop_background_tasks"]

_running_tasks: List[asyncio.Task] = []


async def start_background_tasks() -> None:
    global _running_tasks

    if _running_tasks:
        logger.info("ℹ️ Background tasks already running.")
        return

    loop = asyncio.get_running_loop()
    _running_tasks = await periodic_tasks.start_all_tasks(loop)
    logger.info("✅ Background tasks started.")


async def stop_background_tasks() -> None:
    global _running_tasks

    if not _running_tasks:
        logger.info("ℹ️ No background tasks to stop.")
        return

    logger.info("🛑 Stopping background tasks...")

    for task in _running_tasks:
        task.cancel()

    for task in _running_tasks:
        try:
            await task
        except asyncio.CancelledError:
            logger.debug("✅ Task '%s' cancelled cleanly.", task.get_name())
        except Exception as e:
            logger.error("⚠️ Error while cancelling task '%s': %s", task.get_name(), e)

    _running_tasks.clear()
    logger.info("✅ All background tasks stopped.")
