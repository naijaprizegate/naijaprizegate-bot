# ===============================================
# services/free_trivia_service.py
# ===============================================


from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ===========================================================
# FREE TRIVIA DAILY USAGE
# ===========================================================

DAILY_FREE_TRIVIA_LIMIT = 5
NIGERIA_TIMEZONE = ZoneInfo("Africa/Lagos")


def get_nigeria_today():
    """Return today's calendar date in Nigeria."""
    return datetime.now(NIGERIA_TIMEZONE).date()


async def reserve_free_trivia_game(
    session: AsyncSession,
    user_id: UUID,
) -> int | None:
    """
    Atomically reserve one Free Trivia game.

    Returns:
        int: Number of games started today after reservation.
        None: Daily allowance has already been exhausted.

    The caller controls the database transaction.
    """

    usage_date = get_nigeria_today()

    result = await session.execute(
        text("""
            INSERT INTO public.free_trivia_daily_usage (
                user_id,
                usage_date,
                games_started,
                created_at,
                updated_at
            )
            VALUES (
                :user_id,
                :usage_date,
                1,
                NOW(),
                NOW()
            )
            ON CONFLICT (user_id, usage_date)
            DO UPDATE SET
                games_started =
                    public.free_trivia_daily_usage.games_started + 1,
                updated_at = NOW()
            WHERE
                public.free_trivia_daily_usage.games_started < :daily_limit
            RETURNING games_started
        """),
        {
            "user_id": user_id,
            "usage_date": usage_date,
            "daily_limit": DAILY_FREE_TRIVIA_LIMIT,
        },
    )

    return result.scalar_one_or_none()


async def get_free_trivia_remaining(
    session: AsyncSession,
    user_id: UUID,
) -> int:
    """Return the number of Free Trivia games remaining today."""

    usage_date = get_nigeria_today()

    result = await session.execute(
        text("""
            SELECT games_started
            FROM public.free_trivia_daily_usage
            WHERE user_id = :user_id
              AND usage_date = :usage_date
        """),
        {
            "user_id": user_id,
            "usage_date": usage_date,
        },
    )

    games_started = result.scalar_one_or_none()

    if games_started is None:
        return DAILY_FREE_TRIVIA_LIMIT

    return max(
        0,
        DAILY_FREE_TRIVIA_LIMIT - int(games_started),
    )
