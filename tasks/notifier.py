# ========================================================
# tasks/notifier.py
# =========================================================
"""
Notifier task: retry failed notifications.
"""

import asyncio
from helpers import logger

CHECK_INTERVAL_SECONDS = 60 * 60  # 1h

async def retry_failed_notifications_loop():
    """Loop that retries failed notifications every hour."""
    while True:
        try:
            await retry_failed_notifications()
        except Exception as e:
            logger.exception(f"Notifier task error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def retry_failed_notifications():
    """Placeholder: implement retry logic (e.g., Telegram/Email)."""
    logger.debug("Notification retry task running... (implement logic here)")
    await asyncio.sleep(0.1)  # simulate work

