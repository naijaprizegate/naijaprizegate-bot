# ==============================================================
# handlers/admin.py — MarkdownV2 Safe, Clean Version
# ==============================================================

import os
import re
import logging
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from sqlalchemy import select, update
from db import AsyncSessionLocal
from helpers import add_tries, get_user_by_id
from models import Proof, User, GameState, GlobalCounter

logger = logging.getLogger(__name__)

# Admin ID from environment
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

# ----------------------------
# MarkdownV2 Escape Helper
# ----------------------------
def mdv2_escape(text: str) -> str:
    """
    Escapes all special characters for MarkdownV2 formatting.
    Covers underscores, asterisks, brackets, parentheses, tilde, backtick,
    greater/less, equals, dash, pipe, dot, exclamation, and backslash.
    """
    if not text:
        return ""
    return re.sub(r'([_\*\[\]\(\)~`>#+\-=|{}\.!\\])', r'\\\1', str(text))


# ----------------------------
# Command: /admin (Main Panel)
# ----------------------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the admin main panel with options"""
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.message.reply_text("❌ Access denied\\.", parse_mode="MarkdownV2")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Pending Proofs", callback_data="admin_menu:pending_proofs")],
        [InlineKeyboardButton("📊 Stats", callback_data="admin_menu:stats")],
        [InlineKeyboardButton("👤 User Search", callback_data="admin_menu:user_search")],
    ])

    await update.message.reply_text(
        "⚙️ *Admin Panel*\nChoose an action:",
        parse_mode="MarkdownV2",
        reply_markup=keyboard
    )


