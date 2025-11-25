# ===============================================================
# handlers/leaderboard.py  — Full Featured Public Leaderboard
# ===============================================================

from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from sqlalchemy import select, func

from db import get_async_session
from models import PremiumSpinEntry, User

LEADERBOARD_PAGE_SIZE = 10


# ---------------------------------------------------------
# 🏅 Badge helper (premium spins only)
# ---------------------------------------------------------
def _badge_for_tickets(tickets: int) -> str:
    """
    Simple badge tiers based on premium tickets in the selected scope.
    Safe for public display: no money mentioned.
    """
    if tickets >= 200:
        return "🏆 Legend"
    if tickets >= 100:
        return "🥇 Gold"
    if tickets >= 50:
        return "🥈 Silver"
    if tickets >= 20:
        return "🥉 Bronze"
    if tickets >= 5:
        return "⭐ Active"
    if tickets >= 1:
        return "🎟 New"
    return "—"


# ---------------------------------------------------------
# 📆 Streak helper (premium spins only)
# ---------------------------------------------------------
def _compute_streaks(dates) -> tuple[int, int]:
    """
    Given a list of datetime objects (premium spin timestamps for a user),
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
# 🏆 LEADERBOARD RENDERER
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
      - Top users by premium tickets
      - Badges
      - Your personal stats + streaks in footer
      - Button to view full “My Achievements” screen
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
        filter_clause = PremiumSpinEntry.created_at >= start
        scope_label = "🔥 This Week (last 7 days)"
    else:
        # Default to "cycle": everything in premium_spin_entries
        scope = "cycle"
        scope_label = "🎯 This Cycle"

    async with get_async_session() as session:
        # ---------- Base query for counts ----------
        base_q = select(
            PremiumSpinEntry.user_id,
            func.count(PremiumSpinEntry.id).label("tickets"),
        )
        if filter_clause is not None:
            base_q = base_q.where(filter_clause)
        base_q = base_q.group_by(PremiumSpinEntry.user_id)

        # ---------- Totals ----------
        total_q = select(func.count(PremiumSpinEntry.id))
        distinct_q = select(func.count(func.distinct(PremiumSpinEntry.user_id)))
        if filter_clause is not None:
            total_q = total_q.where(filter_clause)
            distinct_q = distinct_q.where(filter_clause)

        total_tickets = (await session.execute(total_q)).scalar() or 0
        distinct_users = (await session.execute(distinct_q)).scalar() or 0

        # ---------- Page of top users ----------
        offset = max(page - 1, 0) * LEADERBOARD_PAGE_SIZE
        page_q = (
            base_q
            .order_by(func.count(PremiumSpinEntry.id).desc())
            .offset(offset)
            .limit(LEADERBOARD_PAGE_SIZE)
        )

        rows = (await session.execute(page_q)).all()  # list of (user_id, tickets)

        # If page out of range, bounce back to page 1
        if not rows and page != 1:
            return await leaderboard_render(update, context, scope=scope, page=1)

        # ---------- Load user objects in bulk ----------
        user_ids = [uid for (uid, _) in rows]
        users_by_id = {}
        if user_ids:
            users_res = await session.execute(
                select(User).where(User.id.in_(user_ids))
            )
            for u in users_res.scalars():
                users_by_id[u.id] = u

        # ---------- Current viewer's stats ----------
        viewer_db_user = None
        viewer_user_id = None
        if tg_user:
            res_me = await session.execute(
                select(User).where(User.tg_id == tg_user.id)
            )
            viewer_db_user = res_me.scalars().first()
            if viewer_db_user:
                viewer_user_id = viewer_db_user.id

        my_tickets = 0
        my_rank = None
        current_streak = 0
        best_streak = 0

        if viewer_user_id:
            # ticket count in this scope (premium spins only)
            my_count_q = select(func.count(PremiumSpinEntry.id)).where(
                PremiumSpinEntry.user_id == viewer_user_id
            )
            if filter_clause is not None:
                my_count_q = my_count_q.where(filter_clause)
            my_tickets = (await session.execute(my_count_q)).scalar() or 0

            if my_tickets > 0:
                # rank = 1 + number of users who have strictly more tickets
                subq = base_q.subquery()
                better_q = select(func.count()).select_from(subq).where(
                    subq.c.tickets > my_tickets
                )
                better_count = (await session.execute(better_q)).scalar() or 0
                my_rank = better_count + 1

                # streaks based on ALL premium entries currently in table for this user
                streak_dates_res = await session.execute(
                    select(PremiumSpinEntry.created_at).where(
                        PremiumSpinEntry.user_id == viewer_user_id
                    )
                )
                dates = [row[0] for row in streak_dates_res.fetchall()]
                current_streak, best_streak = _compute_streaks(dates)

        # ---------- Build leaderboard text ----------
        text_lines = []
        text_lines.append("🏆 <b>NaijaPrizeGate Leaderboard</b>")
        text_lines.append(f"{scope_label}\n")

        if not rows:
            text_lines.append("No premium spins recorded yet in this scope.\n")
        else:
            text_lines.append("Top players (by premium tickets):\n")
            rank_start = offset + 1

            for i, (uid, tickets) in enumerate(rows, start=rank_start):
                u = users_by_id.get(uid)
                # Safe public identity:
                #  - prefer @username
                #  - else show masked ID with last 4 digits of tg_id if available
                if u and u.username:
                    display_name = f"@{u.username}"
                elif u and u.tg_id:
                    display_name = f"Player {str(u.tg_id)[-4:]}"
                else:
                    display_name = f"Player {str(uid)[-4:]}"

                badge = _badge_for_tickets(tickets)
                text_lines.append(
                    f"<b>{i}.</b> {display_name} — {tickets} ticket(s) {badge}"
                )

        # ---------- Summary footer ----------
        text_lines.append("")
        text_lines.append(f"🎟️ <b>Total Tickets (this scope):</b> {total_tickets}")
        text_lines.append(f"👥 <b>Participants:</b> {distinct_users}")

        # ---------- Personal stats / achievements snippet ----------
        if viewer_user_id:
            badge_me = _badge_for_tickets(my_tickets)
            text_lines.append("\n<b>Your Stats</b>")

            if my_tickets == 0:
                text_lines.append(
                    "• You have 0 premium tickets in this scope yet. Spin to climb the board! 🔥"
                )
            else:
                rank_text = f"#{my_rank}" if my_rank is not None else "N/A"
                text_lines.append(
                    f"• Rank: {rank_text}\n"
                    f"• Tickets: {my_tickets} ({badge_me})\n"
                    f"• Current streak: {current_streak} day(s)\n"
                    f"• Best streak: {best_streak} day(s)"
                )

                # Simple achievement text (safe, non-monetary, premium-only)
                achievements = []
                if my_tickets >= 1:
                    achievements.append("🎉 First Spin")
                if my_tickets >= 10:
                    achievements.append("🎯 Regular Spinner (10+ tickets)")
                if my_tickets >= 25:
                    achievements.append("🔥 High Roller (25+ tickets)")
                if best_streak >= 3:
                    achievements.append(f"⚡ Hot Streak ({best_streak}+ days in a row)")

                if achievements:
                    text_lines.append("\n<b>Quick Achievements</b>")
                    for a in achievements:
                        text_lines.append(f"• {a}")

        text_lines.append(
            "\nℹ️ Weekly board shows last 7 days only. Cycle board resets whenever the jackpot is hit."
        )

        full_text = "\n".join(text_lines)

        # ---------- Keyboard (tabs + pagination + achievements) ----------
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

        # 👇 NEW: My Achievements button
        kb_rows.append(
            [InlineKeyboardButton("📜 View My Achievements", callback_data="my_achievements")]
        )

        keyboard = InlineKeyboardMarkup(kb_rows)

    # ---------- Reply / edit ----------
    if update.callback_query:
        await update.callback_query.edit_message_text(
            full_text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        # Optional: in case you wire a /leaderboard command later
        await update.message.reply_text(
            full_text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


# ---------------------------------------------------------
# 📜 FULL “MY ACHIEVEMENTS” SCREEN (premium-only)
# ---------------------------------------------------------
async def my_achievements_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows a dedicated achievements screen for the current user.
    Premium spins only (based on PremiumSpinEntry).
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

        user_id = db_user.id

        # All-time premium tickets
        total_tickets_all = (
            await session.execute(
                select(func.count(PremiumSpinEntry.id)).where(
                    PremiumSpinEntry.user_id == user_id
                )
            )
        ).scalar() or 0

        # Last 7 days tickets (for extra context)
        now = datetime.now(timezone.utc)
        start_week = now - timedelta(days=7)
        tickets_last_7 = (
            await session.execute(
                select(func.count(PremiumSpinEntry.id)).where(
                    PremiumSpinEntry.user_id == user_id,
                    PremiumSpinEntry.created_at >= start_week,
                )
            )
        ).scalar() or 0

        # Streaks (based on ALL premium spins)
        streak_dates_res = await session.execute(
            select(PremiumSpinEntry.created_at).where(
                PremiumSpinEntry.user_id == user_id
            )
        )
        dates = [row[0] for row in streak_dates_res.fetchall()]
        current_streak, best_streak = _compute_streaks(dates)

    badge = _badge_for_tickets(total_tickets_all)

    # Build achievements text
    lines = []
    lines.append("📜 <b>My Achievements</b>\n")
    lines.append(f"👤 <b>User:</b> @{tg_user.username}" if tg_user.username else "👤 <b>User:</b> You")
    lines.append("")
    lines.append(f"🎟️ <b>Total Premium Spins (all-time):</b> {total_tickets_all}")
    lines.append(f"🔥 <b>Last 7 Days:</b> {tickets_last_7} premium ticket(s)")
    lines.append(f"🏅 <b>Current Badge:</b> {badge}")
    lines.append(f"⚡ <b>Current Streak:</b> {current_streak} day(s)")
    lines.append(f"🏆 <b>Best Streak:</b> {best_streak} day(s)\n")

    # Milestone-style achievements (premium-only)
    achievements = []
    if total_tickets_all >= 1:
        achievements.append("🎉 First Spin — You played your first premium spin!")
    if total_tickets_all >= 10:
        achievements.append("🎯 Regular Spinner — 10+ premium tickets.")
    if total_tickets_all >= 25:
        achievements.append("🔥 High Roller — 25+ premium tickets.")
    if total_tickets_all >= 50:
        achievements.append("💎 Elite Spinner — 50+ premium tickets.")
    if total_tickets_all >= 100:
        achievements.append("👑 VIP Grinder — 100+ premium tickets.")
    if best_streak >= 3:
        achievements.append(f"⚡ Hot Streak — {best_streak}+ days of premium spins in a row.")
    if best_streak >= 7:
        achievements.append("🔥 Weekly Warrior — 7 days of non-stop premium spins.")

    if achievements:
        lines.append("<b>Unlocked Milestones</b>")
        for a in achievements:
            lines.append(f"• {a}")
    else:
        lines.append("<b>Unlocked Milestones</b>")
        lines.append("• None yet — start spinning premium to unlock your first badge! 🚀")

    # Optional: hint upcoming milestones (static text)
    lines.append("\n<b>Next Milestones</b>")
    lines.append("• 10 spins → Regular Spinner")
    lines.append("• 25 spins → High Roller")
    lines.append("• 3 days streak → Hot Streak")

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
