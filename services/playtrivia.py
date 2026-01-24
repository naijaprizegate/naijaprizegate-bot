# ===============================================================
# services/playtrivia.py (OVERHAULED, ATOMIC, LOGGED VERSION)
# ===============================================================
import os
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from models import Play, User, GameState
from helpers import consume_try
from utils.questions_loader import get_random_question

logger = logging.getLogger(__name__)

WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "50000"))

AIRTIME_MILESTONES = {
    1: 100,
    25: 1000,
    50: 2000,
}

NON_AIRTIME_MILESTONES = {
    400: "earpod",
    800: "speaker",
}

#----------------------------
# Reward Logic
# ---------------------------
async def reward_logic(session, user, is_premium: bool) -> str:
    """
    Main reward entrypoint used by handlers.
    DO NOT put UI logic here.
    """

    # 1️⃣ Deduct trivia attempt (bonus → paid)
    try_type = await consume_try(session, user)
    if try_type is None:
        return "no_tries"

    # 2️⃣ Handle premium reward tracking
    if is_premium:
        # premium insert + milestone handled elsewhere
        return "premium"

    # 3️⃣ Standard (non-premium) reward path
    return "standard"

# ---------------------------------------
# REWARD AUDIT / IDEMPOTENCY HELPERS
# ---------------------------------------
async def record_reward_audit(
    session: AsyncSession,
    user: User,
    reward_type: str,
    premium_total: int,
    source: str,
) -> bool:
    """
    Idempotent reward recorder.
    Returns True if recorded, False if already exists.
    """
    res = await session.execute(
        text("""
            INSERT INTO reward_audit_log
                (user_id, tg_id, reward_type, premium_total, source)
            VALUES
                (:u, :tg, :r, :t, :s)
            ON CONFLICT (user_id, reward_type)
            DO NOTHING
            RETURNING id
        """),
        {
            "u": str(user.id),
            "tg": user.tg_id,
            "r": reward_type,
            "t": premium_total,
            "s": source,
        },
    )

    return res.scalar() is not None

#----------------------------------------------
# NOTIFY ADMIN GADGET WIN
# ---------------------------------------------
async def notify_admin_gadget_win(user: User, reward: str, total: int):
    logger.critical(
        "🚨 GADGET WIN 🚨 | tg_id=%s | reward=%s | premium_total=%s",
        user.tg_id,
        reward,
        total,
    )

    # OPTIONAL:
    # await bot.send_message(
    #     ADMIN_CHAT_ID,
    #     f"🎁 Gadget Won!\n"
    #     f"User: {user.tg_id}\n"
    #     f"Reward: {reward}\n"
    #     f"Premium Total: {total}"
    # )

# ===============================================================
# PREMIUM ENTRY (SINGLE SOURCE OF TRUTH)
# ===============================================================
async def record_premium_entry_and_count(
    session: AsyncSession,
    user: User
) -> int:
    """
    Atomically:
      - Insert premium entry
      - Count total entries for user
    """

    await session.execute(
        text("""
            INSERT INTO premium_reward_entries (user_id, tg_id, created_at)
            VALUES (:u, :tg, NOW())
        """),
        {"u": str(user.id), "tg": user.tg_id},
    )

    res = await session.execute(
        text("""
            SELECT COUNT(*)
            FROM premium_reward_entries
            WHERE tg_id = :tg
        """),
        {"tg": user.tg_id},
    )

    total = int(res.scalar() or 0)
    logger.info(
        "[PREMIUM] Entry recorded | tg_id=%s | total=%s",
        user.tg_id,
        total,
    )
    return total


# ===============================================================
# QUESTION FLOW (UNCHANGED)
# ===============================================================
async def save_pending_question(session: AsyncSession, user_id: int, question_id: int):
    await session.execute(
        text("""
            INSERT INTO game_state_question (user_id, question_id, answered)
            VALUES (:u, :q, FALSE)
            ON CONFLICT (user_id)
            DO UPDATE SET question_id = :q, answered = FALSE
        """),
        {"u": user_id, "q": question_id},
    )


async def start_playtrivia_question(
    user: User,
    session: AsyncSession,
    context,
    category: str | None = None,
) -> dict:
    context.user_data.pop("premium_recorded", None)
    q = get_random_question(category)
    await save_pending_question(session, user.id, q["id"])

    return {
        "question_text": q["question"],
        "options": q["options"],
        "question_id": q["id"],
    }


