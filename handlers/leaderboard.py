# ===============================================================
# handlers/leaderboard.py  — Public Quiz Leaderboard (Skill-Based)
# ===============================================================

import os
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from telegram.error import BadRequest
from sqlalchemy import select, func, desc

from db import get_async_session
from models import (
    PremiumRewardEntry,
    User,
    GameState,
    UserCycleStat,
)  # PremiumRewardEntry = quiz entry log

from services.playtrivia import (
    AIRTIME_MILESTONES,
    NON_AIRTIME_MILESTONES,
)

LEADERBOARD_PAGE_SIZE = 10

# WIN_THRESHOLD: total paid questions needed this cycle before prize is awarded
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "0"))


# ---------------------------------------------------------
# 🏅 Reward Rank helper (Reward Season Rank)
# ---------------------------------------------------------
def _reward_rank(points: int) -> str:
    """
    Returns the player's Reward Rank based on
    Premium Points earned in the current Reward Season.
    """

    if points >= 10000:
        return "👑 Grandmaster"

    if points >= 7500:
        return "🏆 Legend"

    if points >= 5000:
        return "💎 Diamond III"

    if points >= 3500:
        return "💎 Diamond II"

    if points >= 2500:
        return "💎 Diamond I"

    if points >= 1800:
        return "🥇 Platinum III"

    if points >= 1200:
        return "🥇 Platinum II"

    if points >= 800:
        return "🥇 Platinum I"

    if points >= 500:
        return "🥈 Gold III"

    if points >= 350:
        return "🥈 Gold II"

    if points >= 250:
        return "🥈 Gold I"

    if points >= 150:
        return "🥉 Silver III"

    if points >= 100:
        return "🥉 Silver II"

    if points >= 60:
        return "🥉 Silver I"

    if points >= 30:
        return "⭐ Bronze III"

    if points >= 15:
        return "⭐ Bronze II"

    if points >= 5:
        return "⭐ Bronze I"

    if points >= 1:
        return "🎓 Rookie"

    return "🌱 Beginner"


# ---------------------------------------------------------
# 🎯 Next Reward Helper
# ---------------------------------------------------------
def _next_reward(points: int):
    """
    Returns information about the player's next milestone reward.
    """

    milestones = []

    # Airtime rewards
    for milestone, amount in AIRTIME_MILESTONES.items():
        milestones.append(
            (
                milestone,
                f"💳 ₦{amount:,} Airtime",
            )
        )

    # Physical rewards
    for milestone, reward in NON_AIRTIME_MILESTONES.items():
        milestones.append(
            (
                milestone,
                f"🎁 {reward}",
            )
        )

    milestones.sort(key=lambda x: x[0])

    for milestone, reward in milestones:

        if points < milestone:

            return {
                "reward": reward,
                "target": milestone,
                "remaining": milestone - points,
            }

    return {
        "reward": "👑 Season Champion",
        "target": None,
        "remaining": 0,
    }

# ---------------------------------------------------------
# 📆 Streak helper (quiz activity days)
# ---------------------------------------------------------
def _compute_streaks(dates) -> tuple[int, int]:
    """
    Given a list of datetime objects (quiz activity timestamps for a user),
    return (current_streak_days, best_streak_days) based on consecutive days.
    """
    if not dates:
        return 0, 0

    # Unique dates (no duplicates), sorted
    day_list = sorted({d.astimezone(timezone.utc).date() for d in dates})
    best = 1
    current = 1

    for prev, curr in zip(day_list, day_list[1:]):
        if (curr - prev).days == 1:
            current += 1
            best = max(best, current)
        else:
            current = 1

    # Current streak is the streak ending on the last recorded day
    return current, best


