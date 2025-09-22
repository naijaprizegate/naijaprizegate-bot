# ===============================================================
# handlers/tryluck.py
# ===============================================================
import asyncio
import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler

from helpers import md_escape, get_or_create_user, consume_try
from services.tryluck import spin_logic

# Inline keyboard for retry
try_again_keyboard = InlineKeyboardMarkup.from_row([
    InlineKeyboardButton("🎰 Try Again", callback_data="tryluck")
])

async def tryluck_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /tryluck command or inline button callback"""
    user = await get_or_create_user(update.effective_user.id, update.effective_user.username)

    if user.tries_total <= 0:
        await update.effective_message.reply_text(
            "😅 You don't have any tries left! Buy more spins or earn free ones.",
            parse_mode="MarkdownV2"
        )
        return

    # Deduct a try and get spin result
    spin_result = await consume_try(user.id)  # {"win": bool, "prize": int or str}

    # Initial spinning message
    msg = await update.effective_message.reply_text("🎰 Spinning...", parse_mode="MarkdownV2")

    # Slot machine frames
    spinner_emojis = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣", "🍀", "🎲"]
    num_reels = 3

    # Determine potential prize for rolling counter
    max_prize = spin_result.get("prize") if spin_result["win"] else 0

    # Randomized suspense spin (3–5 seconds total)
    total_spins = random.randint(6, 10)
    for spin_index in range(total_spins):
        # Random frame
        frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))

        # Rolling win counter (counts up toward prize)
        if spin_result["win"] and max_prize:
            display_prize = int((spin_index + 1) / total_spins * max_prize)
            frame_text = f"🎰 {frame}  💰 {display_prize}"
        else:
            frame_text = f"🎰 {frame}"

        await msg.edit_text(frame_text, parse_mode="MarkdownV2")
        await asyncio.sleep(0.4)  # short pause per frame

    # Final spin result frame
    if spin_result["win"]:
        final_frame = " ".join(["💎"] * num_reels)
        await msg.edit_text(f"🎰 {final_frame}  💰 {max_prize}", parse_mode="MarkdownV2")
        final_text = (
            f"🏆 *Congratulations {md_escape(user.first_name)}!* 🎉\n\n"
            f"You just won: *{md_escape(str(max_prize))}*\n\n"
            "Your arsenal is loaded, your chances just went way up ⚡\n"
            "👉 Don’t keep luck waiting — hit *Try Luck* now and chase that jackpot! 🏆🔥"
        )
    else:
        final_frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        await msg.edit_text(f"🎰 {final_frame}", parse_mode="MarkdownV2")
        final_text = (
            f"😅 {md_escape(user.first_name)}, no win this time.\n\n"
            "Better luck next spin! Try again and chase that jackpot 🎰🔥"
        )

    # Send final text with Try Again button
    await msg.edit_text(final_text, parse_mode="MarkdownV2", reply_markup=try_again_keyboard)


# Callback query handler for inline button "Try Luck"
async def tryluck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await tryluck_handler(update, context)


# Registration function
def register_handlers(application):
    application.add_handler(CommandHandler("tryluck", tryluck_handler))
    application.add_handler(CallbackQueryHandler(tryluck_callback, pattern="^tryluck$"))
