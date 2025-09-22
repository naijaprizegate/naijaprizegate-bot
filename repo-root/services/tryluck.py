# ===============================================================
# services/tryluck.py
# =============================================================

import os
import random
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from models import Play, User, GlobalCounter

WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "14600"))


# ------------------------------------------------------
# Consume a try + spin
# ------------------------------------------------------
async def consume_and_spin(user_id: str, session: AsyncSession) -> dict:
    """
    Deduct a try (bonus first, else paid), update global counter atomically,
    decide win/lose, insert Play row, and return result.
    """

    # Fetch user row (for tries)
    user: User = await session.get(User, user_id)
    if not user:
        raise ValueError("User not found")

    if user.tries_bonus > 0:
        user.tries_bonus -= 1
        paid_spin = False
    elif user.tries_paid > 0:
        user.tries_paid -= 1
        paid_spin = True
    else:
        raise ValueError("No tries available")

    result = "lose"  # default
    is_winner = False

    if paid_spin:
        # Atomically increment global counter
        counter_row = await session.execute(
            text("""
                UPDATE global_counter
                SET paid_tries_total = paid_tries_total + 1
                RETURNING paid_tries_total
            """)
        )
        new_total = counter_row.scalar()

        if new_total >= WIN_THRESHOLD:
            # Reset counter
            await session.execute(
                text("UPDATE global_counter SET paid_tries_total = 0")
            )
            result = "win"
            is_winner = True
        else:
            # Normal loss
            result = "lose"

    # Insert play record
    play = Play(
        user_id=user.id,
        result=result,
        created_at=datetime.utcnow(),
    )
    session.add(play)

    await session.commit()

    return {
        "result": result,
        "winner": is_winner,
        "paid_spin": paid_spin,
        "remaining_bonus": user.tries_bonus,
        "remaining_paid": user.tries_paid,
    }