# ---------------------------------------------------------
# 🏆 LEADERBOARD ROUTER
# ---------------------------------------------------------
async def leaderboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Router for all leaderboard callbacks.

    Callback patterns handled:
      - leaderboard:show          → default (This Week, page 1)
      - leaderboard:week:1        → This Week, page 1
      - leaderboard:cycle:2       → This Cycle, page 2
    """
    query = update.callback_query
    await query.answer()

    data = query.data or "leaderboard:show"
    parts = data.split(":")

    # Initial button: "leaderboard:show"
    if len(parts) == 2 and parts[1] == "show":
        scope = "week"   # default view
        page = 1
    else:
        # e.g. ["leaderboard", "week", "2"]
        scope = parts[1] if len(parts) > 1 else "week"
        try:
            page = int(parts[2]) if len(parts) > 2 else 1
        except ValueError:
            page = 1

    await leaderboard_render(update, context, scope=scope, page=page)


# ---------------------------------------------------------
# 🏆 LEADERBOARD RENDERER (skill / quiz performance)
# ---------------------------------------------------------
async def leaderboard_render(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    scope: str = "week",
    page: int = 1,
):
    """
    Render a leaderboard page with:
      - Tabs: This Week / This Cycle
      - Top users by quiz activity (performance  entries)
      - ranks
      - Your personal stats + streaks in footer
      - Button to view full “My Achievements” screen

    All wording here is framed as *quiz performance / activity*,
    not luck, betting, or gambling.
    """
    tg_user = update.effective_user
    now = datetime.now(timezone.utc)

    # -----------------------
    # Scope filter
    # -----------------------
    filter_clause = None
    scope_label = ""

    if scope == "week":
        start = now - timedelta(days=7)
        filter_clause = PremiumRewardEntry.created_at >= start
        scope_label = "🔥 This Week (last 7 days)"
    else:
        scope = "cycle"
        scope_label = "🏆 Reward Season"

    async with get_async_session() as session:
        # ----- Base query for leaderboard -----

        if scope == "week":

            base_q = select(
                PremiumRewardEntry.user_id,
                func.count(PremiumRewardEntry.id).label("points"),
            )

            if filter_clause is not None:
                base_q = base_q.where(filter_clause)

            base_q = base_q.group_by(PremiumRewardEntry.user_id)

        else:

            gs = await session.get(GameState, 1)

            current_cycle = gs.current_cycle if gs else 1

            base_q = (
                select(
                    UserCycleStat.user_id,
                    UserCycleStat.points.label("points"),
                )
                .where(
                    UserCycleStat.cycle_id == current_cycle
                )
            )

        # ----- Totals -----
        total_q = select(func.count(PremiumRewardEntry.id))
        distinct_q = select(func.count(func.distinct(PremiumRewardEntry.user_id)))
        if filter_clause is not None:
            total_q = total_q.where(filter_clause)
            distinct_q = distinct_q.where(filter_clause)

        total_points = (await session.execute(total_q)).scalar() or 0
        distinct_users = (await session.execute(distinct_q)).scalar() or 0

        # ----- Page of top users -----
        offset = max(page - 1, 0) * LEADERBOARD_PAGE_SIZE
        if scope == "week":
            page_q = (
                base_q
                .order_by(func.count(PremiumRewardEntry.id).desc())
                .offset(offset)
                .limit(LEADERBOARD_PAGE_SIZE)
            )
        else:
            page_q = (
                base_q
                .order_by(UserCycleStat.points.desc())
                .offset(offset)
                .limit(LEADERBOARD_PAGE_SIZE)
            )
        rows = (await session.execute(page_q)).all()

        if not rows and page != 1:
            return await leaderboard_render(update, context, scope=scope, page=1)

        # ----- Load user objects -----
        user_ids = [uid for (uid, _) in rows]
        users_by_id = {}
        if user_ids:
            users_res = await session.execute(
                select(User).where(User.id.in_(user_ids))
            )
            for u in users_res.scalars():
                users_by_id[u.id] = u

        # ----- Viewer info -----
        viewer_db_user = None
        viewer_user_id = None
        if tg_user:
            res_me = await session.execute(
                select(User).where(User.tg_id == tg_user.id)
            )
            viewer_db_user = res_me.scalars().first()
            if viewer_db_user:
                viewer_user_id = str(viewer_db_user.id)

        my_points = 0
        my_rank = None
        current_streak = 0
        best_streak = 0

        if viewer_user_id:

            if scope == "week":

                my_count_q = select(
                    func.count(PremiumRewardEntry.id)
                ).where(
                    PremiumRewardEntry.user_id == viewer_user_id
                )

                if filter_clause is not None:
                    my_count_q = my_count_q.where(
                        filter_clause
                    )

                my_points = (
                    await session.execute(my_count_q)
                ).scalar() or 0

            else:

                gs = await session.get(GameState, 1)

                current_cycle = gs.current_cycle if gs else 1

                my_points_q = select(
                    UserCycleStat.points
                ).where(
                    UserCycleStat.user_id == viewer_user_id,
                    UserCycleStat.cycle_id == current_cycle,
                )

                my_points = (
                    await session.execute(my_points_q)
                ).scalar() or 0

            if my_points > 0:
                subq = base_q.subquery()
                better_q = select(func.count()).select_from(subq).where(
                    subq.c.points > my_points
                )
                better_count = (await session.execute(better_q)).scalar() or 0
                my_rank = better_count + 1

                streak_dates_res = await session.execute(
                    select(PremiumRewardEntry.created_at).where(
                        PremiumRewardEntry.user_id == viewer_user_id
                    )
                )
                dates = [row[0] for row in streak_dates_res.fetchall()]
                current_streak, best_streak = _compute_streaks(dates)

        next_reward_info = _next_reward(my_points)

        # ----- Cycle progress for trust & merit messaging -----
        # Uses GameState.paid_tries_this_cycle (paid questions only)
        paid_this_cycle = 0
        if WIN_THRESHOLD > 0:
            gs = await session.get(GameState, 1)
            if gs and getattr(gs, "paid_tries_this_cycle", None) is not None:
                paid_this_cycle = gs.paid_tries_this_cycle

    # ----- Build leaderboard text -----
    text_lines = []
    text_lines.append("🏆 <b>NaijaPrizeGate Quiz Leaderboard</b>")
    text_lines.append(f"{scope_label}\n")
    
    if viewer_user_id:
        rank_me = _reward_rank(my_points)
        text_lines.append("")
        text_lines.append("━━━━━━━━━━━━━━━━━━")
        text_lines.append("👤 <b>YOUR SEASON DASHBOARD</b>")
        text_lines.append("━━━━━━━━━━━━━━━━━━")
        text_lines.append("")
        if my_points == 0:
            text_lines.append(
                "• You have not earned any Premium Points this season yet. "
                "Answer more questions to climb the Reward Season leaderboard!  🚀"
            )
        else:
            rank_text = f"#{my_rank}" if my_rank is not None else "N/A"
            text_lines.append(f"🏅 <b>Reward Rank</b>\n{rank_me}")
            text_lines.append("")
            text_lines.append(f"⭐ <b>Premium Points</b>\n{my_points}")
            text_lines.append("")
            text_lines.append(f"🏆 <b>Leaderboard Position</b>\n{rank_text}")
            text_lines.append("")
            text_lines.append(
                f"🔥 <b>Current Activity Streak</b>\n{current_streak} day(s)"
            )
            text_lines.append("")
            text_lines.append(
                f"⚡ <b>Best Activity Streak</b>\n{best_streak} day(s)"
            )
            text_lines.append("")
            

            text_lines.append("")
            text_lines.append("━━━━━━━━━━━━━━━━━━")
            text_lines.append("🎯 <b>YOUR NEXT REWARD</b>")
            text_lines.append("━━━━━━━━━━━━━━━━━━")
            text_lines.append("")

            text_lines.append(
                f"🎁 <b>Next Reward</b>\n{next_reward_info['reward']}"
            )

            if next_reward_info["target"] is not None:

                text_lines.append("")
                text_lines.append(
                    f"🏁 <b>Unlocks At</b>\n"
                    f"{next_reward_info['target']} Premium Points"
                )

                text_lines.append("")
                text_lines.append(
                    f"🚀 You're only <b>{next_reward_info['remaining']}</b> "
                    f"Premium Points away from unlocking your next reward!"
                )

            else:

                text_lines.append("")
                text_lines.append(
                    "👑 You've unlocked every milestone reward "
                    "this Reward Season!"
                )

            text_lines.append("")
            text_lines.append(
                "🏆 Keep answering Premium Questions correctly "
                "to unlock more rewards, climb the leaderboard, "
                "and compete to become the <b>👑 Season Champion</b> "
                "and win the Grand Prize!"
            )

            text_lines.append("")
            

            achievements = []

            if my_points >= 1:
                achievements.append(
                    "🎉 <b>First Premium Point</b>\n"
                    "You earned your first Premium Point."
                )

            if my_points >= 10:
                achievements.append(
                    "🎯 <b>Consistent Player</b>\n"
                    "You have earned 10+ Premium Points."
                )

            if my_points >= 25:
                achievements.append(
                    "🔥 <b>Dedicated Challenger</b>\n"
                    "You have earned 25+ Premium Points."
                )

            if best_streak >= 3:
                achievements.append(
                    f"⚡ <b>Streak Builder</b>\n"
                    f"You have maintained a {best_streak}-day Activity Streak."
                )

            if achievements:

                text_lines.append("")
                text_lines.append("━━━━━━━━━━━━━━━━━━")
                text_lines.append("🏅 <b>ACHIEVEMENT HIGHLIGHTS</b>")
                text_lines.append("━━━━━━━━━━━━━━━━━━")
                text_lines.append("")

                for a in achievements:
                    text_lines.append(f"• {a}")
                    text_lines.append("")

    
        if paid_this_cycle >= WIN_THRESHOLD:
            # 🍾 Winner lock state (automatic backend logic)
            text_lines.append(
                "🔒 Prize unlocked — The Season Champion is now being awarded!"
            )
        else:
            text_lines.append("")
            text_lines.append("━━━━━━━━━━━━━━━━━━")
            text_lines.append("👑 <b>CURRENT REWARD SEASON</b>")
            text_lines.append("━━━━━━━━━━━━━━━━━━")
            text_lines.append("")

            text_lines.append(
                "The player ranked <b>#1</b> when this Reward Season ends\n"
                "becomes the <b>👑 Season Champion</b>\n"
                "and wins the Grand Prize.\n\n"
                "📱 <b>iPhone 17 Pro Max</b>\n\n"
                "📱 <b>Samsung Galaxy S26 Ultra</b>\n\n"
                "📱 <b>Samsung Z Flip 6</b>\n\n"
                "🎧 <b>AirPods</b>\n\n"
                "🔊 <b>Bluetooth Speakers</b>\n\n\n\n"
                "🏆 Every correct Premium Question moves you closer to the top!\n\n"
            )

    text_lines.append("**************")
    text_lines.append("\n✔ 100% Skill-Based — no gambling or chance involved.")

    text_lines.append(
        "\nℹ️ Weekly view shows the last 7 days only. \n\n"
        "\nℹ️Cycle view covers the current competition cycle."
    )
    text_lines.append(
        "\n📌 Rankings are based on your quiz activity and knowledge performance."
    )

    # ----------------------------------------
    # 🏆 Top Players
    # ----------------------------------------

    text_lines.append("")
    text_lines.append("━━━━━━━━━━━━━━━━━━")
    text_lines.append("🏆 <b>TOP PLAYERS</b>")
    text_lines.append("━━━━━━━━━━━━━━━━━━")
    text_lines.append("")

    if not rows:
        text_lines.append(
            "No players have earned Premium Points in this period yet."
        )
    else:

        for index, (user_id, points) in enumerate(
            rows,
            start=offset + 1,
        ):

            player = users_by_id.get(user_id)

            if player:

                if getattr(player, "username", None):
                    name = f"@{player.username}"
                else:
                    name = f"Player #{str(player.tg_id)[-4:]}"
            else:
                name = "Unknown Player"

            rank = _reward_rank(points)

            if index == 1:
                medal = "🥇"
            elif index == 2:
                medal = "🥈"
            elif index == 3:
                medal = "🥉"
            else:
                medal = f"{index}."

            text_lines.append(
                f"{medal} {name}  {rank}"
            )

    # Navigation hint back to main menu
    text_lines.append("")
    text_lines.append(
        "➡️ Click /start to go back to the main menu."
    )


    full_text = "\n".join(text_lines)

    # ----- Keyboard -----
    tabs_row = [
        InlineKeyboardButton(
            ("🔥 This Week ✅" if scope == "week" else "🔥 This Week"),
            callback_data="leaderboard:week:1",
        ),
        InlineKeyboardButton(
            ("🏆 This Season ✅" if scope == "cycle" else "🏆 This Season"),
            callback_data="leaderboard:cycle:1",
        ),
    ]

    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                "⬅️ Prev", callback_data=f"leaderboard:{scope}:{page-1}"
            )
        )
    if len(rows) == LEADERBOARD_PAGE_SIZE:
        nav_row.append(
            InlineKeyboardButton(
                "Next ➡️", callback_data=f"leaderboard:{scope}:{page+1}"
            )
        )

    kb_rows = [tabs_row]
    if nav_row:
        kb_rows.append(nav_row)
    kb_rows.append(
        [InlineKeyboardButton("📜 View My Achievements", callback_data="my_achievements")]
    )

    keyboard = InlineKeyboardMarkup(kb_rows)

    # ---------- Reply or Edit (patched!) ----------
    if update.callback_query:
        msg = update.callback_query.message
        # Micro-guard: avoid edit when text is identical
        if msg and msg.text == full_text:
            return

        try:
            await update.callback_query.edit_message_text(
                full_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return  # Ignore harmless re-click
            raise
    else:
        await update.message.reply_text(
            full_text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


# ---------------------------------------------------------
# 📜 FULL “MY ACHIEVEMENTS” SCREEN (quiz-focused)
# ---------------------------------------------------------
async def my_achievements_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows a dedicated achievements screen for the current user.
    Based on PremiumRewardEntry, treated as *performance  entries / points*.
    """
    tg_user = update.effective_user
    query = update.callback_query
    await query.answer()

    async with get_async_session() as session:
        # Find this user in DB
        res_me = await session.execute(
            select(User).where(User.tg_id == tg_user.id)
        )
        db_user = res_me.scalars().first()

        if not db_user:
            return await query.edit_message_text(
                "⚠️ No account data found.\nUse /start to get registered first.",
                parse_mode="HTML",
            )

        user_id = str(db_user.id)

        # All-time performance  entries/points
        total_points_all = (
            await session.execute(
                select(func.count(PremiumRewardEntry.id)).where(
                    PremiumRewardEntry.user_id == user_id
                )
            )
        ).scalar() or 0

        # Last 7 days quiz points (for extra context)
        now = datetime.now(timezone.utc)
        start_week = now - timedelta(days=7)
        points_last_7 = (
            await session.execute(
                select(func.count(PremiumRewardEntry.id)).where(
                    PremiumRewardEntry.user_id == user_id,
                    PremiumRewardEntry.created_at >= start_week,
                )
            )
        ).scalar() or 0

        # Streaks (based on ALL performance  entries)
        streak_dates_res = await session.execute(
            select(PremiumRewardEntry.created_at).where(
                PremiumRewardEntry.user_id == user_id
            )
        )
        dates = [row[0] for row in streak_dates_res.fetchall()]
        current_streak, best_streak = _compute_streaks(dates)

    rank = _reward_rank(total_points_all)

    # Build achievements text
    lines = []
    lines.append("📜 <b>My Quiz Achievements</b>\n")
    lines.append(
        f"👤 <b>User:</b> @{tg_user.username}"
        if tg_user.username
        else "👤 <b>User:</b> You"
    )
    lines.append("")
    lines.append(
        f"🎟️ <b>Total Premium Points (all-time):</b> {total_points_all}"
    )
    lines.append(
        f"🔥 <b>Last 7 Days:</b> {points_last_7} Premium Point(s) earned"
    )
    lines.append(f"🏅 <b>Current Reward Rank:</b> {rank}")
    lines.append(f"⚡ <b>Current Activity Streak:</b> {current_streak} day(s)")
    lines.append(f"🏆 <b>Best Activity Streak:</b> {best_streak} day(s)\n")

    # Milestone-style achievements (quiz-based)
    achievements = []
    if total_points_all >= 1:
        achievements.append("🎉 <b>First Challenge</b> — You completed your first performance  round!")
    if total_points_all >= 10:
        achievements.append("🎯 <b>Consistent Player</b> — 10+ Premium  Points collected.")
    if total_points_all >= 25:
        achievements.append("🔥 <b>Dedicated Challenger</b> — 25+ Premium  Points.")
    if total_points_all >= 50:
        achievements.append("💎 <b>Elite Learner</b> — 50+ Premium  Points.")
    if total_points_all >= 100:
        achievements.append("👑 <b>Quiz Master</b> — 100+ Premium  Points.")
    if best_streak >= 3:
        achievements.append(f"⚡ <b>Streak Builder</b> — {best_streak}+ days of quiz activity in a row.")
    if best_streak >= 7:
        achievements.append("🔥 <b>Weekly Warrior</b> — 7 days of non-stop quiz activity.")

    if achievements:
        lines.append("<b>Unlocked Milestones</b>")
        for a in achievements:
            lines.append(f"• {a}")
    else:
        lines.append("<b>Unlocked Milestones</b>")
        lines.append(
            "• None yet — keep playing quizzes and earning points to unlock your first rank! 🚀"
        )

    # Optional: hint upcoming milestones (static text)
    lines.append("\n<b>Next Milestones</b>")
    lines.append("• 10 quiz points → <b>Consistent Player</b>")
    lines.append("• 25 quiz points → <b>Dedicated Challenger</b>")
    lines.append("• 3-day activity streak → <b>Streak Builder</b>")

    lines.append(
        "\n📌 All progress here reflects your quiz activity and knowledge performance."
    )

    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Leaderboard", callback_data="leaderboard:show")]
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------
# 🔧 Register leaderboard handlers
# ---------------------------------------------------------
def register_leaderboard_handlers(application):
    # Optional: if you ever want a /leaderboard command:
    application.add_handler(CommandHandler("leaderboard", leaderboard_handler))

    # Leaderboard button from /start, /help, fallback:
    # callback_data="leaderboard:show"
    application.add_handler(
        CallbackQueryHandler(leaderboard_handler, pattern=r"^leaderboard")
    )

    # My Achievements button from leaderboard:
    # callback_data="my_achievements"
    application.add_handler(
        CallbackQueryHandler(my_achievements_handler, pattern=r"^my_achievements$")
    )

