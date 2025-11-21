# ===============================================================
# handlers/tryluck.py  (🎰 Final Version with Trivia + Rewards)
# ===============================================================

import os
import asyncio
import random
import logging
import re
import telegram
import time
from sqlalchemy import text
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from helpers import get_or_create_user
from utils.questions_loader import get_random_question
from services.tryluck import spin_logic
from db import get_async_session
from models import GameState
from handlers.payments import handle_buy_callback
from handlers.free import free_menu
from utils.signer import generate_signed_token

logger = logging.getLogger(__name__)

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# =============================
# Markdown escape helper
# =============================
def md_escape(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# =============================
# Try Again keyboard
# =============================
def make_tryluck_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎰 Try Again", callback_data="tryluck"),
            InlineKeyboardButton("📊 Available Tries", callback_data="show_tries"),
        ],
        [InlineKeyboardButton("💳 Buy Tries", callback_data="buy")],
        [InlineKeyboardButton("🎁 Free Tries", callback_data="free")],
    ])

# ================================================================
# STEP 0 — Handle Trivia Category Selection
# ================================================================
async def trivia_category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg_user = query.from_user

    # Extract the chosen category from callback: "cat_History"
    _, category = query.data.split("_")
    context.user_data["chosen_trivia_category"] = category

    # --------------------------
    # Load trivia question (filtered by category)
    # --------------------------
    q = get_random_question(category)

    # ✅ SAVE THE FULL QUESTION
    context.user_data["pending_trivia_question"] = q      # <— IMPORTANT
    context.user_data["pending_trivia_answer"] = q["answer"]
    context.user_data["pending_trivia_qid"] = q["id"]

    context.user_data["trivia_answered"] = False  # user hasn’t answered yet

    # Deadline = now + 20 seconds
    context.user_data["trivia_deadline"] = time.time() + 20

    question_text = (
        f"🧠 *{category} Trivia!*\n\n"
        f"{q['question']}\n\n"
        f"A. {q['options']['A']}\n"
        f"B. {q['options']['B']}\n"
        f"C. {q['options']['C']}\n"
        f"D. {q['options']['D']}"
    )

    # Active answer buttons
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("A", callback_data=f"ans_{q['id']}_A"),
            InlineKeyboardButton("B", callback_data=f"ans_{q['id']}_B"),
        ],
        [
            InlineKeyboardButton("C", callback_data=f"ans_{q['id']}_C"),
            InlineKeyboardButton("D", callback_data=f"ans_{q['id']}_D"),
        ]
    ])

    # Send trivia message
    sent_msg = await query.message.reply_text(
        question_text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )


    # ============================================================
    # ⏳ COUNTDOWN DISPLAY (20 → 1)
    # ============================================================
    async def countdown(message, q_text, kb_markup, secs=20):
        for remaining in range(secs, 0, -1):

            # Stop countdown if user already answered
            if context.user_data.get("trivia_answered", False):
                break

            try:
                await message.edit_text(
                    f"{q_text}\n\n⏳ *Time left:* {remaining}s",
                    parse_mode="Markdown",
                    reply_markup=kb_markup
                )
            except telegram.error.BadRequest:
                break
            except Exception:
                break

            await asyncio.sleep(1)

    asyncio.create_task(countdown(sent_msg, question_text, keyboard))

    # ============================================================
    # 🕒 TIMEOUT TASK (locks buttons after 20 seconds)
    # ============================================================
    old_timer = context.user_data.get("trivia_timer")
    if isinstance(old_timer, asyncio.Task) and not old_timer.done():
        old_timer.cancel()

    context.user_data["trivia_timer"] = asyncio.create_task(
        trivia_timeout_task(
            update,
            context,
            sent_msg.message_id,
            timeout_seconds=20
        )
    )



# ================================================================
# STEP 0b — Begin Trivia AFTER category is chosen
# ================================================================
async def start_trivia_after_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category = context.user_data.get("chosen_trivia_category")

    # Load question filtered by chosen category
    q = get_random_question(category)

    # Move into existing trivia workflow
    return await tryluck_handler(update, context)


