# ===============================================================
# handlers/tryluck.py  (✅ Cleaned + Webform Integration)
# ===============================================================
import os
import asyncio
import random
import logging
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from helpers import get_or_create_user
from services.tryluck import spin_logic
from db import get_async_session
from models import GameState
from handlers.payments import handle_buy_callback
from handlers.free import free_menu

logger = logging.getLogger(__name__)

# -------------------------------
# 🔐 Admin
# -------------------------------
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# -------------------------------
# Markdown escape helper
# -------------------------------
def md_escape(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

def make_tryluck_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎰 Try Again", callback_data="tryluck"),
            InlineKeyboardButton("📊 Available Tries", callback_data="show_tries"),
        ],
        [
            InlineKeyboardButton("💳 Buy Tries", callback_data="buy"),
        ],
        [
            InlineKeyboardButton("🎁 Free Tries", callback_data="free"),
        ]
    ])

# -----------------------------------------------------------------
# 🎰 TRYLUCK HANDLER (Main)
# -----------------------------------------------------------------
async def tryluck_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    logger.info(f"🔔 /tryluck called by {tg_user.id} ({tg_user.username})")

    outcome = "no_tries"

    async with get_async_session() as session:
        try:
            async with session.begin():
                user = await get_or_create_user(session, tg_id=tg_user.id, username=tg_user.username)
                outcome = await spin_logic(session, user)
                await session.refresh(user)

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

    # 🧱 Outcome handling
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

    # 🎞️ Start spinner animation
    msg = await update.effective_message.reply_text("🎰 <i>Spinning...</i>", parse_mode="HTML")

    spinner_emojis = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣", "🍀", "🎲"]
    num_reels = 3
    total_spins = random.randint(6, 10)

    last_frame = None  # 👀 Track last frame to avoid redundant edits

    for _ in range(total_spins):
        frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        new_text = f"🎰 {frame}"

        # ✅ Prevent “Message is not modified” error
        if last_frame != new_text:
            try:
                await msg.edit_text(new_text, parse_mode="HTML")
                last_frame = new_text
            except telegram.error.BadRequest as e:
                if "Message is not modified" in str(e):
                    logger.debug("⚠️ Skipped redundant edit_text (same content).")
                else:
                    logger.warning(f"⚠️ edit_text failed: {e}")
        else:
            logger.debug("⚠️ Skipped redundant frame — identical content.")

        await asyncio.sleep(0.4)

    # 🧍 Player name fallback
    player_name = tg_user.first_name or "Player"

    # 🏁 Final result
    if outcome == "win":
        final_frame = "💎 💎 💎"
        final_text = (
            f"🏆 <b>Congratulations, {player_name}!</b> 🎉\n\n"
            "You just <b>won the jackpot!</b>\n\n"
            "The cycle has been reset — a new round begins now 🔁"
        )
    else:
        final_frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        final_text = (
            f"😅 {player_name}, no win this time.\n\n"
            "Better luck next spin!\n\n Try again and chase that jackpot 🎰🔥"
        )

    safe_message = f"<b>🎰 {final_frame}</b>\n\n{final_text}"

    # 🧠 Safe message update (with graceful fallback)
    try:
        await msg.edit_text(
            text=safe_message,
            parse_mode="HTML",
            reply_markup=None if outcome == "win" else make_tryluck_keyboard(),
        )

        # ✅ If the user WON, show prize choices
        if outcome == "win":
            choice_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 iPhone 16 Pro Max", callback_data="choose_iphone16")],
                [InlineKeyboardButton("📱 iPhone 17 Pro Max", callback_data="choose_iphone17")],
                [InlineKeyboardButton("📱 Samsung Galaxy Z Flip 7", callback_data="choose_flip7")],
                [InlineKeyboardButton("📱 Samsung Galaxy S25 Ultra", callback_data="choose_s25ultra")],
            ])

            await msg.reply_text(
                f"🎉 <b>Congratulations again, {player_name}!</b>\n\n"
                "You’ve unlocked the <b>Grand Jackpot Prize!</b> 🏆\n\n"
                "Please choose your preferred reward below 👇",
                parse_mode="HTML",
                reply_markup=choice_keyboard,
            )

    except Exception as e:
        logger.warning(f"⚠️ Could not edit message: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=safe_message,
            parse_mode="HTML"
        )

