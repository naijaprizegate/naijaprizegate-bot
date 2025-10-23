# ==============================================================
# handlers/admin.py — Clean Unified Admin System (HTML Safe)
# ==============================================================
import os
import re
import asyncio
import logging
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram import InputMediaPhoto
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.error import BadRequest
from sqlalchemy import select, update as sql_update
from db import AsyncSessionLocal, get_async_session
from helpers import add_tries, get_user_by_id
from models import Proof, User, GameState, GlobalCounter

logger = logging.getLogger(__name__)

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

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
# Pending Proofs (Paginated View + Back to Admin)
# ----------------------------
async def pending_proofs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show one pending proof at a time with Next/Prev navigation."""
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.effective_message.reply_text("❌ Access denied.", parse_mode="HTML")

    # --- Fetch pending proofs
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Proof).where(Proof.status == "pending"))
        proofs = result.scalars().all()

    # --- Handle no pending proofs
    if not proofs:
        text = "✅ No pending proofs at the moment.\n\nClick on /admin to go back to the Admin Panel."
        if getattr(update, "callback_query", None):
            return await update.callback_query.edit_message_text(text, parse_mode="HTML")
        return await update.effective_message.reply_text(text, parse_mode="HTML")

    # --- Initialize pagination state in context
    context.user_data["pending_proofs"] = [str(p.id) for p in proofs]
    context.user_data["proof_index"] = 0

    # --- Display the first proof
    await show_single_proof(update, context, index=0)


# ----------------------------
# Helper: Show a single proof page
# ----------------------------
async def show_single_proof(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int):
    """Render one proof (by index) with Prev/Next buttons + Back to Admin."""
    proof_ids = context.user_data.get("pending_proofs", [])
    if not proof_ids:
        if getattr(update, "callback_query", None):
            return await update.callback_query.edit_message_text("✅ No proofs loaded.", parse_mode="HTML")
        return await update.effective_message.reply_text("✅ No proofs loaded.", parse_mode="HTML")

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
                return await update.callback_query.edit_message_text("⚠️ Proof not found.", parse_mode="HTML")
            return await update.effective_message.reply_text("⚠️ Proof not found.", parse_mode="HTML")

        user = await get_user_by_id(session, proof.user_id)

    user_name = (
        f"@{user.username}" if user and user.username
        else user.first_name or str(proof.user_id)
    )

    caption = (
        f"<b>📤 Pending Proof {index + 1} of {total}</b>\n\n"
        f"👤 User: {user_name}\n"
        f"🆔 Proof ID: <code>{proof.id}</code>"
    )

    # --- Navigation buttons
    nav_buttons = []
    if index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_proofnav:{index - 1}"))
    if index < total - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_proofnav:{index + 1}"))

    # --- Inline keyboard (Approve/Reject + Nav + Back)
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve:{proof.id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject:{proof.id}"),
        ],
        nav_buttons,
        [InlineKeyboardButton("🔙 Back to Admin Menu", callback_data="admin_menu:main")],
    ]

    # Remove empty rows
    keyboard = InlineKeyboardMarkup([row for row in keyboard if row])

    # --- Send or edit the message depending on context
    try:
        if getattr(update, "callback_query", None):
            try:
                await update.callback_query.edit_message_media(
                    media=InputMediaPhoto(media=proof.file_id, caption=caption, parse_mode="HTML"),
                    reply_markup=keyboard,
                )
            except Exception as e:
                # fallback if not a photo message or Telegram rejects edit
                logger.warning(f"⚠️ Fallback to sending new proof message: {e}")
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
            logger.error(f"⚠️ Nested Telegram error while showing proof: {inner_e}")


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
            return await query.answer("⚠️ Invalid navigation index.", show_alert=True)
        return await show_single_proof(update, context, index=new_index)

    # ----------------------------
    # Admin Menu Routing
    # ----------------------------
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
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")],
            ])
            return await safe_edit(query, text, parse_mode="HTML", reply_markup=keyboard)

        # ---- User Search ----
        elif action == "user_search":
            context.user_data["awaiting_user_search"] = True
            return await safe_edit(query, "🔍 <b>Send username or user ID</b> to search for a user.", parse_mode="HTML")

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
            return await safe_edit(query, "⚙️ <b>Admin Panel</b>\nChoose an action:", parse_mode="HTML", reply_markup=keyboard)

    # ----------------------------
    # Cycle Reset Flow
    # ----------------------------
    if query.data.startswith("admin_confirm:reset_cycle"):
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Reset", callback_data="admin_action:reset_cycle"),
                InlineKeyboardButton("❌ Cancel", callback_data="admin_menu:stats"),
            ]
        ])
        return await safe_edit(query, "⚠️ Are you sure you want to reset the cycle?", parse_mode="HTML", reply_markup=keyboard)

    if query.data == "admin_action:reset_cycle":
        async with AsyncSessionLocal() as session:
            gs = await session.get(GameState, 1)
            if not gs:
                return await safe_edit(query, "⚠️ GameState not found.", parse_mode="HTML")

            gs.current_cycle += 1
            gs.paid_tries_this_cycle = 0
            gs.created_at = datetime.utcnow()
            await session.commit()

        await query.answer("✅ Cycle reset!", show_alert=True)
        return await safe_edit(query, "🔁 <b>Cycle Reset!</b> New round begins.", parse_mode="HTML")

    # ----------------------------
    # Proof Approve / Reject (✅ Auto-Move + Notify User + Resubmit Option + Return to Admin Panel)
    # ----------------------------
    try:
        action, proof_id = query.data.split(":")
    except ValueError:
        return await safe_edit(query, "⚠️ Invalid callback data.", parse_mode="HTML")

    if action not in ("admin_approve", "admin_reject"):
        return  # ignore unrelated actions

    async with AsyncSessionLocal() as session:
        proof = await session.get(Proof, proof_id)
        if not proof or proof.status != "pending":
            return await safe_edit(query, "⚠️ Proof already processed or not found.", parse_mode="HTML")

        # ✅ Fetch the actual Telegram user ID from the User table
        user = await session.get(User, proof.user_id)
        telegram_id = getattr(user, "tg_id", None)

        # 🎯 Common user main menu buttons
        user_menu_keyboard = [
            [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
            [InlineKeyboardButton("💳 Buy Tries", callback_data="buy")],
            [InlineKeyboardButton("🎁 Free Tries", callback_data="free")],
            [InlineKeyboardButton("📊 Available Tries", callback_data="show_tries")],
        ]

        if action == "admin_approve":
            proof.status = "approved"
            await add_tries(session, proof.user_id, count=1, paid=False)
            msg = "✅ Proof approved and bonus try added!"

            # 🎉 Notify user (with main menu)
            if telegram_id:
                try:
                    await context.bot.send_message(
                        telegram_id,
                        "🎉 Your proof has been approved! You’ve received 1 bonus try. Good luck 🍀",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(user_menu_keyboard)
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Could not notify user {telegram_id}: {e}")
            else:
                logger.warning(f"⚠️ No Telegram ID (tg_id) found for user {proof.user_id}")

        else:
            proof.status = "rejected"
            msg = "❌ Proof rejected."

            # ⏪ Add “Resubmit Proof” button at the top
            reject_keyboard = [
                [InlineKeyboardButton("📤 Resubmit Proof", callback_data="resubmit_proof")],
                *user_menu_keyboard
            ]

            # ⚠️ Notify user (with resubmit + main menu)
            if telegram_id:
                try:
                    await context.bot.send_message(
                        telegram_id,
                        "❌ Your proof has been reviewed but unfortunately was rejected.\n\n"
                        "Please ensure your next proof meets the rules and resubmit below 👇",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(reject_keyboard)
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Could not notify user {telegram_id}: {e}")
            else:
                logger.warning(f"⚠️ No Telegram ID (tg_id) found for user {proof.user_id}")

        await session.commit()

    # ✅ Automatically move to the next pending proof
    current_index = context.user_data.get("proof_index", 0)
    proof_ids = context.user_data.get("pending_proofs", [])

    if current_index + 1 < len(proof_ids):
        context.user_data["proof_index"] = current_index + 1
        await query.answer(msg)
        return await show_single_proof(update, context, index=current_index + 1)

    # ✅ No more proofs left → show admin panel
    await query.answer(msg)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Pending Proofs", callback_data="admin_menu:pending_proofs")],
        [InlineKeyboardButton("📊 Stats", callback_data="admin_menu:stats")],
        [InlineKeyboardButton("👤 User Search", callback_data="admin_menu:user_search")],
        [InlineKeyboardButton("🏆 Winners", callback_data="admin_menu:winners")],
    ])

    return await safe_edit(query, 
        f"{msg}\n\n✅ All proofs reviewed!\n\n⚙️ <b>Back to Admin Panel</b>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


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
# Winners Section (Single-Winner Paging)
# ----------------------------
WINNERS_PER_PAGE = 1  # one winner per page

async def show_winners_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Displays winners one at a time (pageable). Callback data formats handled:
    - admin_winners:all:1
    - admin_winners:pending:1
    - admin_winners:transit:1
    - admin_winners:delivered:1
    - If called from admin_menu:winners, defaults to page 1, all winners.
    """
    query = getattr(update, "callback_query", None)
    page = 1
    filter_status = None  # None | "Pending" | "In Transit" | "Delivered"

    # --- Parse callback data ---
    if query and query.data.startswith("admin_winners"):
        parts = query.data.split(":")  # e.g. admin_winners:transit:2
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
        if len(parts) == 3 and parts[2].isdigit():
            page = int(parts[2])
    else:
        page = 1
        filter_status = None

    offset = (page - 1) * WINNERS_PER_PAGE

    # --- Fetch winners from database ---
    async with get_async_session() as session:
        query_base = select(User).where(User.choice.isnot(None))
        if filter_status:
            if filter_status == "Pending":
                query_base = query_base.where(
                    (User.delivery_status.is_(None)) | (User.delivery_status == "Pending")
                )
            else:
                query_base = query_base.where(User.delivery_status == filter_status)

        result = await session.execute(query_base.order_by(User.id.desc()))
        all_winners = result.scalars().all()

    if not all_winners:
        # 🎯 No Winners Found
        if filter_status == "In Transit":
            text = "📦 No winner found in transit yet.\n\n💡 Tip: Try again after marking someone 'In Transit'!"
        elif filter_status == "Delivered":
            text = "✅ No delivered winners yet.\n\n💡 Tip: Once a delivery is done, mark it 'Delivered' to see them here."
        elif filter_status == "Pending":
            text = "🏆 No confirmed winners yet.\n\n💡 Tip: Winners appear here once they’re selected!"
        else:
            text = "🎯 No winners found yet.\n\n💡 Tip: Start a draw or mark someone as a winner!"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📦 In Transit List", callback_data="admin_winners:transit:1"),
                InlineKeyboardButton("✅ Delivered List", callback_data="admin_winners:delivered:1")
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")]
        ])

        if query:
            return await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            return await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

    # --- Calculate pagination ---
    total_winners = len(all_winners)
    total_pages = max(1, (total_winners + WINNERS_PER_PAGE - 1) // WINNERS_PER_PAGE)

    # Ensure page bounds
    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    winner = all_winners[offset]
    base_prefix = (
        f"admin_winners:{'all' if not filter_status else 'pending' if filter_status=='Pending' "
        f"else 'transit' if filter_status=='In Transit' else 'delivered'}"
    )

    # --- Build message text ---
    filter_label = (
        "🟡 Pending Winners" if filter_status == "Pending"
        else "📦 In Transit List" if filter_status == "In Transit"
        else "✅ Delivered List" if filter_status == "Delivered"
        else "🏆 All Winners"
    )

    text = (
        f"{filter_label}\n"
        f"Winner {page} of {total_winners}\n\n"
        f"👤 <b>{winner.full_name or '-'}</b>\n"
        f"📱 {winner.phone or 'N/A'}\n"
        f"📦 {winner.address or 'N/A'}\n"
        f"🎁 {winner.choice or '-'}\n"
        f"🚚 Status: <b>{winner.delivery_status or 'Pending'}</b>\n"
        f"🔗 @{winner.username or 'N/A'}"
    )

    # --- Inline keyboard ---
    rows = [
        [
            InlineKeyboardButton("🚚 Mark In Transit", callback_data=f"status_transit_{winner.id}"),
            InlineKeyboardButton("✅ Mark Delivered", callback_data=f"status_delivered_{winner.id}")
        ]
    ]

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{base_prefix}:{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next ⏩", callback_data=f"{base_prefix}:{page+1}"))
    if nav_buttons:
        rows.append(nav_buttons)

    rows.append([
        InlineKeyboardButton("📦 In Transit List", callback_data="admin_winners:transit:1"),
        InlineKeyboardButton("✅ Delivered List", callback_data="admin_winners:delivered:1"),
    ])

    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")])
    keyboard = InlineKeyboardMarkup(rows)

    # --- Display message ---
    if query:
        await safe_edit(query, text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ------------------------------
# Show Filtered Winners
# ------------------------------
async def show_filtered_winners(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_winners:1")]
            ])
        )
        return

    # Build winner list message
    text_lines = [f"🏆 <b>Jackpot Winners - {filter_value}</b>\n"]
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
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_winners:1")]
    ])

    await query.edit_message_text(
        "\n".join(text_lines),
        parse_mode="HTML",
        reply_markup=keyboard
    )


