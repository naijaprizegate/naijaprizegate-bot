# ==============================================================
# handlers/admin.py — Clean Unified Admin System (Skill-Based)
# ==============================================================
import os
import re
import io
import csv
import tempfile
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    InputMediaPhoto,
    InputFile,
    Bot,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram.ext import filters as tg_filters  # to avoid name clash
from telegram.error import BadRequest
from telegram.constants import ParseMode
from sqlalchemy import select, text, update as sql_update, func, and_

from handlers.core import fallback
from db import AsyncSessionLocal, get_async_session
from helpers import add_tries, get_user_by_id
from models import Proof, User, Payment, GameState, GlobalCounter, PrizeWinner
from utils.security import is_admin  # external helper (still imported)
from logging_config import setup_logger

logger = logging.getLogger(__name__)

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", 0))  # paid tries needed for a cycle prize

# ----------------------------
# 🔐 ADMIN SECURITY HELPER
# ----------------------------
def is_admin(user_id: int) -> bool:
    """Checks if the user is the authorized admin."""
    return user_id == ADMIN_USER_ID


# Key to store date selection temporary
DATE_SELECTION_KEY = "csv_export_date_range"


# ----------------------------
# SAFE EDIT FUNCTION
# ----------------------------
async def safe_edit(query, text: str, **kwargs):
    """
    Safely edit a message or caption, ignoring harmless 'Message is not modified' errors.
    Works for both photo and text messages.
    """
    try:
        if query.message.photo:
            await query.edit_message_caption(caption=text, **kwargs)
        else:
            await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        # ✅ Ignore harmless Telegram error
        if "message is not modified" in str(e).lower():
            logger.info("ℹ️ Skipped redundant edit — message not modified.")
            return
        else:
            logger.warning(f"⚠️ Telegram BadRequest: {e}")
            raise
    except Exception as e:
        logger.warning(f"[WARN] safe_edit fail: {e}")


