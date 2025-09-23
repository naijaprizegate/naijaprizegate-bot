# ===============================================================
# services/tryluck.py
# =============================================================
import os
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
        return {"result": "error", "error": "User not found"}

    paid_spin = False
    if user.tries_bonus > 0:
        user.tries_bonus -= 1
    elif user.tries_paid > 0:
        user.tries_paid -= 1
        paid_spin = True
    else:
        return {"result": "no_tries"}

    result = "lose"
    is_winner = False

    if paid_spin:
        # Ensure global counter row exists
        await session.execute(
            text("INSERT INTO global_counter (id, paid_tries_total) VALUES (1, 0) ON CONFLICT (id) DO NOTHING")
        )

        # Atomically increment global counter
        counter_row = await session.execute(
            text("""
                UPDATE global_counter
                SET paid_tries_total = paid_tries_total + 1
                WHERE id = 1
                RETURNING paid_tries_total
            """)
        )
        new_total = counter_row.scalar()

        if new_total is not None and new_total >= WIN_THRESHOLD:
            # Reset counter
            await session.execute(
                text("UPDATE global_counter SET paid_tries_total = 0 WHERE id = 1")
            )
            result = "win"
            is_winner = True

    # Insert play record (timestamp handled by DB default)
    play = Play(
        user_id=user.id,
        result=result,
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


# ------------------------------------------------------
# Compatibility wrapper for handlers
# ------------------------------------------------------
async def spin_logic(session: AsyncSession, user: User) -> str:
    """
    Wrapper so handlers/tryluck.py keeps working.
    Calls consume_and_spin() and returns a simplified outcome.
    """
    outcome = await consume_and_spin(user.id, session)

    if outcome["result"] == "no_tries":
        return "no_tries"
    elif outcome["result"] == "win":
        return "win"
    else:
        return "lose"
