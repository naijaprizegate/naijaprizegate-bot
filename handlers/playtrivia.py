# ===================================================================
# handlers/playtrivia.py  (🧠 Trivia-Based Rewards Flow – Compliance-Oriented)
# ===================================================================

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
from services.playtrivia import reward_logic  
from db import get_async_session, AsyncSessionLocal
from models import GameState
from handlers.payments import handle_buy_callback
from handlers.free import free_menu
from handlers.core import ask_phone
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
# Play Again / Tries keyboard
# =============================
def make_play_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🧠 Play Again", callback_data="playtrivia"),
                InlineKeyboardButton("📊 My Available Trivia Attempts", callback_data="show_tries"),
            ],
            [InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy")],
            [InlineKeyboardButton("🎁 Earn Free Trivia Attempts", callback_data="free")],
        ]
    )


# ================================================================
# STEP 0 — Handle Trivia Category Selection
# ================================================================
async def trivia_category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg_user = query.from_user
    logger.info(f"🧠 Trivia category selected by {tg_user.id}: {query.data}")

    # Extract the chosen category from callback: "cat_History"
    _, category = query.data.split("_")
    context.user_data["chosen_trivia_category"] = category

    # --------------------------
    # Load trivia question (filtered by category)
    # --------------------------
    q = get_random_question(category)

    # ✅ SAVE THE FULL QUESTION IN USER STATE
    context.user_data["pending_trivia_question"] = q
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
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("A", callback_data=f"ans_{q['id']}_A"),
                InlineKeyboardButton("B", callback_data=f"ans_{q['id']}_B"),
            ],
            [
                InlineKeyboardButton("C", callback_data=f"ans_{q['id']}_C"),
                InlineKeyboardButton("D", callback_data=f"ans_{q['id']}_D"),
            ],
        ]
    )

    # Send trivia message
    sent_msg = await query.message.reply_text(
        question_text,
        parse_mode="Markdown",
        reply_markup=keyboard,
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
                    reply_markup=kb_markup,
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
            timeout_seconds=20,
        )
    )


