# ========================================================
# tasks/sweeper.py
# ========================================================
"""
Sweeper task: expire old pending payments.
Runs periodically (default: every 24h).
"""

import asyncio
from datetime import datetime, timedelta

from db import get_async_session
from models import Payment
from logger import logger

CHECK_INTERVAL_SECONDS = 60 * 60 * 24  # 24h

async def expire_pending_payments_loop():
    """Loop that periodically expires pending payments older than 24h."""
    while True:
        try:
            await expire_pending_payments()
        except Exception as e:
            logger.exception(f"Sweeper task error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def expire_pending_payments():
    """Mark payments as expired if older than 24 hours."""
    async with get_async_session() as session:
        now = datetime.utcnow()
        expiry_time = now - timedelta(hours=24)
        result = await session.execute(
            Payment.__table__.update()
            .where(Payment.status == "pending")
            .where(Payment.created_at < expiry_time)
            .values(status="expired")
        )
        await session.commit()
        if result.rowcount:
            logger.info(f"Expired {result.rowcount} pending payments.")
        else:
            logger.debug("No pending payments to expire.")

