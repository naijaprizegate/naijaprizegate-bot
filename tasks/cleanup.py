# =======================================================
# tasks/cleanup.py
# =======================================================
"""
Cleanup task: housekeeping (temp files, old logs, etc).
"""

import asyncio
from helpers import logger

CHECK_INTERVAL_SECONDS = 60 * 60 * 6  # every 6h

async def cleanup_loop():
    """Loop that runs cleanup tasks every 6 hours."""
    while True:
        try:
            await cleanup_temp_files()
        except Exception as e:
            logger.exception(f"Cleanup task error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

async def cleanup_temp_files():
    """Placeholder for removing old temp files or rotating logs."""
    logger.debug("Cleanup task running... (implement logic here)")
    await asyncio.sleep(0.1)  # simulate work

