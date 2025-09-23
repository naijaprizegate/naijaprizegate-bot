# =======================================================
# tasks/__init__.py
# =======================================================
"""
Background tasks package for NaijaPrizeGate Bot.
Provides a unified entrypoint for starting all periodic tasks.
"""

from helpers import logger
from . import sweeper, notifier, cleanup, periodic_tasks

__all__ = [
    "start_background_tasks",
    "sweeper",
    "notifier",
    "cleanup",
    "periodic_tasks",
]


async def start_background_tasks():
    """
    Unified entrypoint: starts all background tasks
    defined in periodic_tasks.py.
    Call this in app.py on startup.
    """
    await periodic_tasks.start_all_tasks()
    logger.info("Background tasks started ✅")
