# ========================================================
# tasks/periodic_tasks.py
# ========================================================
"""
Periodic background task manager for:
- Airtime auto payouts
- Battle completion checker
- Sweeper for pending payments
- Notification retries
- DB cleanup
"""
import asyncio
from logger import logger

from . import sweeper, notifier, cleanup, battle_notifier
from bot_instance import bot


async def start_all_tasks(loop: asyncio.AbstractEventLoop = None) -> list[asyncio.Task]:
    """
    Boot all repeating service loops (non-blocking)
    """
    if loop is None:
        loop = asyncio.get_running_loop()

    tasks = [
        loop.create_task(sweeper.expire_pending_payments_loop(), name="SweeperLoop"),
        loop.create_task(notifier.notifier_loop(), name="AirtimeNotifierLoop"),
        loop.create_task(notifier.retry_failed_notifications_loop(), name="RetryFailedNotificationsLoop"),
        loop.create_task(battle_notifier.battle_notifier_loop(bot), name="BattleNotifierLoop"),
        loop.create_task(cleanup.cleanup_loop(), name="CleanupLoop"),
    ]

    logger.info("🚀 All periodic background tasks are now running")
    return tasks

