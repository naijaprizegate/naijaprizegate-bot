# ===============================================================
# handlers/tryluck.py
# ===============================================================
import asyncio
import random
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from helpers import md_escape, get_or_create_user
from services.tryluck import spin_logic
from db import get_async_session

logger = logging.getLogger(__name__)

# Inline keyboard for retry
try_again_keyboard = InlineKeyboardMarkup.from_row([
    InlineKeyboardButton("🎰 Try Again", callback_data="tryluck")
])

async def tryluck_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /tryluck command or inline button callback"""
    tg_user = update.effective_user
    logger.info(f"🔔 /tryluck called by tg_id={tg_user.id}, username={tg_user.username}")

    # Always open DB session
    async with get_async_session() as session:
        user = await get_or_create_user(
            session,
            tg_id=tg_user.id,
            username=tg_user.username
        )

        # 📊 Log BEFORE spin
        logger.info(
            f"📊 Before spin: db_user.id={user.id}, tg_id={user.tg_id}, "
            f"paid={user.tries_paid}, bonus={user.tries_bonus}"
        )

        # Spin the wheel using core game logic
        outcome = await spin_logic(session, user)

        # Commit any changes from spin_logic before logging after-state
        await session.commit()
        await session.refresh(user)

        # 🎲 Log AFTER spin
        logger.info(
            f"🎲 Outcome={outcome} | After spin: db_user.id={user.id}, "
            f"tg_id={user.tg_id}, paid={user.tries_paid}, bonus={user.tries_bonus}"
        )

    # Handle outcomes
    if outcome == "no_tries":
        return await update.effective_message.reply_text(
            "😅 You don’t have any tries left\\! Buy more spins or earn free ones\\.",
            parse_mode="MarkdownV2"
        )

    # Initial spinning message
    msg = await update.effective_message.reply_text("🎰 Spinning\\.\\.\\.", parse_mode="MarkdownV2")

    # Slot machine animation (3 reels)
    spinner_emojis = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣", "🍀", "🎲"]
    num_reels = 3

    total_spins = random.randint(6, 10)
    for spin_index in range(total_spins):
        frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        await msg.edit_text(f"🎰 {frame}", parse_mode="MarkdownV2")
        await asyncio.sleep(0.4)

    # Final frame + text
    if outcome == "win":
        final_frame = " ".join(["💎"] * num_reels)
        final_text = (
            f"🏆 *Congratulations {md_escape(tg_user.first_name)}\\!* 🎉\n\n"
            f"You just won the jackpot\\!\n\n"
            "Your arsenal is loaded, your chances just went way up ⚡\n"
            "👉 Don’t keep luck waiting — hit *Try Luck* now and chase that jackpot 🏆🔥"
        )
    else:  # outcome == "lose"
        final_frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        final_text = (
            f"😅 {md_escape(tg_user.first_name)}, no win this time\\.\n\n"
            "Better luck next spin\\! Try again and chase that jackpot 🎰🔥"
        )

    await msg.edit_text(
        f"🎰 {final_frame}\n\n{final_text}",
        parse_mode="MarkdownV2",
        reply_markup=try_again_keyboard
    )


# Callback query handler for inline button "Try Luck"
async def tryluck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await tryluck_handler(update, context)


# Registration function
def register_handlers(application):
    application.add_handler(CommandHandler("tryluck", tryluck_handler))
    application.add_handler(CallbackQueryHandler(tryluck_callback, pattern="^tryluck$"))
