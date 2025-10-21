# ==============================================================
# handlers/admin.py — Clean Unified Admin System (Final Version)
# ==============================================================

import os
import re
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
# MarkdownV2 Escape Helper
# ----------------------------
def mdv2_escape(text: str) -> str:
    if not text:
        return ""
    return re.sub(r'([_\*\[\]\(\)~`>#+\-=|{}\.!\\])', r'\\\1', str(text))

# ----------------------------
# Command: /admin (Main Panel)
# ----------------------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.message.reply_text("❌ Access denied.", parse_mode="MarkdownV2")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Pending Proofs", callback_data="admin_menu:pending_proofs")],
        [InlineKeyboardButton("📊 Stats", callback_data="admin_menu:stats")],
        [InlineKeyboardButton("👤 User Search", callback_data="admin_menu:user_search")],
        [InlineKeyboardButton("🏆 Winners", callback_data="admin_winners")],
    ])
    await update.message.reply_text("⚙️ *Admin Panel*\nChoose an action:", parse_mode="MarkdownV2", reply_markup=keyboard)

# ----------------------------
# Pending Proofs
# ----------------------------
async def pending_proofs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.effective_message.reply_text("❌ Access denied.", parse_mode="MarkdownV2")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Proof).where(Proof.status == "pending"))
        proofs = result.scalars().all()

    if not proofs:
        if getattr(update, "callback_query", None):
            return await update.callback_query.edit_message_text("✅ No pending proofs at the moment.", parse_mode="MarkdownV2")
        return await update.effective_message.reply_text("✅ No pending proofs at the moment.", parse_mode="MarkdownV2")

    for proof in proofs:
        user = await get_user_by_id(proof.user_id)
        user_name = mdv2_escape(user.username or user.first_name if user else str(proof.user_id))
        caption = f"*Pending Proof*\n👤 User: {user_name}\n🆔 Proof ID: `{mdv2_escape(proof.id)}`"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve:{proof.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject:{proof.id}")
            ]
        ])
        await update.effective_message.reply_photo(photo=proof.file_id, caption=caption, parse_mode="MarkdownV2", reply_markup=keyboard)

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
        return await safe_edit("❌ Access denied.", parse_mode="MarkdownV2")

    if query.data.startswith("admin_menu:"):
        action = query.data.split(":")[1]

        if action == "pending_proofs":
            return await pending_proofs(update, context)

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
                days = diff.days
                hours = int(diff.seconds / 3600)
                since_text = f"{days}d {hours}h ago"
            else:
                since_text = "Unknown"

            text = (f"📊 *Bot Stats*\n\n"
                    f"💰 Lifetime Paid Tries: {lifetime_paid}\n"
                    f"🔄 Current Cycle: {current_cycle}\n"
                    f"🎯 Paid Tries (cycle): {paid_this_cycle}\n"
                    f"🕒 Cycle Started: {since_text}")
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Reset Cycle", callback_data="admin_confirm:reset_cycle")],
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")]
            ])
            return await safe_edit(text, parse_mode="MarkdownV2", reply_markup=keyboard)

        elif action == "user_search":
            return await safe_edit("👤 User search coming soon...", parse_mode="MarkdownV2")

        elif query.data == "admin_winners":
            return await show_winners_section(update, context)

        elif action == "main":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 Pending Proofs", callback_data="admin_menu:pending_proofs")],
                [InlineKeyboardButton("📊 Stats", callback_data="admin_menu:stats")],
                [InlineKeyboardButton("👤 User Search", callback_data="admin_menu:user_search")],
                [InlineKeyboardButton("🏆 Winners", callback_data="admin_winners")],
            ])
            return await safe_edit("⚙️ *Admin Panel*\nChoose an action:", parse_mode="MarkdownV2", reply_markup=keyboard)

    # Cycle reset confirm
    if query.data.startswith("admin_confirm:reset_cycle"):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Reset", callback_data="admin_action:reset_cycle"),
             InlineKeyboardButton("❌ Cancel", callback_data="admin_menu:stats")]
        ])
        return await safe_edit("⚠️ *Are you sure you want to reset the cycle?*", parse_mode="MarkdownV2", reply_markup=keyboard)

    # Cycle reset action
    if query.data == "admin_action:reset_cycle":
        async with AsyncSessionLocal() as session:
            gs = await session.get(GameState, 1)
            if not gs:
                return await safe_edit("⚠️ GameState not found.", parse_mode="MarkdownV2")
            gs.current_cycle += 1
            gs.paid_tries_this_cycle = 0
            gs.created_at = datetime.utcnow()
            await session.commit()
        await query.answer("✅ Cycle reset!", show_alert=True)
        return await safe_edit("🔁 *Cycle Reset!* New round begins.", parse_mode="MarkdownV2")

    # Proof approve/reject
    try:
        action, proof_id = query.data.split(":")
        proof_id = int(proof_id)
    except Exception:
        return await safe_edit("⚠️ Invalid callback data.", parse_mode="MarkdownV2")

    async with AsyncSessionLocal() as session:
        proof = await session.get(Proof, proof_id)
        if not proof or proof.status != "pending":
            return await safe_edit("⚠️ Proof already processed.", parse_mode="MarkdownV2")

        if action == "admin_approve":
            proof.status = "approved"
            await add_tries(proof.user_id, 1, paid=False)
            msg = "✅ Proof approved and bonus try added!"
        else:
            proof.status = "rejected"
            msg = "❌ Proof rejected."
        await session.commit()
    return await safe_edit(msg, parse_mode="MarkdownV2")

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
        await update.message.reply_text("⚠️ No user found.", parse_mode="MarkdownV2")
    else:
        reply = (f"👤 *User Info*\n"
                 f"🆔 ID: `{mdv2_escape(user.id)}`\n"
                 f"📛 Username: {mdv2_escape(user.username or '-')}\n"
                 f"🎲 Paid: {user.tries_paid} | Bonus: {user.tries_bonus}\n"
                 f"🎁 Choice: {mdv2_escape(user.choice or '-')}")
        await update.message.reply_text(reply, parse_mode="MarkdownV2")
    context.user_data["awaiting_user_search"] = False

