# ===============================================================
# services/playtrivia.py (CYCLE-BASED + LOGGED + IDEMPOTENT)
# ===============================================================
import os
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, Literal, Callable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from models import User, GameState

logger = logging.getLogger(__name__)

WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "50000"))

AIRTIME_MILESTONES = {
    1: 100,
    10: 300,
    25: 700,
    50: 1500,
    75: 2500,
    100: 3000,
}

NON_AIRTIME_MILESTONES = {
    500: "earpod",
    1000: "speaker",
}

OutcomeType = Literal[
    "no_tries",
    "lose",
    "none",
    "airtime",
    "gadget",
    "cycle_end",
]


@dataclass
class TriviaOutcome:
    type: OutcomeType
    paid_spin: bool = False
    cycle_id: int = 1
    points: int = 0

    airtime_amount: Optional[int] = None
    gadget: Optional[str] = None

    cycle_ended: bool = False
    winner_tg_id: Optional[int] = None
    winner_user_id: Optional[str] = None
    winner_points: Optional[int] = None


# ---------------------------------------------------------------
# Optional reward audit (safe)
# If you don't have reward_audit_log table, it won't crash.
# ---------------------------------------------------------------
async def _record_reward_audit_safe(
    session: AsyncSession,
    user: User,
    reward_type: str,
    cycle_id: int,
    points: int,
    source: str,
) -> bool:
    """
    Returns True if inserted, False if already existed or table doesn't exist.
    Safe: will not crash if reward_audit_log table is absent.
    IMPORTANT: Uses a SAVEPOINT so failures do NOT abort the outer transaction.
    """
    try:
        async with session.begin_nested():  # ✅ SAVEPOINT
            res = await session.execute(
                text("""
                    INSERT INTO reward_audit_log
                        (user_id, tg_id, reward_type, cycle_id, premium_total, source)
                    VALUES
                        (:u, :tg, :r, :c, :t, :s)
                    ON CONFLICT (cycle_id, user_id, reward_type)
                    DO NOTHING
                    RETURNING id
                """),
                {
                    "u": str(user.id),
                    "tg": int(user.tg_id),
                    "r": reward_type,
                    "c": int(cycle_id),
                    "t": int(points),
                    "s": source,
                },
            )
            return res.scalar_one_or_none() is not None
    except Exception:
        # table missing or constraint differs -> ignore
        return False


# ---------------------------------------------------------------
# GameState / Cycle helpers
# ---------------------------------------------------------------
async def _ensure_game_state(session: AsyncSession) -> GameState:
    gs = await session.get(GameState, 1)
    if not gs:
        gs = GameState(id=1)
        session.add(gs)
        await session.flush()

    if gs.current_cycle is None:
        gs.current_cycle = 1
    if gs.paid_tries_this_cycle is None:
        gs.paid_tries_this_cycle = 0
    if gs.lifetime_paid_tries is None:
        gs.lifetime_paid_tries = 0

    return gs


async def _ensure_cycle_row(session: AsyncSession, cycle_id: int):
    await session.execute(
        text("""
            INSERT INTO cycles (id, started_at)
            VALUES (:c, COALESCE(NOW(), CURRENT_TIMESTAMP))
            ON CONFLICT (id) DO NOTHING
        """),
        {"c": cycle_id},
    )


# ---------------------------------------------------------------
# Points (cycle-based)
# ---------------------------------------------------------------
async def _get_or_create_cycle_points(session: AsyncSession, cycle_id: int, user: User) -> int:
    res = await session.execute(
        text("""
            INSERT INTO user_cycle_stats (cycle_id, user_id, tg_id, points, updated_at)
            VALUES (:c, :u, :tg, 0, NOW())
            ON CONFLICT (cycle_id, user_id) DO NOTHING
            RETURNING points
        """),
        {"c": cycle_id, "u": str(user.id), "tg": int(user.tg_id)},
    )
    inserted = res.scalar_one_or_none()
    if inserted is not None:
        return int(inserted)

    res2 = await session.execute(
        text("""
            SELECT points
            FROM user_cycle_stats
            WHERE cycle_id = :c AND user_id = :u
            LIMIT 1
        """),
        {"c": cycle_id, "u": str(user.id)},
    )
    return int(res2.scalar_one_or_none() or 0)