# ---------------------------------------------------------------
# 📱 HANDLE PHONE CHOICE (STEP 2 → Webform)
# ---------------------------------------------------------------
async def handle_phone_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_user = query.from_user
    choice = query.data
    await query.answer()

    # ✅ Map button callback data to actual phone names
    choice_map = {
        "choose_iphone17": "iPhone 17 Pro Max",
        "choose_iphone16": "iPhone 16 Pro Max",
        "choose_flip7": "Samsung Galaxy Z Flip 7",
        "choose_s25ultra": "Samsung Galaxy S25 Ultra",
    }

    user_choice = choice_map.get(choice)
    if not user_choice:
        await query.edit_message_text("⚠️ Invalid choice. Please try again.", parse_mode="HTML")
        return

    # ✅ Save user’s choice
    async with get_async_session() as session:
        user = await get_or_create_user(session, tg_id=tg_user.id, username=tg_user.username)
        user.choice = user_choice
        await session.commit()

    # ✅ Generate winner form URL
    if not RENDER_EXTERNAL_URL:
        await query.edit_message_text(
            "⚠️ Server URL not configured. Please contact admin.",
            parse_mode="HTML",
        )
        return

    winner_url = f"{RENDER_EXTERNAL_URL}/winner-form?tgid={tg_user.id}&choice={user_choice}"

    # ✅ Confirm selection and share form link
    await query.edit_message_text(
        f"✅ You selected: <b>{user_choice}</b>\n\n"
        f"🎉 Please fill your delivery details securely using the form below 👇\n\n"
        f"<a href='{winner_url}'>📝 Fill Form</a>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

# ---------------------------------------------------------------
# 📊 SHOW TRIES CALLBACK
# ---------------------------------------------------------------
async def show_tries_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    logger.info(f"📊 show_tries_callback called by tg_id={tg_user.id}")

    async with get_async_session() as session:
        user = await get_or_create_user(session, tg_id=tg_user.id, username=tg_user.username)
        total_paid = user.tries_paid or 0
        total_bonus = user.tries_bonus or 0
        total = total_paid + total_bonus

        # --- Create the inline buttons
        keyboard = [
            [
                InlineKeyboardButton("🎯 Try Luck", callback_data="tryluck"),
                InlineKeyboardButton("💰 Buy Try", callback_data="buy"),
            ],
            [
                InlineKeyboardButton("🎁 Free Tries", callback_data="free"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # --- Answer the callback and send message
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            md_escape(
                f"📊 *Available Tries*\n\n"
                f"🎟️ Paid: {total_paid}\n"
                f"🎁 Bonus: {total_bonus}\n"
                f"💫 Total: {total}"
            ),
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
        )

# --------------------------------------------------------------
# 🎰 TRY AGAIN CALLBACK
# --------------------------------------------------------------
async def tryluck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await tryluck_handler(update, context)

# ----------------------------------------------------------------
# 🧩 REGISTER HANDLERS (Order Matters)
# ----------------------------------------------------------------
def register_handlers(application):
    # 1️⃣ Commands
    application.add_handler(CommandHandler("tryluck", tryluck_handler))

    # 2️⃣ Callbacks (specific → general)
    application.add_handler(CallbackQueryHandler(tryluck_callback, pattern="^tryluck$"))
    application.add_handler(CallbackQueryHandler(show_tries_callback, pattern="^show_tries$"))
    application.add_handler(CallbackQueryHandler(handle_buy_callback, pattern="^buy$"))
    application.add_handler(CallbackQueryHandler(free_menu, pattern="^free$"))

    # ✅ Handle all phone choices (iPhone + Samsung)
    application.add_handler(CallbackQueryHandler(handle_phone_choice, pattern=r"^choose_(iphone|flip|s25)"))

    # 3️⃣ (No text form handlers needed anymore ✅)
    # 4️⃣ Fallback handler
    application.add_handler(
        MessageHandler(filters.ALL, lambda u, c: u.message.reply_text("Use /start to begin the journey 🎰"))
    )
