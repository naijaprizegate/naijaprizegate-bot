# ===============================================================
# handlers/tryluck.py  (✅ HTML version — Telegram-safe <br/>)
# ===============================================================
import os
import asyncio
import random
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.ext import MessageHandler, filters
from helpers import get_or_create_user
from services.tryluck import spin_logic
from db import get_async_session
from models import GameState  # ✅ handles game cycle reset

logger = logging.getLogger(__name__)

from os import getenv
ADMIN_USER_ID = int(getenv("ADMIN_USER_ID", 0))

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

# ------------------------------------------------------
# 🧠 GLOBAL STORE — MULTI-WINNER SAFE TRYLUCK HANDLER
# ------------------------------------------------------
winner_flows = {}  # { user_id: {"stage": ..., "choice": ..., "data": {...}} }


# ------------------------------
# 🎰 TRYLUCK HANDLER (Main)
# ------------------------------
async def tryluck_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /tryluck command or button click"""
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

    # -----------------------------
    # 🪩 OUTCOME HANDLING
    # ------------------------------
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

    msg = await update.effective_message.reply_text("🎰 <i>Spinning...</i>", parse_mode="HTML")

    spinner_emojis = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣", "🍀", "🎲"]
    num_reels = 3
    total_spins = random.randint(6, 10)

    for _ in range(total_spins):
        frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        await msg.edit_text(f"🎰 {frame}", parse_mode="HTML")
        await asyncio.sleep(0.4)

    player_name = tg_user.first_name or "Player"

    if outcome == "win":
        final_frame = "💎 💎 💎"
        final_text = (
            f"🏆 <b>Congratulations, {player_name}!</b> 🎉\n\n"
            "You just <b>won the jackpot!</b>\n\n"
            "The cycle has been reset — a new round begins now 🔁\n\n"
            "👉 Don’t keep luck waiting — hit <b>Try Luck</b> again and chase the next jackpot 🏆🔥"
        )
    else:
        final_frame = " ".join(random.choice(spinner_emojis) for _ in range(num_reels))
        final_text = (
            f"😅 {player_name}, no win this time.\n\n"
            "Better luck next spin! Try again and chase that jackpot 🎰🔥"
        )

    safe_message = f"<b>🎰 {final_frame}</b>\n\n{final_text}"

    try:
        await msg.edit_text(
            text=safe_message,
            parse_mode="HTML",
            reply_markup=None if outcome == "win" else make_tryluck_keyboard(),
        )

        # ✅ If the user WON, ask them to choose their prize
        if outcome == "win":
            choice_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 iPhone 16 Pro Max", callback_data="choose_iphone16")],
                [InlineKeyboardButton("📱 iPhone 17 Pro Max", callback_data="choose_iphone17")]
            ])

            await msg.reply_text(
                f"🎉 <b>Congratulations again, {player_name}!</b>\n\n"
                "You’ve unlocked the <b>Grand Jackpot Prize!</b> 🏆\n\n"
                "Please choose your preferred reward below 👇",
                parse_mode="HTML",
                reply_markup=choice_keyboard
            )

    except Exception as e:
        logger.warning(f"⚠️ Could not edit message: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=safe_message,
            parse_mode="HTML",
        )


# --------------------------------------
# 📱 HANDLE iPHONE CHOICE (STEP 2)
# --------------------------------------
async def handle_iphone_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_user = query.from_user
    choice = query.data
    await query.answer()

    user_choice = "iPhone 17 Pro Max" if choice == "choose_iphone17" else "iPhone 16 Pro Max"

    # ✅ Save choice + start form
    async with get_async_session() as session:
        user = await get_or_create_user(session, tg_id=tg_user.id, username=tg_user.username)
        user.choice = user_choice
        user.winner_stage = "ask_name"
        user.winner_data = {}
        await session.commit()

    await query.edit_message_text(
        f"✅ You selected: <b>{user_choice}</b>\n\nLet’s get your delivery details next 📦",
        parse_mode="HTML",
    )

    await query.message.reply_text("1️⃣ What’s your <b>full name?</b>", parse_mode="HTML")


# -------------------------------------------------------
# 🧠 PERSISTENT WINNER FORM (DB-BACKED)
# -------------------------------------------------------
async def winner_form_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    text = update.message.text.strip()

    async with get_async_session() as session:
        user = await get_or_create_user(session, tg_id=tg_user.id, username=tg_user.username)
        stage = user.winner_stage
        data = user.winner_data or {}
        choice = user.choice

        # 🚨 Ignore if not in a flow
        if not stage:
            return await update.message.reply_text(
                "🤔 I’m not expecting that right now.\nUse /tryluck to start again 🎰",
                parse_mode="HTML",
            )

        # --- Step 1: Name
        if stage == "ask_name":
            data["full_name"] = text
            user.winner_stage = "ask_phone"
            user.winner_data = data
            await session.commit()
            return await update.message.reply_text("2️⃣ What’s your <b>phone number?</b>", parse_mode="HTML")

        # --- Step 2: Phone
        elif stage == "ask_phone":
            # ✅ Basic validation
            if not text.replace("+", "").replace(" ", "").isdigit():
                return await update.message.reply_text(
                    "⚠️ Please enter a valid phone number (digits only).", parse_mode="HTML"
                )

            data["phone"] = text
            user.winner_stage = "ask_address"
            user.winner_data = data
            await session.commit()
            return await update.message.reply_text("3️⃣ Enter your <b>delivery address</b> 🏠", parse_mode="HTML")

        # --- Step 3: Address (Final Step Before Confirmation)
        elif stage == "ask_address":
            data["address"] = text

            summary = (
                f"📋 <b>Please confirm your details:</b>\n\n"
                f"👤 <b>Name:</b> {data['full_name']}\n"
                f"📱 <b>Phone:</b> {data['phone']}\n"
                f"🏠 <b>Address:</b> {data['address']}\n"
                f"🎁 <b>Selected Prize:</b> {choice}\n\n"
                "Are these details correct?"
            )

            keyboard = [
                [
                    InlineKeyboardButton("✅ Confirm", callback_data="confirm_yes"),
                    InlineKeyboardButton("🔁 Edit", callback_data="confirm_no"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            user.winner_stage = "confirm_details"
            user.winner_data = data
            await session.commit()

            return await update.message.reply_text(summary, parse_mode="HTML", reply_markup=reply_markup)


# -------------------------------------------------------
# 🎯 HANDLE CONFIRMATION BUTTONS
# -------------------------------------------------------
async def handle_confirmation_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_user = query.from_user
    choice = query.data
    await query.answer()

    async with get_async_session() as session:
        user = await get_or_create_user(session, tg_id=tg_user.id, username=tg_user.username)
        data = user.winner_data or {}
        prize_choice = user.choice

        # --- User wants to edit
        if choice == "confirm_no":
            user.winner_stage = "ask_name"
            user.winner_data = {}
            await session.commit()

            await query.edit_message_text(
                "🔁 Okay, let’s start over.\n\n1️⃣ What’s your <b>full name?</b>",
                parse_mode="HTML",
            )
            return

        # --- User confirms
        elif choice == "confirm_yes":
            user.winner_stage = None
            user.winner_data = {}
            await session.commit()

            await query.edit_message_text(
                "✅ <b>All done!</b>\n\nYour delivery details have been recorded successfully. 📦\n"
                "Our team will contact you soon to arrange your prize delivery. 🚚✨",
                parse_mode="HTML",
            )

            # ✅ Notify Admin safely
            try:
                if ADMIN_USER_ID:
                    username_display = (
                        f"@{tg_user.username}"
                        if tg_user.username
                        else tg_user.full_name or tg_user.first_name
                    )

                    alert = (
                        f"📢 <b>NEW WINNER ALERT!</b>\n\n"
                        f"👤 <b>Name:</b> {data['full_name']}\n"
                        f"📱 <b>Phone:</b> {data['phone']}\n"
                        f"🏠 <b>Address:</b> {data['address']}\n"
                        f"🎁 <b>Choice:</b> {prize_choice}\n"
                        f"🆔 <b>Telegram:</b> {username_display}\n"
                        f"🕒 <i>Recorded just now</i>"
                    )
                    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=alert, parse_mode="HTML")

            except Exception as e:
                logger.error(f"❌ Failed to alert admin: {e}")

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
    application.add_handler(CallbackQueryHandler(handle_iphone_choice, pattern="^choose_iphone"))
    application.add_handler(CallbackQueryHandler(handle_confirmation_choice, pattern="^confirm_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, winner_form_handler))
    