# ----------------------------
# Command: /pending_proofs
# ----------------------------
async def pending_proofs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all pending proofs with Approve/Reject buttons"""
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.effective_message.reply_text("❌ Access denied\\.", parse_mode="MarkdownV2")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Proof).where(Proof.status == "pending"))
        proofs = result.scalars().all()

    if not proofs:
        # If triggered from a button → edit the message
        if getattr(update, "callback_query", None):
            return await update.callback_query.edit_message_text("✅ No pending proofs at the moment\\.", parse_mode="MarkdownV2")
        # If triggered from command → send a new message
        return await update.effective_message.reply_text("✅ No pending proofs at the moment\\.", parse_mode="MarkdownV2")

    for proof in proofs:
        user = await get_user_by_id(proof.user_id)
        user_name = mdv2_escape(user.username or user.first_name if user else str(proof.user_id))

        caption = (
            f"*Pending Proof*\n"
            f"👤 User: {user_name}\n"
            f"🆔 Proof ID: `{mdv2_escape(proof.id)}`"
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
            parse_mode="MarkdownV2",
            reply_markup=keyboard
        )


# ----------------------------
# Callback: Approve / Reject / Menu Actions
# ----------------------------
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Approve/Reject and menu clicks"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"

    # --- Helper: Safe edit (handles caption/text safely & auto-escapes MarkdownV2)
    async def safe_edit(query, text, parse_mode=None, **kwargs):
        if parse_mode == "MarkdownV2":
            text = mdv2_escape(text)
        try:
            if query.message.photo:
                await query.edit_message_caption(caption=text, parse_mode=parse_mode, **kwargs)
            else:
                await query.edit_message_text(text, parse_mode=parse_mode, **kwargs)
        except Exception as e:
            logger.warning(f"[WARN] safe_edit failed: {e}")
            try:
                await query.edit_message_text(text, parse_mode=parse_mode, **kwargs)
            except Exception as e2:
                logger.error(f"[FATAL] Both edit attempts failed: {e2}")

    # --- Access control ---
    if user_id != ADMIN_USER_ID:
        return await safe_edit(query, "❌ Access denied.", parse_mode="MarkdownV2")

    # --- Admin menu navigation ---
    if query.data.startswith("admin_menu:"):
        action = query.data.split(":")[1]

        if action == "pending_proofs":
            return await pending_proofs(update, context)

        elif action == "stats":
            async with AsyncSessionLocal() as session:
                gc = await session.get(GlobalCounter, 1)
                gs = await session.get(GameState, 1)

            lifetime_paid = gc.paid_tries_total if gc else 0
            lifetime_paid_tries = getattr(gs, "lifetime_paid_tries", 0)
            current_cycle = gs.current_cycle if gs else 1
            paid_this_cycle = gs.paid_tries_this_cycle if gs else 0
            created_at = gs.created_at if gs else None

            # 🕒 Time since cycle start
            if created_at:
                now = datetime.now(timezone.utc)
                diff = now - created_at
                hours = diff.total_seconds() // 3600
                days = int(hours // 24)
                hours = int(hours % 24)
                since_text = f"{days}d {hours}h ago" if days > 0 else f"{hours}h ago" if hours > 0 else "Less than 1h ago"
            else:
                since_text = "Unknown"

            stats_text = (
                "📊 *Bot Stats*\n\n"
                f"💰 Lifetime Paid Tries: {lifetime_paid}\n"
                f"💎 Lifetime Paid Tries (GameState): {lifetime_paid_tries}\n"
                f"🔄 Current Cycle: {current_cycle}\n"
                f"🎯 Paid Tries (this cycle): {paid_this_cycle}\n"
                f"🕒 Cycle Started: {since_text}"
            )

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Reset Cycle", callback_data="admin_confirm:reset_cycle")],
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")]
            ])

            return await safe_edit(query, stats_text, parse_mode="MarkdownV2", reply_markup=keyboard)

        elif action == "user_search":
            return await safe_edit(query, "👤 User search coming soon...", parse_mode="MarkdownV2")

        elif action == "main":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 Pending Proofs", callback_data="admin_menu:pending_proofs")],
                [InlineKeyboardButton("📊 Stats", callback_data="admin_menu:stats")],
                [InlineKeyboardButton("👤 User Search", callback_data="admin_menu:user_search")],
            ])
            return await safe_edit(query, "⚙️ *Admin Panel*\nChoose an action:", parse_mode="MarkdownV2", reply_markup=keyboard)

    # --- Confirmation step for reset cycle ---
    if query.data.startswith("admin_confirm:"):
        action = query.data.split(":")[1]
        if action == "reset_cycle":
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Yes, Reset", callback_data="admin_action:reset_cycle"),
                    InlineKeyboardButton("❌ Cancel", callback_data="admin_menu:stats")
                ]
            ])
            confirm_text = "⚠️ *Are you sure you want to reset the current cycle?*\n_This will increment the cycle and reset paid tries count._"
            return await safe_edit(query, confirm_text, parse_mode="MarkdownV2", reply_markup=keyboard)

    # --- Actual reset cycle action ---
    if query.data == "admin_action:reset_cycle":
        async with AsyncSessionLocal() as session:
            gs = await session.get(GameState, 1)
            if not gs:
                return await safe_edit(query, "⚠️ GameState not found.", parse_mode="MarkdownV2")

            gs.current_cycle += 1
            gs.paid_tries_this_cycle = 0
            gs.created_at = datetime.utcnow()
            new_cycle = gs.current_cycle
            reset_time = gs.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            await session.commit()

        logger.info(f"[ADMIN] Cycle reset by {username} (ID: {user_id}) → New Cycle: {new_cycle} at {reset_time}")
        await query.answer("✅ Cycle reset successfully!", show_alert=True)

        reset_msg = (
            f"🔁 *Cycle Reset Successfully!*\n\n"
            f"🆕 New Cycle: {new_cycle}\n"
            f"🕒 Reset Time: {reset_time}\n\n"
            "Let the new jackpot hunt begin 🚀"
        )

        return await safe_edit(query, reset_msg, parse_mode="MarkdownV2")

    # --- Handle approve/reject proof actions ---
    try:
        action, proof_id = query.data.split(":")
        proof_id = int(proof_id)
    except Exception:
        return await safe_edit(query, "⚠️ Invalid callback data.", parse_mode="MarkdownV2")

    async with AsyncSessionLocal() as session:
        proof = await session.get(Proof, proof_id)
        if not proof or proof.status != "pending":
            return await safe_edit(query, "⚠️ Proof already processed.", parse_mode="MarkdownV2")

        if action == "admin_approve":
            proof.status = "approved"
            await add_tries(proof.user_id, 1, paid=False)
            caption = "✅ Proof approved and bonus try added!"
            await query.answer("✅ Proof approved!")
        else:
            proof.status = "rejected"
            caption = "❌ Proof rejected."
            await query.answer("🚫 Proof rejected.")

        await session.commit()

    return await safe_edit(query, caption, parse_mode="MarkdownV2")


# ----------------------------
# User Search Handler
# ----------------------------
async def user_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user text input for search"""
    if update.effective_user.id != ADMIN_USER_ID or not context.user_data.get("awaiting_user_search"):
        return

    query_text = update.message.text.strip()
    async with AsyncSessionLocal() as session:
        user = None
        if query_text.isdigit():
            user = await session.get(User, query_text)
        else:
            result = await session.execute(select(User).where(User.username == query_text))
            user = result.scalars().first()

    if not user:
        await update.message.reply_text("⚠️ No user found\\.", parse_mode="MarkdownV2")
    else:
        reply = (
            f"👤 *User Info*\n"
            f"🆔 UUID: `{mdv2_escape(user.id)}`\n"
            f"📛 Username: {mdv2_escape(user.username or '-')}\n"
            f"🎲 Paid Tries: {user.tries_paid}\n"
            f"🎁 Bonus Tries: {user.tries_bonus}\n"
            f"👥 Referred By: {mdv2_escape(user.referred_by or 'None')}"
        )
        await update.message.reply_text(reply, parse_mode="MarkdownV2")

    context.user_data["awaiting_user_search"] = False

