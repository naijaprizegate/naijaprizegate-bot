# ===================================================================
# handlers/playtrivia.py (CYCLE-AWARE + UX: countdown/timeout/locks)
# ===================================================================
import os
import asyncio
import random
import logging
import re
import time
import telegram

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from sqlalchemy import text

from db import get_async_session
from helpers import get_or_create_user, consume_try, md_escape
from utils.questions_loader import get_next_question_for_user
from utils.signer import generate_signed_token

from services.playtrivia import resolve_trivia_attempt, admin_add_cycle_points, admin_reset_cycle
from services.airtime_service import create_pending_airtime_payout

from models import GameState, GlobalCounter

logger = logging.getLogger(__name__)

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")

TRIVIA_TIMEOUT_SECONDS = int(os.getenv("TRIVIA_TIMEOUT_SECONDS", "20"))


# =============================
# Play keyboard
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


# =============================
# Category keyboard
# =============================
def make_category_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📘 History", callback_data="cat_History"),
                InlineKeyboardButton("🎬 Entertainment", callback_data="cat_Entertainment"),
            ],
            [
                InlineKeyboardButton("⚽ Football", callback_data="cat_Football"),
                InlineKeyboardButton("🌍 Geography", callback_data="cat_Geography"),
            ],
            [
                InlineKeyboardButton("📖 English", callback_data="cat_English"),
                InlineKeyboardButton("🔬 Sciences", callback_data="cat_Sciences"),
            ],
            [
                InlineKeyboardButton("➗ Mathematics", callback_data="cat_Mathematics"),
            ],
        ]
    )

# ================================================================
# STEP 1 — Entry point
# ================================================================
async def playtrivia_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    logger.info("🔔 playtrivia triggered | tg_id=%s", tg.id)

    # Check tries
    async with get_async_session() as session:
        async with session.begin():
            user = await get_or_create_user(
                session,
                tg_id=tg.id,
                username=tg.username,
                full_name=getattr(tg, "full_name", None),
            )
            total = int(user.tries_paid or 0) + int(user.tries_bonus or 0)

            if total <= 0:
                return await update.effective_message.reply_text(
                    "😅 You have no trivia attempts left.\n\n"
                    "Use *Get More Trivia Attempts* or *Earn Free Trivia Attempts* to continue playing.\n\n"
                    "You could become a proud owner of\n"
                    "*AirPods*, *Bluetooth Speakers* and *Smart Phones*",
                    parse_mode="Markdown",
                    reply_markup=make_play_keyboard(),
                )

    return await update.effective_message.reply_text(
        "🧠 *Choose your trivia category:*\n\n"
        "✅ Correct answers increase your points.\n"
        "🏁 When the campaign threshold is reached, the top scorer wins the grand prize.\n\n"
        "*AirPods* • *Bluetooth Speakers* • *Smart Phones*",
        parse_mode="Markdown",
        reply_markup=make_category_keyboard(),
    )


# ================================================================
# STEP 2 — Category chosen: send question + countdown + timeout
# ================================================================
async def trivia_category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg_user = query.from_user
    logger.info("🧠 category selected | tg_id=%s | data=%s", tg_user.id, query.data)

    _, category = query.data.split("_", 1)

    # ✅ TRUE sequential (per user, per category) using DB
    q = await get_next_question_for_user(tg_user.id, category)

    # Safety fallback
    if not q:
        return await query.message.reply_text(
            "⚠️ No questions found for this category yet.",
            reply_markup=make_category_keyboard(),
        )

    # Save pending data
    context.user_data["pending_trivia_question"] = q
    context.user_data["trivia_answered"] = False
    context.user_data["trivia_deadline"] = time.time() + TRIVIA_TIMEOUT_SECONDS

    question_text = (
        f"🧠 *{category} Trivia!*\n\n"
        f"{q['question']}\n\n"
        f"A. {q['options']['A']}\n"
        f"B. {q['options']['B']}\n"
        f"C. {q['options']['C']}\n"
        f"D. {q['options']['D']}"
    )

    # Answer buttons
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

    # Send message
    sent_msg = await query.message.reply_text(
        question_text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

    # Countdown display
    async def countdown(message, base_text, kb_markup, secs: int):
        for remaining in range(secs, 0, -1):
            if context.user_data.get("trivia_answered", False):
                break
            try:
                await message.edit_text(
                    f"{base_text}\n\n⏳ *Time left:* {remaining}s",
                    parse_mode="Markdown",
                    reply_markup=kb_markup,
                )
            except telegram.error.BadRequest:
                break
            except Exception:
                break
            await asyncio.sleep(1)

    asyncio.create_task(countdown(sent_msg, question_text, keyboard, TRIVIA_TIMEOUT_SECONDS))

    # Timeout task (cancel old one)
    old_timer = context.user_data.get("trivia_timer")
    if isinstance(old_timer, asyncio.Task) and not old_timer.done():
        old_timer.cancel()

    context.user_data["trivia_timer"] = asyncio.create_task(
        trivia_timeout_task(update, context, sent_msg.message_id, TRIVIA_TIMEOUT_SECONDS)
    )


# ================================================================
# ⏱️ TRIVIA TIMEOUT TASK
# ================================================================
async def trivia_timeout_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_id: int,
    timeout_seconds: int,
):
    await asyncio.sleep(timeout_seconds)

    if context.user_data.get("trivia_answered", False):
        return

    context.user_data["trivia_answered"] = True
    context.user_data["is_correct_answer"] = False

    try:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=message_id,
            text=(
                "⏳ *Time’s up!* You didn’t answer in time.\n\n"
                "This attempt will be processed as *incorrect*."
            ),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    await run_spin_and_apply_reward(update, context)