# ----------------------------
# Command: /admin (Main Panel)
# ----------------------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.message.reply_text("❌ Access denied.", parse_mode="HTML")

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📂 Pending Proofs", callback_data="admin_menu:pending_proofs"
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 Stats", callback_data="admin_menu:stats"
                )
            ],
            [
                InlineKeyboardButton(
                    "📵 Failed Airtime Payouts",
                    callback_data="admin_airtime_failed:1"
                )
            ],

            [
                InlineKeyboardButton(
                    "👤 User Search", callback_data="admin_menu:user_search"
                )
            ],
            [
                InlineKeyboardButton(
                    "🏆 Winners", callback_data="admin_menu:winners"
                )
            ],
            [
                InlineKeyboardButton(
                    "📬 Support Inbox", callback_data="admin_menu:support_inbox"
                )
            ],

            # Renamed label to avoid “Top-Tier Campaign Reward” / random connotation
            [
                InlineKeyboardButton(
                    "📈 Cycle Entries Overview",
                    callback_data="admin_menu:top_tier_campaign_reward_points",
                )
            ],
        ]
    )
    await update.message.reply_text(
        "⚙️ <b>Admin Panel</b>\nChoose an action:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )

# ----------------------------------------------------
# Admin Support Inbox
# ----------------------------------------------------
async def admin_support_inbox(query, session):
    res = await session.execute(text("""
        SELECT id, tg_id, first_name, username, message, created_at
        FROM support_tickets
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT 20
    """))
    return res.fetchall()


async def admin_support_inbox_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔐 Restrict admin access
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Unauthorized.", show_alert=True)

    # Fetch pending tickets using your existing helper
    async with AsyncSessionLocal() as session:
        rows = await admin_support_inbox(query, session)  # should return list of tuples

    # Build text (HTML to match your admin UI)
    text_lines = ["<b>📬 Support Inbox (Pending)</b>\n"]

    # Build keyboard buttons
    buttons = []

    if not rows:
        text_lines.append("✅ No pending support messages.\n")
    else:
        text_lines.append("Tap a ticket button below to reply.\n")

        for (tid, tg_id, first_name, username, message, created_at) in rows:
            short = (message[:120] + "…") if len(message) > 120 else message
            who = first_name or "User"
            if username:
                who += f" (@{username})"

            text_lines.append(
                f"🆔 <b>Ticket:</b> <code>{tid}</code>\n"
                f"👤 <b>From:</b> {who}\n"
                f"📌 <b>TG_ID:</b> <code>{tg_id}</code>\n"
                f"💬 <b>Msg:</b> {short}\n"
                f"🕒 <b>Time:</b> {created_at}\n"
                f"—"
            )

            # ✅ One reply button per ticket
            buttons.append(
                [InlineKeyboardButton(f"✍️ Reply to #{tid}", callback_data=f"admin_support_reply:{tid}")]
            )

        # Optional hint (you can remove this if you're fully switching to button reply)
        text_lines.append(
            "\n<i>Tip:</i> Use the reply buttons to respond without revealing admin identity."
        )

    # Footer buttons
    buttons.append([InlineKeyboardButton("🔁 Refresh", callback_data="admin_menu:support_inbox")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")])

    text = "\n".join(text_lines)

    return await safe_edit(
        query,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

# ---------------------------------------------------------
# Admin Support Reply Text 
# ----------------------------------------------------------
async def admin_support_reply_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return

    if not context.user_data.get("awaiting_support_reply"):
        return

    ticket_id = context.user_data.get("support_reply_ticket_id")
    if not ticket_id:
        context.user_data["awaiting_support_reply"] = False
        return await update.message.reply_text("⚠️ No ticket selected. Go back to Support Inbox.")

    reply_text = (update.message.text or "").strip()
    if not reply_text:
        return await update.message.reply_text("⚠️ Reply cannot be empty. Type your reply:")

    async with context.bot_data["sessionmaker"]() as session:
        res = await session.execute(text("""
            SELECT tg_id, status
            FROM support_tickets
            WHERE id = :id
            LIMIT 1
        """), {"id": int(ticket_id)})
        row = res.fetchone()

        if not row:
            context.user_data["awaiting_support_reply"] = False
            context.user_data.pop("support_reply_ticket_id", None)
            return await update.message.reply_text("❌ Ticket not found. Please reopen Support Inbox.")

        tg_id, status = row
        if status != "pending":
            context.user_data["awaiting_support_reply"] = False
            context.user_data.pop("support_reply_ticket_id", None)
            return await update.message.reply_text(f"⚠️ Ticket is not pending (status: {status}).")

        # send reply to user (admin identity hidden)
        try:
            await context.bot.send_message(
                chat_id=int(tg_id),
                text=f"✅ <b>Support Reply</b>\n\n{reply_text}",
                parse_mode="HTML"
            )
        except Exception:
            return await update.message.reply_text("❌ Could not send message (user may have blocked the bot).")

        await session.execute(text("""
            UPDATE support_tickets
            SET status='replied', admin_reply=:r, replied_at=NOW()
            WHERE id=:id
        """), {"id": int(ticket_id), "r": reply_text})
        await session.commit()

    # clear state
    context.user_data["awaiting_support_reply"] = False
    context.user_data.pop("support_reply_ticket_id", None)

    return await update.message.reply_text(f"✅ Replied to ticket #{ticket_id}.")


# -----------------------------------------
# ADMIN: View Cycle Entries / Score Source
# -----------------------------------------
async def show_top_tier_campaign_reward_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin view of how many performance  entries (scored attempts) exist,
    and which users have the most recorded entries. This is purely
    merit-based usage of premium_reward_entries as the scoring source.
    """
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.callback_query.answer(
            "❌ Access denied.", show_alert=True
        )

    async with AsyncSessionLocal() as session:
        # Total number of recorded performance  entries
        count_res = await session.execute(
            text("SELECT COUNT(*) FROM premium_reward_entries")
        )
        total_entries = count_res.scalar() or 0

        # Optional: show top users with most entries (skill participation)
        detail_res = await session.execute(
            text(
                """
            SELECT user_id, tg_id, COUNT(*) AS entries
            FROM premium_reward_entries
            GROUP BY user_id, tg_id
            ORDER BY entries DESC
            LIMIT 20
        """
            )
        )
        rows = detail_res.fetchall()

        # Also show how far the current paid cycle has progressed vs threshold
        gs = await session.get(GameState, 1)
        paid_this_cycle = gs.paid_tries_this_cycle if gs else 0
        current_cycle = gs.current_cycle if gs else 1

    details = (
        "\n".join(
            [
                f"👤 User {row.user_id} — 🧠 {row.entries} recorded quiz entries"
                for row in rows
            ]
        )
        if rows
        else "No entries yet."
    )

    threshold_line = ""
    if WIN_THRESHOLD > 0:
        threshold_line = (
            f"\n🎯 <b>Cycle Progress:</b> {paid_this_cycle}/{WIN_THRESHOLD} "
            f"paid questions this cycle\n"
            f"🔢 <b>Current Cycle:</b> {current_cycle}"
        )
    else:
        threshold_line = (
            "\n🎯 <b>Cycle Progress:</b> WIN_THRESHOLD is not configured in env."
        )

    text_msg = (
        "📈 <b>Competition Entries Overview</b>\n\n"
        "This panel summarises how many <b>performance  attempts</b> have been "
        "recorded and who is most active. The main leaderboard uses these "
        "entries to rank users by performance.\n\n"
        f"📊 <b>Total Recorded Entries:</b> {total_entries}"
        f"{threshold_line}\n\n"
        f"🏅 <b>Most Active Participants (by entries)</b>\n"
        f"{details}\n\n"
        "ℹ️ Prizes are awarded based on leaderboard performance once the "
        "cycle’s paid question threshold is reached.\n\n"
        "🔙 Back to Admin Menu with /admin"
    )

    await update.callback_query.edit_message_text(text_msg, parse_mode="HTML")


# ----------------------------
# Pending Proofs (Paginated View + Back to Admin)
# ----------------------------
async def pending_proofs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show one pending proof at a time with Next/Prev navigation."""
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.effective_message.reply_text(
            "❌ Access denied.", parse_mode="HTML"
        )

    # --- Fetch pending proofs
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Proof).where(Proof.status == "pending")
        )
        proofs = result.scalars().all()

    # --- Handle no pending proofs
    if not proofs:
        text = (
            "✅ No pending proofs at the moment.\n\n"
            "Click on /admin to go back to the Admin Panel."
        )
        if getattr(update, "callback_query", None):
            return await update.callback_query.edit_message_text(
                text, parse_mode="HTML"
            )
        return await update.effective_message.reply_text(
            text, parse_mode="HTML"
        )

    # --- Initialize pagination state in context
    context.user_data["pending_proofs"] = [str(p.id) for p in proofs]
    context.user_data["proof_index"] = 0

    # --- Display the first proof
    await show_single_proof(update, context, index=0)


# ----------------------------
# Helper: Show a single proof page
# ----------------------------
async def show_single_proof(
    update: Update, context: ContextTypes.DEFAULT_TYPE, index: int
):
    """Render one proof (by index) with Prev/Next buttons + Back to Admin."""
    proof_ids = context.user_data.get("pending_proofs", [])
    if not proof_ids:
        if getattr(update, "callback_query", None):
            return await update.callback_query.edit_message_text(
                "✅ No proofs loaded.", parse_mode="HTML"
            )
        return await update.effective_message.reply_text(
            "✅ No proofs loaded.", parse_mode="HTML"
        )

    # --- Clamp index within range
    total = len(proof_ids)
    index = max(0, min(index, total - 1))
    context.user_data["proof_index"] = index
    proof_id = proof_ids[index]

    # --- Fetch proof + user info
    async with AsyncSessionLocal() as session:
        proof = await session.get(Proof, proof_id)
        if not proof:
            if getattr(update, "callback_query", None):
                return await update.callback_query.edit_message_text(
                    "⚠️ Proof not found.", parse_mode="HTML"
                )
            return await update.effective_message.reply_text(
                "⚠️ Proof not found.", parse_mode="HTML"
            )

        user = await get_user_by_id(session, proof.user_id)

    # --- Determine best user-display name
    if user:
        if user.username:
            user_name = f"@{user.username}"
        elif getattr(user, "name", None):  # if you store a "name" field
            user_name = user.name
        else:
            user_name = str(proof.user_id)
    else:
        user_name = str(proof.user_id)

    # --- Caption
    caption = (
        f"<b>📤 Pending Proof {index + 1} of {total}</b>\n\n"
        f"👤 User: {user_name}\n"
        f"🆔 Proof ID: <code>{proof.id}</code>"
    )

    # --- Navigation buttons
    nav_buttons = []
    if index > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                "⬅️ Prev", callback_data=f"admin_proofnav:{index - 1}"
            )
        )
    if index < total - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                "Next ➡️", callback_data=f"admin_proofnav:{index + 1}"
            )
        )

    # --- Inline keyboard (Approve/Reject + Nav + Back)
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Approve", callback_data=f"admin_approve:{proof.id}"
            ),
            InlineKeyboardButton(
                "❌ Reject", callback_data=f"admin_reject:{proof.id}"
            ),
        ],
        nav_buttons,
        [
            InlineKeyboardButton(
                "🔙 Back to Admin Menu", callback_data="admin_menu:main"
            )
        ],
    ]

    # Remove empty rows
    keyboard = InlineKeyboardMarkup([row for row in keyboard if row])

    # --- Send or edit the message depending on context
    try:
        if getattr(update, "callback_query", None):
            try:
                await update.callback_query.edit_message_media(
                    media=InputMediaPhoto(
                        media=proof.file_id, caption=caption, parse_mode="HTML"
                    ),
                    reply_markup=keyboard,
                )
            except Exception as e:
                # fallback if not a photo message or Telegram rejects edit
                logger.warning(
                    f"⚠️ Fallback to sending new proof message: {e}"
                )
                await update.callback_query.message.reply_photo(
                    photo=proof.file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
        else:
            await update.effective_message.reply_photo(
                photo=proof.file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
    except Exception as e:
        logger.error(f"❌ Failed to display proof {proof.id}: {e}")
        try:
            if getattr(update, "callback_query", None):
                await update.callback_query.edit_message_caption(
                    caption=f"⚠️ Could not display proof #{proof.id}.",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            else:
                await update.effective_message.reply_text(
                    f"⚠️ Could not display proof #{proof.id}.",
                    parse_mode="HTML",
                )
        except Exception as inner_e:
            logger.error(
                f"⚠️ Nested Telegram error while showing proof: {inner_e}"
            )


# ----------------------------
# Admin Callback Router (✅ Final with Auto-Move + Proof Navigation)
# ----------------------------
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # acknowledge click
    user_id = update.effective_user.id

    # 🔐 Restrict admin access
    if user_id != ADMIN_USER_ID:
        return await safe_edit(query, "❌ Access denied.", parse_mode="HTML")

    # ----------------------------
    # ✅ Proof Navigation (Prev / Next)
    # ----------------------------
    if query.data.startswith("admin_proofnav:"):
        try:
            new_index = int(query.data.split(":")[1])
        except ValueError:
            return await query.answer(
                "⚠️ Invalid navigation index.", show_alert=True
            )
        return await show_single_proof(update, context, index=new_index)    

    # ----------------------------
    # ✅ Support Reply Button Click (Ticket → ask admin to type reply)
    # ----------------------------
    if query.data.startswith("admin_support_reply:"):
        ticket_id = int(query.data.split(":")[1])
        context.user_data["support_reply_ticket_id"] = ticket_id
        context.user_data["awaiting_support_reply"] = True
        return await safe_edit(
            query,
            f"✍️ <b>Reply to Ticket #{ticket_id}</b>\n\nType your reply message now:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data="admin_menu:support_inbox")]]
            ),
        )


    # ----------------------------
    # Admin Menu Routing
    # ----------------------------
    if query.data.startswith("admin_menu:"):
        action = query.data.split(":")[1]

        # ---- Pending Proofs ----
        if action == "pending_proofs":
            return await pending_proofs(update, context)

        # ---- Stats ----
        elif action in ("stats", "stats_refresh"):
            if not is_admin(query.from_user.id):
                return await query.answer(
                    "⛔ Unauthorized access.", show_alert=True
                )

            async with AsyncSessionLocal() as session:
                # --- Core objects ---
                gc = await session.get(GlobalCounter, 1)
                gs = await session.get(GameState, 1)

                # --- Current UTC time (aware)
                now_aware = datetime.now(timezone.utc)

                # --- Convert all to naive UTC (to match DB TIMESTAMP WITHOUT TIME ZONE) ---
                def naive_utc(dt: datetime):
                    """Converts a timezone-aware datetime to naive UTC."""
                    return dt.replace(tzinfo=None)

                start_of_today = naive_utc(
                    now_aware.replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                )
                start_of_yesterday = start_of_today - timedelta(days=1)
                start_of_week = naive_utc(
                    (
                        now_aware - timedelta(days=now_aware.weekday())
                    ).replace(hour=0, minute=0, second=0, microsecond=0)
                )
                start_of_month = naive_utc(
                    datetime(
                        now_aware.year,
                        now_aware.month,
                        1,
                        tzinfo=timezone.utc,
                    )
                )
                now = naive_utc(now_aware)

                # --- Revenue metrics (access to paid questions) ---
                total_revenue = (
                    await session.execute(select(func.sum(Payment.amount)))
                ).scalar() or 0

                revenue_month = (
                    await session.execute(
                        select(func.sum(Payment.amount)).where(
                            Payment.created_at >= start_of_month
                        )
                    )
                ).scalar() or 0

                revenue_week = (
                    await session.execute(
                        select(func.sum(Payment.amount)).where(
                            Payment.created_at >= start_of_week
                        )
                    )
                ).scalar() or 0

                revenue_today = (
                    await session.execute(
                        select(func.sum(Payment.amount)).where(
                            Payment.created_at >= start_of_today
                        )
                    )
                ).scalar() or 0

                revenue_yesterday = (
                    await session.execute(
                        select(func.sum(Payment.amount)).where(
                            and_(
                                Payment.created_at >= start_of_yesterday,
                                Payment.created_at < start_of_today,
                            )
                        )
                    )
                ).scalar() or 0

                # --- Cycle start (for "This Cycle" revenue + top buyers) ---
                cycle_started_at = None
                if gs and gs.created_at:
                    cycle_created = gs.created_at
                    # ensure aware UTC
                    if cycle_created.tzinfo is None:
                        cycle_created = cycle_created.replace(tzinfo=timezone.utc)
                    # convert to naive UTC to match DB timestamps
                    cycle_started_at = naive_utc(cycle_created)

                # --- Revenue THIS CYCLE ---
                revenue_cycle = 0
                if cycle_started_at:
                    revenue_cycle = (
                        await session.execute(
                            select(func.sum(Payment.amount)).where(
                                Payment.created_at >= cycle_started_at
                            )
                        )
                    ).scalar() or 0


                # --- Users ---
                total_users = (
                    await session.execute(select(func.count(User.id)))
                ).scalar() or 0

                # --- Top 3 buyers this month (quiz access, not gambling) ---
                top_spenders_q = await session.execute(
                    select(User.username, func.sum(Payment.amount).label("spent"))
                    .join(User, User.id == Payment.user_id)
                    .where(Payment.created_at >= start_of_month)
                    .group_by(User.username)
                    .order_by(func.sum(Payment.amount).desc())
                    .limit(3)
                )
                top_spenders = top_spenders_q.all()

                # --- Top 3 buyers THIS CYCLE ---
                top_spenders_cycle = []
                if cycle_started_at:
                    top_spenders_cycle_q = await session.execute(
                        select(User.username, func.sum(Payment.amount).label("spent"))
                        .join(User, User.id == Payment.user_id)
                        .where(Payment.created_at >= cycle_started_at)
                        .group_by(User.username)
                        .order_by(func.sum(Payment.amount).desc())
                        .limit(3)
                    )
                    top_spenders_cycle = top_spenders_cycle_q.all()


                # --- Winners ---
                total_winners = (
                    await session.execute(
                        select(func.count(PrizeWinner.id))
                    )
                ).scalar() or 0

                # --- Game state ---
                lifetime_paid = gc.paid_tries_total if gc else 0
                current_cycle = gs.current_cycle if gs else 1
                paid_this_cycle = gs.paid_tries_this_cycle if gs else 0
                created_at = gs.created_at if gs else None

                now = datetime.now(timezone.utc)

                if created_at:
                    # Ensure created_at is timezone-aware (assume it's UTC if not)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    diff = now - created_at
                    since_text = f"{diff.days}d {int(diff.seconds / 3600)}h ago"
                else:
                    since_text = "Unknown"

            # --- Build stats text ---
            text = (
                f"<b>📊 Bot Statistics (Skill-Based Quiz)</b>\n\n"
                f"💰 <b>Payments (Quiz Access)</b>\n"
                f"• Total: ${total_revenue:,.2f}\n"
                f"• This Month: ${revenue_month:,.2f}\n"
                f"• This Week: ${revenue_week:,.2f}\n"
                f"• Yesterday: ${revenue_yesterday:,.2f}\n"
                f"• Today: ${revenue_today:,.2f}\n"
                f"• This Cycle: ${revenue_cycle:,.2f}\n\n"
                f"👥 <b>Users</b>\n"
                f"• Total Registered: {total_users}\n"
            )

            # --- Top Spenders (access only, not chance-based) ---
            if top_spenders:
                text += "🏅 <b>Top Quiz Access Buyers (This Month)</b>\n"
                for i, (username, spent) in enumerate(top_spenders, start=1):
                    uname = username or f"User{i}"
                    text += f"  {i}️⃣ @{uname} — ${spent:,.2f}\n"
            else:
                text += "🏅 <b>Top Quiz Access Buyers (This Month)</b>\n  None yet\n"

            # --- Top Spenders (this cycle) ---
            if top_spenders_cycle:
                text += "🏅 <b>Top Quiz Access Buyers (This Cycle)</b>\n"
                for i, (username, spent) in enumerate(top_spenders_cycle, start=1):
                    uname = username or f"User{i}"
                    text += f"  {i}️⃣ @{uname} — ${spent:,.2f}\n"
            else:
                text += "🏅 <b>Top Quiz Access Buyers (This Cycle)</b>\n  None yet\n"
    

            # --- Giveaway & Cycle info (merit-based) ---
            text += (
                f"\n🎁 <b>Prizes Awarded</b>\n"
                f"• Total Winners (all cycles): {total_winners}\n\n"
                f"🎯 <b>Current Quiz Cycle</b>\n"
                f"• Cycle Number: {current_cycle}\n"
                f"• Paid Questions This Cycle: {paid_this_cycle}\n"
                f"• Cycle Started: {since_text}\n"
            )

            if WIN_THRESHOLD > 0:
                progress_pct = int((paid_this_cycle / WIN_THRESHOLD) * 100) if paid_this_cycle > 0 else 0
                remaining = max(WIN_THRESHOLD - paid_this_cycle, 0)

                if paid_this_cycle >= WIN_THRESHOLD:
                    status = (
                        f"✅ {progress_pct}% reached — {paid_this_cycle}/{WIN_THRESHOLD}. "
                        f"Remaining: {remaining}."
                    )
                else:
                    status = (
                        f"⏳ {progress_pct}% reached — {paid_this_cycle}/{WIN_THRESHOLD}. "
                        f"Remaining: {remaining}."
                    )

                text += (
                    f"• Cycle Threshold (paid questions): {WIN_THRESHOLD}\n"
                    f"• Status: {status}"
                )
            else:
                text += "• Cycle Threshold: <i>Not configured (WIN_THRESHOLD env missing).</i>"


            # --- Inline keyboard (includes Refresh) ---
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔁 Refresh", callback_data="admin_menu:stats_refresh"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔄 Reset Cycle",
                            callback_data="admin_confirm:reset_cycle",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ Back", callback_data="admin_menu:main"
                        )
                    ],
                ]
            )

            # --- Safe edit reply ---
            return await safe_edit(
                query, text, parse_mode="HTML", reply_markup=keyboard
            )

        # ---- User Search ----
        elif action == "user_search":
            context.user_data["awaiting_user_search"] = True
            return await safe_edit(
                query,
                "🔍 <b>Send username or user ID</b> to search for a user.",
                parse_mode="HTML",
            )

        # ---- Winners ----
        elif action == "winners":
            return await show_winners_section(update, context)

        # ✅ NEW: Support Inbox
        elif action == "support_inbox":
            return await admin_support_inbox_page(update, context)

        # ---- Main Menu ----
        elif action == "main":
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📂 Pending Proofs",
                            callback_data="admin_menu:pending_proofs",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "📊 Stats", callback_data="admin_menu:stats"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "👤 User Search",
                            callback_data="admin_menu:user_search",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🏆 Winners", callback_data="admin_menu:winners"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "📬 Support Inbox", callback_data="admin_menu:support_inbox"
                            )
                    ],
                    [
                        InlineKeyboardButton(
                            "📈 Cycle Entries Overview",
                            callback_data="admin_menu:top_tier_campaign_reward_points",
                        )
                    ],
                ]
            )
            return await safe_edit(
                query,
                "⚙️ <b>Admin Panel</b>\nChoose an action:",
                parse_mode="HTML",
                reply_markup=keyboard,
            )

    # ----------------------------
    # Cycle Reset Flow
    # ----------------------------
    if query.data.startswith("admin_confirm:reset_cycle"):
        if not is_admin(query.from_user.id):
            return await query.answer(
                "⛔ Unauthorized access.", show_alert=True
            )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Yes, Reset", callback_data="admin_action:reset_cycle"
                    ),
                    InlineKeyboardButton(
                        "❌ Cancel", callback_data="admin_menu:stats"
                    ),
                ]
            ]
        )
        return await safe_edit(
            query,
            "⚠️ Are you sure you want to reset the cycle?",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    if query.data == "admin_action:reset_cycle":
        async with AsyncSessionLocal() as session:
            gs = await session.get(GameState, 1)
            if not gs:
                return await safe_edit(
                    query, "⚠️ GameState not found.", parse_mode="HTML"
                )

            gs.current_cycle += 1
            gs.paid_tries_this_cycle = 0
            gs.created_at = datetime.utcnow()
            await session.commit()

        await query.answer("✅ Cycle reset!", show_alert=True)
        return await safe_edit(
            query,
            "🔁 <b>Cycle Reset!</b> New round begins.",
            parse_mode="HTML",
        )

    # ----------------------------
    # Proof Approve / Reject (✅ Auto-Move + Notify User + Resubmit Option + Return to Admin Panel)
    # ----------------------------
    try:
        action, proof_id = query.data.split(":")
    except ValueError:
        return await safe_edit(
            query, "⚠️ Invalid callback data.", parse_mode="HTML"
        )

    if action not in ("admin_approve", "admin_reject"):
        return  # ignore unrelated actions

    async with AsyncSessionLocal() as session:
        proof = await session.get(Proof, proof_id)
        if not proof or proof.status != "pending":
            return await safe_edit(
                query,
                "⚠️ Proof already processed or not found.",
                parse_mode="HTML",
            )

        # ✅ Fetch the actual Telegram user ID from the User table
        user = await session.get(User, proof.user_id)
        telegram_id = getattr(user, "tg_id", None)

        # 🎯 Common user main menu buttons
        user_menu_keyboard = [
            [
                InlineKeyboardButton(
                    "🧠 Play Trivia Question", callback_data="playtrivia"
                )
            ],
            [
                InlineKeyboardButton(
                    "💳 Get More Trivia Attempts", callback_data="buy"
                )
            ],
            [
                InlineKeyboardButton(
                    "🎁 Earn Free Trivia Attempts", callback_data="free"
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 Available Trivia Attempts", callback_data="show_tries"
                )
            ],
        ]

        if action == "admin_approve":
            proof.status = "approved"
            # Bonus tries are additional quiz access, separate from paid ones
            await add_tries(session, proof.user_id, count=1, paid=False)
            msg = "✅ Proof approved and bonus quiz question added!"

            # 🎉 Notify user (with main menu)
            if telegram_id:
                try:
                    await context.bot.send_message(
                        telegram_id,
                        "🎉 Your proof has been approved! You’ve received 1 bonus question. Keep climbing the leaderboard 🧠🔥",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(user_menu_keyboard),
                    )
                except Exception as e:
                    logger.warning(
                        f"⚠️ Could not notify user {telegram_id}: {e}"
                    )
            else:
                logger.warning(
                    f"⚠️ No Telegram ID (tg_id) found for user {proof.user_id}"
                )

        else:
            proof.status = "rejected"
            msg = "❌ Proof rejected."

            # ⏪ Add “Resubmit Proof” button at the top
            reject_keyboard = [
                [
                    InlineKeyboardButton(
                        "📤 Resubmit Proof",
                        callback_data="resubmit_proof",
                    )
                ],
                *user_menu_keyboard,
            ]

            # ⚠️ Notify user (with resubmit + main menu)
            if telegram_id:
                try:
                    await context.bot.send_message(
                        telegram_id,
                        "❌ Your proof has been reviewed but unfortunately was rejected.\n\n"
                        "Please ensure your next proof meets the rules and resubmit below 👇",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(reject_keyboard),
                    )
                except Exception as e:
                    logger.warning(
                        f"⚠️ Could not notify user {telegram_id}: {e}"
                    )
            else:
                logger.warning(
                    f"⚠️ No Telegram ID (tg_id) found for user {proof.user_id}"
                )

        await session.commit()

    # ✅ Automatically move to the next pending proof
    current_index = context.user_data.get("proof_index", 0)
    proof_ids = context.user_data.get("pending_proofs", [])

    if current_index + 1 < len(proof_ids):
        context.user_data["proof_index"] = current_index + 1
        await query.answer(msg)
        return await show_single_proof(
            update, context, index=current_index + 1
        )

    # ✅ No more proofs left → show admin panel
    await query.answer(msg)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📂 Pending Proofs",
                    callback_data="admin_menu:pending_proofs",
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 Stats", callback_data="admin_menu:stats"
                )
            ],
            [
                InlineKeyboardButton(
                    "👤 User Search",
                    callback_data="admin_menu:user_search",
                )
            ],
            [
                InlineKeyboardButton(
                    "🏆 Winners", callback_data="admin_menu:winners"
                )
            ],
            [
                InlineKeyboardButton(
                    "📈 Cycle Entries Overview",
                    callback_data="admin_menu:top_tier_campaign_reward_points",
                )
            ],
        ]
    )

    return await safe_edit(
        query,
        f"{msg}\n\n✅ All proofs reviewed!\n\n⚙️ <b>Back to Admin Panel</b>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ----------------------------
# User Search Handler
# ----------------------------
async def user_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if (
        update.effective_user.id != ADMIN_USER_ID
        or not context.user_data.get("awaiting_user_search")
    ):
        return
    query_text = update.message.text.strip()
    async with AsyncSessionLocal() as session:
        if query_text.isdigit():
            user = await session.get(User, query_text)
        else:
            result = await session.execute(
                select(User).where(User.username == query_text)
            )
            user = result.scalars().first()
    if not user:
        await update.message.reply_text(
            "⚠️ No user found.", parse_mode="HTML"
        )
    else:
        reply = (
            f"<b>👤 User Info</b>\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"📛 Username: @{user.username or '-'}\n"
            f"🧮 Paid Questions: {user.tries_paid} | Bonus Questions: {user.tries_bonus}\n"
            f"🎁 Last Prize Choice: {user.choice or '-'}"
        )
        await update.message.reply_text(reply, parse_mode="HTML")
    context.user_data["awaiting_user_search"] = False


# ----------------------------
# 🏆 Winners Section (PrizeWinner-based Paging)
# ----------------------------
WINNERS_PER_PAGE = 1
admin_offset = {}  # remembers page per admin


async def show_winners_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = getattr(update, "callback_query", None)
    admin_id = update.effective_user.id
    page = 1
    filter_status = None  # None | Pending | In Transit | Delivered

    # 🧭 Log callback for debugging
    if query:
        logger.debug(f"🧭 Callback data received: {query.data}")

    # 🔍 Parse callback data safely
    if query and query.data and query.data.startswith("admin_winners"):
        parts = query.data.split(":")
        if len(parts) >= 2:
            key = parts[1]
            if key == "pending":
                filter_status = "Pending"
            elif key == "transit":
                filter_status = "In Transit"
            elif key == "delivered":
                filter_status = "Delivered"
            elif key == "all":
                filter_status = None

        # Page number (optional third part)
        if len(parts) == 3 and parts[2].isdigit():
            page = int(parts[2])
    else:
        # Restore last page seen by this admin
        page = admin_offset.get(admin_id, 1)

    offset = (page - 1) * WINNERS_PER_PAGE

    # 📦 Fetch from PrizeWinner table
    async with get_async_session() as session:
        qb = select(PrizeWinner)
        if filter_status:
            qb = qb.where(PrizeWinner.delivery_status == filter_status)
        qb = qb.order_by(PrizeWinner.id.desc())
        res = await session.execute(qb)
        all_winners = res.scalars().all()

    # 📭 No winners found
    if not all_winners:
        text = (
            "📭 No winners found for this category.\n\n"
            "💡 Tip: Mark winners in the correct status to track delivery progress!"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📦 In Transit",
                        callback_data="admin_winners:transit:1",
                    ),
                    InlineKeyboardButton(
                        "✅ Delivered",
                        callback_data="admin_winners:delivered:1",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "📥 Export Winners CSV",
                        callback_data="admin_export_csv",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back", callback_data="admin_menu:main"
                    )
                ],
            ]
        )

        return (
            await safe_edit(query, text, parse_mode="HTML", reply_markup=keyboard)
            if query
            else await update.effective_message.reply_text(
                text, parse_mode="HTML", reply_markup=keyboard
            )
        )

    # 🧮 Pagination setup
    total_winners = len(all_winners)
    total_pages = total_winners
    page = max(1, min(page, total_pages))
    admin_offset[admin_id] = page

    # 🎯 Current winner
    winner = all_winners[offset]

    # 🧾 Extract winner details
    data = winner.delivery_data or {}
    full_name = data.get("full_name", "-")
    phone = data.get("phone", "N/A")
    address = data.get("address", "N/A")

    status_label_map = {
        None: "all",
        "Pending": "pending",
        "In Transit": "transit",
        "Delivered": "delivered",
    }
    base_prefix = f"admin_winners:{status_label_map.get(filter_status, 'all')}"

    filter_label = {
        None: "🏆 All Winners (Leaderboard-based)",
        "Pending": "🟡 Pending Winners",
        "In Transit": "📦 In Transit Winners",
        "Delivered": "✅ Delivered Winners",
    }[filter_status]

    text = (
        f"{filter_label}\n"
        f"Winner {page} of {total_winners}\n\n"
        f"👤 <b>{full_name}</b>\n"
        f"📱 {phone}\n"
        f"🏠 {address}\n"
        f"🎁 {winner.choice}\n"
        f"🚚 Status: <b>{winner.delivery_status or 'Pending'}</b>\n"
        f"🆔 <code>{winner.tg_id}</code>\n"
        f"📌 PrizeWinner ID: <code>{winner.id}</code>"
    )

    # 🧩 Action buttons
    rows = [
        [
            InlineKeyboardButton(
                "🚚 Mark In Transit",
                callback_data=f"pw_status_transit_{winner.id}",
            ),
            InlineKeyboardButton(
                "✅ Mark Delivered",
                callback_data=f"pw_status_delivered_{winner.id}",
            ),
        ]
    ]

    # 🧭 Navigation
    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                "⬅️ Prev", callback_data=f"{base_prefix}:{page-1}"
            )
        )
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                "Next ⏩", callback_data=f"{base_prefix}:{page+1}"
            )
        )
    if nav:
        rows.append(nav)

    # 🗂️ Filter & Export options
    rows.append(
        [
            InlineKeyboardButton(
                "📦 In Transit", callback_data="admin_winners:transit:1"
            ),
            InlineKeyboardButton(
                "✅ Delivered", callback_data="admin_winners:delivered:1"
            ),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                "📥 Export Winners CSV", callback_data="admin_export_csv"
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Back", callback_data="admin_menu:main"
            )
        ]
    )

    keyboard = InlineKeyboardMarkup(rows)

    # ✅ Edit or send fresh
    try:
        return (
            await safe_edit(
                query, text, parse_mode="HTML", reply_markup=keyboard
            )
            if query
            else await update.effective_message.reply_text(
                text, parse_mode="HTML", reply_markup=keyboard
            )
        )
    except Exception as e:
        logger.warning(f"⚠️ Could not edit winners message: {e}")
        return await update.effective_message.reply_text(
            text, parse_mode="HTML", reply_markup=keyboard
        )


