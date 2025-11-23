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
# PREMIUM SPIN ENTRIES (weighted tickets for jackpot)
# ===============================================================
async def record_premium_spin_entry(session: AsyncSession, user: User):
    """
    Save 1 entry per premium paid spin.
    Each row = 1 ticket toward jackpot.
    Users with more premium spins have more rows (higher chance).
    """
    await session.execute(
        text("""
            INSERT INTO premium_spin_entries (user_id, tg_id, created_at)
            VALUES (:u, :tg, NOW())
        """),
        {"u": str(user.id), "tg": user.tg_id}
    )


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
# STEP 3 — CONSUME TRY + JACKPOT COUNTER (threshold trigger only)
# ===============================================================
async def consume_and_spin(user: User, session: AsyncSession) -> dict:
    """
    Handles:
    - Try consumption (bonus → paid)
    - Global paid try counter
    - Jackpot threshold detection
    - NO jackpot winner selection here (done later)
    """
    # Consume one try
    spin_type = await consume_try(session, user)
    if spin_type is None:
        return {"result": "no_tries"}

    paid_spin = (spin_type == "paid")
    result = "lose"
    jackpot_triggered = False

    # -----------------------------------------------------------
    # PAID SPIN → Increment global jackpot counter
    # -----------------------------------------------------------
    if paid_spin:
        # Ensure the global counter row exists
        await session.execute(text("""
            INSERT INTO global_counter (id, paid_tries_total)
            VALUES (1, 0)
            ON CONFLICT (id) DO NOTHING
        """))

        # Increment global paid tries counter
        counter_row = await session.execute(text("""
            UPDATE global_counter
            SET paid_tries_total = paid_tries_total + 1
            WHERE id = 1
            RETURNING paid_tries_total
        """))
        new_total = counter_row.scalar()

        # Load or create GameState row
        gs = await session.get(GameState, 1)
        if not gs:
            gs = GameState(id=1)
            session.add(gs)
            await session.flush()

        # Update bookkeeping
        gs.paid_tries_this_cycle += 1
        gs.lifetime_paid_tries += 1

        # -------------------------------------------------------
        # JACKPOT THRESHOLD REACHED?
        # -------------------------------------------------------
        if new_total is not None and new_total >= WIN_THRESHOLD:

            # Reset global counter for next jackpot cycle
            await session.execute(text("""
                UPDATE global_counter
                SET paid_tries_total = 0
                WHERE id = 1
            """))

            gs.current_cycle += 1
            gs.paid_tries_this_cycle = 0

            jackpot_triggered = True
            result = "win"   # a jackpot event occurred this cycle

    # -----------------------------------------------------------
    # Save the spin result (NOT jackpot winner)
    # -----------------------------------------------------------
    session.add(Play(
        user_id=user.id,
        result=result
    ))

    # -----------------------------------------------------------
    # Return structured result
    # -----------------------------------------------------------
    return {
        "result": result,
        "paid_spin": paid_spin,
        "jackpot_triggered": jackpot_triggered,
        "remaining_bonus": user.tries_bonus,
        "remaining_paid": user.tries_paid,
    }