# ================================================================
# STEP 3 — Answer handler (lock + evaluate)
# ================================================================
async def trivia_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Already answered / expired
    if context.user_data.get("trivia_answered", False):
        return await query.edit_message_text(
            "⏳ This trivia round is already closed.\n\n"
            "Tap *Play Again* to start a new round.",
            parse_mode="Markdown",
            reply_markup=make_play_keyboard(),
        )

    # Lock
    context.user_data["trivia_answered"] = True

    # Cancel timer
    timer = context.user_data.pop("trivia_timer", None)
    if isinstance(timer, asyncio.Task) and not timer.done():
        try:
            timer.cancel()
        except Exception:
            pass

    # Extract answer
    _, qid, selected = query.data.split("_", 2)
    question = context.user_data.get("pending_trivia_question")

    if not question or str(question.get("id")) != str(qid):
        return await query.edit_message_text(
            "⚠️ Trivia round expired or missing data.\n\nPlease start a new round.",
            parse_mode="Markdown",
            reply_markup=make_play_keyboard(),
        )

    correct_letter = question["answer"]
    correct_text = question["options"][correct_letter]
    is_correct = (selected == correct_letter)

    context.user_data["is_correct_answer"] = is_correct

    if is_correct:
        await query.edit_message_text(
            "🎯 *Correct!*\n\n_Calculating your reward..._",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(
            "🙈 *Not correct!*\n"
            f"👉 Correct answer: `{correct_letter}` — *{correct_text}*\n\n"
            "_Calculating your reward..._",
            parse_mode="Markdown",
        )

    await asyncio.sleep(0.8)
    await run_spin_and_apply_reward(update, context)


# ================================================================
# STEP 4 — Spin animation + DB resolve + UI apply
# ================================================================
async def run_spin_and_apply_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    tg_id = tg.id
    username = tg.username
    player_name = tg.first_name or "Player"

    correct = bool(context.user_data.pop("is_correct_answer", False))

    # Spin animation FIRST (UX)
    msg = await update.effective_message.reply_text("🎡 *Spinning...*", parse_mode="Markdown")

    symbols = ["⭐", "🎯", "💫", "🎉", "📚", "🎁", "🏅", "🔔"]
    last_frame = None
    for _ in range(random.randint(7, 12)):
        frame = " ".join(random.choice(symbols) for _ in range(3))
        if frame != last_frame:
            try:
                await msg.edit_text(f"🎡 {frame}")
            except Exception:
                pass
            last_frame = frame
        await asyncio.sleep(0.30)

    # Resolve in DB (atomic)
    try:
        async with get_async_session() as session:
            async with session.begin():
                user = await get_or_create_user(
                    session,
                    tg_id=tg_id,
                    username=username,
                    full_name=getattr(tg, "full_name", None),
                )

                outcome = await resolve_trivia_attempt(
                    session=session,
                    user=user,
                    correct_answer=correct,
                    consume_try_fn=consume_try,
                )

                cycle_id = int(outcome.cycle_id or 1)
                points = int(outcome.points or 0)

                # NO TRIES
                if outcome.type == "no_tries":
                    return await msg.edit_text(
                        "🚫 You have no trivia attempts left.\n\n"
                        "Use *Get More Trivia Attempts* or *Earn Free Trivia Attempts* to continue.",
                        parse_mode="Markdown",
                        reply_markup=make_play_keyboard(),
                    )

                # -----------------------------
                # Primary outcome (milestones)
                # -----------------------------

                # AIRTIME
                if outcome.type == "airtime" and outcome.airtime_amount:
                    payout = await create_pending_airtime_payout(
                        session=session,
                        user_id=str(user.id),
                        tg_id=tg_id,
                        total_premium_spins=points,
                        cycle_id=outcome.cycle_id,
                    )

                    if not payout:
                        await msg.edit_text(
                            "⚠️ Could not create airtime reward right now. Please try again.",
                            parse_mode="Markdown",
                            reply_markup=make_play_keyboard(),
                        )
                    else:
                        payout_id = payout["payout_id"]  # UUID string

                        keyboard = InlineKeyboardMarkup(
                            [[InlineKeyboardButton("⚡ Claim Airtime Reward", callback_data=f"claim_airtime:{payout_id}")]]
                        )

                        await msg.edit_text(
                            f"🏆 *Milestone Unlocked!* 🎉\n\n"
                            f"🎯 Points: *{points}* (Cycle {cycle_id})\n"
                            f"💸 *₦{outcome.airtime_amount} Airtime Reward* unlocked!\n\n"
                            "Tap the button below to claim 👇",
                            parse_mode="Markdown",
                            reply_markup=keyboard,
                        )

                # GADGET
                elif outcome.type == "gadget" and outcome.gadget in ("earpod", "speaker"):
                    prize_label = "Wireless Earpods" if outcome.gadget == "earpod" else "Bluetooth Speaker"
                    emoji = "🎧" if outcome.gadget == "earpod" else "🔊"

                    await msg.edit_text(
                        f"🏆 *BIG MILESTONE UNLOCKED!* 🎉🔥\n\n"
                        f"🎯 Points: *{points}* (Cycle {cycle_id})\n"
                        f"🎁 Reward: *{prize_label}* {emoji}\n\n"
                        "Please complete your delivery details 👇",
                        parse_mode="Markdown",
                    )

                    if not RENDER_EXTERNAL_URL:
                        await update.effective_chat.send_message(
                            "⚠️ Server URL missing. Please contact support.",
                            parse_mode="Markdown",
                        )
                    else:
                        token = generate_signed_token(
                            tgid=tg_id,
                            choice=prize_label,
                            expires_seconds=3600,
                        )
                        link = f"{RENDER_EXTERNAL_URL}/winner-form?token={token}"
                        await update.effective_chat.send_message(
                            f"<a href='{link}'>📝 Fill Delivery Form</a>",
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )

                # NONE / LOSE / cycle_end (without milestone)
                else:
                    if correct:
                        await msg.edit_text(
                            f"✅ *Correct!* Your points are now *{points}* (Cycle {cycle_id}).\n\n"
                            "Keep going 💪",
                            parse_mode="Markdown",
                            reply_markup=make_play_keyboard(),
                        )
                    else:
                        await msg.edit_text(
                            "❌ Not correct.\n\n"
                            "Try again — your next correct answer adds points.",
                            parse_mode="Markdown",
                            reply_markup=make_play_keyboard(),
                        )

                # -----------------------------
                # Cycle end announcement (after primary message)
                # -----------------------------
                if bool(outcome.cycle_ended) and outcome.winner_tg_id:
                    winner_tg = int(outcome.winner_tg_id)
                    winner_points = int(outcome.winner_points or 0)

                    if winner_tg == tg_id:
                        # Winner sees phone choices
                        await update.effective_chat.send_message(
                            f"🎉 *Congratulations, {player_name}!* 🎉\n\n"
                            f"You finished *Cycle {cycle_id}* at the top of the leaderboard 🏆🔥\n"
                            f"Winning points: *{winner_points}*\n\n"
                            "Please choose your smartphone reward below 👇",
                            parse_mode="Markdown",
                        )

                        keyboard = InlineKeyboardMarkup(
                            [
                                [InlineKeyboardButton("📱 iPhone 16 Pro Max", callback_data="choose_iphone16")],
                                [InlineKeyboardButton("📱 iPhone 17 Pro Max", callback_data="choose_iphone17")],
                                [InlineKeyboardButton("📱 Samsung Flip 7", callback_data="choose_flip7")],
                                [InlineKeyboardButton("📱 Samsung S25 Ultra", callback_data="choose_s25ultra")],
                            ]
                        )
                        await update.effective_chat.send_message(
                            "🎁 Select your reward option 👇",
                            reply_markup=keyboard,
                            parse_mode="Markdown",
                        )

                        # Admin notification
                        try:
                            if ADMIN_USER_ID:
                                await context.bot.send_message(
                                    ADMIN_USER_ID,
                                    "🏁 CYCLE WINNER\n\n"
                                    f"Cycle: {cycle_id}\n"
                                    f"User: {player_name}\n"
                                    f"TG ID: {tg_id}\n"
                                    f"Username: @{username}\n"
                                    f"Points: {winner_points}",
                                )
                        except Exception:
                            pass
                    else:
                        # Everyone else just sees cycle ended notice
                        await update.effective_chat.send_message(
                            f"🏁 *Cycle {cycle_id} ended!*\n\n"
                            "A new cycle has started. Keep playing to top the leaderboard 🔥",
                            parse_mode="Markdown",
                            reply_markup=make_play_keyboard(),
                        )

    except Exception:
        logger.exception("❌ Reward processing failure")
        return await msg.edit_text(
            "⚠️ Reward processing error. Please try again.",
            parse_mode="Markdown",
            reply_markup=make_play_keyboard(),
        )


# ================================================================
# 📱 PHONE CHOICE (winner form flow)
# ================================================================
async def handle_phone_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg = query.from_user
    tg_id = tg.id
    choice = query.data

    mapping = {
        "choose_iphone16": "Smartphone Option 1",
        "choose_iphone17": "Smartphone Option 2",
        "choose_flip7": "Smartphone Option 3",
        "choose_s25ultra": "Smartphone Option 4",
    }
    user_choice = mapping.get(choice)
    if not user_choice:
        return await query.edit_message_text("⚠️ Invalid choice")

    if not RENDER_EXTERNAL_URL:
        return await query.edit_message_text("⚠️ Server URL missing")

    token = generate_signed_token(
        tgid=tg_id,
        choice=user_choice,
        expires_seconds=3600,
    )
    link = f"{RENDER_EXTERNAL_URL}/winner-form?token={token}"

    await query.edit_message_text(
        f"🎉 You selected <b>{user_choice}</b>!\n\n"
        f"<a href='{link}'>📝 Fill Delivery Form</a>\n\n"
        "📌 Rewards are promotional, subject to verification.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ================================================================
# 📊 SHOW TRIES
# ================================================================
async def show_tries_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user

    async with get_async_session() as session:
        async with session.begin():
            user = await get_or_create_user(
                session,
                tg_id=tg.id,
                username=tg.username,
                full_name=getattr(tg, "full_name", None),
            )
            paid = int(user.tries_paid or 0)
            bonus = int(user.tries_bonus or 0)

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
# 🧪 ADMIN TEST: add points to current cycle
# Usage: /testpoints 9
# ================================================================
async def testpoints_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id

    if tg_id != ADMIN_USER_ID:
        return await update.effective_message.reply_text("Not allowed.")

    if not context.args:
        return await update.effective_message.reply_text("Usage: /testpoints <number>")

    try:
        delta = int(context.args[0])
    except Exception:
        return await update.effective_message.reply_text("Delta must be a number.")

    if delta == 0:
        return await update.effective_message.reply_text("Delta must not be 0.")

    async with get_async_session() as session:
        async with session.begin():
            user = await get_or_create_user(
                session,
                tg_id=tg_id,
                username=update.effective_user.username,
                full_name=getattr(update.effective_user, "full_name", None),
            )

            gs = await session.get(GameState, 1)
            cycle_id = int(gs.current_cycle or 1) if gs else 1

            new_points = await admin_add_cycle_points(session, user, cycle_id, delta)

    return await update.effective_message.reply_text(
        f"✅ Added {delta} points.\nCycle {cycle_id} points now: {new_points}"
    )


# ================================================================
# 🧪 ADMIN TEST: reduce points to zero in current cycle
# Usage: /resetpoints
# ================================================================
async def resetpoints_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user

    # 🔐 Admin-only
    if tg.id != ADMIN_USER_ID:
        return await update.effective_message.reply_text("❌ Not authorized.")

    async with get_async_session() as session:
        async with session.begin():

            # Get admin user row
            user = await get_or_create_user(
                session,
                tg_id=tg.id,
                username=tg.username,
                full_name=getattr(tg, "full_name", None),
            )

            # Get current cycle
            gs = await session.get(GameState, 1)
            cycle_id = int(gs.current_cycle or 1) if gs else 1

            # 🔥 RESET POINTS FOR THIS CYCLE ONLY
            await session.execute(
                text("""
                    UPDATE user_cycle_stats
                    SET points = 0,
                        updated_at = NOW()
                    WHERE user_id = :uid
                      AND cycle_id = :cycle
                """),
                {
                    "uid": str(user.id),
                    "cycle": cycle_id,
                },
            )

    return await update.effective_message.reply_text(
        f"♻️ Points reset successful.\n\n"
        f"Cycle: {cycle_id}\n"
        f"Points: 0\n\n"
        "You can now test milestones again."
    )

# ================================================================
# 🧪 ADMIN — ADD PAID TRIES (TESTING ONLY)
# Command: /addtries <number>
# ================================================================
async def addtries_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    tg_id = tg.id

    # Admin check
    if tg_id != ADMIN_USER_ID:
        return await update.effective_message.reply_text("⛔ Not allowed.")

    if not context.args:
        return await update.effective_message.reply_text(
            "Usage: /addtries <number>\nExample: /addtries 20"
        )

    try:
        count = int(context.args[0])
        if count <= 0:
            raise ValueError
    except Exception:
        return await update.effective_message.reply_text(
            "❌ Number of tries must be a positive integer."
        )

    async with get_async_session() as session:
        async with session.begin():
            # Get or create admin user
            user = await get_or_create_user(
                session,
                tg_id=tg_id,
                username=tg.username,
                full_name=getattr(tg, "full_name", None),
            )

            # Add paid tries
            user.tries_paid = int(user.tries_paid or 0) + count

            # Ensure counters exist
            gc = await session.get(GlobalCounter, 1)
            if not gc:
                gc = GlobalCounter(id=1, paid_tries_total=0)
                session.add(gc)

            gs = await session.get(GameState, 1)
            if not gs:
                gs = GameState(
                    id=1,
                    current_cycle=1,
                    paid_tries_this_cycle=0,
                    lifetime_paid_tries=0,
                )
                session.add(gs)

            # Update counters
            gc.paid_tries_total += count
            gs.paid_tries_this_cycle += count
            gs.lifetime_paid_tries += count

    return await update.effective_message.reply_text(
        f"✅ Added *{count} paid tries*\n\n"
        f"🎟️ Paid tries now: *{user.tries_paid}*",
        parse_mode="Markdown",
    )

# ================================================================
# 🔄 ADMIN — RESET CYCLE (TESTING / EMERGENCY)
# ================================================================
async def resetcycle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id

    if tg_id != ADMIN_USER_ID:
        return await update.effective_message.reply_text("❌ Not allowed.")

    async with get_async_session() as session:
        async with session.begin():
            info = await admin_reset_cycle(session)

    ended = info["ended_cycle"]
    new_cycle = info["new_cycle"]
    winner = info.get("winner")

    text = (
        f"🛑 *Cycle Reset Successful*\n\n"
        f"📦 Ended cycle: *{ended}*\n"
        f"🚀 New cycle started: *{new_cycle}*\n"
    )

    if winner:
        text += (
            "\n🏆 *Winner at reset*\n"
            f"TG ID: `{winner['tg_id']}`\n"
            f"Points: *{winner['points']}*"
        )
    else:
        text += "\nℹ️ No winner in ended cycle."

    await update.effective_message.reply_text(text, parse_mode="Markdown")


# ================================================================
# REGISTER HANDLERS
# ================================================================
def register_handlers(application, handle_buy_callback=None, free_menu=None):
    # category selection
    application.add_handler(CallbackQueryHandler(trivia_category_handler, pattern=r"^cat_"))

    # answers
    application.add_handler(CallbackQueryHandler(trivia_answer_handler, pattern=r"^ans_\d+_[A-D]$"))

    # entry
    application.add_handler(CommandHandler("playtrivia", playtrivia_handler))
    
    # admin test points (temporary)
    application.add_handler(CommandHandler("testpoints", testpoints_handler))
    application.add_handler(CommandHandler("resetpoints", resetpoints_handler))
    application.add_handler(CommandHandler("addtries", addtries_handler))
    application.add_handler(CommandHandler("resetcycle", resetcycle_handler))

    application.add_handler(CallbackQueryHandler(playtrivia_handler, pattern=r"^playtrivia$"))

    # phone choice (winner)
    application.add_handler(CallbackQueryHandler(handle_phone_choice, pattern=r"^choose_"))

    # show tries
    application.add_handler(CallbackQueryHandler(show_tries_callback, pattern=r"^show_tries$"))

    # buy/free hooks (optional injection)
    if handle_buy_callback:
        application.add_handler(CallbackQueryHandler(handle_buy_callback, pattern=r"^buy$"))
    if free_menu:
        application.add_handler(CallbackQueryHandler(free_menu, pattern=r"^free$"))
