# ==================================================================
# services/playtrivia.py (SKILL-BASED, LEADERBOARD Top-Tier Campaign Reward VERSION)
# ==================================================================
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
    3: 50,    # at 3 correct premium reward tiers → ₦50 airtime
    25: 100,   # at 25 correct premium reward tiers → ₦100 airtime
    50: 200,   # at 50 correct premium reward tiers → ₦200 airtime
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
    Store one entry per *premium* spin (i.e. correct answer / premium attempt).

    Each row now represents:
      - 1 performance point on the "premium leaderboard".
    Users with more correct premium reward tiers have more entries.

    Returns:
        total_premium_rewards_for_user (int) after inserting this row.
    """
    await session.execute(
        text("""
            INSERT INTO premium_reward_entries (user_id, tg_id, created_at)
            VALUES (:u, :tg, NOW())
        """),
        {"u": str(user.id), "tg": user.tg_id}
    )

    # Count how many premium entries this user has so far
    res = await session.execute(
        text("""
            SELECT COUNT(*) 
            FROM premium_reward_entries
            WHERE user_id = :u
        """),
        {"u": str(user.id)}
    )
    return res.scalar() or 0


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
    category: str | None = None
) -> dict:
    """
    Start a trivia round by picking a random question from the pool.
    This is *skill-based*: outcome depends on the user's answer.
    """
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
    Deterministically award small rewards when a user hits
    specific premium reward tier milestones.

    No randomness here:
      - If total_premium_rewards == 3  → airtime ₦50
      - If total_premium_rewards == 25  → airtime ₦100
      - If total_premium_rewards == 50  → airtime ₦200
      - If total_premium_rewards == 400  → earpod
      - If total_premium_rewards == 800 → speaker
    """

    # 1) Airtime milestones
    if total_premium_rewards in AIRTIME_MILESTONES:
        amount = AIRTIME_MILESTONES[total_premium_rewards]

        if not user.phone_number:
            logger.warning(
                "Airtime milestone reached but no phone number on file: "
                f"user_id={user.id}, spins={total_premium_rewards}"
            )
            return "ask_phone"

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
        return f"airtime_{amount}"

    # 2) Non-airtime milestones (gadgets, accessories)
    if total_premium_rewards in NON_AIRTIME_MILESTONES:
        reward_type = NON_AIRTIME_MILESTONES[total_premium_rewards]

        await session.execute(
            text("""
                INSERT INTO non_airtime_winners (user_id, tg_id, reward_type)
                VALUES (:u, :tg, :rtype)
            """),
            {"u": str(user.id), "tg": user.tg_id, "rtype": reward_type}
        )
        return reward_type

    return "none"


# ===============================================================
# STEP 4 — MAIN LOGIC: Top-Tier Campaign Reward SELECTION + MILESTONE REWARDS
# ===============================================================
async def reward_logic(
    session: AsyncSession,
    user: User,
    is_premium_reward: bool = False
) -> str:
    """
    Unified reward engine.

    Flow:
      1) Consume a try (bonus or paid) & update global counters.
      2) If this was a *premium* spin (correct answer):
         - record a premium entry (skill/score point).
         - check milestone rewards deterministically.
      3) If WIN_THRESHOLD is reached this spin:
         - select Top-Tier Campaign Reward winner from *leaderboard*:
           highest premium score, tie-breaker = earliest achiever.
    """
    # 1) Consume try and update counters
    outcome = await consume_and_spin(user, session)

    # No tries → caller should show "no tries" message
    if outcome.get("result") == "no_tries":
        return "no_tries"

    # 2) PREMIUM PERFORMANCE ENTRY (SKILL POINT)
    total_premium_rewards = None
    if is_premium_reward:
        total_premium_rewards = await record_premium_reward_entry(session, user)
    else:
        total_premium_rewards = None

    # 3) LEADERBOARD-BASED Top-Tier Campaign Reward ON THRESHOLD
    if outcome.get("Top-Tier Campaign Reward_triggered"):
        logger.info("🏆 Top-Tier Campaign Reward threshold reached! Selecting leaderboard winner…")

        # Leaderboard: highest number of premium entries wins.
        # Tie-breaker: earliest created_at (first to reach that score).
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

        # If no entries (edge case), fall back to current user
        if row is None:
            top_tier_campaign_reward_user_id = user.id
            top_tier_campaign_reward_tg_id = user.tg_id
            total_points = 1
            logger.warning(
                "Top-Tier Campaign Reward triggered but premium_reward_entries empty. "
                "Defaulting Top-Tier Campaign Reward winner to current user."
            )
        else:
            top_tier_campaign_reward_user_id = row.user_id
            top_tier_campaign_reward_tg_id = row.tg_id
            total_points = row.points

        # Record Top-Tier Campaign Reward play event
        session.add(Play(user_id=top_tier_campaign_reward_user_id, result="Top-Tier Campaign Reward"))

        # Count total entries for admin info (before reset)
        count_res = await session.execute(
            text("SELECT COUNT(*) FROM premium_reward_entries")
        )
        total_points = count_res.scalar() or 0

        # Reset premium entries for next Top-Tier Campaign Reward cycle (new race)
        await session.execute(text("DELETE FROM premium_reward_entries"))

        # --- # Notify Top-Tier Campaign Reward winner (DM) --- 
        try:
            from telegram import Bot
            from config import BOT_TOKEN, ADMIN_USER_ID  # adjust import path if needed

            bot = Bot(token=BOT_TOKEN)

            await bot.send_message(
                chat_id=top_tier_campaign_reward_tg_id,
                text=(
                    "🎉 *Congratulations!* 🎉\n\n"
                    "You finished this Top-Tier Campaign Reward cycle at the "
                    "*top of the leaderboard* 🏆🔥\n\n"
                    "You are our current *Top-Tier Campaign Reward Winner* and "
                    "will be contacted to claim your prize!"
                ),
                parse_mode="Markdown",
            )

        except Exception as e:
            logger.error(f"📩 Failed to notify Top-Tier winner: {e}")

            # --- Admin notification ---
            await bot.send_message(
                ADMIN_USER_ID,
                "🏆 *Top-Tier Campaign Reward WINNER SELECTED (LEADERBOARD)*\n\n"
                f"👤 User ID: `{top_tier_campaign_reward_user_id}`\n"
                f"📱 TG ID: `{top_tier_campaign_reward_tg_id}`\n"
                f"🎯 Premium Points (cycle): *{total_points}*\n"
                f"🎟️ Total Premium Entries This Cycle: *{total_points}*\n"
                f"🔔 Cycle triggered by spin from User ID: `{user.id}`\n\n"
                "Premium leaderboard has been reset for the next cycle.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"❌ Failed to notify Top-Tier Campaign Reward winner/admin: {e}")

        # If CURRENT spinner is the Top-Tier Campaign Reward winner
        if str(top_tier_campaign_reward_user_id) == str(user.id):
            logger.info(f"🎉 Top-Tier Campaign Reward WON by current spinner user_id={user.id}")
            return "Top-Tier Campaign Reward"

        # Someone else won the Top-Tier Campaign Reward. Current user may still earn
        # a milestone reward below (if premium & at a threshold).

    # 4) SMALL, DETERMINISTIC REWARDS (MILESTONES, NOT RANDOM)
    if is_premium_reward and total_premium_rewards is not None:
        reward_code = await apply_milestone_reward(session, user, total_premium_rewards)
        if reward_code != "none":
            return reward_code

    # No milestone hit, no Top-Tier Campaign Reward for this user → no reward this spin
    return "lose"

