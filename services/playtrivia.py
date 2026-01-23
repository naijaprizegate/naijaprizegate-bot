# ===============================================================
# services/playtrivia.py (SKILL-BASED, LEADERBOARD Top-Tier Campaign Reward VERSION)
# ===============================================================
import os
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from models import Play, User, GameState
from helpers import consume_try
from utils.questions_loader import get_random_question

logger = logging.getLogger(__name__)

# Global threshold: after this many PAID tries in total,
# we trigger a Top-Tier Campaign Reward selection based on leaderboard.
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "50000"))

# Milestone rewards for consistent high performers (deterministic, not random)
# Each key = number of premium (correct) spins a user has reached in total.
# When user hits exactly that count, they get the configured reward.
AIRTIME_MILESTONES = {
    1: 100,    # at 1 correct premium reward tiers → ₦100 airtime
    25: 1000,   # at 25 correct premium reward tiers → ₦1000 airtime
    50: 2000,   # at 50 correct premium reward tiers → ₦2000 airtime
}

NON_AIRTIME_MILESTONES = {
    400: "earpod",   # at 400 premium reward tiers → earpod reward
    800: "speaker", # at 800 premium reward tiers → speaker reward
}


# ===============================================================
# PREMIUM ENTRIES = SKILL / PERFORMANCE POINTS FOR LEADERBOARD
# ===============================================================
async def record_premium_reward_entry(session: AsyncSession, user: User) -> int:
    """
    Store one entry per *premium* spin (i.e. correct answer).

    Each row represents:
      - 1 premium performance point.

    Returns:
        total_premium_rewards_for_user (int)
    """

    # 1️⃣ Insert the premium entry
    await session.execute(
        text("""
            INSERT INTO premium_reward_entries (user_id, tg_id, created_at)
            VALUES (:u, :tg, NOW())
        """),
        {
            "u": str(user.id),
            "tg": user.tg_id,
        }
    )

    # 2️⃣ Count using tg_id (authoritative identifier)
    res = await session.execute(
        text("""
            SELECT COUNT(*)
            FROM premium_reward_entries
            WHERE tg_id = :tg
        """),
        {"tg": user.tg_id}
    )

    return int(res.scalar() or 0)

# ===============================================================
# STEP 1 — Save Trivia Question (unchanged, still pure skill)
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


async def start_playtrivia_question(
    user: User,
    session: AsyncSession,
    context,
    category: str | None = None
) -> dict:
    """
    Start a trivia round by picking a random question from the pool.
    This is *skill-based*: outcome depends on the user's answer.
    """

    # 🔓 RESET premium guard for new question
    context.user_data.pop("premium_recorded", None)

    q = get_random_question(category)
    await save_pending_question(session, user.id, q["id"])

    return {
        "question_text": q["question"],
        "options": q["options"],
        "question_id": q["id"],
    }

# ===============================================================
# STEP 2 — CONSUME TRY + Top-Tier Campaign Reward COUNTER (threshold trigger only)
# ===============================================================
async def consume_and_spin(user: User, session: AsyncSession) -> dict:
    """
    Handles:
    - Try consumption (bonus vs paid)
    - Global PAID try counter
    - Top-Tier Campaign Reward threshold detection

    This function **does not** pick winners. It only:
      - Tracks when we've reached WIN_THRESHOLD for this cycle.
      - Records a Play row for analytics.
    """
    # Consume one try (bonus or paid)
    spin_type = await consume_try(session, user)
    if spin_type is None:
        # No tries left at all
        return {"result": "no_tries", "paid_spin": False, "top_tier_campaign_reward_triggered": False}

    paid_spin = (spin_type == "paid")
    result = "lose"
    top_tier_campaign_reward_triggered = False

    # -----------------------------------------------------------
    # PAID SPIN → Increment global Top-Tier Campaign Reward counter
    # (Only PAID tries count toward WIN_THRESHOLD, so you can
    #  fund rewards from real revenue.)
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
        # Top-Tier Campaign Reward THRESHOLD REACHED? (leaderboard winner later)
        # -------------------------------------------------------
        if new_total is not None and new_total >= WIN_THRESHOLD:

            # Reset global counter for next Top-Tier Campaign Reward cycle
            await session.execute(text("""
                UPDATE global_counter
                SET paid_tries_total = 0
                WHERE id = 1
            """))

            gs.current_cycle += 1
            gs.paid_tries_this_cycle = 0

            top_tier_campaign_reward_triggered = True
            result = "win"   # “win event” at the cycle level

    # Record the spin itself (analytics only, not reward)
    session.add(Play(
        user_id=user.id,
        result=result
    ))

    return {
        "result": result,
        "paid_spin": paid_spin,
        "top_tier_campaign_reward_triggered": top_tier_campaign_reward_triggered,
        "remaining_bonus": user.tries_bonus,
        "remaining_paid": user.tries_paid,
    }