# ================================================================
# STEP 1 — Send Trivia Question (with TIMER + COUNTDOWN + LOCK)
# ================================================================
async def tryluck_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    tg_user = update.effective_user
    logger.info(f"🔔 /tryluck triggered by {tg_user.id}")

    # --------------------------
    # Check tries (NO deduction here!)
    # --------------------------
    async with get_async_session() as session:
        async with session.begin():
            user = await get_or_create_user(
                session,
                tg_id=tg_user.id,
                username=tg_user.username
            )

            if (user.tries_paid + user.tries_bonus) <= 0:
                return await update.effective_message.reply_text(
                    "😅 You have no tries left. Buy more or earn free ones.",
                    parse_mode="HTML"
                )

            # DO NOT deduct here!
            await session.commit()

    # --------------------------
    # STEP A — Ask for Trivia Category
    # --------------------------
    category_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📘 History", callback_data="cat_History"),
            InlineKeyboardButton("🎬 Entertainment", callback_data="cat_Entertainment"),
        ],
        [
            InlineKeyboardButton("⚽ Football", callback_data="cat_Football"),
            InlineKeyboardButton("🌍 Geography", callback_data="cat_Geography"),
        ],
    ])

    # Stop the handler here — trivia will continue after category selection
    return await update.effective_message.reply_text(
        "🧠 *Choose your Trivia Category:*",
        parse_mode="Markdown",
        reply_markup=category_keyboard
    )


# ================================================================
# ⏱️ TRIVIA TIMEOUT TASK
# ================================================================
async def trivia_timeout_task(update: Update, context: ContextTypes.DEFAULT_TYPE, message_id: int, timeout_seconds: int):
    """Automatically triggers BASIC spin if user fails to answer within time."""
    await asyncio.sleep(timeout_seconds)

    # If already answered — do nothing
    if context.user_data.get("trivia_answered"):
        return

    # Mark as answered (to block further input)
    context.user_data["trivia_answered"] = True

    chat_id = update.effective_chat.id

    try:
        # Inform user time is up
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="⏳ *Time’s up!* You didn’t answer in time.\n\nYou get a **Basic Spin** 🎰🔥",
            parse_mode="Markdown"
        )
    except:
        pass

    # Perform the spin as BASIC
    context.user_data["is_premium_spin"] = False  # force basic spin
    await run_spin_after_trivia(update, context)


