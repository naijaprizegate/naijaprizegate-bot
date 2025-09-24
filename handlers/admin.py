# ==============================================================
# handlers/admin.py
# ==============================================================
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from sqlalchemy.future import select
from db import AsyncSessionLocal
from helpers import add_tries, get_user_by_id, md_escape
from models import Proof

# Admin ID from environment
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

# ----------------------------
# Command: /pending_proofs
# ----------------------------
async def pending_proofs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all pending proofs with Approve/Reject buttons"""
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Access denied.")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Proof).where(Proof.status == "pending"))
        proofs = result.scalars().all()

    if not proofs:
        return await update.message.reply_text("✅ No pending proofs at the moment.")

    for proof in proofs:
        # Optional: fetch user details for better display
        user = await get_user_by_id(proof.user_id)
        user_name = md_escape(user.first_name if user else str(proof.user_id))

        caption = (
            f"*Pending Proof*\n"
            f"👤 User: {user_name}\n"
            f"🆔 Proof ID: {proof.id}"
        )

        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve:{proof.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject:{proof.id}")
            ]]
        )

        await update.message.reply_photo(photo=proof.file_id, caption=caption, parse_mode="MarkdownV2", reply_markup=keyboard)

# ----------------------------
# Callback: Approve / Reject
# ----------------------------
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Approve/Reject button clicks"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if user_id != ADMIN_ID:
        return await query.edit_message_caption(caption="❌ Access denied.", parse_mode="MarkdownV2")

    try:
        action, proof_id = query.data.split(":")
        proof_id = int(proof_id)
    except Exception:
        return await query.edit_message_caption(caption="⚠️ Invalid callback data.", parse_mode="MarkdownV2")

    async with AsyncSessionLocal() as session:
        proof = await session.get(Proof, proof_id)
        if not proof or proof.status != "pending":
            return await query.edit_message_caption(caption="⚠️ Proof already processed.", parse_mode="MarkdownV2")

        if action == "admin_approve":
            proof.status = "approved"
            await add_tries(proof.user_id, 1, paid=False)
            caption = "✅ Proof approved and bonus try added!"
        else:
            proof.status = "rejected"
            caption = "❌ Proof rejected."

        await session.commit()
        await query.edit_message_caption(caption=caption, parse_mode="MarkdownV2")

# ----------------------------
# Handler registration helper
# ----------------------------
def register_handlers(application):
    """Register admin command and callback handlers"""
    application.add_handler(CommandHandler("pending_proofs", pending_proofs))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