# ===============================================================
# SPIN CONSUMPTION + GLOBAL CAMPAIGN COUNTER
# ===============================================================
async def consume_and_spin(user: User, session: AsyncSession) -> dict:
    spin_type = await consume_try(session, user)
    if spin_type is None:
        return {"status": "no_tries"}

    paid_spin = spin_type == "paid"
    top_tier_triggered = False

    if paid_spin:
        await session.execute(text("""
            INSERT INTO global_counter (id, paid_tries_total)
            VALUES (1, 0)
            ON CONFLICT (id) DO NOTHING
        """))

        res = await session.execute(text("""
            UPDATE global_counter
            SET paid_tries_total = paid_tries_total + 1
            WHERE id = 1
            RETURNING paid_tries_total
        """))
        new_total = res.scalar()

        gs = await session.get(GameState, 1)
        if not gs:
            gs = GameState(id=1)
            session.add(gs)
            await session.flush()

        gs.paid_tries_this_cycle += 1
        gs.lifetime_paid_tries += 1

        if new_total and new_total >= WIN_THRESHOLD:
            logger.warning("[CAMPAIGN] Top-tier threshold reached")

            await session.execute(text("""
                UPDATE global_counter
                SET paid_tries_total = 0
                WHERE id = 1
            """))

            gs.current_cycle += 1
            gs.paid_tries_this_cycle = 0
            top_tier_triggered = True

    session.add(Play(user_id=user.id, result="spin"))
    return {
        "status": "ok",
        "paid_spin": paid_spin,
        "top_tier_triggered": top_tier_triggered,
    }


# ===============================================================
# MILESTONE ENGINE (FIXED + AUDITED)
# ===============================================================
async def apply_milestone_reward(
    session: AsyncSession,
    user: User,
    total: int,
) -> str:
    # ----------------------------------------------------------
    # 1️⃣ NON-AIRTIME (GADGETS) — THRESHOLD + IDEMPOTENT
    # ----------------------------------------------------------
    for milestone, reward in sorted(NON_AIRTIME_MILESTONES.items()):
        if total >= milestone:

            # Guard: already awarded?
            res = await session.execute(
                text("""
                    SELECT 1
                    FROM non_airtime_winners
                    WHERE user_id = :u
                      AND reward_type = :r
                    LIMIT 1
                """),
                {"u": str(user.id), "r": reward},
            )

            if res.scalar():
                logger.debug(
                    "[MILESTONE] Gadget already awarded | user=%s reward=%s",
                    user.tg_id,
                    reward,
                )
                continue  # keep checking higher milestones safely

            # ✅ 1️⃣ AUDIT FIRST (single source of truth)
            audit_created = await record_reward_audit(
                session=session,
                user=user,
                reward_type=reward,
                premium_total=total,
                source="premium_milestone",
            )

            if not audit_created:
                logger.warning(
                    "[MILESTONE] Audit already exists, skipping award | user=%s reward=%s",
                    user.tg_id,
                    reward,
                )
                continue

            # ✅ 2️⃣ RECORD WINNER
            await session.execute(
                text("""
                    INSERT INTO non_airtime_winners
                    (user_id, tg_id, reward_type)
                    VALUES (:u, :tg, :r)
                """),
                {"u": str(user.id), "tg": user.tg_id, "r": reward},
            )

            logger.info(
                "[MILESTONE] Non-airtime milestone HIT | user=%s reward=%s total=%s",
                user.tg_id,
                reward,
                total,
            )

            # ✅ 3️⃣ ADMIN ALERT (SIDE EFFECT LAST)
            try:
                await notify_admin_gadget_win(user, reward, total)
            except Exception:
                logger.exception(
                    "[ADMIN ALERT FAILED] Gadget win | user=%s reward=%s",
                    user.tg_id,
                    reward,
                )

            return reward

    # ----------------------------------------------------------
    # 2️⃣ AIRTIME — EXACT MATCH (NO AUDIT HERE BY DESIGN)
    # ----------------------------------------------------------
    if total in AIRTIME_MILESTONES:
        amount = AIRTIME_MILESTONES[total]

        logger.info(
            "[MILESTONE] Airtime milestone HIT | user=%s amount=%s total=%s",
            user.tg_id,
            amount,
            total,
        )

        return f"airtime_{amount}"

    # ----------------------------------------------------------
    # 3️⃣ NO MILESTONE
    # ----------------------------------------------------------
    return "none"


# ===============================================================
# FINAL ORCHESTRATOR (THIS FIXES EVERYTHING)
# ===============================================================
async def resolve_trivia_reward(
    session: AsyncSession,
    user: User,
    correct_answer: bool,
) -> str:
    """
    This is the ONLY function that decides rewards.
    """

    spin = await consume_and_spin(user, session)
    if spin.get("status") == "no_tries":
        return "no_tries"

    if not correct_answer:
        logger.info("[FLOW] Wrong answer | no premium entry")
        return "lose"

    # ---- Correct answer = premium entry
    total = await record_premium_entry_and_count(session, user)

    reward = await apply_milestone_reward(session, user, total)

    logger.info(
        "[FLOW] Final reward outcome resolved | outcome=%s",
        reward,
    )

    return reward
