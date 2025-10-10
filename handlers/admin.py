# ==============================================================
# handlers/admin.py
# ==============================================================
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from sqlalchemy import select, func
from db import AsyncSessionLocal
from helpers import add_tries, get_user_by_id, md_escape
from models import Proof, User, GameState  # ✅ GameState tracks cycles & paid tries

# Admin ID from environment
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

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
        return await update.effective_message.reply_text(
            "❌ Access denied\\.", parse_mode="MarkdownV2"
        )

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Proof).where(Proof.status == "pending"))
        proofs = result.scalars().all()

    if not proofs:
        # If triggered from a button → edit the message
        if update.callback_query:
            return await update.callback_query.edit_message_text(
                "✅ No pending proofs at the moment\\.", parse_mode="MarkdownV2"
            )
        # If triggered from command → send a new message
        return await update.effective_message.reply_text(
            "✅ No pending proofs at the moment\\.", parse_mode="MarkdownV2"
        )

    for proof in proofs:
        user = await get_user_by_id(proof.user_id)
        user_name = md_escape(
            user.username or user.first_name if user else str(proof.user_id)
        )

        caption = (
            f"*Pending Proof*\n"
            f"👤 User: {user_name}\n"
            f"🆔 Proof ID: `{md_escape(str(proof.id))}`"
        )

        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve:{proof.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject:{proof.id}")
            ]]
        )

        await update.effective_message.reply_photo(
            photo=proof.file_id,
            caption=caption,
            parse_mode="MarkdownV2",
            reply_markup=keyboard
        )

# ----------------------------
# Callback: Approve / Reject
# ----------------------------
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Approve/Reject and menu clicks"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if user_id != ADMIN_USER_ID:
        return await query.edit_message_caption(caption="❌ Access denied\\.", parse_mode="MarkdownV2")

    # --- Handle main menu clicks ---
    if query.data.startswith("admin_menu:"):
        action = query.data.split(":")[1]

        if action == "pending_proofs":
            # Replace message with pending proofs list
            await pending_proofs(update, context)

        elif action == "stats":
            # ✅ Fetch stats from DB
            from models import GlobalCounter, GameState
            async with AsyncSessionLocal() as session:
                # GlobalCounter (lifetime total paid tries)
                gc = await session.get(GlobalCounter, 1)

                # GameState (cycle-based tracking)
                gs = await session.get(GameState, 1)

            lifetime_paid = gc.paid_tries_total if gc else 0
            current_cycle = gs.current_cycle if gs else 1
            paid_this_cycle = gs.paid_tries_this_cycle if gs else 0

            stats_text = (
                "📊 *Bot Stats*\n\n"
                f"💰 Lifetime Paid Tries: *{lifetime_paid}*\n"
                f"🔄 Current Cycle: *{current_cycle}*\n"
                f"🎯 Paid Tries (this cycle): *{paid_this_cycle}*"
            )

            await query.edit_message_text(
                stats_text,
                parse_mode="MarkdownV2"
            )

        elif action == "user_search":
            await query.edit_message_text(
                "👤 User search coming soon...",
                parse_mode="MarkdownV2"
            )
        return

    # --- Handle approve/reject proof ---
    try:
        action, proof_id = query.data.split(":")
        proof_id = int(proof_id)
    except Exception:
        return await query.edit_message_caption(caption="⚠️ Invalid callback data\\.", parse_mode="MarkdownV2")

    async with AsyncSessionLocal() as session:
        proof = await session.get(Proof, proof_id)
        if not proof or proof.status != "pending":
            return await query.edit_message_caption(caption="⚠️ Proof already processed\\.", parse_mode="MarkdownV2")

        if action == "admin_approve":
            proof.status = "approved"
            await add_tries(proof.user_id, 1, paid=False)
            caption = "✅ Proof approved and bonus try added\\!"
        else:
            proof.status = "rejected"
            caption = "❌ Proof rejected\\."

        await session.commit()
        await query.edit_message_caption(caption=caption, parse_mode="MarkdownV2")

# ----------------------------
# User Search (text input)
# ----------------------------
async def user_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID or not context.user_data.get("awaiting_user_search"):
        return

    query_text = update.message.text.strip()
    async with AsyncSessionLocal() as session:
        user = None
        if query_text.isdigit():
            user = await session.get(User, query_text)  # UUID search would need explicit cast
        else:
            result = await session.execute(select(User).where(User.username == query_text))
            user = result.scalars().first()

    if not user:
        await update.message.reply_text("⚠️ No user found.", parse_mode="MarkdownV2")
    else:
        reply = (
            f"👤 *User Info*\n"
            f"🆔 UUID: `{user.id}`\n"
            f"📛 Username: {md_escape(user.username or '-')}\n"
            f"🎲 Paid Tries: {user.tries_paid}\n"
            f"🎁 Bonus Tries: {user.tries_bonus}\n"
            f"👥 Referred By: {user.referred_by or 'None'}"
        )
        await update.message.reply_text(reply, parse_mode="MarkdownV2")

    context.user_data["awaiting_user_search"] = False

# ----------------------------
# Handler registration helper
# ----------------------------
def register_handlers(application):
    """Register admin command and callback handlers"""
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("pending_proofs", pending_proofs))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_search_handler))

