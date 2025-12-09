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
from config import BOT_TOKEN, ADMIN_USER_ID

from . import sweeper, notifier, cleanup
from db import AsyncSessionLocal, get_async_session

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))


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
    ]

    logger.info("🚀 All periodic background tasks are now running")
    return tasks