async def _increment_cycle_points(session: AsyncSession, cycle_id: int, user: User) -> int:
    res = await session.execute(
        text("""
            INSERT INTO user_cycle_stats (cycle_id, user_id, tg_id, points, updated_at)
            VALUES (:c, :u, :tg, 1, NOW())
            ON CONFLICT (cycle_id, user_id)
            DO UPDATE SET
                points = user_cycle_stats.points + 1,
                tg_id = EXCLUDED.tg_id,
                updated_at = NOW()
            RETURNING points
        """),
        {"c": cycle_id, "u": str(user.id), "tg": int(user.tg_id)},
    )
    return int(res.scalar_one())

# -----------------------------------------------------
# Admin Add Cycle Points... (this is for testing only)
# -----------------------------------------------------
async def admin_add_cycle_points(
    session: AsyncSession,
    user: User,
    cycle_id: int,
    delta: int,
) -> int:
    """
    ADMIN TEST ONLY:
    Adds delta points to user_cycle_stats for the given cycle.
    Returns new points.
    """
    await _get_or_create_cycle_points(session, cycle_id, user)

    res = await session.execute(
        text("""
            UPDATE user_cycle_stats
            SET points = points + :d,
                updated_at = NOW()
            WHERE cycle_id = :c AND user_id = :u
            RETURNING points
        """),
        {"d": int(delta), "c": int(cycle_id), "u": str(user.id)},
    )
    return int(res.scalar_one())


# ---------------------------------------------------------------
# Tie-break logging entry (premium_reward_entries)
# ---------------------------------------------------------------
async def _record_premium_entry(session: AsyncSession, cycle_id: int, user: User) -> None:
    await session.execute(
        text("""
            INSERT INTO premium_reward_entries (user_id, tg_id, cycle_id, created_at)
            VALUES (:u, :tg, :c, NOW())
        """),
        {"u": str(user.id), "tg": int(user.tg_id), "c": int(cycle_id)},
    )


# ---------------------------------------------------------------
# Gadget award (idempotent per-cycle)
# ---------------------------------------------------------------
async def _try_award_gadget(session: AsyncSession, cycle_id: int, user: User, reward_type: str) -> bool:
    res = await session.execute(
        text("""
            INSERT INTO non_airtime_winners (user_id, tg_id, reward_type, cycle_id, created_at)
            VALUES (:u, :tg, :r, :c, NOW())
            ON CONFLICT (cycle_id, user_id, reward_type)
            DO NOTHING
            RETURNING id
        """),
        {"u": str(user.id), "tg": int(user.tg_id), "r": reward_type, "c": int(cycle_id)},
    )
    return res.scalar_one_or_none() is not None


# ---------------------------------------------------------------
# Winner selection (max points, tie -> earliest reached max)
# ---------------------------------------------------------------
async def _select_cycle_winner(session: AsyncSession, cycle_id: int) -> Optional[Dict[str, Any]]:
    # max points
    res = await session.execute(
        text("SELECT MAX(points) FROM user_cycle_stats WHERE cycle_id = :c"),
        {"c": cycle_id},
    )
    max_points = res.scalar_one_or_none()
    if not max_points or int(max_points) <= 0:
        return None
    max_points = int(max_points)

    # tied list
    res = await session.execute(
        text("""
            SELECT user_id::text, tg_id, points
            FROM user_cycle_stats
            WHERE cycle_id = :c AND points = :p
        """),
        {"c": cycle_id, "p": max_points},
    )
    tied = res.fetchall()
    if not tied:
        return None

    if len(tied) == 1:
        u, tg, pts = tied[0]
        return {"user_id": u, "tg_id": int(tg), "points": int(pts)}

    # tie-break: time of Nth correct (OFFSET N-1)
    best = None
    best_time = None

    for (user_id, tg_id, pts) in tied:
        res2 = await session.execute(
            text("""
                SELECT created_at
                FROM premium_reward_entries
                WHERE cycle_id = :c AND user_id = :u
                ORDER BY created_at ASC
                OFFSET :off
                LIMIT 1
            """),
            {"c": cycle_id, "u": user_id, "off": max_points - 1},
        )
        reached_time = res2.scalar_one_or_none()
        if reached_time is None:
            continue

        if best_time is None or reached_time < best_time:
            best_time = reached_time
            best = {"user_id": str(user_id), "tg_id": int(tg_id), "points": int(pts)}

    return best or {"user_id": str(tied[0][0]), "tg_id": int(tied[0][1]), "points": int(tied[0][2])}