# ---------------------------------------------
# 🧠 ADMIN-ONLY: View and Manage Winners
# ---------------------------------------------

async def winners_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    # ✅ Restrict to admin
    if tg_user.id != ADMIN_TG_ID:
        await update.message.reply_text("🚫 You’re not authorized to use this command.")
        return

    async with get_async_session() as session:
        result = await session.execute(
            select(User)
            .where(User.full_name.isnot(None))
            .order_by(User.created_at.desc())
            .limit(10)
        )
        winners = result.scalars().all()

        if not winners:
            await update.message.reply_text("😅 No winners found yet.")
            return

        for w in winners:
            text = (
                f"🏆 <b>Winner:</b> {w.full_name}\n"
                f"🎁 <b>Choice:</b> {w.choice or 'N/A'}\n"
                f"📱 <b>Phone:</b> {w.phone}\n"
                f"🏠 <b>Address:</b> {w.address}\n"
                f"🆔 <b>Telegram:</b> @{w.username or '—'}\n"
                f"📦 <b>Status:</b> {w.delivery_status}\n"
                f"🕒 <i>{w.created_at.strftime('%Y-%m-%d %H:%M')}</i>"
            )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🚚 In Transit", callback_data=f"status_transit_{w.id}"),
                    InlineKeyboardButton("✅ Delivered", callback_data=f"status_delivered_{w.id}")
                ]
            ])

            await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

# ------------------------------------------------
# 📦 Update Winner Delivery Status (Admin Action)
# ------------------------------------------------
async def handle_delivery_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg_user = query.from_user
    if tg_user.id != ADMIN_TG_ID:
        await query.answer("🚫 Not authorized.", show_alert=True)
        return

    data = query.data  # e.g. "status_delivered_<uuid>"
    if not data.startswith("status_"):
        return

    _, status, user_id = data.split("_", 2)
    new_status = "Delivered" if status == "delivered" else "In Transit"

    async with get_async_session() as session:
        async with session.begin():
            # ✅ Update DB
            await session.execute(
                update(User)
                .where(User.id == user_id)
                .values(delivery_status=new_status)
            )

            # ✅ Get winner details
            result = await session.execute(select(User).where(User.id == user_id))
            winner = result.scalar_one_or_none()

        await session.commit()

    # ✅ Acknowledge to admin
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✅ Updated status for <b>{winner.full_name}</b> to <b>{new_status}</b>.",
        parse_mode="HTML"
    )

    # ✅ Notify winner
    if winner and winner.tg_id:
        try:
            if new_status == "In Transit":
                msg = (
                    f"🚚 <b>Good news, {winner.full_name}!</b>\n\n"
                    "Your prize is <b>on the way</b> 🎁📦\n\n"
                    "Our delivery team will contact you soon."
                )
            else:
                msg = (
                    f"✅ <b>Congratulations again, {winner.full_name}!</b>\n\n"
                    "Your prize has been <b>successfully delivered</b> 🥳🎉\n\n"
                    "Enjoy your new iPhone and keep spinning on TryLuck! 🍀"
                )

            await context.bot.send_message(
                chat_id=winner.tg_id,
                text=msg,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"❌ Failed to notify winner ({winner.tg_id}): {e}")

# ----------------------------
# Handler registration helper
# ----------------------------
def register_handlers(application):
    """Register admin command and callback handlers"""
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("pending_proofs", pending_proofs))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_search_handler))
    application.add_handler(CommandHandler("winners", winners_admin_handler))
    application.add_handler(CallbackQueryHandler(handle_delivery_status, pattern=r"^status_"))

