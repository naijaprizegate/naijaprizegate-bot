# ==============================================================
# handlers/admin.py — Clean Unified Admin System (HTML Safe)
# ==============================================================
import os
import re
import io
import csv
import tempfile
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram import InputMediaPhoto, InputFile
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.ext import filters as tg_filters  # to avoid name clash
from telegram.error import BadRequest
from sqlalchemy import select, update as sql_update, and_
from db import AsyncSessionLocal, get_async_session
from helpers import add_tries, get_user_by_id
from models import Proof, User, GameState, GlobalCounter, PrizeWinner
from utils.security import is_admin 

logger = logging.getLogger(__name__)

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

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
            "💡 Tip: Mark winners in the correct status to track progress!"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📦 In Transit", callback_data="admin_winners:transit:1"),
                InlineKeyboardButton("✅ Delivered", callback_data="admin_winners:delivered:1")
            ],
            [InlineKeyboardButton("📥 Export Winners CSV", callback_data="admin_export_csv")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")]
        ])

        return await safe_edit(query, text, parse_mode="HTML", reply_markup=keyboard) \
            if query else await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

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
        "Delivered": "delivered"
    }
    base_prefix = f"admin_winners:{status_label_map.get(filter_status, 'all')}"

    filter_label = {
        None: "🏆 All Winners",
        "Pending": "🟡 Pending Winners",
        "In Transit": "📦 In Transit Winners",
        "Delivered": "✅ Delivered Winners"
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
            InlineKeyboardButton("🚚 Mark In Transit", callback_data=f"pw_status_transit_{winner.id}"),
            InlineKeyboardButton("✅ Mark Delivered", callback_data=f"pw_status_delivered_{winner.id}")
        ]
    ]

    # 🧭 Navigation
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{base_prefix}:{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ⏩", callback_data=f"{base_prefix}:{page+1}"))
    if nav:
        rows.append(nav)

    # 🗂️ Filter & Export options
    rows.append([
        InlineKeyboardButton("📦 In Transit", callback_data="admin_winners:transit:1"),
        InlineKeyboardButton("✅ Delivered", callback_data="admin_winners:delivered:1")
    ])

    rows.append([
        InlineKeyboardButton("📥 Export Winners CSV", callback_data="admin_export_csv")
    ])

    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data="admin_menu:main")
    ])

    keyboard = InlineKeyboardMarkup(rows)

    # ✅ Edit or send fresh
    try:
        return await safe_edit(query, text, parse_mode="HTML", reply_markup=keyboard) \
            if query else await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.warning(f"⚠️ Could not edit winners message: {e}")
        return await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# --------------------------------------
# handle_pwhandle_pw_mark_in_transit
# --------------------------------------
async def handle_pw_mark_in_transit(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                text=f"🚚 Hi! Your prize ({pw.choice}) is now *In Transit*. We'll update you when it's delivered.",
                parse_mode="Markdown"
            )
        except Exception:
            logger.exception("Failed to notify winner about In Transit")

    # refresh admin display: stay on same page
    await show_winners_section(update, context)


# -----------------------------
# ✅ CSV Generation, Upload (temp file), Date Range Flow
# -----------------------------

DATE_SELECTION_KEY = "csv_export_date_range"

# -------------------------
# Show Export Range Menu (triggered from admin_winners view)
# -------------------------
async def admin_export_csv_menu(update, context):
    """Show export options (presets + custom). Callback_data previously used: admin_export_winners"""
    query = getattr(update, "callback_query", None)
    if not query:
        return

    #  Resolve user id safely
    user_id = None
    if getattr(update, "effective_user", None):
        user_id = getattr(update.effective_user, "id", None)

    # --- Admin check (robust)
    # Preferred simple check using ADMIN_USER_ID (already defined at top of handlers/admin.py)
    if user_id is None or user_id != ADMIN_USER_ID:
        # If you also have an is_admin helper, try it as a fallback:
        try:
            # if is_admin is async and expects id
            ok = await is_admin(user_id) if user_id is not None else False
        except TypeError:
            # maybe is_admin expects update or is sync; try sync call without await
            try:
                ok = is_admin(user_id)
            except Exception:
                ok = False
        except Exception:
            ok = False

        if not ok:
            return await query.answer("⛔ Unauthorized", show_alert=True)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🕐 Last 24 hours", callback_data="export_csv:24h"),
         InlineKeyboardButton("🗓 Last 7 days", callback_data="export_csv:7d")],
        [InlineKeyboardButton("📆 Last 30 days", callback_data="export_csv:30d"),
         InlineKeyboardButton("📅 This Month", callback_data="export_csv:thismonth")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_winners:all:1")],
        [InlineKeyboardButton("🔧 Custom range (YYYY-MM-DD)", callback_data="export_csv:custom")]
    ])

    await query.edit_message_text(
        "📥 <b>Export Winners CSV</b>\n\n"
        "Choose a preset or select Custom range (you will be asked to send dates in <b>YYYY-MM-DD</b> format). All times use <b>UTC</b>.",
        parse_mode="HTML",
        reply_markup=keyboard
    )

