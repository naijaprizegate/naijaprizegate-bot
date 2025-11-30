# ===============================================================
# handlers/leaderboard.py  — Public Quiz Leaderboard (Skill-Based)
# ===============================================================

import os
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from telegram.error import BadRequest
from sqlalchemy import select, func

from db import get_async_session
from models import PremiumRewardEntry, User, GameState  # PremiumRewardEntry = quiz entry log

LEADERBOARD_PAGE_SIZE = 10

# WIN_THRESHOLD: total paid questions needed this cycle before prize is awarded
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "0"))


# ---------------------------------------------------------
# 🏅 Badge helper (based on quiz activity)
# ---------------------------------------------------------
def _badge_for_points(points: int) -> str:
    """
    Simple badge tiers based on the user's performance  entries/points
    in the selected scope. No money or luck implied.
    """
    if points >= 200:
        return "🏆 Legend"
    if points >= 100:
        return "🥇 Gold"
    if points >= 50:
        return "🥈 Silver"
    if points >= 20:
        return "🥉 Bronze"
    if points >= 5:
        return "⭐ Active"
    if points >= 1:
        return "🎓 New Challenger"
    return "—"


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
      - Badges
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
        scope_label = "🎯 This Cycle"

    async with get_async_session() as session:
        # ----- Base query for counts -----
        # "points" here represent *earned performance  entries/points*
        base_q = select(
            PremiumRewardEntry.user_id,
            func.count(PremiumRewardEntry.id).label("points"),
        )
        if filter_clause is not None:
            base_q = base_q.where(filter_clause)
        base_q = base_q.group_by(PremiumRewardEntry.user_id)

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
        page_q = (
            base_q
            .order_by(func.count(PremiumRewardEntry.id).desc())
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
            my_count_q = select(func.count(PremiumRewardEntry.id)).where(
                PremiumRewardEntry.user_id == viewer_user_id
            )
            if filter_clause is not None:
                my_count_q = my_count_q.where(filter_clause)

            my_points = (await session.execute(my_count_q)).scalar() or 0

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

    if not rows:
        text_lines.append("No quiz activity recorded yet in this period.\n")
    else:
        text_lines.append("Top players (based on earned performance points):\n")
        rank_start = offset + 1
        for i, (uid, points) in enumerate(rows, start=rank_start):
            u = users_by_id.get(uid)

            # Prefer first_name → username → masked ID
            if u and getattr(u, "first_name", None):
                display_name = u.first_name
            elif u and u.username:
                display_name = f"@{u.username}"
            elif u and u.tg_id:
                display_name = f"Player {str(u.tg_id)[-4:]}"
            else:
                display_name = f"Player {str(uid)[-4:]}"

            badge = _badge_for_points(points)
            text_lines.append(
                f"<b>{i}.</b> {display_name} — {points} quiz point(s) {badge}"
            )

    text_lines.append("")
    text_lines.append(f"📊 <b>Total Quiz Points (this period):</b> {total_points}")
    text_lines.append(f"👥 <b>Active Players:</b> {distinct_users}")

    if viewer_user_id:
        badge_me = _badge_for_points(my_points)
        text_lines.append("\n<b>Your Stats</b>")
        if my_points == 0:
            text_lines.append(
                "• You have 0 performance  points in this period yet. "
                "Answer more questions to climb the board! 🔥"
            )
        else:
            rank_text = f"#{my_rank}" if my_rank is not None else "N/A"
            text_lines.append(
                f"• Rank: {rank_text}\n"
                f"• performance  points: {my_points} ({badge_me})\n"
                f"• Current activity streak: {current_streak} day(s)\n"
                f"• Best activity streak: {best_streak} day(s)"
            )

            achievements = []
            if my_points >= 1:
                achievements.append("🎉 First Challenge — You joined your first performance  round.")
            if my_points >= 10:
                achievements.append("🎯 Consistent Player — 10+ performance  points earned.")
            if my_points >= 25:
                achievements.append("🔥 Dedicated Challenger — 25+ performance  points.")
            if best_streak >= 3:
                achievements.append(f"⚡ Streak Builder — {best_streak}+ days of quiz activity in a row.")

            if achievements:
                text_lines.append("\n<b>Quick Achievements</b>")
                for a in achievements:
                    text_lines.append(f"• {a}")

    # ---- Cycle progress + trust signal (merit-based winner) ----
    text_lines.append("")
    if WIN_THRESHOLD > 0:
        # Percent progress (rounded)
        if WIN_THRESHOLD > 0:
            progress_pct = int((paid_this_cycle / WIN_THRESHOLD) * 100) if paid_this_cycle > 0 else 0

        # Create simple progress bar (10 blocks)
        total_blocks = 10
        filled_blocks = int(progress_pct / (100 / total_blocks))
        progress_bar = "█" * filled_blocks + "░" * (total_blocks - filled_blocks)

        text_lines.append(f"🎯 <b>Cycle Progress:</b> {progress_bar} ({progress_pct}%)")

        if paid_this_cycle >= WIN_THRESHOLD:
            # 🍾 Winner lock state (automatic backend logic)
            text_lines.append(
                "🔒 Prize unlocked — Top scorer is now being awarded!"
            )
        else:
            text_lines.append(
                "🏆 Top scorer at the end of the cycle will be awarded the prize.\n"
                "🔥 Keep scoring to reach the top!"
            )

    text_lines.append("✔ 100% Skill-Based — no gambling or chance involved.")

    text_lines.append(
        "\nℹ️ Weekly view shows the last 7 days only. "
        "Cycle view covers the current competition cycle."
    )
    text_lines.append(
        "📌 Rankings are based on your quiz activity and knowledge performance."
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
            ("🎯 This Cycle ✅" if scope == "cycle" else "🎯 This Cycle"),
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

    badge = _badge_for_points(total_points_all)

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
        f"🎟️ <b>Total performance  Points (all-time):</b> {total_points_all}"
    )
    lines.append(
        f"🔥 <b>Last 7 Days:</b> {points_last_7} performance  point(s) earned"
    )
    lines.append(f"🏅 <b>Current Badge:</b> {badge}")
    lines.append(f"⚡ <b>Current Activity Streak:</b> {current_streak} day(s)")
    lines.append(f"🏆 <b>Best Activity Streak:</b> {best_streak} day(s)\n")

    # Milestone-style achievements (quiz-based)
    achievements = []
    if total_points_all >= 1:
        achievements.append("🎉 First Challenge — You completed your first performance  round!")
    if total_points_all >= 10:
        achievements.append("🎯 Consistent Player — 10+ performance  points collected.")
    if total_points_all >= 25:
        achievements.append("🔥 Dedicated Challenger — 25+ performance  points.")
    if total_points_all >= 50:
        achievements.append("💎 Elite Learner — 50+ performance  points.")
    if total_points_all >= 100:
        achievements.append("👑 Quiz Master — 100+ performance  points.")
    if best_streak >= 3:
        achievements.append(f"⚡ Streak Builder — {best_streak}+ days of quiz activity in a row.")
    if best_streak >= 7:
        achievements.append("🔥 Weekly Warrior — 7 days of non-stop quiz activity.")

    if achievements:
        lines.append("<b>Unlocked Milestones</b>")
        for a in achievements:
            lines.append(f"• {a}")
    else:
        lines.append("<b>Unlocked Milestones</b>")
        lines.append(
            "• None yet — keep playing quizzes and earning points to unlock your first badge! 🚀"
        )

    # Optional: hint upcoming milestones (static text)
    lines.append("\n<b>Next Milestones</b>")
    lines.append("• 10 quiz points → Consistent Player")
    lines.append("• 25 quiz points → Dedicated Challenger")
    lines.append("• 3-day activity streak → Streak Builder")

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