# --------------------------------------
# handle_pw_mark_in_transit
# --------------------------------------
async def handle_pw_mark_in_transit(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()
    data = query.data  # e.g. "pw_status_transit_12"
    _, _, record_id = data.rpartition("_")
    if not record_id.isdigit():
        return await query.edit_message_text("Invalid record id.")
    rid = int(record_id)

    async with get_async_session() as session:
        pw = await session.get(PrizeWinner, rid)
        if not pw:
            return await query.edit_message_text("Record not found.")

        pw.delivery_status = "In Transit"
        pw.in_transit_at = datetime.utcnow()
        pw.last_updated_by = update.effective_user.id
        await session.commit()

        # notify winner
        try:
            bot = Bot(token=os.getenv("BOT_TOKEN"))
            await bot.send_message(
                chat_id=pw.tg_id,
                text=(
                    f"🚚 Hi! Your prize ({pw.choice}) is now *In Transit*. "
                    "We’ll update you when it’s delivered."
                ),
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception("Failed to notify winner about In Transit")

    # refresh admin display: stay on same page
    await show_winners_section(update, context)


# -----------------------------------------------------------------------------
# Unified CSV Export (winners export)
# -----------------------------------------------------------------------------

# -------------------------
# Step 1 — Show Export Range Menu
# -------------------------
async def admin_export_csv_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show export options (presets + custom).
    Registered as CallbackQueryHandler(..., pattern=r"^admin_export_csv$")
    """
    query = getattr(update, "callback_query", None)
    if not query:
        return

    # Resolve user id safely
    user_id = getattr(getattr(update, "effective_user", None), "id", None)

    # Admin check (robust)
    if user_id is None or user_id != ADMIN_USER_ID:
        try:
            ok = await is_admin(user_id) if user_id is not None else False
        except TypeError:
            try:
                ok = is_admin(user_id)
            except Exception:
                ok = False
        except Exception:
            ok = False

        if not ok:
            return await query.answer("⛔ Unauthorized", show_alert=True)

    # Menu: includes additional presets Last Month and All Time
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🕐 Last 24 Hours", callback_data="export_csv:24h"
                ),
                InlineKeyboardButton(
                    "🗓️ Last 7 Days", callback_data="export_csv:7days"
                ),
            ],
            [
                InlineKeyboardButton(
                    "📆 Last 30 Days", callback_data="export_csv:30days"
                ),
                InlineKeyboardButton(
                    "📅 This Month", callback_data="export_csv:thismonth"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🗓️ Last Month", callback_data="export_csv:lastmonth"
                ),
                InlineKeyboardButton(
                    "📋 All Time", callback_data="export_csv:all"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔧 Custom Range (YYYY-MM-DD)",
                    callback_data="export_csv:custom",
                )
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_winners:all:1")],
        ]
    )

    text = (
        "📥 <b>Export Winners CSV</b>\n\n"
        "Choose a preset or select Custom range (you will be asked to send dates in <b>YYYY-MM-DD</b> format). "
        "All timestamps are in <b>UTC</b>."
    )

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


# -------------------------
# Step 2 — Handle Range Selection + CSV Creation
# -------------------------
async def export_csv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles callback_data like export_csv:24h, export_csv:custom, etc.
    Registered as CallbackQueryHandler(..., pattern=r"^export_csv:")
    """
    query = getattr(update, "callback_query", None)
    if not query:
        return

    # Resolve user id safely
    user_id = getattr(getattr(update, "effective_user", None), "id", None)

    # Admin check (robust)
    if user_id is None or user_id != ADMIN_USER_ID:
        try:
            ok = await is_admin(user_id) if user_id is not None else False
        except TypeError:
            try:
                ok = is_admin(user_id)
            except Exception:
                ok = False
        except Exception:
            ok = False

        if not ok:
            return await query.answer("⛔ Unauthorized", show_alert=True)

    # Parse selection label (after first ":")
    label = query.data.split(":", 1)[1].strip().lower()

    now = datetime.now(timezone.utc)
    start_dt = None
    end_dt = None

    if label == "24h":
        start_dt = now - timedelta(days=1)
        end_dt = now
    elif label == "7days":
        start_dt = now - timedelta(days=7)
        end_dt = now
    elif label == "30days":
        start_dt = now - timedelta(days=30)
        end_dt = now
    elif label == "thismonth":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
    elif label == "lastmonth":
        first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_end = first_of_this_month - timedelta(microseconds=1)
        last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_dt = last_month_start
        end_dt = last_month_end
    elif label == "all":
        start_dt = datetime(2000, 1, 1, tzinfo=timezone.utc)
        end_dt = now
    elif label == "custom":
        # ✅ IMPORTANT: Arm the date input router ONLY for this admin
        context.user_data["awaiting_date_range"] = True

        await query.edit_message_text(
            "📅 <b>Custom Range</b>\n\n"
            "Please send your full range in one message using either format:\n\n"
            "<code>2025-10-01 to 2025-10-31</code>\n"
            "or\n"
            "<code>2025-10-01,2025-10-31</code>\n\n"
            "<i>Tip:</i> Send it as plain text (don’t add extra words).",
            parse_mode="HTML",
        )
        return
    else:
        return await query.answer("⚠️ Invalid selection", show_alert=True)

    # For predefined ranges: disarm custom mode just in case
    context.user_data.pop("awaiting_date_range", None)

    await query.edit_message_text("⏳ Generating CSV... please wait.")
    await generate_and_send_csv(update, context, start_dt, end_dt, label=label)


# ---------------------------------------------------------
# Step 3 — Single Message Custom Range Date Input
# ---------------------------------------------------------
async def date_range_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles admin text input for custom CSV date ranges.
    Accepts one message like '2025-10-01 to 2025-10-31' or '2025-10-01,2025-10-31'.
    Generates the CSV immediately.

    ✅ Will ONLY run when context.user_data["awaiting_date_range"] is True.
    """
    user_id = getattr(getattr(update, "effective_user", None), "id", None)

    # --- Admin check (async/sync safe)
    if user_id is None or user_id != ADMIN_USER_ID:
        try:
            ok = await is_admin(user_id) if user_id is not None else False
        except TypeError:
            try:
                ok = is_admin(user_id)
            except Exception:
                ok = False
        except Exception:
            ok = False
        if not ok:
            return  # Ignore — not admin

    # ✅ GUARD: Only process if admin selected "custom" and we asked for dates
    if not context.user_data.get("awaiting_date_range"):
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    # --- Parse both dates from input
    # IMPORTANT: do NOT include "-" because YYYY-MM-DD contains "-"
    separators = [" to ", "to", ",", "–", "—"]
    parts = None

    # Keep original hyphens (inside dates) intact
    normalized = text.strip()

    for sep in separators:
        if sep in normalized:
            parts = [p.strip() for p in normalized.split(sep, 1)]
            break

    if not parts or len(parts) != 2 or not parts[0] or not parts[1]:
        await update.message.reply_text(
            "❌ Please send both dates like:\n\n"
            "`2025-10-01 to 2025-10-31`\n\nor\n`2025-10-01,2025-10-31`",
            parse_mode="MarkdownV2",
        )
        context.user_data.pop("awaiting_date_range", None)
        return

    start_str, end_str = parts[0], parts[1]

    # --- Parse ISO dates
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid date format.\nMake sure both dates are in `YYYY-MM-DD` format.",
            parse_mode="MarkdownV2",
        )
        context.user_data.pop("awaiting_date_range", None)
        return

    if start_dt > end_dt:
        await update.message.reply_text(
            "⚠️ Start date must be before end date.",
            parse_mode="MarkdownV2",
        )
        context.user_data.pop("awaiting_date_range", None)
        return

    # ✅ Disarm immediately (prevents hijacking after success)
    context.user_data.pop("awaiting_date_range", None)

    # --- Generate and send CSV
    await update.message.reply_text("⏳ Generating CSV... please wait.")
    await generate_and_send_csv(
        update,
        context,
        start_dt,
        end_dt,
        label=f"custom_{start_dt.date()}_to_{end_dt.date()}",
    )


# ---------------------------------------------------------
# generate_and_send_csv() — Robust final version (drop-in)
# ---------------------------------------------------------
async def generate_and_send_csv(
    update, context, start_dt: datetime, end_dt: datetime, label: str = "range"
):
    """
    Query PrizeWinner by submitted_at between start_dt and end_dt (inclusive),
    create CSV in OS tmp dir (UTF-8 with BOM for Excel), send to admin using an open file object,
    then delete file safely.
    """

    # ---------------------------
    # Admin validation
    # ---------------------------
    user_id = getattr(getattr(update, "effective_user", None), "id", None)
    ok = False
    if user_id == ADMIN_USER_ID:
        ok = True
    elif user_id is not None:
        try:
            ok = await is_admin(user_id)
        except TypeError:
            try:
                ok = is_admin(user_id)
            except Exception:
                ok = False
        except Exception:
            ok = False

    if not ok:
        if getattr(update, "callback_query", None):
            return await update.callback_query.answer(
                "⛔ Unauthorized", show_alert=True
            )
        return await update.message.reply_text("⛔ Unauthorized access.")

    # ---------------------------
    # Query winners
    # ---------------------------
    async with get_async_session() as session:
        qb = (
            select(PrizeWinner)
            .where(
                and_(
                    PrizeWinner.submitted_at >= start_dt,
                    PrizeWinner.submitted_at <= end_dt,
                )
            )
            .order_by(PrizeWinner.submitted_at.asc())
        )
        result = await session.execute(qb)
        winners = result.scalars().all()

    if not winners:
        msg = (
            f"📭 No winners found between {start_dt.isoformat()} and "
            f"{end_dt.isoformat()} (UTC)."
        )
        if getattr(update, "callback_query", None):
            return await update.callback_query.edit_message_text(msg)
        return await update.message.reply_text(msg)

    # ---------------------------
    # Create temp CSV file (utf-8-sig for Excel)
    # ---------------------------
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    filename = f"winners_{start_str}_to_{end_str}.csv"

    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8-sig",
        newline="",
        delete=False,
        suffix=".csv",
    )
    tmp_path = tmp.name

    try:
        writer = csv.writer(tmp)
        # header
        writer.writerow(
            [
                "Full Name",
                "Phone",
                "Address",
                "Prize",
                "Date Won (UTC)",
                "Delivery Status",
            ]
        )

        for w in winners:
            data = w.delivery_data or {}
            full_name = data.get("full_name", "") or ""
            phone = data.get("phone", "") or ""
            address = data.get("address", "") or ""
            prize = (
                getattr(w, "choice", "") or getattr(w, "prize_name", "") or ""
            )
            date_won = (
                w.submitted_at.astimezone(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                if getattr(w, "submitted_at", None)
                else ""
            )
            status = getattr(w, "delivery_status", "") or "Pending"

            writer.writerow([full_name, phone, address, prize, date_won, status])

        tmp.flush()
        tmp.close()

        caption = (
            f"📦 Winners Export — {start_str} → {end_str} (UTC)\n"
            f"📊 Count: {len(winners)}"
        )
        admin_chat_id = getattr(
            getattr(update, "effective_user", None), "id"
        )

        # ---------------------------
        # Send using an open file object (most robust)
        # ---------------------------
        try:
            with open(tmp_path, "rb") as f:
                input_file = InputFile(f, filename=filename)

                if getattr(update, "callback_query", None):
                    await update.callback_query.message.reply_document(
                        document=input_file, caption=caption
                    )
                else:
                    await update.message.reply_document(
                        document=input_file, caption=caption
                    )

                # Slight wait to ensure upload started/completed reading file
                await asyncio.sleep(0.6)

        except Exception as e_send:
            # Log and attempt fallback with context.bot (also using an open file)
            logger.warning(
                f"⚠️ Primary send failed: {e_send}. Retrying via context.bot..."
            )

            try:
                with open(tmp_path, "rb") as f2:
                    await context.bot.send_document(
                        chat_id=admin_chat_id,
                        document=InputFile(f2, filename=filename),
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    )
                await asyncio.sleep(0.6)
            except Exception as e_fb:
                # Final fallback: inform admin and attach path (only as last resort)
                logger.error(
                    f"❌ Both sends failed: primary={e_send}, fallback={e_fb}",
                    exc_info=True,
                )
                err_msg = (
                    "❌ Failed to send CSV file automatically. "
                    "I have saved it to the bot host (path shown below). Please retrieve it manually.\n\n"
                    f"<code>{tmp_path}</code>"
                )
                if getattr(update, "callback_query", None):
                    await update.callback_query.message.reply_html(err_msg)
                else:
                    await update.message.reply_html(err_msg)
                # in this failure case, we do NOT delete the file so you can retrieve it.
                return

        # ---------------------------
        # Acknowledge success
        # ---------------------------
        success_msg = (
            f"✅ CSV exported and sent to you ({len(winners)} rows)."
        )
        if getattr(update, "callback_query", None):
            # edit the original menu message to show success (keeps chat tidy)
            try:
                await update.callback_query.edit_message_text(success_msg)
            except Exception:
                # fallback to sending as a message
                await update.callback_query.message.reply_text(success_msg)
        else:
            await update.message.reply_text(success_msg)

    except Exception as e:
        logger.exception(
            f"❌ Error during CSV generation/sending: {e}"
        )
        err_text = f"❌ Error generating CSV: <code>{e}</code>"
        if getattr(update, "callback_query", None):
            await update.callback_query.message.reply_html(err_text)
        else:
            await update.message.reply_html(err_text)

    finally:
        # ---------------------------
        # Cleanup: delete temp file if it still exists and we successfully sent it
        # If file was left for manual retrieval (failure path), don't delete.
        # ---------------------------
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                logger.info(f"🧹 Temp file deleted: {tmp_path}")
        except Exception as e_rm:
            logger.warning(
                f"⚠️ Could not delete temp file {tmp_path}: {e_rm}"
            )


# ----------------------------------
# handle_pw_mark_delivered
# ------------------------------------
async def handle_pw_mark_delivered(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()
    _, _, record_id = query.data.rpartition("_")
    if not record_id.isdigit():
        return await query.edit_message_text("Invalid record id.")
    rid = int(record_id)

    async with get_async_session() as session:
        pw = await session.get(PrizeWinner, rid)
        if not pw:
            return await query.edit_message_text("Record not found.")

        pw.delivery_status = "Delivered"
        pw.delivered_at = datetime.utcnow()
        pw.last_updated_by = update.effective_user.id
        await session.commit()

        try:
            bot = Bot(token=os.getenv("BOT_TOKEN"))
            await bot.send_message(
                chat_id=pw.tg_id,
                text=(
                    f"✅ Hi! Your prize ({pw.choice}) has been *delivered*. "
                    "Congratulations again on topping the leaderboard!"
                ),
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception("Failed to notify winner about Delivered")

    # refresh admin display
    await show_winners_section(update, context)


# ------------------------------
# Show Filtered Winners
# ------------------------------
async def show_filtered_winners(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Show winners filtered by delivery status (In Transit or Delivered)"""
    query = update.callback_query
    await query.answer()

    # Extract filter value from callback data (e.g. "In Transit" or "Delivered")
    _, filter_value = query.data.split(":", 1)

    async with get_async_session() as session:
        result = await session.execute(
            select(User).where(User.delivery_status == filter_value)
        )
        winners = result.scalars().all()

    if not winners:
        await query.edit_message_text(
            f"😅 No {filter_value} winners found yet.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Back", callback_data="admin_winners:1"
                        )
                    ]
                ]
            ),
        )
        return

    # Build winner list message
    text_lines = [f"🏆 <b>Top-Tier Campaign Reward Winners - {filter_value}</b>\n"]
    for w in winners:
        text_lines.append(
            f"👤 <b>{w.full_name or '-'}</b>\n"
            f"📱 {w.phone or 'N/A'}\n"
            f"📦 {w.address or 'N/A'}\n"
            f"🎁 {w.choice or '-'}\n"
            f"🚚 Status: <b>{w.delivery_status or 'Pending'}</b>\n"
            f"🔗 @{w.username or 'N/A'}\n"
        )

    # Inline keyboard for navigation
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Back", callback_data="admin_winners:1"
                )
            ]
        ]
    )

    await query.edit_message_text(
        "\n".join(text_lines), parse_mode="HTML", reply_markup=keyboard
    )