# ----------------------------
# Winners Section (with Filters + Pagination)
# ----------------------------
WINNERS_PER_PAGE = 5  # Adjustable anytime

async def show_winners_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show paginated list of winners with filters + delivery control buttons"""
    query = getattr(update, "callback_query", None)
    await (query.answer() if query else asyncio.sleep(0))  # Prevent Telegram 'loading' hang

    # -----------------------------
    # 1️⃣ Parse callback data
    # -----------------------------
    page = 1
    filter_status = None  # Can be: None (all), 'Pending', 'In Transit', 'Delivered'

    if query:
        data_parts = query.data.split(":")  # e.g. admin_winners:pending:2
        if len(data_parts) >= 2:
            if data_parts[1] in ["pending", "transit", "delivered"]:
                filter_status = {
                    "pending": "Pending",
                    "transit": "In Transit",
                    "delivered": "Delivered",
                }[data_parts[1]]
            if len(data_parts) == 3 and data_parts[2].isdigit():
                page = int(data_parts[2])

    # -----------------------------
    # 2️⃣ Fetch winners from DB
    # -----------------------------
    async with get_async_session() as session:
        query_base = select(User).where(User.choice.isnot(None))
        if filter_status:
            if filter_status == "Pending":
                query_base = query_base.where(
                    (User.delivery_status.is_(None)) | (User.delivery_status == "Pending")
                )
            else:
                query_base = query_base.where(User.delivery_status == filter_status)

        total_query = await session.execute(query_base.order_by(User.id.desc()))
        all_winners = total_query.scalars().all()
        winners = all_winners[(page - 1) * WINNERS_PER_PAGE: page * WINNERS_PER_PAGE]

    # -----------------------------
    # 3️⃣ No winners found
    # -----------------------------
    if not winners:
        text = "😅 No winners found for this category."
        if query:
            return await query.edit_message_text(text)
        return await update.effective_message.reply_text(text)

    # -----------------------------
    # 4️⃣ Build message text
    # -----------------------------
    total_pages = max(1, (len(all_winners) + WINNERS_PER_PAGE - 1) // WINNERS_PER_PAGE)
    filter_label = (
        "🟡 Pending" if filter_status == "Pending"
        else "🚚 In Transit" if filter_status == "In Transit"
        else "✅ Delivered" if filter_status == "Delivered"
        else "🏆 All Winners"
    )

    text_lines = [f"{filter_label} (Page {page}/{total_pages})\n"]

    for w in winners:
        text_lines.append(
            f"👤 <b>{w.full_name}</b>\n"
            f"📱 {w.phone or 'N/A'}\n"
            f"📦 {w.address or 'N/A'}\n"
            f"🎁 {w.choice or '-'}\n"
            f"🚚 Status: <b>{w.delivery_status or 'Pending'}</b>\n"
            f"🔗 @{w.username or 'N/A'}\n"
        )

    # -----------------------------
    # 5️⃣ Build inline keyboard
    # -----------------------------
    rows = []

    # Per-winner delivery controls (only if not yet delivered)
    for w in winners:
        if w.delivery_status != "Delivered":
            rows.append([
                InlineKeyboardButton("🚚 In Transit", callback_data=f"status_transit_{w.id}"),
                InlineKeyboardButton("✅ Delivered", callback_data=f"status_delivered_{w.id}")
            ])

    # Navigation (pagination)
    nav_buttons = []
    base_prefix = f"admin_winners:{'pending' if filter_status == 'Pending' else 'transit' if filter_status == 'In Transit' else 'delivered' if filter_status == 'Delivered' else 'all'}"
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{base_prefix}:{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next ⏩", callback_data=f"{base_prefix}:{page+1}"))
    if nav_buttons:
        rows.append(nav_buttons)

    # Filter buttons
    filter_buttons = [
        InlineKeyboardButton("🟡 Pending", callback_data="admin_winners:pending:1"),
        InlineKeyboardButton("🚚 In Transit", callback_data="admin_winners:transit:1"),
        InlineKeyboardButton("✅ Delivered", callback_data="admin_winners:delivered:1"),
    ]
    rows.append(filter_buttons)

    # Back button
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")])

    keyboard = InlineKeyboardMarkup(rows)

    # -----------------------------
    # 6️⃣ Send or edit message
    # -----------------------------
    if query:
        await query.edit_message_text(
            "\n".join(text_lines),
            parse_mode="HTML",
            reply_markup=keyboard
        )
    else:
        await update.effective_message.reply_text(
            "\n".join(text_lines),
            parse_mode="HTML",
            reply_markup=keyboard
        )

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

    # Notify the winner
    if winner and winner.tg_id:
        msg = ("🚚 <b>Your prize is on the way!</b>" if new_status == "In Transit"
               else "✅ <b>Your prize has been delivered!</b>")
        try:
            await context.bot.send_message(chat_id=winner.tg_id, text=msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to DM winner: {e}")

    # Optional: alert admin privately on delivery
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
    application.add_handler(CallbackQueryHandler(handle_delivery_status, pattern=r"^admin_status_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_search_handler))
