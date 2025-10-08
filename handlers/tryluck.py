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

    outcome = "no_tries"

    async with get_async_session() as session:
        try:
            # Begin transaction
            async with session.begin():
                # Ensure user exists
                user = await get_or_create_user(
                    session,
                    tg_id=tg_user.id,
                    username=tg_user.username
                )

                # 📊 Log BEFORE spin
                logger.info(
                    f"📊 Before spin: user_id={user.id}, tg_id={user.tg_id}, "
                    f"paid={user.tries_paid}, bonus={user.tries_bonus}"
                )

                # Run core game logic (consume + spin + record play)
                outcome = await spin_logic(session, user)

                # After transaction, refresh user state
                await session.refresh(user)

                # 🎲 Log AFTER spin
                logger.info(
                    f"🎲 Outcome={outcome} | After spin: user_id={user.id}, "
                    f"tg_id={user.tg_id}, paid={user.tries_paid}, bonus={user.tries_bonus}"
                )

        except Exception as e:
            logger.exception(f"❌ Error during /tryluck for tg_id={tg_user.id}: {e}")
            outcome = "error"

    # ----------------- Outcome Handling -----------------
    if outcome == "no_tries":
        return await update.effective_message.reply_text(
            md_escape("😅 You don’t have any tries left! Buy more spins or earn free ones."),
            parse_mode="MarkdownV2"
        )
    if outcome == "error":
        return await update.effective_message.reply_text(
            md_escape("⚠️ Oops! Something went wrong while processing your spin. Please try again."),
            parse_mode="MarkdownV2"
        )

    # Initial spinning message
    msg = await update.effective_message.reply_text(
        md_escape("🎰 Spinning..."),
        parse_mode="MarkdownV2"
    )

    # Slot machine animation (3 reels)
    spinner_emojis = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣", "🍀", "🎲"]
    num_reels = 3

    total_spins = random.randint(6, 10)
    for _ in range(total_spins):
        frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        await msg.edit_text(md_escape(f"🎰 {frame}"), parse_mode="MarkdownV2")
        await asyncio.sleep(0.4)

    # Final frame + text
    if outcome == "win":
        final_frame = " ".join(["💎"] * num_reels)
        final_text = (
            f"🏆 *Congratulations {md_escape(tg_user.first_name)}!* 🎉\n\n"
            f"{md_escape('You just won the jackpot!')}\n\n"
            f"{md_escape('Your arsenal is loaded, your chances just went way up ⚡')}\n"
            f"{md_escape('👉 Don’t keep luck waiting — hit *Try Luck* now and chase that jackpot 🏆🔥')}"
        )
    else:  # outcome == "lose"
        final_frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        final_text = (
            f"😅 {md_escape(tg_user.first_name)}, {md_escape('no win this time.')}\n\n"
            f"{md_escape('Better luck next spin! Try again and chase that jackpot 🎰🔥')}"
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