# ===============================================================
# STEP 4 — WRAPPER WITH REWARDS + JACKPOT SELECTION + DM + LOGS
# ===============================================================
async def spin_logic(
    session: AsyncSession,
    user: User,
    is_premium_spin: bool = False
) -> str:

    # 1) Consume try and update counters
    outcome = await consume_and_spin(user, session)

    # -------------------------------------------------------------
    # 🚨 Safety: No tries left → treat as loss
    # -------------------------------------------------------------
    if outcome.get("result") == "no_tries":
        return "lose"

    # -------------------------------------------------------------
    # ❌ Bonus spins NEVER produce jackpot or rewards
    # -------------------------------------------------------------
    if not outcome.get("paid_spin"):
        return "lose"

    # -------------------------------------------------------------
    # 2️⃣ RECORD ONE PREMIUM TICKET (only for paid + premium spins)
    # -------------------------------------------------------------
    if is_premium_spin:
        await session.execute(
            text("""
                INSERT INTO premium_spin_entries (user_id, tg_id)
                VALUES (:uid, :tgid)
            """),
            {"uid": str(user.id), "tgid": user.tg_id}
        )

    # -------------------------------------------------------------
    # 3️⃣ JACKPOT LOGIC — threshold reached on THIS spin
    # outcome["winner"] means threshold was hit in consume_and_spin
    # -------------------------------------------------------------
    if outcome.get("winner") is True:

        logger.info("💰 Jackpot threshold reached! Selecting random winner (premium only)…")

        # 🎟️ Weighted-random pick (each row = 1 ticket)
        result = await session.execute(text("""
            SELECT user_id, tg_id
            FROM premium_spin_entries
            ORDER BY RANDOM()
            LIMIT 1
        """))
        row = result.first()

        # If NO premium entries exist (rare)
        if row is None:
            jackpot_user_id = user.id
            jackpot_tg_id = user.tg_id
            logger.warning(
                "⚠️ Jackpot triggered but premium_spin_entries empty. "
                "Defaulting to current spinner."
            )
        else:
            jackpot_user_id = row.user_id
            jackpot_tg_id = row.tg_id

        # Count tickets for admin report
        count_res = await session.execute(text("SELECT COUNT(*) FROM premium_spin_entries"))
        total_tickets = count_res.scalar()

        # Record jackpot play event
        session.add(Play(user_id=jackpot_user_id, result="jackpot"))

        # 🔄 Reset tickets for next jackpot cycle
        await session.execute(text("DELETE FROM premium_spin_entries"))

        # ---------------------------------------------------------
        # 3A — DM jackpot winner
        # ---------------------------------------------------------
        try:
            from app import application
            bot = application.bot

            await bot.send_message(
                jackpot_tg_id,
                "🎉 *Congratulations!* 🎉\n\n"
                "You have been *randomly selected* as the Jackpot Winner! 🏆🔥\n"
                "You will now be able to choose your phone prize.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"❌ Failed to DM jackpot winner: {e}")

        # ---------------------------------------------------------
        # 3B — Admin jackpot log
        # ---------------------------------------------------------
        try:
            await bot.send_message(
                ADMIN_USER_ID,
                f"🏆 *JACKPOT WINNER SELECTED!*\n\n"
                f"👤 User ID: `{jackpot_user_id}`\n"
                f"📱 TG ID: `{jackpot_tg_id}`\n"
                f"🎟️ Total Premium Tickets: *{total_tickets}*\n"
                f"🔔 Triggered By Spin From User ID: `{user.id}`\n\n"
                f"Premium ticket pool has been reset.",
                parse_mode="Markdown"
            )
        except:
            pass

        # ---------------------------------------------------------
        # If CURRENT spinner is the jackpot winner
        # ---------------------------------------------------------
        if str(jackpot_user_id) == str(user.id):
            logger.info(f"🎉 Jackpot WON by current spinner user_id={user.id}")
            return "jackpot"

        # ---------------------------------------------------------
        # If someone else won → current spinner continues
        # ---------------------------------------------------------
        logger.info(
            f"🎉 Jackpot WON by user_id={jackpot_user_id}, "
            f"triggered by spin from user_id={user.id}"
        )
        # Continue into small rewards

    # -------------------------------------------------------------
    # 4️⃣ SMALL REWARDS (only if no jackpot for current user)
    # -------------------------------------------------------------
    reward = get_spin_reward(is_premium_spin)

    # ------------------ MULTI-SIZE AIRTIME -----------------------
    if reward.startswith("airtime_"):
        amount = int(reward.split("_")[1])

        if not user.phone_number:
            logger.error(
                f"❌ Airtime payout blocked: user {user.id} has NO phone number."
            )
            raise ValueError("No phone number on file for airtime payout")

        await session.execute(
            text("""
                INSERT INTO airtime_payouts (user_id, tg_id, phone_number, amount, status)
                VALUES (:u, :tg, :phone, :amt, 'pending')
            """),
            {
                "u": str(user.id),
                "tg": user.tg_id,
                "phone": user.phone_number,
                "amt": amount,
            }
        )
        return reward

    # ---------------------- EARPod -------------------------------
    if reward == "earpod":
        await session.execute(
            text("""
                INSERT INTO non_airtime_winners (user_id, tg_id, reward_type)
                VALUES (:u, :tg, 'earpod')
            """),
            {"u": str(user.id), "tg": user.tg_id}
        )
        return "earpod"

    # ---------------------- SPEAKER ------------------------------
    if reward == "speaker":
        await session.execute(
            text("""
                INSERT INTO non_airtime_winners (user_id, tg_id, reward_type)
                VALUES (:u, :tg, 'speaker')
            """),
            {"u": str(user.id), "tg": user.tg_id}
        )
        return "speaker"

    # ---------------------- NO REWARD ----------------------------
    return "lose"
