# ===============================================================
# handlers/tryluck.py  (✅ HTML version — Telegram-safe <br/>)
# ===============================================================
import asyncio
import random
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.ext import ContextTypes
from helpers import get_or_create_user
from services.tryluck import spin_logic
from db import get_async_session
from models import GameState  # ✅ handles game cycle reset

logger = logging.getLogger(__name__)

import re

def md_escape(text: str) -> str:
    """
    Escapes MarkdownV2 special characters for Telegram.
    """
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# --------------------
# Inline Keyboards
# --------------------
def make_tryluck_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎰 Try Again", callback_data="tryluck"),
                InlineKeyboardButton("📊 Available Tries", callback_data="show_tries"),
            ]
        ]
    )


# -------------------------
# Main TryLuck Handler
# --------------------------
async def tryluck_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /tryluck command or button click"""
    tg_user = update.effective_user
    logger.info(f"🔔 /tryluck called by {tg_user.id} ({tg_user.username})")

    outcome = "no_tries"

    async with get_async_session() as session:
        try:
            async with session.begin():
                user = await get_or_create_user(
                    session, tg_id=tg_user.id, username=tg_user.username
                )

                logger.info(
                    f"📊 Before spin: user_id={user.id}, paid={user.tries_paid}, bonus={user.tries_bonus}"
                )

                outcome = await spin_logic(session, user)
                await session.refresh(user)

                logger.info(
                    f"🎲 Outcome={outcome} | After spin: paid={user.tries_paid}, bonus={user.tries_bonus}"
                )

                # ✅ Reset game cycle on jackpot win
                if outcome == "win":
                    gs = await session.get(GameState, 1)
                    if gs:
                        gs.current_cycle += 1
                        gs.paid_tries_this_cycle = 0
                        await session.commit()
                        logger.info(f"🔁 New game cycle started: {gs.current_cycle}")

        except Exception as e:
            logger.exception(f"❌ Error during /tryluck for {tg_user.id}: {e}")
            outcome = "error"

    # -----------------------
    # Outcome Messaging
    # -----------------------
    if outcome == "no_tries":
        return await update.effective_message.reply_text(
            "😅 You don’t have any tries left! Buy more spins or earn free ones.",
            parse_mode="HTML",
        )

    if outcome == "error":
        return await update.effective_message.reply_text(
            "⚠️ <b>Oops!</b> Something went wrong while processing your spin. Please try again.",
            parse_mode="HTML",
        )

    msg = await update.effective_message.reply_text(
        "🎰 <i>Spinning...</i>", parse_mode="HTML"
    )

    spinner_emojis = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣", "🍀", "🎲"]
    num_reels = 3
    total_spins = random.randint(6, 10)

    for _ in range(total_spins):
        frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        await msg.edit_text(f"🎰 {frame}", parse_mode="HTML")
        await asyncio.sleep(0.4)

    # ------------------------
    # Final Outcome
    # ------------------------
    player_name = tg_user.first_name or "Player"

    if outcome == "win":
        final_frame = "💎 💎 💎"
        final_text = (
            f"🏆 <b>Congratulations, {player_name}!</b> 🎉<br/><br/>"
            "You just <b>won the jackpot!</b><br/><br/>"
            "The cycle has been reset — a new round begins now 🔁<br/><br/>"
            "👉 Don’t keep luck waiting — hit <b>Try Luck</b> again and chase the next jackpot 🏆🔥"
        )
    else:
        final_frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        final_text = (
            f"😅 {player_name}, no win this time.<br/><br/>"
            "Better luck next spin! Try again and chase that jackpot 🎰🔥"
        )

    safe_message = f"<b>🎰 {final_frame}</b><br/><br/>{final_text}"

    try:
        await msg.edit_text(
            text=safe_message,
            parse_mode="HTML",
            reply_markup=make_tryluck_keyboard(),
        )
    except Exception as e:
        logger.warning(f"⚠️ Could not edit message: {e}")
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=safe_message,
                parse_mode="HTML",
                reply_markup=make_tryluck_keyboard(),
            )
        except Exception as inner_e:
            logger.error(f"❌ Failed to send fallback message: {inner_e}")

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
