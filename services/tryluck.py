# ===============================================================
# services/tryluck.py (FINAL — MULTI AIRTIME REWARDS ADDED)
# ===============================================================
import os
import logging
import random
import time
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from models import Play, User, GameState
from helpers import consume_try
from utils.questions_loader import get_random_question
from db import get_async_session

logger = logging.getLogger(__name__)

WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "14600"))


# ===============================================================
# STEP 1 — Save Trivia Question (unchanged)
# ===============================================================
async def save_pending_question(session: AsyncSession, user_id: int, question_id: int):
    await session.execute(
        text("""
            INSERT INTO game_state_question (user_id, question_id, answered)
            VALUES (:u, :q, FALSE)
            ON CONFLICT (user_id)
            DO UPDATE SET question_id = :q, answered = FALSE
        """),
        {"u": user_id, "q": question_id}
    )


async def start_tryluck_question(user: User, session: AsyncSession, category: str = None) -> dict:
    q = get_random_question(category)
    await save_pending_question(session, user.id, q["id"])

    return {
        "question_text": q["question"],
        "options": q["options"],
        "question_id": q["id"],
    }


# ===============================================================
# STEP 2 — PREMIUM VS BASIC REWARD ENGINE (UPDATED AIRTIME)
# ===============================================================
def get_spin_reward(is_premium: bool) -> str:
    """
    Returns:
        none
        airtime_50
        airtime_100
        airtime_200
        earpod
        speaker
    """

    if is_premium:
        # PREMIUM spin percentages
        table = [
            ("airtime_50", 0.08),     # 8%
            ("airtime_100", 0.015),   # 1.5%
            ("airtime_200", 0.005),   # 0.5%

            ("earpod", 0.01),         # 1%
            ("speaker", 0.005),       # 0.5%

            ("none", 0.885)           # 88.5%
        ]

    else:
        # BASIC spin percentages
        table = [
            ("airtime_50", 0.03),     # 3%
            ("airtime_100", 0.007),   # 0.7%
            ("airtime_200", 0.003),   # 0.3%

            ("earpod", 0.002),        # 0.2%
            ("speaker", 0.001),       # 0.1%

            ("none", 0.957)           # 95.7%
        ]

    r = random.random()
    cumulative = 0
    for reward, prob in table:
        cumulative += prob
        if r <= cumulative:
            return reward

    return "none"


# ===============================================================
# STEP 3 — CONSUME TRY + JACKPOT LOGIC (unchanged)
# ===============================================================
async def consume_and_spin(user: User, session: AsyncSession) -> dict:
    spin_type = await consume_try(session, user)
    if spin_type is None:
        return {"result": "no_tries"}

    paid_spin = (spin_type == "paid")
    result = "lose"
    is_jackpot_winner = False

    # ---------- JACKPOT COUNTER (UNCHANGED) ----------
    if paid_spin:
        await session.execute("""
            INSERT INTO global_counter (id, paid_tries_total)
            VALUES (1, 0)
            ON CONFLICT (id) DO NOTHING
        """)

        counter_row = await session.execute("""
            UPDATE global_counter
            SET paid_tries_total = paid_tries_total + 1
            WHERE id = 1
            RETURNING paid_tries_total
        """)
        new_total = counter_row.scalar()

        gs = await session.get(GameState, 1)
        if not gs:
            gs = GameState(id=1)
            session.add(gs)
            await session.flush()

        gs.paid_tries_this_cycle += 1
        gs.lifetime_paid_tries += 1

        if new_total is not None and new_total >= WIN_THRESHOLD:
            await session.execute(
                text("UPDATE global_counter SET paid_tries_total = 0 WHERE id = 1")
            )

            gs.current_cycle += 1
            gs.paid_tries_this_cycle = 0

            result = "win"
            is_jackpot_winner = True

    # Save play
    session.add(Play(user_id=user.id, result=result))

    return {
        "result": result,
        "winner": is_jackpot_winner,
        "paid_spin": paid_spin,
        "remaining_bonus": user.tries_bonus,
        "remaining_paid": user.tries_paid,
    }


# ===============================================================
# STEP 4 — WRAPPER WITH REWARDS + DB RECORD CREATION
# ===============================================================
async def spin_logic(
    session: AsyncSession,
    user: User,
    is_premium_spin: bool = False
) -> str:

    outcome = await consume_and_spin(user, session)

    # -------------------------------------------------------------
    # 🚨 Fix: Protect against "no_tries" return to avoid KeyError
    # -------------------------------------------------------------
    if outcome.get("result") == "no_tries":
        # Treat like loss (prevents crash)
        return "lose"

    # ❌ Bonus tries NEVER produce rewards
    if outcome.get("paid_spin") is False:
        return "lose"

    # ----------------- JACKPOT (UNCHANGED) -----------------------
    if outcome.get("winner") is True:
        return "jackpot"

    # ----------------- SMALL REWARDS -----------------------------
    reward = get_spin_reward(is_premium_spin)

    # ----------------- MULTI-SIZE AIRTIME ------------------------
    if reward.startswith("airtime_"):
        amount = int(reward.split("_")[1])

        await session.execute(
            text("""
                INSERT INTO airtime_payouts (user_id, tg_id, amount, status)
                VALUES (:u, :tg, :amt, 'pending')
            """),
            {
                "u": str(user.id),
                "tg": user.tg_id,
                "amt": amount,
            }
        )

        return reward   # "airtime_50" / "airtime_100" / "airtime_200"

    # ----------------- EARPod -----------------------------------
    if reward == "earpod":
        await session.execute(
            text("""
                INSERT INTO non_airtime_winners (user_id, tg_id, reward_type)
                VALUES (:u, :tg, 'earpod')
            """),
            {"u": str(user.id), "tg": user.tg_id}
        )
        return "earpod"

    # ----------------- SPEAKER ----------------------------------
    if reward == "speaker":
        await session.execute(
            text("""
                INSERT INTO non_airtime_winners (user_id, tg_id, reward_type)
                VALUES (:u, :tg, 'speaker')
            """),
            {"u": str(user.id), "tg": user.tg_id}
        )
        return "speaker"

    # ----------------- NO REWARD --------------------------------
    return "lose"

