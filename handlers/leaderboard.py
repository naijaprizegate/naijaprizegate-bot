# ===============================================================
# handlers/leaderboard.py
# ===============================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from sqlalchemy import select, func
from db import AsyncSessionLocal
from models import PremiumSpinEntry, User

LEADERBOARD_PAGE_SIZE = 10


# ---------------------------------------------------------------
# Entry point → load page 1
# ---------------------------------------------------------------
async def leaderboard_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await leaderboard_render(update, context, page=1)


# ---------------------------------------------------------------
# Render leaderboard page
# ---------------------------------------------------------------
async def leaderboard_render(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    async with AsyncSessionLocal() as session:

        # Total premium spin tickets this cycle
        total_tickets = (
            await session.execute(select(func.count()).select_from(PremiumSpinEntry))
        ).scalar() or 0

        # Distinct users this cycle
        distinct_users = (
            await session.execute(
                select(func.count(func.distinct(PremiumSpinEntry.user_id)))
            )
        ).scalar() or 0

        offset = (page - 1) * LEADERBOARD_PAGE_SIZE

        # Top users
        result = await session.execute(
            select(
                PremiumSpinEntry.user_id,
                func.count(PremiumSpinEntry.id).label("tickets")
            )
            .group_by(PremiumSpinEntry.user_id)
            .order_by(func.count(PremiumSpinEntry.id).desc())
            .offset(offset)
            .limit(LEADERBOARD_PAGE_SIZE)
        )

        rows = result.all()

    # If empty → return to page 1
    if not rows and page != 1:
        return await leaderboard_render(update, context, page=1)

    # ---------------------------------------------
    # Build leaderboard text
    # ---------------------------------------------
    rank_start = offset + 1
    lines = ["🏆 <b>NaijaPrizeGate Leaderboard</b>\n"]
    lines.append("Top premium spin users this cycle:\n")

    async with AsyncSessionLocal() as session:
        for i, (user_id, ticket_count) in enumerate(rows, start=rank_start):
            user = await session.get(User, user_id)

            if user and user.username:
                name = f"@{user.username}"
            else:
                # masked for privacy
                name = f"User {str(user_id)[-4:]}"

            lines.append(f"<b>{i}.</b> {name} — {ticket_count} ticket(s)")

    # Footer summary
    lines.append("")
    lines.append(f"🎟️ <b>Total Tickets:</b> {total_tickets}")
    lines.append(f"👥 <b>Participants:</b> {distinct_users}")

    text = "\n".join(lines)

    # ---------------------------------------------
    # Pagination buttons
    # ---------------------------------------------
    buttons = []
    if page > 1:
        buttons.append(
            InlineKeyboardButton("⬅️ Prev", callback_data=f"leaderboard:page:{page-1}")
        )
    if len(rows) == LEADERBOARD_PAGE_SIZE:
        buttons.append(
            InlineKeyboardButton("Next ➡️", callback_data=f"leaderboard:page:{page+1}")
        )

    keyboard = InlineKeyboardMarkup([buttons] if buttons else [])

    # ---------------------------------------------
    # Output (edit or send)
    # ---------------------------------------------
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=keyboard
        )


# ---------------------------------------------------------------
# Router for pagination
# ---------------------------------------------------------------
async def leaderboard_page_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, _, page = query.data.split(":")
    return await leaderboard_render(update, context, page=int(page))


# ---------------------------------------------------------------
# Register leaderboard handlers
# ---------------------------------------------------------------
def register_leaderboard_handlers(application):
    application.add_handler(
        CallbackQueryHandler(leaderboard_show, pattern=r"^leaderboard:show$")
    )
    application.add_handler(
        CallbackQueryHandler(leaderboard_page_router, pattern=r"^leaderboard:page:")
    )