# ================================================================
# STEP 1 — Entry point: “Play Trivia” (was /playtrivia)
# ================================================================
async def playtrivia_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    This handler is the main entry for playing a trivia round.
    User-facing language is skill-based: “Play Trivia” instead of “Try Luck”.
    """
    tg_user = update.effective_user
    logger.info(f"🔔 Trivia/rewards flow triggered by {tg_user.id}")

    # --------------------------
    # Check available tries (credits to play trivia)
    # --------------------------
    async with get_async_session() as session:
        async with session.begin():
            user = await get_or_create_user(
                session,
                tg_id=tg_user.id,
                username=tg_user.username,
            )

            if (user.tries_paid + user.tries_bonus) <= 0:
                return await update.effective_message.reply_text(
                    "😅 You have no trivia attempts left.\n\n"
                    "Use *Get More Trivia Attempts* or *Earn Free Trivia Attempts* to continue playing.",
                    parse_mode="Markdown",
                )

            # NOTE: Tries deduction is handled inside reward logic (reward_logic).
            await session.commit()

    # --------------------------
    # STEP A — Ask for Trivia Category
    # --------------------------
    category_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📘 History", callback_data="cat_History"),
                InlineKeyboardButton("🎬 Entertainment", callback_data="cat_Entertainment"),
            ],
            [
                InlineKeyboardButton("⚽ Football", callback_data="cat_Football"),
                InlineKeyboardButton("🌍 Geography", callback_data="cat_Geography"),
            ],
        ]
    )

    return await update.effective_message.reply_text(
        "🧠 *Choose your trivia category:*",
        parse_mode="Markdown",
        reply_markup=category_keyboard,
    )


# ================================================================
# ⏱️ TRIVIA TIMEOUT TASK
# ================================================================
async def trivia_timeout_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_id: int,
    timeout_seconds: int = 20,
):
    """
    When user does not answer within the time limit:
    - Mark trivia as answered
    - Assign a *standard* reward tier (non-premium)
    - Continue to reward calculation
    """
    await asyncio.sleep(timeout_seconds)

    # If already answered — do nothing
    if context.user_data.get("trivia_answered"):
        return

    context.user_data["trivia_answered"] = True
    context.user_data["is_premium_reward"] = False  # standard reward tier

    chat_id = update.effective_chat.id

    try:
        # Inform user time is up
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                "⏳ *Time’s up!* You didn’t answer in time.\n\n"
                "This attempt will be processed in the *standard reward tier*."
            ),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    # Proceed to reward calculation (same flow)
    await run_spin_after_trivia(update, context)


# ================================================================
# STEP 2 — Handle Trivia Answer (with lock + expired protection)
# ================================================================
async def trivia_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # ---------------------------------------------------------
    # If user tries answering AFTER time expired or answered
    # ---------------------------------------------------------
    if context.user_data.get("trivia_answered", False):
        return await query.edit_message_text(
            "⏳ This trivia round is already closed.\n\n"
            "Your reward for this attempt will follow the *standard tier* rules.",
            parse_mode="Markdown",
        )

    # 🔒 LOCK NOW — prevents double clicking
    context.user_data["trivia_answered"] = True

    # ⛔ Cancel countdown timeout task
    timer = context.user_data.pop("trivia_timer", None)
    if isinstance(timer, asyncio.Task) and not timer.done():
        try:
            timer.cancel()
        except Exception:
            pass

    # 🎯 Evaluate Answer (uses saved question object)
    _, qid, selected = query.data.split("_")

    question = context.user_data.get("pending_trivia_question")

    if not question:
        return await query.edit_message_text(
            "⚠️ Error: Trivia round has expired or data is missing.\n\n"
            "Please start a new round.",
            parse_mode="Markdown",
        )

    correct_letter = question["answer"]
    correct_text = question["options"][correct_letter]

    is_correct = selected == correct_letter

    # Save reward tier status
    # True  → premium/high tier reward
    # False → standard/basic reward
    context.user_data["is_premium_reward"] = is_correct

    # 📝 Respond to user (skill-focused language)
    if is_correct:
        await query.edit_message_text(
            "🎯 *Correct!*\n\n"
            "You’ve unlocked the *premium reward tier* for this attempt.\n\n"
            "_Calculating your reward..._",
            parse_mode="Markdown",
        )
        # CORRECT → continue immediately 🔥
        return await run_spin_after_trivia(update, context)

    # INCORRECT → show correct answer then delay before reward
    await query.edit_message_text(
        "🙈 *Not correct this time!*\n"
        f"👉 *Correct answer:* `{correct_letter}` — *{correct_text}*\n\n"
        "This attempt will use the *standard reward tier*.\n\n"
        "_Calculating your reward..._",
        parse_mode="Markdown",
    )

    # Wait before starting reward logic so user sees this
    await asyncio.sleep(1.5)

    # Continue to reward phase
    await run_spin_after_trivia(update, context)


# ================================================================
# STEP 3 — Reward Calculation After Trivia
# (Previously “spin”; now treated as reward processing / animation)
# ================================================================
async def run_spin_after_trivia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    # Extract state once & clear ASAP
    is_premium = context.user_data.pop("is_premium_reward", False)

    # Perform reward logic in DB
    async with get_async_session() as session:
        try:
            async with session.begin():
                user = await get_or_create_user(
                    session, tg_id=tg_user.id, username=tg_user.username
                )

                # ⚠️ reward_logic should eventually be renamed & adjusted
                outcome = await reward_logic(session, user, is_premium)
                await session.refresh(user)

                # Cycle reset logic (internal accounting)
                if outcome == "Top-Tier Campaign Reward":
                    gs = await session.get(GameState, 1)
                    if gs:
                        gs.current_cycle += 1
                        gs.paid_tries_this_cycle = 0
                        await session.commit()

        except Exception:
            logger.exception("Reward processing failure", exc_info=True)
            return await update.effective_message.reply_text(
                "⚠️ Reward processing error. Please try another round.",
                parse_mode="HTML",
            )
    # 🆕 If user must supply phone number first, redirect now
    if outcome == "ask_phone":
        return await ask_phone(update, context)
    
    # Reward animation (now framed as “processing your reward”)
    msg = await update.effective_message.reply_text(
        "🔄 *Evaluating your earned reward...*",
        parse_mode="Markdown",
    )

    # Simple animation using neutral icons
    symbols = ["⭐", "🎯", "💫", "🎉", "📚", "🎁", "🏅", "🔔"]
    last_frame = None
    for _ in range(random.randint(6, 10)):
        frame = " ".join(random.choice(symbols) for _ in range(3))
        if frame != last_frame:
            try:
                await msg.edit_text(f"🔄 {frame}")
            except Exception:
                pass
            last_frame = frame
        await asyncio.sleep(0.4)

    player_name = tg_user.first_name or "Player"

    # ============================================================
    # 🎯 OUTCOME HANDLING (User-facing text = promotional rewards, not gambling)
    # ============================================================

    # 🏆 TOP-TIER REWARD (internal: "Top-Tier Campaign Reward")
    if outcome == "Top-Tier Campaign Reward":
        await msg.edit_text(
            f"🎉 *Outstanding performance, {player_name}!* \n\n"
            "You’ve unlocked a *top-tier campaign reward* for this round\\.\n\n"
            "Please choose your preferred reward option below:",
            parse_mode="Markdown",
        )

        choice_keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📱 iPhone 16 Pro Max", callback_data="choose_iphone16")],
                [InlineKeyboardButton("📱 iPhone 17 pro Max", callback_data="choose_iphone17")],
                [InlineKeyboardButton("📱 Samsung Flip 7", callback_data="choose_flip7")],
                [InlineKeyboardButton("📱 Samsung S25 Ultra", callback_data="choose_s25ultra")],
            ]
        )

        return await msg.reply_text(
            "🎁 Select a reward option 👇\n\n"
            "📌 *Rewards are skill-based and subject to campaign rules\\.*",
            parse_mode="Markdown",
            reply_markup=choice_keyboard,
        )

    # 🎁 NEW Airtime Reward Flow — No direct phone request here!
    if outcome.startswith("airtime_"):
        # Extract airtime value
        reward_amount = int(outcome.split("_")[1])

        # Update DB: add milestone spin tracker
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    UPDATE users
                    SET premium_spins = premium_spins + 1
                    WHERE tg_id = :tg
                """),
                {"tg": update.effective_user.id}
            )
            await session.commit()

        # Fetch updated premium spin count
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text("SELECT premium_spins FROM users WHERE tg_id = :tg"),
                {"tg": update.effective_user.id}
            )
            row = res.first()
            current_premium_spins = row[0] if row else 1

        # Trigger unified payout reward UI 🚀
        await create_pending_airtime_payout_and_prompt(
            session=session,
            update=update,
            user_id=db_user_id,
            tg_id=update.effective_user.id,
            username=update.effective_user.username,
            total_premium_spins=current_premium_spins
        )

        # Replace the spinning message with notice
        return await msg.edit_text(
            f"🎉 You unlocked an airtime bonus of ₦{reward_amount} 🎉\n\n"
            "👇 Tap the button below to claim your reward.",
            parse_mode="Markdown"
        )

    # 🎧 EARPODS (campaign reward)
    if outcome == "earpod":
        prize_label = "Wireless Earpods"

        await msg.edit_text(
            f"🎉 *You unlocked a campaign reward:* {prize_label} 🎧\n\n"
            "📌 Rewards are promotional and subject to verification.",
            parse_mode="Markdown",
        )

        # Notify admin
        try:
            await context.bot.send_message(
                ADMIN_USER_ID,
                f"🎧 *Earpods Reward*\nUser: {tg_user.id} (@{tg_user.username})",
            )
        except Exception:
            pass

        if not RENDER_EXTERNAL_URL:
            return await msg.reply_text(
                "⚠️ Delivery form unavailable at the moment. Please contact support.",
                parse_mode="HTML",
            )

        async with get_async_session() as session:
            async with session.begin():
                db_user = await get_or_create_user(
                    session, tg_id=tg_user.id, username=tg_user.username
                )
                db_user.choice = prize_label
                await session.commit()

        token = generate_signed_token(
            tgid=tg_user.id, choice=prize_label, expires_seconds=3600
        )
        link = f"{RENDER_EXTERNAL_URL}/winner-form?token={token}"

        return await msg.reply_text(
            f"🎉 Please complete your delivery details for your <b>{prize_label}</b>:\n\n"
            f"<a href='{link}'>📝 Fill Delivery Form</a>",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    # 🔊 BLUETOOTH SPEAKER (campaign reward)
    if outcome == "speaker":
        prize_label = "Bluetooth Speaker"

        await msg.edit_text(
            f"🎉 *You unlocked a campaign reward:* {prize_label} 🔊\n\n"
            "📌 Rewards are promotional and subject to verification.",
            parse_mode="Markdown",
        )

        try:
            await context.bot.send_message(
                ADMIN_USER_ID,
                f"🔊 *Speaker Reward*\nUser: {tg_user.id} (@{tg_user.username})",
            )
        except Exception:
            pass

        if not RENDER_EXTERNAL_URL:
            return await msg.reply_text(
                "⚠️ Delivery form unavailable at the moment. Please contact support.",
                parse_mode="HTML",
            )

        async with get_async_session() as session:
            async with session.begin():
                db_user = await get_or_create_user(
                    session, tg_id=tg_user.id, username=tg_user.username
                )
                db_user.choice = prize_label
                await session.commit()

        token = generate_signed_token(
            tgid=tg_user.id, choice=prize_label, expires_seconds=3600
        )
        link = f"{RENDER_EXTERNAL_URL}/winner-form?token={token}"

        return await msg.reply_text(
            f"🎉 Please complete your delivery details for your <b>{prize_label}</b>:\n\n"
            f"<a href='{link}'>📝 Fill Delivery Form</a>",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    # ============================================================
    # No tangible reward this attempt
    # ============================================================
    final = " ".join(random.choice(["⭐", "📚", "🎯", "💫"]) for _ in range(3))

    await msg.edit_text(
        f"{final}\n\n"
        "ℹ️ No campaign reward unlocked on this attempt.\n\n"
        "Keep playing trivia to improve your stats and leaderboard position! 🏅\n\n"
        "You can click on /start to go back to the main menu",
        parse_mode="Markdown",
        reply_markup=make_play_keyboard(),
    )


# ================================================================
# 📱 PHONE CHOICE (TOP-TIER REWARD FORM FLOW)
# ================================================================
async def handle_phone_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_user = query.from_user
    choice = query.data
    await query.answer()

    mapping = {
        "choose_iphone17": "Smartphone Option 2",
        "choose_iphone16": "Smartphone Option 1",
        "choose_flip7": "Smartphone Option 3",
        "choose_s25ultra": "Smartphone Option 4",
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
        expires_seconds=3600,
    )

    link = f"{RENDER_EXTERNAL_URL}/winner-form?token={token}"

    await query.edit_message_text(
        f"🎉 You selected <b>{user_choice}</b>!\n\n"
        f"<a href='{link}'>📝 Fill Delivery Form</a>\n\n"
        "📌 Rewards are promotional, subject to availability and verification.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ================================================================
# 📊 SHOW TRIES (renamed buttons, same logic)
# ================================================================
async def show_tries_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    async with get_async_session() as session:
        user = await get_or_create_user(session, tg_id=tg_user.id)
        paid = user.tries_paid or 0
        bonus = user.tries_bonus or 0

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🧠 Play Trivia Questions", callback_data="playtrivia"),
                InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy"),
            ],
            [InlineKeyboardButton("🎁 Earn Free Trivia Attempts", callback_data="free")],
        ]
    )

    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        md_escape(
            f"📊 *Available Trivia Attempts*\n\n"
            f"🎟️ Paid: {paid}\n"
            f"🎁 Bonus: {bonus}\n"
            f"💫 Total: {paid + bonus}"
        ),
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
    )


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

    # Main trivia + rewards flow
    application.add_handler(CommandHandler("playtrivia", playtrivia_handler))
    application.add_handler(
        CallbackQueryHandler(playtrivia_handler, pattern="^playtrivia$")
    )

    # Top-tier reward phone-choice → delivery form
    application.add_handler(
        CallbackQueryHandler(handle_phone_choice, pattern=r"^choose_")
    )

    # Show tries / Buy / Free
    application.add_handler(
        CallbackQueryHandler(show_tries_callback, pattern="^show_tries$")
    )
    application.add_handler(
        CallbackQueryHandler(handle_buy_callback, pattern="^buy$")
    )
    application.add_handler(
        CallbackQueryHandler(free_menu, pattern="^free$")
    )

    # Fallback (generic catch-all message handler)
    application.add_handler(
        MessageHandler(
            filters.ALL,
            lambda u, c: u.message.reply_text("Use /start to begin 🧠 Trivia Rewards"),
        )
    )
