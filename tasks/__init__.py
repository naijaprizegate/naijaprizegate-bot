# =======================================================
# tasks/__init__.py
# ======================================================
"""
Background tasks package for NaijaPrizeGate Bot.
"""

import asyncio
from typing import Optional

from logger import logger
from tasks import sweeper, notifier, cleanup

__all__ = ["register_background_tasks", "sweeper", "notifier", "cleanup"]

def register_background_tasks(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """
    Register background tasks with the given event loop.
    Call this from your FastAPI startup.
    """
    if loop is None:
        loop = asyncio.get_event_loop()

    loop.create_task(sweeper.expire_pending_payments_loop())
    loop.create_task(notifier.retry_failed_notifications_loop())
    loop.create_task(cleanup.cleanup_loop())

    logger.info("Background tasks registered ✅")

