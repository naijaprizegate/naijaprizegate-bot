# ==============================================================
# handlers/admin.py — Clean Unified Admin System (HTML Safe)
# ==============================================================

import os
import re
import asyncio
import logging
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from sqlalchemy import select, update
from db import AsyncSessionLocal, get_async_session
from helpers import add_tries, get_user_by_id
from models import Proof, User, GameState, GlobalCounter

logger = logging.getLogger(__name__)

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

# ----------------------------
# Command: /admin (Main Panel)
# ----------------------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.message.reply_text("❌ Access denied.", parse_mode="HTML")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Pending Proofs", callback_data="admin_menu:pending_proofs")],
        [InlineKeyboardButton("📊 Stats", callback_data="admin_menu:stats")],
        [InlineKeyboardButton("👤 User Search", callback_data="admin_menu:user_search")],
        [InlineKeyboardButton("🏆 Winners", callback_data="admin_menu:winners")],
    ])
    await update.message.reply_text(
        "⚙️ <b>Admin Panel</b>\nChoose an action:",
        parse_mode="HTML",
        reply_markup=keyboard
    )

# ----------------------------
# Pending Proofs
# ----------------------------
async def pending_proofs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.effective_message.reply_text("❌ Access denied.", parse_mode="HTML")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Proof).where(Proof.status == "pending"))
        proofs = result.scalars().all()

    if not proofs:
        text = "✅ No pending proofs at the moment."
        if getattr(update, "callback_query", None):
            return await update.callback_query.edit_message_text(text, parse_mode="HTML")
        return await update.effective_message.reply_text(text, parse_mode="HTML")

    for proof in proofs:
        user = await get_user_by_id(proof.user_id)
        user_name = user.username or user.first_name or str(proof.user_id)
        caption = (
            f"<b>Pending Proof</b>\n"
            f"👤 User: @{user_name}\n"
            f"🆔 Proof ID: <code>{proof.id}</code>"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve:{proof.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject:{proof.id}")
            ]
        ])
        await update.effective_message.reply_photo(
            photo=proof.file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )

# ----------------------------
# Admin Callback Router
# ----------------------------
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    async def safe_edit(text, **kwargs):
        try:
            if query.message.photo:
                await query.edit_message_caption(caption=text, **kwargs)
            else:
                await query.edit_message_text(text, **kwargs)
        except Exception as e:
            logger.warning(f"[WARN] edit fail: {e}")

    if user_id != ADMIN_USER_ID:
        return await safe_edit("❌ Access denied.", parse_mode="HTML")

    if query.data.startswith("admin_menu:"):
        action = query.data.split(":")[1]

        # ---- Pending Proofs ----
        if action == "pending_proofs":
            return await pending_proofs(update, context)

        # ---- Stats ----
        elif action == "stats":
            async with AsyncSessionLocal() as session:
                gc = await session.get(GlobalCounter, 1)
                gs = await session.get(GameState, 1)
            lifetime_paid = gc.paid_tries_total if gc else 0
            current_cycle = gs.current_cycle if gs else 1
            paid_this_cycle = gs.paid_tries_this_cycle if gs else 0
            created_at = gs.created_at if gs else None

            if created_at:
                diff = datetime.now(timezone.utc) - created_at
                since_text = f"{diff.days}d {int(diff.seconds / 3600)}h ago"
            else:
                since_text = "Unknown"

            text = (
                f"<b>📊 Bot Stats</b>\n\n"
                f"💰 Lifetime Paid Tries: {lifetime_paid}\n"
                f"🔄 Current Cycle: {current_cycle}\n"
                f"🎯 Paid Tries (cycle): {paid_this_cycle}\n"
                f"🕒 Cycle Started: {since_text}"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Reset Cycle", callback_data="admin_confirm:reset_cycle")],
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")]
            ])
            return await safe_edit(text, parse_mode="HTML", reply_markup=keyboard)

        # ---- User Search ----
        elif action == "user_search":
            context.user_data["awaiting_user_search"] = True
            return await safe_edit(
                "🔍 <b>Send username or user ID</b> to search for a user.",
                parse_mode="HTML"
            )

        # ---- Winners ----
        elif action == "winners":
            return await show_winners_section(update, context)

        # ---- Main Menu ----
        elif action == "main":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 Pending Proofs", callback_data="admin_menu:pending_proofs")],
                [InlineKeyboardButton("📊 Stats", callback_data="admin_menu:stats")],
                [InlineKeyboardButton("👤 User Search", callback_data="admin_menu:user_search")],
                [InlineKeyboardButton("🏆 Winners", callback_data="admin_menu:winners")],
            ])
            return await safe_edit(
                "⚙️ <b>Admin Panel</b>\nChoose an action:",
                parse_mode="HTML",
                reply_markup=keyboard
            )

    # ---- Cycle reset confirm ----
    if query.data.startswith("admin_confirm:reset_cycle"):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Reset", callback_data="admin_action:reset_cycle"),
             InlineKeyboardButton("❌ Cancel", callback_data="admin_menu:stats")]
        ])
        return await safe_edit("⚠️ Are you sure you want to reset the cycle?", parse_mode="HTML", reply_markup=keyboard)

    # ---- Cycle reset action ----
    if query.data == "admin_action:reset_cycle":
        async with AsyncSessionLocal() as session:
            gs = await session.get(GameState, 1)
            if not gs:
                return await safe_edit("⚠️ GameState not found.", parse_mode="HTML")
            gs.current_cycle += 1
            gs.paid_tries_this_cycle = 0
            gs.created_at = datetime.utcnow()
            await session.commit()
        await query.answer("✅ Cycle reset!", show_alert=True)
        return await safe_edit("🔁 <b>Cycle Reset!</b> New round begins.", parse_mode="HTML")

    # ---- Proof approve/reject ----
    try:
        action, proof_id = query.data.split(":")
    except ValueError:
        return await safe_edit("⚠️ Invalid callback data.", parse_mode="HTML")

    async with AsyncSessionLocal() as session:
        proof = await session.get(Proof, proof_id)
        if not proof or proof.status != "pending":
            return await safe_edit("⚠️ Proof already processed.", parse_mode="HTML")

        if action == "admin_approve":
            proof.status = "approved"
            await add_tries(proof.user_id, 1, paid=False)
            msg = "✅ Proof approved and bonus try added!"
        else:
            proof.status = "rejected"
            msg = "❌ Proof rejected."
        await session.commit()
    return await safe_edit(msg, parse_mode="HTML")