# ----------------------------
# Delivery Status Update (per-winner)
# ----------------------------
async def handle_delivery_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # show spinner -> we'll also send a short toast below

    if query.from_user.id != ADMIN_USER_ID:
        return await query.answer("🚫 Not authorized.", show_alert=True)

    # callback format: status_transit_<uuid> or status_delivered_<uuid>
    try:
        _, status, user_id = query.data.split("_", 2)
    except Exception:
        return await query.answer("⚠️ Invalid callback data.", show_alert=True)

    new_status = "Delivered" if status == "delivered" else "In Transit"

    async with get_async_session() as session:
        async with session.begin():
            await session.execute(sql_update(User).where(User.id == user_id).values(delivery_status=new_status))
            result = await session.execute(select(User).where(User.id == user_id))
            winner = result.scalar_one_or_none()
        await session.commit()

    # Short toast confirmation (non-blocking)
    try:
        await query.answer(text="✅ Updated!", show_alert=False)
    except Exception:
        # ignore double-answer errors
        pass

    # Notify the winner privately (if contact exists)
    if winner and winner.tg_id:
        try:
            dm_text = (
                f"🚚 <b>Good news, {winner.full_name or 'Winner'}!</b>\n\n"
                "Your prize is now <b>on the way</b> 🎁📦"
                if new_status == "In Transit" else
                f"✅ <b>Good news, {winner.full_name or 'Winner'}!</b>\n\nYour prize has been <b>delivered</b> 🎉"
            )
            await context.bot.send_message(chat_id=winner.tg_id, text=dm_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to DM winner: {e}")

    # Optional: notify admin privately when delivered
    if new_status == "Delivered":
        try:
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=f"📦 Delivery Confirmed\n🎉 {winner.full_name or 'Winner'}'s prize marked as delivered.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to DM admin about delivery: {e}")

    # Finally: refresh the winners list in-place so admin sees updated status (auto-refresh)
    # Reuse the same callback handling by altering the update.callback_query.data to a winners view request
    # Fortunately show_winners_section reads current update and will refresh correctly (it ignores the status_* data)
    try:
        await show_winners_section(update, context)
    except Exception as e:
        logger.error(f"Failed to refresh winners list after status update: {e}")
        # fallback - send a confirmation message
        await query.message.reply_text(f"✅ Updated {winner.full_name or 'Winner'} → <b>{new_status}</b>.", parse_mode="HTML")


# ----------------------------
# Register Handlers
# ----------------------------
def register_handlers(application):
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("pending_proofs", pending_proofs))
    application.add_handler(CommandHandler("winners", show_winners_section))
    application.add_handler(CallbackQueryHandler(show_winners_section, pattern="^admin_winners"))
    application.add_handler(CallbackQueryHandler(show_filtered_winners, pattern="^admin_winners_filter:"))
    # admin callbacks (menu, winners pagination/filters, approvals)
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    # delivery status actions (per-winner)
    application.add_handler(CallbackQueryHandler(handle_delivery_status, pattern=r"^status_"))
    # user search text handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_search_handler))

