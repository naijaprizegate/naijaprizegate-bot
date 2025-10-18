# ===============================================================
# services/tryluck.py
# ===============================================================
import os
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from models import Play, User, GameState  # ✅ include GameState
from helpers import consume_try  # ✅ centralize try deduction
from db import get_async_session  # ✅ for safety (if needed)

logger = logging.getLogger(__name__)

WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "14600"))

# ------------------------------------------------------
# Consume a try + spin
# ------------------------------------------------------
async def consume_and_spin(user: User, session: AsyncSession) -> dict:
    """
    Deduct a try (bonus first, else paid) via helpers.consume_try,
    update global counter + GameState if paid, decide win/lose, insert Play row,
    and return structured result.
    NOTE: This function does not commit — caller must commit.
    """

    spin_type = await consume_try(session, user)  # returns "paid", "bonus", or None
    if spin_type is None:
        logger.warning(f"⚠️ User {user.id} ({user.tg_id}) tried to spin with no tries left.")
        return {"result": "no_tries"}

    result = "lose"
    is_winner = False
    paid_spin = (spin_type == "paid")

    # Handle global counter if spin is paid
    if paid_spin:
        # Ensure global counter row exists
        await session.execute(
            text("INSERT INTO global_counter (id, paid_tries_total) VALUES (1, 0) "
                 "ON CONFLICT (id) DO NOTHING")
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

        # ✅ Also increment GameState counters
        gs = await session.get(GameState, 1)
        if not gs:
            # create if missing
            gs = GameState(id=1)
            session.add(gs)
            await session.flush()

        gs.paid_tries_this_cycle += 1
        gs.lifetime_paid_tries += 1

        logger.info(
            f"🏆 Updated GameState → cycle={gs.current_cycle}, "
            f"paid_tries_this_cycle={gs.paid_tries_this_cycle}, "
            f"lifetime_paid_tries={gs.lifetime_paid_tries}"
        )

        # Check if WIN_THRESHOLD reached
        if new_total is not None and new_total >= WIN_THRESHOLD:
            # Reset counter and start new cycle
            await session.execute(text("UPDATE global_counter SET paid_tries_total = 0 WHERE id = 1"))

            gs.current_cycle += 1
            gs.paid_tries_this_cycle = 0  # reset for new cycle
            result = "win"
            is_winner = True

            logger.info(f"🎉 Jackpot triggered! Starting new cycle → #{gs.current_cycle}")

    # Insert play record (timestamp handled by DB default)
    play = Play(user_id=user.id, result=result)
    session.add(play)

    logger.info(
        f"🎰 Spin result for user {user.id} (tg_id={user.tg_id}): {result.upper()} "
        f"[spin_type={spin_type}, paid_spin={paid_spin}, "
        f"remaining_paid={user.tries_paid}, remaining_bonus={user.tries_bonus}]"
    )

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
    Returns simplified outcome: 'no_tries', 'win', or 'lose'.
    """
    outcome = await consume_and_spin(user, session)
    return outcome["result"]