# ----------------------------
# User Search Handler
# ----------------------------
async def user_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID or not context.user_data.get("awaiting_user_search"):
        return
    query_text = update.message.text.strip()
    async with AsyncSessionLocal() as session:
        if query_text.isdigit():
            user = await session.get(User, query_text)
        else:
            result = await session.execute(select(User).where(User.username == query_text))
            user = result.scalars().first()
    if not user:
        await update.message.reply_text("⚠️ No user found.", parse_mode="HTML")
    else:
        reply = (
            f"<b>👤 User Info</b>\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"📛 Username: @{user.username or '-'}\n"
            f"🎲 Paid: {user.tries_paid} | Bonus: {user.tries_bonus}\n"
            f"🎁 Choice: {user.choice or '-'}"
        )
        await update.message.reply_text(reply, parse_mode="HTML")
    context.user_data["awaiting_user_search"] = False

# ----------------------------
# Winners Section (with Pagination)
# ----------------------------
WINNERS_PER_PAGE = 5

async def show_winners_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = getattr(update, "callback_query", None)
    await (query.answer() if query else asyncio.sleep(0))

    page = 1
    if query and ":" in query.data:
        parts = query.data.split(":")
        if len(parts) == 2 and parts[1].isdigit():
            page = int(parts[1])

    offset = (page - 1) * WINNERS_PER_PAGE
    async with get_async_session() as session:
        total_query = await session.execute(select(User).where(User.choice.isnot(None)))
        all_winners = total_query.scalars().all()
        winners = all_winners[offset:offset + WINNERS_PER_PAGE]

    if not winners:
        text = "😅 No winners found yet."
        if query:
            return await query.edit_message_text(text, parse_mode="HTML")
        return await update.effective_message.reply_text(text, parse_mode="HTML")

    total_pages = max(1, (len(all_winners) + WINNERS_PER_PAGE - 1) // WINNERS_PER_PAGE)
    text_lines = [f"🏆 <b>Jackpot Winners</b> (Page {page}/{total_pages})\n"]

    for w in winners:
        text_lines.append(
            f"👤 <b>{w.full_name}</b>\n"
            f"📱 {w.phone or 'N/A'}\n"
            f"📦 {w.address or 'N/A'}\n"
            f"🎁 {w.choice or '-'}\n"
            f"🚚 Status: <b>{w.delivery_status or 'Pending'}</b>\n"
            f"🔗 @{w.username or 'N/A'}\n"
        )

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_winners:{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next ⏩", callback_data=f"admin_winners:{page+1}"))

    rows = []
    for w in winners:
        rows.append([
            InlineKeyboardButton("🚚 In Transit", callback_data=f"status_transit_{w.id}"),
            InlineKeyboardButton("✅ Delivered", callback_data=f"status_delivered_{w.id}")
        ])
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")])

    keyboard = InlineKeyboardMarkup(rows)

    if query:
        await query.edit_message_text("\n".join(text_lines), parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.effective_message.reply_text("\n".join(text_lines), parse_mode="HTML", reply_markup=keyboard)

# ----------------------------
# Delivery Status Update
# ----------------------------
async def handle_delivery_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_USER_ID:
        return await query.answer("🚫 Not authorized.", show_alert=True)

    _, status, user_id = query.data.split("_", 2)
    new_status = "Delivered" if status == "delivered" else "In Transit"

    async with get_async_session() as session:
        async with session.begin():
            await session.execute(update(User).where(User.id == user_id).values(delivery_status=new_status))
            result = await session.execute(select(User).where(User.id == user_id))
            winner = result.scalar_one_or_none()
        await session.commit()

    await query.edit_message_reply_markup(None)
    await query.message.reply_text(f"✅ Updated {winner.full_name} → <b>{new_status}</b>.", parse_mode="HTML")

    if winner and winner.tg_id:
        msg = ("🚚 <b>Your prize is on the way!</b>" if new_status == "In Transit"
               else "✅ <b>Your prize has been delivered!</b>")
        try:
            await context.bot.send_message(chat_id=winner.tg_id, text=msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to DM winner: {e}")

    if new_status == "Delivered":
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"📦 Delivery Confirmed\n🎉 {winner.full_name}'s prize marked as delivered.",
            parse_mode="HTML"
        )

# ----------------------------
# Register Handlers
# ----------------------------
def register_handlers(application):
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("pending_proofs", pending_proofs))
    application.add_handler(CommandHandler("winners", show_winners_section))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    application.add_handler(CallbackQueryHandler(handle_delivery_status, pattern=r"^status_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_search_handler))