# ===============================================================
# STEP 3 — MILESTONE REWARDS (DETERMINISTIC, NOT RANDOM)
# ===============================================================
async def apply_milestone_reward(
    session: AsyncSession,
    user: User,
    total_premium_rewards: int
) -> str:
    """
    Deterministically award milestone rewards.

    - Airtime: exact milestones
    - Gadgets: threshold-based (>=) with safety guard
    """

    # --------------------------------------------------
    # 1️⃣ NON-AIRTIME MILESTONES (THRESHOLD + GUARD)
    # --------------------------------------------------
    for milestone, reward_type in sorted(
        NON_AIRTIME_MILESTONES.items()
    ):
        if total_premium_rewards >= milestone:

            # Has this reward already been given?
            res = await session.execute(
                text("""
                    SELECT 1
                    FROM non_airtime_winners
                    WHERE user_id = :u
                      AND reward_type = :rtype
                    LIMIT 1
                """),
                {
                    "u": str(user.id),
                    "rtype": reward_type,
                },
            )

            already_awarded = res.scalar() is not None

            if not already_awarded:
                await session.execute(
                    text("""
                        INSERT INTO non_airtime_winners (user_id, tg_id, reward_type)
                        VALUES (:u, :tg, :rtype)
                    """),
                    {
                        "u": str(user.id),
                        "tg": user.tg_id,
                        "rtype": reward_type,
                    },
                )
                return reward_type

    # --------------------------------------------------
    # 2️⃣ AIRTIME MILESTONES (EXACT)
    # --------------------------------------------------
    if total_premium_rewards in AIRTIME_MILESTONES:
        amount = AIRTIME_MILESTONES[total_premium_rewards]
        return f"airtime_{amount}"

    # --------------------------------------------------
    # 3️⃣ NO MILESTONE
    # --------------------------------------------------
    return "none"

# ===============================================================
# STEP 4 - REWARD LOGIC — DECISION ONLY (NO DB INSERTS, NO UI)
# ===============================================================
async def reward_logic(
    session: AsyncSession,
    user: User,
    is_premium_reward: bool = False,
) -> str:
    """
    Pure reward decision engine.

    Responsibilities:
      - Consume a spin/try
      - Detect Top-Tier Campaign Reward threshold
      - Decide outcome symbolically

    NOT allowed:
      ❌ premium progress insertion
      ❌ milestone rewards
      ❌ UI or messaging
    """

    # 1️⃣ Consume a try
    outcome = await consume_and_spin(user, session)

    if outcome.get("result") == "no_tries":
        return "no_tries"

    # 2️⃣ Check Top-Tier Campaign Reward trigger
    if outcome.get("Top-Tier Campaign Reward_triggered"):
        logger.info("🏆 Top-Tier Campaign Reward threshold reached")

        # Select leaderboard winner
        result = await session.execute(
            text("""
                SELECT 
                    user_id,
                    tg_id,
                    COUNT(*) AS points,
                    MIN(created_at) AS first_at
                FROM premium_reward_entries
                GROUP BY user_id, tg_id
                ORDER BY points DESC, first_at ASC
                LIMIT 1
            """)
        )

        row = result.first()

        # Fallback safety
        if row:
            winner_user_id = row.user_id
            winner_tg_id = row.tg_id
        else:
            winner_user_id = user.id
            winner_tg_id = user.tg_id

        # Record campaign win event
        session.add(
            Play(
                user_id=winner_user_id,
                result="Top-Tier Campaign Reward"
            )
        )

        # Reset leaderboard for next cycle
        await session.execute(
            text("DELETE FROM premium_reward_entries")
        )

        # Return symbolic outcome
        if str(winner_user_id) == str(user.id):
            return "Top-Tier Campaign Reward"
        else:
            return "lose"

    # 3️⃣ No special reward this spin
    return "lose"