# -----------------------------------
# ✅ Handler for "Mark In Transit"
# ----------------------------------
async def update_delivery_status_transit(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    parts = query.data.split("_")  # pw_status_transit_12
    winner_id = int(parts[-1])

    async with get_async_session() as session:
        result = await session.execute(
            select(PrizeWinner).where(PrizeWinner.id == winner_id)
        )
        winner = result.scalar_one_or_none()

        if not winner:
            await query.answer("❌ Winner not found!", show_alert=True)
            return

        # ✅ Update & commit
        winner.delivery_status = "In Transit"
        await session.commit()

    await query.answer("✅ Marked as In Transit")
    # Refresh winner screen
    await show_winners_section(update, context)


# -------------------------------------
# ✅ Handler for "Mark Delivered"
# -------------------------------------
async def update_delivery_status_delivered(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    parts = query.data.split("_")  # pw_status_delivered_12
    winner_id = int(parts[-1])

    async with get_async_session() as session:
        result = await session.execute(
            select(PrizeWinner).where(PrizeWinner.id == winner_id)
        )
        winner = result.scalar_one_or_none()

        if not winner:
            await query.answer("❌ Winner not found!", show_alert=True)
            return

        # ✅ Update & commit
        winner.delivery_status = "Delivered"
        await session.commit()

    await query.answer("✅ Marked as Delivered")
    # Refresh winner screen
    await show_winners_section(update, context)


# ===================================================================
# 📵 Failed Airtime Payouts — Paginated Admin View
# ===================================================================
FAILED_PER_PAGE = 10

async def show_failed_airtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1]) if ":" in query.data else 1
    offset = (page - 1) * FAILED_PER_PAGE

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text("""
                SELECT id, tg_id, phone_number, amount, status, updated_at
                FROM airtime_payouts
                WHERE status IN ('failed','pending_phone','claim_phone_set')
                ORDER BY updated_at DESC
                LIMIT :limit OFFSET :offset
            """), {"limit": FAILED_PER_PAGE, "offset": offset}
        )
        rows = res.fetchall()

        total_rows = await session.scalar(
            text("""
                SELECT COUNT(*) FROM airtime_payouts
                WHERE status IN ('failed','pending_phone','claim_phone_set')
            """)
        )

    if not rows:
        return await safe_edit(
            query,
            "🟢 No failed or pending airtime payouts! System clean 🎉",
            parse_mode="HTML",
        )

    pages = (total_rows // FAILED_PER_PAGE) + (1 if total_rows % FAILED_PER_PAGE else 0)

    text_lines = []
    keyboard_rows = []

    for row in rows:
        p = row._mapping
        payout_id = p["id"]
        masked = "Unknown"
        phone = p["phone_number"]
        if phone:
            masked = phone[:-4].rjust(len(phone), "•")

        text_lines.append(
            f"⚠️ <b>Payout</b> — {payout_id}\n"
            f"👤 TG: {p['tg_id']}\n"
            f"📱 {masked}\n"
            f"💸 ₦{p['amount']}\n"
            f"⏱️ {p['status']} — {p['updated_at']}\n"
        )

        keyboard_rows.append([
            InlineKeyboardButton(
                f"🔁 Retry {payout_id}",
                callback_data=f"admin_retry:{payout_id}"
            )
        ])

    # Pagination Controls
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_airtime_failed:{page-1}"))
    if page < pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_airtime_failed:{page+1}"))

    keyboard = InlineKeyboardMarkup(keyboard_rows + [nav] + [[
        InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")
    ]])

    await safe_edit(
        query,
        "\n".join(text_lines),
        parse_mode="HTML",
        reply_markup=keyboard
    )

#--------Register Handlers--------------
def register_handlers(application):
    ADMIN_GROUP = 10  # ✅ Admin runs later than user flows

    # ✅ ADMIN COMMANDS
    application.add_handler(CommandHandler("admin", admin_panel), group=ADMIN_GROUP)
    application.add_handler(CommandHandler("pending_proofs", pending_proofs), group=ADMIN_GROUP)
    application.add_handler(CommandHandler("winners", show_winners_section), group=ADMIN_GROUP)

    # ✅ ADMIN SUB-SECTIONS
    application.add_handler(CallbackQueryHandler(pending_proofs, pattern=r"^admin_pending"), group=ADMIN_GROUP)
    application.add_handler(CallbackQueryHandler(user_search_handler, pattern=r"^admin_usersearch"), group=ADMIN_GROUP)
    application.add_handler(CallbackQueryHandler(show_winners_section, pattern=r"^admin_winners"), group=ADMIN_GROUP)
    application.add_handler(CallbackQueryHandler(show_filtered_winners, pattern=r"^admin_winners_filter:"), group=ADMIN_GROUP)
    application.add_handler(
        CallbackQueryHandler(show_top_tier_campaign_reward_points, pattern=r"^admin_menu:top_tier_campaign_reward_points$"),
        group=ADMIN_GROUP
    )

    # ✅ CSV EXPORT FLOW
    application.add_handler(CallbackQueryHandler(admin_export_csv_menu, pattern=r"^admin_export_csv$"), group=ADMIN_GROUP)
    application.add_handler(CallbackQueryHandler(export_csv_handler, pattern=r"^export_csv:"), group=ADMIN_GROUP)

    # ✅ Admin custom date input (only acts when awaiting_date_range=True)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, date_range_message_router),
        group=ADMIN_GROUP
    )

    # ✅ Admin Menu routing
    application.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin_"), group=ADMIN_GROUP)

    # ✅ Delivery status updates (NOTE: you have duplicates — keep only ONE per pattern)
    application.add_handler(
        CallbackQueryHandler(update_delivery_status_transit, pattern=r"^pw_status_transit_\d+$"),
        group=ADMIN_GROUP
    )
    application.add_handler(
        CallbackQueryHandler(update_delivery_status_delivered, pattern=r"^pw_status_delivered_\d+$"),
        group=ADMIN_GROUP
    )

    # ✅ Support reply flow (only when awaiting_support_reply=True)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, admin_support_reply_text_handler),
        group=ADMIN_GROUP
    )

    # ✅ User search text handler (also must be guarded or scoped)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, user_search_handler),
        group=ADMIN_GROUP
    )

    # Failed Airtime pagination
    application.add_handler(CallbackQueryHandler(show_failed_airtime, pattern=r"^admin_airtime_failed"), group=ADMIN_GROUP)