async def _end_cycle_and_start_new(session: AsyncSession, gs: GameState, winner: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    current_cycle = int(gs.current_cycle or 1)

    logger.warning(
        "[CAMPAIGN] Cycle threshold reached | cycle=%s | paid_tries=%s",
        current_cycle,
        int(gs.paid_tries_this_cycle or 0),
    )

    # close cycle
    await session.execute(
        text("""
            UPDATE cycles
            SET ended_at = NOW(),
                paid_tries_final = :final,
                winner_user_id = :wuid::uuid,
                winner_tg_id = :wtg,
                winner_points = :wpts,
                winner_decided_at = CASE WHEN :wtg IS NULL THEN NULL ELSE NOW() END
            WHERE id = :c
        """),
        {
            "final": int(gs.paid_tries_this_cycle or 0),
            "wuid": winner["user_id"] if winner else None,
            "wtg": winner["tg_id"] if winner else None,
            "wpts": winner["points"] if winner else None,
            "c": current_cycle,
        },
    )

    # new cycle
    new_cycle = current_cycle + 1
    gs.current_cycle = new_cycle
    gs.paid_tries_this_cycle = 0
    await _ensure_cycle_row(session, new_cycle)

    logger.warning("[CAMPAIGN] New cycle started | new_cycle=%s", new_cycle)

    return {"ended_cycle": current_cycle, "new_cycle": new_cycle, "winner": winner}


# ---------------------------------------------------------------
# (Optional) record play row for audit trail
# Safe: if schema differs, it won't crash the reward flow.
# ---------------------------------------------------------------
async def _record_play_safe(session: AsyncSession, user: User, result: str):
    try:
        async with session.begin_nested():  # ✅ SAVEPOINT
            await session.execute(
                text("""
                    INSERT INTO plays (user_id, result, created_at)
                    VALUES (:u, :r, NOW())
                """),
                {"u": str(user.id), "r": result},
            )
    except Exception:
        pass


# ---------------------------------------------------------------
# Public API: Resolve one trivia attempt
# ---------------------------------------------------------------
async def resolve_trivia_attempt(
    session: AsyncSession,
    user: User,
    correct_answer: bool,
    consume_try_fn,
    notify_gadget_win: Optional[Callable[[User, str, int], Any]] = None,
) -> TriviaOutcome:
    """
    ONE attempt = consume one try (bonus first, then paid).

    Paid tries contribute to cycle threshold.
    Correct answers increase cycle points.
    Rewards are based on cycle points milestones.
    Winner is selected when threshold is reached.
    Tie-break: first to reach max points.
    """

    gs = await _ensure_game_state(session)
    cycle_id = int(gs.current_cycle or 1)
    await _ensure_cycle_row(session, cycle_id)

    # 1) consume try
    spin_type = await consume_try_fn(session, user)
    if spin_type is None:
        logger.info("[FLOW] No tries left | tg_id=%s", user.tg_id)
        return TriviaOutcome(type="no_tries", paid_spin=False, cycle_id=cycle_id, points=0)

    paid_spin = (spin_type == "paid")
    logger.info(
        "[FLOW] Try consumed | tg_id=%s | spin_type=%s | cycle=%s",
        user.tg_id, spin_type, cycle_id
    )

    # record play (optional)
    await _record_play_safe(session, user, "spin")

    # 2) update paid try counters if paid
    cycle_ended_now = False
    if paid_spin:
        gs.paid_tries_this_cycle = int(gs.paid_tries_this_cycle or 0) + 1
        gs.lifetime_paid_tries = int(gs.lifetime_paid_tries or 0) + 1

        if gs.paid_tries_this_cycle >= WIN_THRESHOLD:
            cycle_ended_now = True

    # 3) wrong answer -> no points
    if not correct_answer:
        logger.info("[FLOW] Wrong answer | tg_id=%s | cycle=%s", user.tg_id, cycle_id)

        if cycle_ended_now:
            winner = await _select_cycle_winner(session, cycle_id)
            info = await _end_cycle_and_start_new(session, gs, winner)

            return TriviaOutcome(
                type="cycle_end",
                paid_spin=paid_spin,
                cycle_id=cycle_id,
                points=0,
                cycle_ended=True,
                winner_tg_id=info["winner"]["tg_id"] if info["winner"] else None,
                winner_user_id=info["winner"]["user_id"] if info["winner"] else None,
                winner_points=info["winner"]["points"] if info["winner"] else None,
            )

        return TriviaOutcome(type="lose", paid_spin=paid_spin, cycle_id=cycle_id, points=0)

    # 4) correct answer -> increment points + premium entry
    await _get_or_create_cycle_points(session, cycle_id, user)
    new_points = await _increment_cycle_points(session, cycle_id, user)
    await _record_premium_entry(session, cycle_id, user)

    logger.info(
        "[FLOW] Point incremented | tg_id=%s | cycle=%s | points=%s",
        user.tg_id, cycle_id, new_points
    )

    # 5) milestone decision
    if new_points in AIRTIME_MILESTONES:
        amount = AIRTIME_MILESTONES[new_points]
        logger.info(
            "[MILESTONE] Airtime HIT | tg_id=%s | cycle=%s | points=%s | amount=%s",
            user.tg_id, cycle_id, new_points, amount
        )

        # optional audit
        await _record_reward_audit_safe(
            session=session,
            user=user,
            reward_type=f"airtime_{amount}",
            cycle_id=cycle_id,
            points=new_points,
            source="cycle_milestone",
        )

        outcome = TriviaOutcome(
            type="airtime",
            paid_spin=paid_spin,
            cycle_id=cycle_id,
            points=new_points,
            airtime_amount=amount,
        )

    else:
        # ✅ Threshold-based gadget milestone check (handles admin jumps too)
        awarded_outcome = None

        # Sort milestones ascending (e.g., 500 then 1000)
        for milestone, reward in sorted(NON_AIRTIME_MILESTONES.items(), key=lambda x: int(x[0])):
            if new_points >= int(milestone):
                awarded = await _try_award_gadget(session, cycle_id, user, reward)

                if awarded:
                    logger.critical(
                        "🚨 GADGET WIN 🚨 | tg_id=%s | cycle=%s | reward=%s | points=%s",
                        user.tg_id, cycle_id, reward, new_points
                    )

                    # optional audit
                    await _record_reward_audit_safe(
                        session=session,
                        user=user,
                        reward_type=reward,
                        cycle_id=cycle_id,
                        points=new_points,
                        source="cycle_milestone",
                    )

                    # optional admin notifier hook
                    if notify_gadget_win:
                        try:
                            await notify_gadget_win(user, reward, new_points)
                        except Exception:
                            logger.exception(
                                "[ADMIN ALERT FAILED] Gadget win | tg_id=%s reward=%s",
                                user.tg_id, reward
                            )

                    outcome = TriviaOutcome(
                        type="gadget",
                        paid_spin=paid_spin,
                        cycle_id=cycle_id,
                        points=new_points,
                        gadget=reward,
                    )
                    break #  stop after the best eligible milestone (awarded or already taken)

        # If no new gadget was awarded, default to none
        outcome = awarded_outcome or TriviaOutcome(
            type="none",
            paid_spin=paid_spin,
            cycle_id=cycle_id,
            points=new_points,
        )


    # 6) if threshold hit, end cycle (but we keep milestone outcome)
    if cycle_ended_now:
        winner = await _select_cycle_winner(session, cycle_id)
        info = await _end_cycle_and_start_new(session, gs, winner)

        outcome.cycle_ended = True
        outcome.winner_tg_id = info["winner"]["tg_id"] if info["winner"] else None
        outcome.winner_user_id = info["winner"]["user_id"] if info["winner"] else None
        outcome.winner_points = info["winner"]["points"] if info["winner"] else None

        # only override type if there was no milestone
        if outcome.type in ("none", "lose"):
            outcome.type = "cycle_end"

    return outcome

