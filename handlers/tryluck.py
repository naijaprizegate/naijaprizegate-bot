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
from models import GameState  # ✅ added to handle cycle reset

logger = logging.getLogger(__name__)

# Inline keyboards
def make_tryluck_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎰 Try Again", callback_data="tryluck"),
            InlineKeyboardButton("📊 Available Tries", callback_data="show_tries")
        ]
    ])

async def tryluck_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /tryluck command or inline button callback"""
    tg_user = update.effective_user
    logger.info(f"🔔 /tryluck called by tg_id={tg_user.id}, username={tg_user.username}")

    outcome = "no_tries"

    async with get_async_session() as session:
        try:
            async with session.begin():
                user = await get_or_create_user(
                    session,
                    tg_id=tg_user.id,
                    username=tg_user.username
                )

                logger.info(
                    f"📊 Before spin: user_id={user.id}, tg_id={user.tg_id}, "
                    f"paid={user.tries_paid}, bonus={user.tries_bonus}"
                )

                outcome = await spin_logic(session, user)

                await session.refresh(user)

                logger.info(
                    f"🎲 Outcome={outcome} | After spin: user_id={user.id}, "
                    f"tg_id={user.tg_id}, paid={user.tries_paid}, bonus={user.tries_bonus}"
                )

                # ✅ If jackpot/win: reset the game cycle
                if outcome == "win":
                    gs = await session.get(GameState, 1)
                    if gs:
                        gs.current_cycle += 1
                        gs.paid_tries_this_cycle = 0
                        await session.commit()
                        logger.info(f"🔁 New game cycle started: {gs.current_cycle}")

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

    msg = await update.effective_message.reply_text(
        md_escape("🎰 Spinning..."),
        parse_mode="MarkdownV2"
    )

    spinner_emojis = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣", "🍀", "🎲"]
    num_reels = 3

    total_spins = random.randint(6, 10)
    for _ in range(total_spins):
        frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        await msg.edit_text(md_escape(f"🎰 {frame}"), parse_mode="MarkdownV2")
        await asyncio.sleep(0.4)

    if outcome == "win":
        final_frame = " ".join(["💎"] * num_reels)
        final_text = (
            f"🏆 *Congratulations {md_escape(tg_user.first_name)}!* 🎉\n\n"
            f"{md_escape('You just won the jackpot!')}\n\n"
            f"{md_escape('The cycle has been reset — a new round begins now 🔁')}\n"
            f"{md_escape('👉 Don’t keep luck waiting — hit *Try Luck* again and chase the next jackpot 🏆🔥')}"
        )
    else:
        final_frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        final_text = (
            f"😅 {md_escape(tg_user.first_name)}, {md_escape('no win this time.')}\n\n"
            f"{md_escape('Better luck next spin! Try again and chase that jackpot 🎰🔥')}"
        )

    await msg.edit_text(
        f"🎰 {final_frame}\n\n{final_text}",
        parse_mode="MarkdownV2",
        reply_markup=make_tryluck_keyboard()
    )

# ---------------------------------------------------------------
# Callback for "Available Tries" button
# ---------------------------------------------------------------
async def show_tries_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    logger.info(f"📊 show_tries_callback called by tg_id={tg_user.id}")

    async with get_async_session() as session:
        user = await get_or_create_user(session, tg_id=tg_user.id, username=tg_user.username)
        total_paid = user.tries_paid or 0
        total_bonus = user.tries_bonus or 0
        total = total_paid + total_bonus

        await update.callback_query.answer()  # remove "loading" animation
        await update.callback_query.message.reply_text(
            md_escape(
                f"📊 *Available Tries*\n\n"
                f"🎟️ Paid: {total_paid}\n"
                f"🎁 Bonus: {total_bonus}\n"
                f"💫 Total: {total}"
            ),
            parse_mode="MarkdownV2"
        )

# ---------------------------------------------------------------
# Callback for "Try Again"
# ---------------------------------------------------------------
async def tryluck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await tryluck_handler(update, context)

# ---------------------------------------------------------------
# Register Handlers
# ---------------------------------------------------------------
def register_handlers(application):
    application.add_handler(CommandHandler("tryluck", tryluck_handler))
    application.add_handler(CallbackQueryHandler(tryluck_callback, pattern="^tryluck$"))
    application.add_handler(CallbackQueryHandler(show_tries_callback, pattern="^show_tries$"))