# ================================================================
# STEP 2 — Handle Trivia Answer (with lock + expired protection)
# ================================================================
async def trivia_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # ---------------------------------------------------------
    # ❌ If user tries answering AFTER time expired or answered
    # ---------------------------------------------------------
    if context.user_data.get("trivia_answered", False):
        return await query.edit_message_text(
            "⏳ Time already expired — you get a **Basic Spin** 🎰🔥",
            parse_mode="Markdown"
        )

    # ---------------------------------------------------------
    # 🔒 LOCK NOW — prevents double clicking
    # ---------------------------------------------------------
    context.user_data["trivia_answered"] = True

    # ---------------------------------------------------------
    # ⛔ Cancel countdown timeout task
    # ---------------------------------------------------------
    timer = context.user_data.pop("trivia_timer", None)
    if isinstance(timer, asyncio.Task) and not timer.done():
        try:
            timer.cancel()
        except:
            pass

    # ---------------------------------------------------------
    # 🎯 Evaluate Answer (uses saved question object)
    # ---------------------------------------------------------
    _, qid, selected = query.data.split("_")

    # Get the FULL question stored earlier
    question = context.user_data.get("pending_trivia_question")

    if not question:
        return await query.edit_message_text(
            "⚠️ Error: Trivia expired or missing. Please try again."
        )

    correct_letter = question["answer"]

    correct_text = question["options"][correct_letter]
    
    is_correct = (selected == correct_letter)

    # Save premium spin status
    context.user_data["is_premium_spin"] = is_correct

    # ---------------------------------------------------------
    # 📝 Respond to user
    # ---------------------------------------------------------
    if is_correct:
        await query.edit_message_text(
            f"🎯 *Correct!* \nYou unlocked a **Premium Spin** 🔥\n\n"
            f"Spinning...",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(
            f"🙈 *Not correct this time!* \n"
            f"👉 *Correct answer:* `{correct_letter}` — *{correct_text}*\n\n"
            f"But no worries — you still get a **Basic Spin** 🎰🔥\n\n"
            f"Spinning...",
            parse_mode="Markdown"
        )

    # ---------------------------------------------------------
    # 🎰 Continue to spin phase
    # ---------------------------------------------------------
    await run_spin_after_trivia(update, context)


# ================================================================
# STEP 3 — Run Spin After Trivia
# ================================================================
async def run_spin_after_trivia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    is_premium = context.user_data.pop("is_premium_spin", False)

    # Perform spin in DB
    async with get_async_session() as session:
        try:
            async with session.begin():
                user = await get_or_create_user(
                    session, tg_id=tg_user.id, username=tg_user.username
                )

                outcome = await spin_logic(session, user, is_premium)
                await session.refresh(user)

                # Jackpot accounting
                if outcome == "jackpot":
                    gs = await session.get(GameState, 1)
                    if gs:
                        gs.current_cycle += 1
                        gs.paid_tries_this_cycle = 0
                        await session.commit()

        except Exception as e:
            logger.exception("Spin failure", exc_info=True)
            return await update.effective_message.reply_text(
                "⚠️ Spin error. Please try again.", parse_mode="HTML"
            )

    # Spinner animation
    msg = await update.effective_message.reply_text(
        "🎰 *Spinning...*", parse_mode="Markdown"
    )

    spinner = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣", "🍀", "🎲"]

    last_frame = None
    for _ in range(random.randint(6, 10)):
        frame = " ".join(random.choice(spinner) for _ in range(3))
        if frame != last_frame:
            try:
                await msg.edit_text(f"🎰 {frame}")
            except:
                pass
            last_frame = frame
        await asyncio.sleep(0.4)

    player_name = tg_user.first_name or "Player"

    # ============================================================
    # 🎯 OUTCOME HANDLING
    # ============================================================

    # 🏆 JACKPOT → same phone selection → same delivery form
    if outcome == "jackpot":
        await msg.edit_text(
            f"🎰 💎💎💎\n\n🏆 *Congratulations, {player_name}!* You won the *JACKPOT!* 🔥",
            parse_mode="Markdown"
        )

        choice_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 iPhone 16 Pro Max", callback_data="choose_iphone16")],
            [InlineKeyboardButton("📱 iPhone 17 Pro Max", callback_data="choose_iphone17")],
            [InlineKeyboardButton("📱 Samsung Galaxy Z Flip 7", callback_data="choose_flip7")],
            [InlineKeyboardButton("📱 Samsung Galaxy S25 Ultra", callback_data="choose_s25ultra")],
        ])

        return await msg.reply_text(
            "🎉 Choose your prize 👇",
            parse_mode="HTML",
            reply_markup=choice_keyboard
        )

    # ============================================================
    # 🎁 MULTI-SIZE AIRTIME → ₦50 / ₦100 / ₦200
    # ============================================================
    if outcome.startswith("airtime_"):
        amount = int(outcome.split("_")[1])

        context.user_data["airtime_amount"] = amount
        context.user_data["awaiting_airtime_number"] = True

        return await msg.edit_text(
            f"🎉 *You Won ₦{amount} Airtime!* 🎉\n\n"
            "📲 Send your *phone number* to receive your airtime.",
            parse_mode="Markdown"
        )

    # ============================================================
    # 🎧 EARPODS → now uses SAME DELIVERY FORM as jackpot
    # ============================================================
    if outcome == "earpod":
        prize_label = "Wireless Earpods"

        await msg.edit_text(
            f"🎰 🎧🎧🎧\n\n🎉 *You won {prize_label}!*",
            parse_mode="Markdown"
        )

        # Notify admin
        try:
            await context.bot.send_message(
                ADMIN_USER_ID,
                f"🎧 *Earpod Winner*\nUser: {tg_user.id} (@{tg_user.username})"
            )
        except Exception:
            pass

        # Save choice for delivery form
        if not RENDER_EXTERNAL_URL:
            return await msg.reply_text(
                "⚠️ Delivery form unavailable. Please contact support.",
                parse_mode="HTML"
            )

        async with get_async_session() as session:
            async with session.begin():
                db_user = await get_or_create_user(
                    session, tg_id=tg_user.id, username=tg_user.username
                )
                db_user.choice = prize_label
                await session.commit()

        token = generate_signed_token(tgid=tg_user.id, choice=prize_label, expires_seconds=3600)
        link = f"{RENDER_EXTERNAL_URL}/winner-form?token={token}"

        return await msg.reply_text(
            f"🎉 Please complete delivery details for your <b>{prize_label}</b>:\n\n"
            f"<a href='{link}'>📝 Fill Delivery Form</a>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    # ============================================================
    # 🔊 BLUETOOTH SPEAKER → also uses SAME DELIVERY FORM
    # ============================================================
    if outcome == "speaker":
        prize_label = "Bluetooth Speaker"

        await msg.edit_text(
            f"🎰 🔊🔊🔊\n\n🎉 *You won a {prize_label}!*",
            parse_mode="Markdown"
        )

        # Notify admin
        try:
            await context.bot.send_message(
                ADMIN_USER_ID,
                f"🔊 *Speaker Winner*\nUser: {tg_user.id} (@{tg_user.username})"
            )
        except Exception:
            pass

        # Save choice for delivery form
        if not RENDER_EXTERNAL_URL:
            return await msg.reply_text(
                "⚠️ Delivery form unavailable. Please contact support.",
                parse_mode="HTML"
            )

        async with get_async_session() as session:
            async with session.begin():
                db_user = await get_or_create_user(
                    session, tg_id=tg_user.id, username=tg_user.username
                )
                db_user.choice = prize_label
                await session.commit()

        token = generate_signed_token(tgid=tg_user.id, choice=prize_label, expires_seconds=3600)
        link = f"{RENDER_EXTERNAL_URL}/winner-form?token={token}"

        return await msg.reply_text(
            f"🎉 Please complete your delivery details for your <b>{prize_label}</b>:\n\n"
            f"<a href='{link}'>📝 Fill Delivery Form</a>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    # ============================================================
    # ❌ LOSS
    # ============================================================
    final = " ".join(random.choice(spinner) for _ in range(3))

    await msg.edit_text(
        f"🎰 {final}\n\n😅 No win this time.\n\nTry again! 🎰🔥",
        parse_mode="Markdown",
        reply_markup=make_tryluck_keyboard()
    )

# ================================================================
# 📲 AIRTIME NUMBER HANDLER (AUTO-PAYOUT for ₦50/₦100/₦200)
# ================================================================
async def airtime_number_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only run if user is indeed submitting a number
    if not context.user_data.get("awaiting_airtime_number"):
        return

    raw_input = update.message.text.strip()
    user = update.effective_user

    # Stop waiting immediately
    context.user_data["awaiting_airtime_number"] = False

    # Retrieve airtime amount determined during spin (₦50, ₦100, ₦200)
    amount = context.user_data.pop("airtime_amount", 100)

    # -------------------------------------------
    # Normalize + validate Nigerian numbers
    # -------------------------------------------
    number = raw_input.replace(" ", "").replace("-", "")

    if number.startswith("+"):
        number = number[1:]

    if number.startswith("0"):  # 0803… → 234803…
        number = "234" + number[1:]

    if not (number.startswith("234") and len(number) == 13):
        return await update.message.reply_text(
            "❌ Invalid number format.\n\n"
            "Please send a valid Nigerian number.\n"
            "Example: 0803xxxxxxx"
        )

    # -------------------------------------------
    # Insert into airtime_payouts table
    # -------------------------------------------
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    INSERT INTO airtime_payouts (user_id, tg_id, phone_number, amount, status)
                    VALUES (:uid, :tg, :phone, :amt, 'pending')
                """),
                {
                    "uid": None,   # optional
                    "tg": user.id,
                    "phone": number,
                    "amt": amount,
                }
            )

    # -------------------------------------------
    # Notify user
    # -------------------------------------------
    await update.message.reply_text(
        f"🎉 Great! Your airtime of *₦{amount}* will be delivered shortly to:\n"
        f"📱 {number}",
        parse_mode="Markdown"
    )

    # -------------------------------------------
    # Notify Admin
    # -------------------------------------------
    try:
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=(
                "📲 *New Airtime Payout (AUTO)*\n\n"
                f"User: {user.id} (@{user.username})\n"
                f"Amount: ₦{amount}\n"
                f"Phone: {number}"
            ),
            parse_mode="Markdown"
        )
    except:
        pass


# ================================================================
# 📱 PHONE CHOICE (JACKPOT FLOW — unchanged)
# ================================================================
async def handle_phone_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_user = query.from_user
    choice = query.data
    await query.answer()

    mapping = {
        "choose_iphone17": "iPhone 17 Pro Max",
        "choose_iphone16": "iPhone 16 Pro Max",
        "choose_flip7": "Samsung Galaxy Z Flip 7",
        "choose_s25ultra": "Samsung Galaxy S25 Ultra",
    }

    user_choice = mapping.get(choice)
    if not user_choice:
        return await query.edit_message_text("⚠️ Invalid choice")

    async with get_async_session() as session:
        user = await get_or_create_user(session, tg_id=tg_user.id)
        user.choice = user_choice
        await session.commit()

    if not RENDER_EXTERNAL_URL:
        return await query.edit_message_text("⚠️ Server URL missing")

    token = generate_signed_token(
        tgid=tg_user.id,
        choice=user_choice,
        expires_seconds=3600
    )

    link = f"{RENDER_EXTERNAL_URL}/winner-form?token={token}"

    await query.edit_message_text(
        f"🎉 You selected <b>{user_choice}</b>!\n\n"
        f"<a href='{link}'>📝 Fill Delivery Form</a>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# ================================================================
# 📊 SHOW TRIES (unchanged)
# ================================================================
async def show_tries_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    async with get_async_session() as session:
        user = await get_or_create_user(session, tg_id=tg_user.id)
        paid = user.tries_paid or 0
        bonus = user.tries_bonus or 0

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 Try Luck", callback_data="tryluck"),
            InlineKeyboardButton("💰 Buy Try", callback_data="buy")
        ],
        [InlineKeyboardButton("🎁 Free Tries", callback_data="free")]
    ])

    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        md_escape(
            f"📊 *Available Tries*\n\n"
            f"🎟️ Paid: {paid}\n"
            f"🎁 Bonus: {bonus}\n"
            f"💫 Total: {paid + bonus}"
        ),
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
    )

# ================================================================
# ⏳ TRIVIA TIMEOUT TASK (locks buttons + forces Basic Spin)
# ================================================================
async def trivia_timeout_task(update, context, message_id, timeout_seconds=8):
    try:
        # Wait for the allowed time
        await asyncio.sleep(timeout_seconds)

        # If the user already answered, stop
        if context.user_data.get("trivia_answered", False):
            return

        # Mark as answered + lock trivia (prevents buttons being used)
        context.user_data["trivia_answered"] = True
        context.user_data["is_premium_spin"] = False   # force BASIC spin

        # Send timeout message
        try:
            await update.effective_chat.send_message(
                "⏳ *Time is up!* You didn’t answer fast enough.\n"
                "You'll get a **Basic Spin** 🎰🔥",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        # Run the spin automatically
        await run_spin_after_trivia(update, context)

    except asyncio.CancelledError:
        # This happens when the user answers before timeout
        return


# ================================================================
# 🧩 REGISTER ALL HANDLERS
# ================================================================
def register_handlers(application):

    # Trivia category selection
    application.add_handler(
        CallbackQueryHandler(trivia_category_handler, pattern=r"^cat_")
    )


    # Trivia answers
    application.add_handler(
        CallbackQueryHandler(trivia_answer_handler, pattern=r"^ans_\d+_[A-D]$")
    )

    # Main tryluck flow
    application.add_handler(CommandHandler("tryluck", tryluck_handler))
    application.add_handler(CallbackQueryHandler(tryluck_handler, pattern="^tryluck$"))

    # Jackpot phone-choice → delivery form
    application.add_handler(
        CallbackQueryHandler(handle_phone_choice, pattern=r"^choose_")
    )

    # Show tries / Buy / Free
    application.add_handler(
        CallbackQueryHandler(show_tries_callback, pattern="^show_tries$")
    )
    application.add_handler(CallbackQueryHandler(handle_buy_callback, pattern="^buy$"))
    application.add_handler(CallbackQueryHandler(free_menu, pattern="^free$"))

    # Airtime phone handler
    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), airtime_number_handler)
    )

    # Fallback
    application.add_handler(
        MessageHandler(filters.ALL, lambda u, c: u.message.reply_text("Use /start to begin 🎰"))
    )