# -------------------------
# Handle preset or start custom flow
# -------------------------
async def export_csv_handler(update, context):
    query = getattr(update, "callback_query", None)
    if not query:
        return

    # admin check
    if not await is_admin(update):
        return await query.answer("⛔ Unauthorized", show_alert=True)

    label = query.data.split(":", 1)[1]

    now = datetime.now(timezone.utc)
    start = None
    end = None

    if label == "24h":
        start = now - timedelta(days=1)
        end = now
    elif label == "7d":
        start = now - timedelta(days=7)
        end = now
    elif label == "30d":
        start = now - timedelta(days=30)
        end = now
    elif label == "thismonth":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif label == "custom":
        # begin custom flow: expect start date text next
        context.user_data[DATE_SELECTION_KEY] = {"stage": "awaiting_start"}
        await query.edit_message_text(
            "📅 <b>Custom Range</b>\n\nSend the <b>start date</b> in UTC using format: <code>YYYY-MM-DD</code>",
            parse_mode="HTML"
        )
        return
    else:
        await query.answer("⚠️ Invalid selection", show_alert=True)
        return

    # do the export for preset ranges
    await query.edit_message_text("⏳ Generating CSV... please wait.")
    await generate_and_send_csv(update, context, start, end, label=label)


# -------------------------
# Message handler: start/date inputs + end date inputs
# This will be registered as a MessageHandler(filters.TEXT & ~filters.COMMAND,...)
# It will quietly return early if not in an export flow.
# -------------------------
async def date_range_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Captures admin text messages when they are setting start/end dates for CSV export.
    Expects YYYY-MM-DD strings; stores intermediate state in context.user_data[DATE_SELECTION_KEY].
    """
    # only proceed if admin
    if not await is_admin(update):
        return  # not admin — do nothing here

    if DATE_SELECTION_KEY not in context.user_data:
        return  # not in export flow

    state = context.user_data[DATE_SELECTION_KEY]
    stage = state.get("stage")

    text = (update.message.text or "").strip()
    # parse date
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return await update.message.reply_text("❌ Invalid format. Send date as: YYYY-MM-DD (UTC)")

    if stage == "awaiting_start":
        # store start and prompt for end
        context.user_data[DATE_SELECTION_KEY] = {"stage": "awaiting_end", "start": parsed}
        return await update.message.reply_text(
            "✅ Start date recorded.\nNow send the <b>end date</b> (UTC) in format: <code>YYYY-MM-DD</code>",
            parse_mode="HTML"
        )

    if stage == "awaiting_end":
        start_dt = state.get("start")
        if not start_dt:
            # missing start — ask to restart
            context.user_data.pop(DATE_SELECTION_KEY, None)
            return await update.message.reply_text("❌ Start date missing. Restart export from the menu.")

        end_dt = parsed
        # normalize end to include entire day (23:59:59.999999)
        end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

        # clear state
        context.user_data.pop(DATE_SELECTION_KEY, None)

        # Confirm and generate CSV
        await update.message.reply_text("⏳ Generating CSV... please wait.")
        await generate_and_send_csv(update, context, start_dt, end_dt, label=f"custom_{start_dt.date()}_to_{end_dt.date()}")
        return

    # Otherwise, not expected — clear and inform
    context.user_data.pop(DATE_SELECTION_KEY, None)
    return await update.message.reply_text("⚠️ Unexpected state. Please re-open the export menu.")


# -------------------------
# Core CSV generator + sender
# -------------------------
async def generate_and_send_csv(update, context, start_dt: datetime, end_dt: datetime, label: str = "range"):
    """
    Query PrizeWinner by submitted_at between start_dt and end_dt (both inclusive),
    create CSV in OS tmp dir (UTF-8 with BOM for Excel), send to admin, then delete file.
    """
    # Ensure admin
    if not await is_admin(update):
        # If called from callback, answer; if from message, reply
        if getattr(update, "callback_query", None):
            return await update.callback_query.answer("⛔ Unauthorized", show_alert=True)
        return await update.message.reply_text("⛔ Unauthorized")

    # Query DB
    async with get_async_session() as session:
        qb = select(PrizeWinner).where(
            and_(
                PrizeWinner.submitted_at >= start_dt,
                PrizeWinner.submitted_at <= end_dt
            )
        ).order_by(PrizeWinner.submitted_at.asc())

        result = await session.execute(qb)
        winners = result.scalars().all()

    if not winners:
        msg = f"📭 No winners found between {start_dt.isoformat()} and {end_dt.isoformat()} (UTC)."
        if getattr(update, "callback_query", None):
            return await update.callback_query.edit_message_text(msg)
        else:
            return await update.message.reply_text(msg)

    # Build filename
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = (end_dt if isinstance(end_dt, datetime) else end_dt).strftime("%Y-%m-%d")
    filename = f"winners_{start_str}_to_{end_str}.csv"

    # Create temp file (safe): using NamedTemporaryFile in tmp dir; delete=False so we can send
    tmp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", delete=False, suffix=".csv")
    tmp_path = tmp.name

    try:
        writer = csv.writer(tmp)
        # Header
        writer.writerow(["Full Name", "Phone", "Address", "Prize", "Date Won (UTC)", "Delivery Status"])

        # Rows
        for w in winners:
            data = w.delivery_data or {}
            full_name = data.get("full_name", "")
            phone = data.get("phone", "")
            address = data.get("address", "")
            prize = w.choice or ""
            date_won = w.submitted_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if w.submitted_at else ""
            status = w.delivery_status or "Pending"

            writer.writerow([full_name, phone, address, prize, date_won, status])

        tmp.flush()
        tmp.close()

        # Send the file (only to ADMIN_USER_ID)
        admin_chat_id = update.effective_user.id
        caption = f"📦 Winners Export — {start_str} → {end_str} (UTC)\nCount: {len(winners)}"

        # Prefer callback_query.message.reply_document if available to preserve edit context
        try:
            if getattr(update, "callback_query", None):
                await update.callback_query.message.reply_document(
                    document=InputFile(tmp_path, filename=filename),
                    caption=caption
                )
            else:
                await update.message.reply_document(
                    document=InputFile(tmp_path, filename=filename),
                    caption=caption
                )
        except Exception as send_err:
            # Try direct bot send
            try:
                await context.bot.send_document(
                    chat_id=admin_chat_id,
                    document=InputFile(tmp_path, filename=filename),
                    caption=caption
                )
            except Exception as bot_err:
                # give a helpful message and keep temp file for manual retrieval (log it)
                logger.exception("❌ Failed to send CSV to Telegram", exc_info=bot_err)
                return await (update.callback_query.edit_message_text if getattr(update, "callback_query", None) else update.message.reply_text)(
                    "❌ Failed to send CSV. Check bot permissions and disk. Temp file at: " + tmp_path
                )

        # Delete temp file
        try:
            os.remove(tmp_path)
        except Exception as e:
            logger.warning(f"⚠️ Could not delete temp file {tmp_path}: {e}")

        # Acknowledge to admin (edit message or reply)
        if getattr(update, "callback_query", None):
            await update.callback_query.edit_message_text(f"✅ CSV exported and sent to you ({len(winners)} rows).")
        else:
            await update.message.reply_text(f"✅ CSV exported and sent to you ({len(winners)} rows).")

    finally:
        # Safety: ensure file removed if any path remains open
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

# ----------------------------------
# handle_pw_mark_delivered
# ------------------------------------
async def handle_pw_mark_delivered(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                text=f"✅ Hi! Your prize ({pw.choice}) has been *delivered*. Congratulations!",
                parse_mode="Markdown"
            )
        except Exception:
            logger.exception("Failed to notify winner about Delivered")

    # refresh admin display
    await show_winners_section(update, context)

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

# -----------------------------------
# ✅ Handler for "Mark In Transit"
# ----------------------------------
async def update_delivery_status_transit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")  # pw_status_transit_12
    winner_id = int(parts[-1])

    async with get_async_session() as session:
        result = await session.execute(select(PrizeWinner).where(PrizeWinner.id == winner_id))
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
async def update_delivery_status_delivered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")  # pw_status_delivered_12
    winner_id = int(parts[-1])

    async with get_async_session() as session:
        result = await session.execute(select(PrizeWinner).where(PrizeWinner.id == winner_id))
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


# ----------------------------------------------------
# 📥 EXPORT WINNERS CSV — with Date Range (UTC-based)
# ----------------------------------------------------
# ----------------
# STEP 1 — Show Range Selection Menu
# ----------------
async def admin_export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = getattr(update, "callback_query", None)
    if not query:
        return
    
    # ✅ Verify Admin Access
    if not await is_admin(update):
        return await query.answer("⛔ You are not authorized.", show_alert=True)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗓️ Last 7 Days", callback_data="export_csv:7days"),
            InlineKeyboardButton("📆 Last 30 Days", callback_data="export_csv:30days")
        ],
        [
            InlineKeyboardButton("📅 This Month", callback_data="export_csv:thismonth"),
            InlineKeyboardButton("🗓️ Last Month", callback_data="export_csv:lastmonth")
        ],
        [
            InlineKeyboardButton("📋 All Time", callback_data="export_csv:all"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_winners:all:1")]
    ])

    text = (
        "📥 <b>Export Winners CSV</b>\n\n"
        "Please select a date range for export.\n"
        "All timestamps are in <b>UTC</b>."
    )

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


# -------------
# STEP 2 — Handle Range Selection + CSV Creation
# --------------
async def export_csv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = getattr(update, "callback_query", None)
    if not query:
        return
    
    if not await is_admin(update):
        return await query.answer("⛔ Unauthorized", show_alert=True)

    now = datetime.now(timezone.utc)
    start_date, end_date = None, None
    label = query.data.split(":")[1]

    if label == "7days":
        start_date = now - timedelta(days=7)
    elif label == "30days":
        start_date = now - timedelta(days=30)
    elif label == "thismonth":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif label == "lastmonth":
        first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = first_of_this_month
        start_date = (first_of_this_month - timedelta(days=1)).replace(day=1)
    elif label == "all":
        start_date = datetime(2000, 1, 1, tzinfo=timezone.utc)  # old default
    else:
        await query.answer("Invalid selection", show_alert=True)
        return

    if not end_date:
        end_date = now

    # ✅ Fetch Data
    async with get_async_session() as session:
        qb = select(PrizeWinner).where(
            and_(
                PrizeWinner.submitted_at >= start_date,
                PrizeWinner.submitted_at <= end_date
            )
        ).order_by(PrizeWinner.submitted_at.desc())
        result = await session.execute(qb)
        winners = result.scalars().all()

    if not winners:
        return await query.edit_message_text(
            f"📭 No winners found for this range ({label}).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_export_csv")]
            ])
        )

    # ✅ Generate CSV In-Memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Full Name", "Phone", "Address", "Prize", "Submitted At (UTC)"])

    for w in winners:
        data = w.delivery_data or {}
        writer.writerow([
            data.get("full_name", "-"),
            data.get("phone", "-"),
            data.get("address", "-"),
            w.choice or "-",
            w.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if w.submitted_at else "-"
        ])

    output.seek(0)
    csv_bytes = io.BytesIO(output.getvalue().encode("utf-8"))
    csv_bytes.name = f"winners_{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    # ✅ Send file to Telegram
    await query.message.reply_document(
        document=InputFile(csv_bytes),
        filename=csv_bytes.name,
        caption=f"📦 Winners Export ({label})\nRange: {start_date.date()} → {end_date.date()} (UTC)"
    )

    await query.answer("✅ CSV exported successfully!", show_alert=True)

# ------------------------------------
# Export Winners Start
# --------------------------------------
async def export_winners_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.effective_message.reply_text("❌ You are not authorized")

    # Clear any previous selections
    context.user_data.pop(DATE_SELECTION_KEY, None)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Last 24h", callback_data="csv_range:24h"),
            InlineKeyboardButton("Last 7 days", callback_data="csv_range:7d")
        ],
        [InlineKeyboardButton("Custom Range", callback_data="csv_range:custom")],
        [InlineKeyboardButton("⬅️ Cancel", callback_data="admin_winners:all:1")]
    ])

    await update.callback_query.answer()
    return await update.callback_query.edit_message_text(
        "📥 Export Winner CSV\n\nChoose a date range:",
        reply_markup=keyboard
    )

# --------------------------
# Export Winners Quick-Select (24h/7 days)
# ---------------------------
async def export_winners_quick(update: Update, context: ContextTypes.DEFAULT_TYPE, period: str):
    now = datetime.now(timezone.utc)

    if period == "24h":
        start = now.replace(microsecond=0) - timedelta(days=1)
    elif period == "7d":
        start = now.replace(microsecond=0) - timedelta(days=7)
    else:
        return

    await generate_and_send_csv(update, context, start, now)

# -----------------------------
# Custom Date Date Range - Step 1: Ask for Start Date
# -----------------------------
async def choose_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[DATE_SELECTION_KEY] = {}

    await update.callback_query.answer()
    return await update.callback_query.edit_message_text(
        "📅 Send Start Date (UTC) in format:\n\n<b>YYYY-MM-DD</b>",
        parse_mode="HTML"
    )

# -------------------------
# Custom Date Range — Step 2: Ask for End Date
# ---------------------------
async def handle_start_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        start_date = datetime.strptime(update.message.text, "%Y-%m-%d")
        start_date = start_date.replace(tzinfo=timezone.utc)
    except ValueError:
        return await update.message.reply_text("❌ Invalid format. Use YYYY-MM-DD")

    context.user_data[DATE_SELECTION_KEY]["start"] = start_date

    return await update.message.reply_text(
        "✅ Start date saved.\nNow send End Date (UTC):\n<b>YYYY-MM-DD</b>",
        parse_mode="HTML"
    )

# ----------------------------
# Final Step — Generate CSV
# ----------------------------
async def handle_end_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data.get(DATE_SELECTION_KEY, {})
    if "start" not in data:
        return await update.message.reply_text("❌ Start date not set. Restart export.")

    try:
        end_date = datetime.strptime(update.message.text, "%Y-%m-%d")
        end_date = end_date.replace(tzinfo=timezone.utc)
    except ValueError:
        return await update.message.reply_text("❌ Invalid format. Use YYYY-MM-DD")

    start_date = data["start"]
    context.user_data.pop(DATE_SELECTION_KEY, None)

    await generate_and_send_csv(update, context, start_date, end_date)

# ----------------------------
# Register Handlers (CLEAN & ORDERED ✅)
# ----------------------------
def register_handlers(application):

    # ✅ ADMIN COMMANDS
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("pending_proofs", pending_proofs))
    application.add_handler(CommandHandler("winners", show_winners_section))

    # ✅ ADMIN SUB-SECTIONS — must come BEFORE generic admin_callback
    # Each section gets its own clean pattern
    application.add_handler(CallbackQueryHandler(pending_proofs, pattern=r"^admin_pending"))
    application.add_handler(CallbackQueryHandler(user_search_handler, pattern=r"^admin_usersearch"))
    application.add_handler(CallbackQueryHandler(show_winners_section, pattern=r"^admin_winners"))
    application.add_handler(CallbackQueryHandler(show_filtered_winners, pattern=r"^admin_winners_filter:"))

    # ✅ CSV EXPORT FLOW (must be ABOVE generic text handlers)
    application.add_handler(CallbackQueryHandler(admin_export_csv_menu, pattern=r"^admin_export_csv$"))
    application.add_handler(CallbackQueryHandler(export_csv_handler, pattern=r"^export_csv:"))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        date_range_message_router
    ))

    # ✅ Admin Menu and main routing (keep AFTER the section-specific handlers)
    application.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin_"))

    # ✅ Delivery status updates
    application.add_handler(CallbackQueryHandler(update_delivery_status_transit, pattern=r"^pw_status_transit_\d+$"))
    application.add_handler(CallbackQueryHandler(update_delivery_status_delivered, pattern=r"^pw_status_delivered_\d+$"))
    application.add_handler(CallbackQueryHandler(handle_pw_mark_in_transit, pattern=r"^pw_status_transit_\d+$"))
    application.add_handler(CallbackQueryHandler(handle_pw_mark_delivered, pattern=r"^pw_status_delivered_\d+$"))

    # ✅ Fallback: User Search Text Handler (must be LAST text handler)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        user_search_handler
    ))
