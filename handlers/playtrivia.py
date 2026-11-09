# ===================================================================
# handlers/playtrivia.py (CYCLE-AWARE + UX: countdown/timeout/locks)
# ===================================================================
import os
import asyncio
import random
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from sqlalchemy import text

from db import get_async_session
from helpers import get_or_create_user, consume_try
from utils.questions_loader import get_next_question_for_user
from utils.signer import generate_signed_token
from services.question_history_service import record_question_history, make_json_question_key
from services.playtrivia import (
    resolve_trivia_attempt,
    admin_add_cycle_points,
    admin_reset_cycle,
    _reward_rank,
    _next_reward,
)
from services.airtime_service import create_pending_airtime_payout
from services.free_trivia_service import (
    reserve_free_trivia_game,
    get_free_trivia_remaining,
    DAILY_FREE_TRIVIA_LIMIT,
)

from models import GameState, GlobalCounter

logger = logging.getLogger(__name__)

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
BASE_URL = os.getenv("BASE_URL", "")
TRIVIA_TIMEOUT_SECONDS = int(os.getenv("TRIVIA_TIMEOUT_SECONDS", "20"))

CATEGORY_KEY_MAP = {
    "History": "nigeria_history",
    "Entertainment": "nigeria_entertainment",
    "Football": "football",
    "Geography": "geography",
    "English": "english",
    "Sciences": "sciences",
    "Mathematics": "mathematics",
}


# =============================
# Shared navigation keyboards
# =============================
def make_back_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Back to Other Menu", callback_data="menu:other")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_play_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🧠 Play Again", callback_data="playtrivia"),
                InlineKeyboardButton("📊 My Available Trivia Attempts", callback_data="show_tries"),
            ],
            [InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy")],
            [InlineKeyboardButton("🎁 Earn Free Trivia Attempts", callback_data="free")],
            [InlineKeyboardButton("⬅️ Back to Other Menu", callback_data="menu:other")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


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
            [
                InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy"),
            ],
            [
                InlineKeyboardButton("⬅️ Back to Other Menu", callback_data="menu:other"),
            ],
            [
                InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main"),
            ],
        ]
    )

def make_free_trivia_category_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📘 History", callback_data="freecat_History"),
                InlineKeyboardButton("🎬 Entertainment", callback_data="freecat_Entertainment"),
            ],
            [
                InlineKeyboardButton("⚽ Football", callback_data="freecat_Football"),
                InlineKeyboardButton("🌍 Geography", callback_data="freecat_Geography"),
            ],
            [
                InlineKeyboardButton("📖 English", callback_data="freecat_English"),
                InlineKeyboardButton("🔬 Sciences", callback_data="freecat_Sciences"),
            ],
            [
                InlineKeyboardButton("➗ Mathematics", callback_data="freecat_Mathematics"),
            ],
            [
                InlineKeyboardButton("🏆 Play Paid Trivia - Win Prizes", callback_data="playtrivia"),
            ],
            [
                InlineKeyboardButton("⬅️ Back to Other Menu", callback_data="menu:other"),
            ],
            [
                InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main"),
            ],
        ]
    )

def make_show_tries_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🧠 Play Trivia Questions", callback_data="playtrivia"),
                InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy"),
            ],
            [InlineKeyboardButton("🎁 Earn Free Trivia Attempts", callback_data="free")],
            [InlineKeyboardButton("⬅️ Back to Other Menu", callback_data="menu:other")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


# ================================================================
# FREE TRIVIA — ENTRY POINT
# ================================================================
async def free_trivia_start_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    tg = update.effective_user

    if query:
        await query.answer()

    try:
        async with get_async_session() as session:
            async with session.begin():
                user = await get_or_create_user(
                    session,
                    tg_id=tg.id,
                    username=tg.username,
                    full_name=getattr(tg, "full_name", None),
                )

                games_started = await reserve_free_trivia_game(
                    session,
                    user.id,
                )

                remaining = await get_free_trivia_remaining(
                    session,
                    user.id,
                )

        if games_started is None:
            return await update.effective_message.reply_text(
                "⛔ *Your Free Trivia allowance is exhausted for today.*\n\n"
                f"You can play up to {DAILY_FREE_TRIVIA_LIMIT} free games "
                "each day.\n\n"
                "Your allowance will reset at midnight Nigeria time.",
                parse_mode="Markdown",
                reply_markup=make_back_menu_keyboard(),
            )

        # Mark this session as Free Trivia.
        context.user_data["trivia_mode"] = "free"
        context.user_data["free_trivia_category_selected"] = False
        context.user_data["trivia_origin"] = "normal"

        # Clear any stale question from a previous round.
        context.user_data.pop("pending_trivia_question", None)
        context.user_data["trivia_answered"] = False
        context.user_data["trivia_processing_lock"] = False

        old_timer = context.user_data.pop("trivia_timer", None)
        if isinstance(old_timer, asyncio.Task) and not old_timer.done():
            old_timer.cancel()


        await update.effective_message.reply_text(
            "🆓 *WELCOME TO FREE TRIVIA!*\n\n"
            "Your knowledge deserves a test, and we've got just the challenge for you! 🚀\n\n"
            "Enjoy *5 FREE GAMES EVERY DAY!* "
            "Choose your favourite category, answer the questions "
            "and have fun putting your knowledge to work.\n\n"
            "🎮 *YOUR DAILY FREE ALLOWANCE*\n\n"
            f"Games started today: *{games_started}/{DAILY_FREE_TRIVIA_LIMIT}*\n"
            f"Free games remaining: *{remaining}*\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🏆 *READY TO PLAY FOR REAL PRIZES?*\n\n"
            "Free Trivia is just the beginning! When you're ready "
            "for bigger opportunities, step into *PAID TRIVIA* "
            "and put your knowledge to the test!\n\n"
            "🎁 *PLAY. WIN. CELEBRATE!*\n\n"
            "Exciting prizes are waiting, including:\n\n"
            "📱 iPhone 18 Pro Max\n"
            "📱 Samsung Galaxy S26 Ultra\n"
            "💸 Instant Airtime Rewards\n"
            "🎧 AirPods\n"
            "🔊 Bluetooth Speakers\n"
            "⌚ Smart Watches\n"
            "📱 Android Smartphones\n\n"
            "Earn *Premium Points* with correct paid answers, "
            "unlock exciting milestone rewards and climb the "
            "*Reward Season Leaderboard!*\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "💰 *WHY NOT EARN BY INVITING FRIENDS TOO?*\n\n"
            "Share NaijaPrizeGate with your friends, grow your "
            "referral network and earn rewards when your qualifying "
            "referrals participate in paid Trivia!\n\n"
            "🔥 *YOU COULD EARN ₦50,000 OR MORE A DAY!*\n\n"
            "Your referral earning potential has no fixed maximum. "
            "The more eligible activity your referral network "
            "generates, the more you could earn.\n\n"
            "💵 Minimum withdrawal: *₦2,000*.\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🚀 *YOUR NaijaPrizeGate JOURNEY STARTS HERE!*\n\n"
            "🆓 Play Free Trivia. Test your knowledge.\n"
            "🏆 Play Paid Trivia. Win exciting prizes.\n"
            "👥 Invite friends. Earn referral rewards.\n\n"
            "*PLAY SMART. WIN BIG. EARN MORE!*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "_Please note: Free Trivia does not earn Premium Points, "
            "prizes or Reward Season leaderboard progress. "
            "Paid Trivia and referral rewards are subject to their "
            "respective eligibility requirements._",
            parse_mode="Markdown",
            reply_markup=make_free_trivia_category_keyboard(),
        )


    except Exception:
        logger.exception("Free Trivia entry failure | tg_id=%s", tg.id)

        return await update.effective_message.reply_text(
            "⚠️ We couldn't start Free Trivia right now. "
            "Please try again shortly.",
            reply_markup=make_back_menu_keyboard(),
        )


# ================================================================
# FINANCE → TRIVIA ENTRY
# ================================================================
async def finance_playtrivia_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Start trivia specifically for Withdrawal Eligibility.

    This explicitly marks the current trivia flow as originating
    from Finance. It prevents ordinary trivia categories such as
    Entertainment from accidentally returning to Withdrawal
    Eligibility merely because an eligibility session ID remains
    in user_data.
    """

    context.user_data["trivia_origin"] = "withdrawal"

    return await playtrivia_handler(update, context)


# ================================================================
# STEP 1 — Entry point
# ================================================================
async def playtrivia_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user

    # Normal trivia entry must never inherit a previous
    # Withdrawal Eligibility trivia origin.
    if context.user_data.get("trivia_origin") != "withdrawal":
        context.user_data["trivia_origin"] = "normal"

    logger.info("🔔 playtrivia triggered | tg_id=%s", tg.id)

    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

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
                    "😅 *You have no Trivia Attempts left.*\n\n"
                    "But your Reward Season journey doesn't have to end here!\n\n"
                    "🏆 *You're competing for:*\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "🏆 *GRAND PRIZE*\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "📱 *iPhone 18 Pro Max*\n\n"
                    "📱 *Samsung Galaxy S26 Ultra*\n\n"
                    "🏆 *The winner chooses one of the two phones\\.*\n\n\n\n"
                    "*Other Milestone Rewards*\n\n"
                    "💸 *Instant Airtime Rewards*\n\n"
                    "🎧 *AirPods*\n\n"
                    "🔊 *Bluetooth Speakers*\n\n"
                    "⌚ *Smart Watch*\n\n"
                    "📱 *Android Smartphone*\n\n\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "📊 *Available Trivia Attempts*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🎟️ Paid: *{int(user.tries_paid or 0)}*\n"
                    f"🎁 Bonus: *{int(user.tries_bonus or 0)}*\n"
                    f"💫 Total: *{total}*\n\n\n\n"
                    "👇 Get more Trivia Attempts to continue earning Premium Points, unlocking milestone rewards and climbing the Reward Season Leaderboard.",
                    parse_mode="Markdown",
                    reply_markup=make_play_keyboard(),
                )

    return await update.effective_message.reply_text(
        "🧠 *Choose Your Trivia Category*\n\n"
        "Welcome to the *NaijaPrizeGate*."
        "*Reward Season!*\n\n"
        "📚 Answer Premium Questions correctly.\n\n"
        "⭐ Earn Premium Points.\n\n"
        "🎁 Unlock milestone rewards.\n\n"
        "🏆 Climb the Reward Season Leaderboard.\n\n"
        "👑 Become the Season Champion and win the Grand Prize.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏆 *GRAND PRIZE*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📱 *iPhone 18 Pro Max*\n\n"
        "📱 *Samsung Galaxy S26 Ultra*\n\n"
        "🏆 *The winner chooses one of the two phones.*\n\n\n\n"
        "*Other Milestone Rewards*\n\n"
        "💸 *Instant Airtime Rewards*\n\n"
        "🎧 *AirPods*\n\n"
        "🔊 *Bluetooth Speakers*\n\n"
        "⌚ *Smart Watch*\n\n"
        "📱 *Android Smartphone*\n\n\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *Available Trivia Attempts*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎟️ Paid: *{int(user.tries_paid or 0)}*\n"
        f"🎁 Bonus: *{int(user.tries_bonus or 0)}*\n"
        f"💫 Total: *{total}*\n\n\n\n"
        "👇 Select a category below to begin.",
        parse_mode="Markdown",
        reply_markup=make_category_keyboard(),
    )


# ================================================================
# FREE TRIVIA — CATEGORY AND QUESTION HANDLER
# ================================================================
async def free_trivia_category_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if not query:
        return

    tg_user = query.from_user

    if context.user_data.get("trivia_mode") != "free":
        await query.answer()
        return await query.message.reply_text(
            "Your Free Trivia session has expired. Please start again.",
            reply_markup=make_back_menu_keyboard(),
        )

    if context.user_data.get("free_trivia_category_selected", False):
        return await query.answer(
            "You've already selected a category for this game. Finish this question first.",
            show_alert=True,
        )

    await query.answer()
    context.user_data["free_trivia_category_selected"] = True

    try:
        category_label = query.data.split("_", 1)[1]
    except Exception:
        return await query.message.reply_text(
            "Invalid category selection.",
            reply_markup=make_free_trivia_category_keyboard(),
        )

    category_key = CATEGORY_KEY_MAP.get(
        category_label,
        category_label,
    )

    try:
        q = await get_next_question_for_user(
            tg_user.id,
            category_key,
            source_type="json_free",
        )

        if not q:
            context.user_data["free_trivia_category_selected"] = False
            return await query.message.reply_text(
                "No questions found for this category yet.",
                reply_markup=make_free_trivia_category_keyboard(),
            )

        q = {
            **q,
            "options": dict(q.get("options") or {}),
        }

        q["category_label"] = category_label
        q["category_key"] = category_key

        # Record the question as seen when served.
        question_key = q.get("id") or make_json_question_key(
            category_key or "unknown",
            q["question"],
        )

        async with get_async_session() as session:
            async with session.begin():
                await record_question_history(
                    session,
                    tg_id=tg_user.id,
                    source_type="json_free",
                    category=category_key,
                    question_key=str(question_key),
                )

        # Free Trivia uses completely separate question state.
        context.user_data["free_trivia_question"] = q
        context.user_data["free_trivia_answered"] = False
        context.user_data["free_trivia_processing_lock"] = False

        question_text = (
            f"🆓 *FREE {category_label} TRIVIA!*\n\n"
            f"{q['question']}\n\n"
            f"A. {q['options']['A']}\n"
            f"B. {q['options']['B']}\n"
            f"C. {q['options']['C']}\n"
            f"D. {q['options']['D']}"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "A",
                        callback_data=f"freeans_{q['id']}_A",
                    ),
                    InlineKeyboardButton(
                        "B",
                        callback_data=f"freeans_{q['id']}_B",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "C",
                        callback_data=f"freeans_{q['id']}_C",
                    ),
                    InlineKeyboardButton(
                        "D",
                        callback_data=f"freeans_{q['id']}_D",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back to Other Menu",
                        callback_data="menu:other",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🏠 Back to Main Menu",
                        callback_data="menu:main",
                    ),
                ],
            ]
        )

        sent_msg = await query.message.reply_text(
            question_text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

        current_qid = str(q["id"])

        async def free_countdown():
            for remaining_seconds in range(
                TRIVIA_TIMEOUT_SECONDS,
                0,
                -1,
            ):
                current_question = context.user_data.get(
                    "free_trivia_question"
                )

                if context.user_data.get("free_trivia_answered"):
                    break

                if (
                    not current_question
                    or str(current_question.get("id")) != current_qid
                ):
                    break

                try:
                    await sent_msg.edit_text(
                        f"{question_text}\n\n"
                        f"⏳ *Time left: {remaining_seconds}s*",
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
                except BadRequest:
                    break
                except Exception:
                    logger.exception(
                        "Free Trivia countdown update failed."
                    )
                    break

                await asyncio.sleep(1)

        old_timer = context.user_data.pop(
            "free_trivia_timer",
            None,
        )

        if isinstance(old_timer, asyncio.Task) and not old_timer.done():
            old_timer.cancel()

        context.user_data["free_trivia_timer"] = asyncio.create_task(
            free_countdown()
        )

        context.user_data["free_trivia_timeout_task"] = asyncio.create_task(
            free_trivia_timeout_task(
                update,
                context,
                sent_msg.message_id,
                TRIVIA_TIMEOUT_SECONDS,
                current_qid,
            )
        )

    except Exception:
        context.user_data["free_trivia_category_selected"] = False

        logger.exception(
            "Free Trivia category failure | tg_id=%s",
            tg_user.id,
        )

        return await query.message.reply_text(
            "⚠️ We couldn't load that question. Please try another category.",
            reply_markup=make_free_trivia_category_keyboard(),
        )


# ================================================================
# STEP 2 — Category chosen: send question + countdown + timeout
# ================================================================
async def _trivia_category_handler_inner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    # Prevent another category selection while a question is active
    # or another category selection is still loading.
    if (
        context.user_data.get("trivia_category_selection_lock", False)
        or context.user_data.get("pending_trivia_question")
    ):
        await query.answer(
            "You already have an active question. Please answer it or wait for the timer to expire.",
            show_alert=True,
        )
        return

    # Lock immediately to prevent rapid double-clicks.
    context.user_data["trivia_category_selection_lock"] = True

    await query.answer()

    tg_user = query.from_user
    logger.info("🧠 category selected | tg_id=%s | data=%s", tg_user.id, query.data)

    try:
        _, category_label = query.data.split("_", 1)
    except Exception:
        await query.message.reply_text(
            "⚠️ Invalid category selection.",
            reply_markup=make_category_keyboard(),
        )
        context.user_data["trivia_category_selection_lock"] = False
        return

    # Normalize the UI label to the internal category key BEFORE selection.
    # Example: "Entertainment" -> "nigeria_entertainment"
    category_key = CATEGORY_KEY_MAP.get(category_label or "", category_label)

    q = await get_next_question_for_user(tg_user.id, category_key)

    if not q:
        context.user_data["trivia_category_selection_lock"] = False
        return await query.message.reply_text(
            "⚠️ No questions found in this category yet.",
            reply_markup=make_category_keyboard(),
        )

    # Make a safe copy so cached question objects are not mutated
    q = {
        **q,
        "options": dict(q.get("options") or {}),
    }

    # Keep friendly label for display, keep normalized key for internal tracking
    context.user_data["pending_trivia_category"] = category_label
    context.user_data["pending_trivia_category_key"] = category_key
    q["category_label"] = category_label
    q["category_key"] = category_key

    # ------------------------------------------------------------
    # Record shared question history WHEN QUESTION IS SERVED
    # so timed-out questions also count as seen.
    # ------------------------------------------------------------
    try:
        question_key = q.get("id") or make_json_question_key(
            category_key or "unknown",
            q["question"],
        )

        if category_key:
            async with get_async_session() as session:
                async with session.begin():
                    await record_question_history(
                        session,
                        tg_id=tg_user.id,
                        source_type="json_paid",
                        category=category_key,
                        question_key=str(question_key),
                    )
    except Exception:
        logger.exception("❌ Failed to record paid trivia question history on serve")

    context.user_data["pending_trivia_question"] = q
    context.user_data["trivia_answered"] = False
    context.user_data["trivia_processing_lock"] = False
    context.user_data["trivia_deadline"] = time.time() + TRIVIA_TIMEOUT_SECONDS

    question_text = (
        f"🧠 *{category_label} Trivia!*\n\n"
        f"{q['question']}\n\n"
        f"A. {q['options']['A']}\n"
        f"B. {q['options']['B']}\n"
        f"C. {q['options']['C']}\n"
        f"D. {q['options']['D']}"
    )

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
            [
                InlineKeyboardButton("⬅️ Back to Other Menu", callback_data="menu:other"),
            ],
            [
                InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main"),
            ],
        ]
    )

    sent_msg = await query.message.reply_text(
        question_text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

    current_qid = str(q["id"])

    async def countdown(message, base_text, kb_markup, secs: int, qid: str):
        for remaining in range(secs, 0, -1):
            current_question = context.user_data.get("pending_trivia_question")

            if context.user_data.get("trivia_answered", False):
                break

            if not current_question or str(current_question.get("id")) != qid:
                break

            try:
                await message.edit_text(
                    f"{base_text}\n\n⏳ *Time left:* {remaining}s",
                    parse_mode="Markdown",
                    reply_markup=kb_markup,
                )
            except BadRequest:
                break
            except Exception:
                break

            await asyncio.sleep(1)

    asyncio.create_task(
        countdown(sent_msg, question_text, keyboard, TRIVIA_TIMEOUT_SECONDS, current_qid)
    )

    old_timer = context.user_data.get("trivia_timer")
    if isinstance(old_timer, asyncio.Task) and not old_timer.done():
        old_timer.cancel()

    context.user_data["trivia_timer"] = asyncio.create_task(
        trivia_timeout_task(update, context, sent_msg.message_id, TRIVIA_TIMEOUT_SECONDS)
    )
    context.user_data["trivia_category_selection_lock"] = False

# =============================================
# Trivia Category Handler (Wrapper)
# ============================================
async def trivia_category_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Handle paid Trivia category selection and always release its locks."""

    try:
        await _trivia_category_handler_inner(update, context)

    except Exception:
        # Clean up incomplete question setup if an error occurs.
        timer = context.user_data.pop("trivia_timer", None)

        if isinstance(timer, asyncio.Task) and not timer.done():
            timer.cancel()

        context.user_data.pop("pending_trivia_question", None)
        context.user_data.pop("pending_trivia_category", None)
        context.user_data.pop("pending_trivia_category_key", None)
        context.user_data.pop("trivia_deadline", None)

        context.user_data["trivia_answered"] = False
        context.user_data["trivia_processing_lock"] = False

        raise

    finally:
        context.user_data["trivia_category_selection_lock"] = False


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

    if context.user_data.get("trivia_processing_lock", False):
        return

    context.user_data["trivia_processing_lock"] = True
    context.user_data["trivia_answered"] = True
    context.user_data["is_correct_answer"] = False

    question = context.user_data.get("pending_trivia_question")

    if question:
        context.user_data["trivia_question_id"] = str(
            question["id"]
        )

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


    await run_spin_and_apply_reward(
        update,
        context,
        trivia_message_id=message_id,
    )


# ================================================================
# FREE TRIVIA — RESULT PROCESSOR
# ================================================================
async def finish_free_trivia_round(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_id: int,
    is_correct: bool,
    correct_letter: str,
    correct_text: str,
    timed_out: bool = False,
):
    """Display Free Trivia results without paid rewards or points."""

    tg = update.effective_user

    async with get_async_session() as session:
        async with session.begin():
            user = await get_or_create_user(
                session,
                tg_id=tg.id,
                username=tg.username,
                full_name=getattr(tg, "full_name", None),
            )

            remaining = await get_free_trivia_remaining(
                session,
                user.id,
            )

    if timed_out:
        result_text = (
            "⏳ *TIME'S UP!*\n\n"
            "You didn't answer within the allotted time.\n"
        )
    elif is_correct:
        result_text = (
            "🎯 *CORRECT ANSWER!*\n\n"
            "Excellent! You got it right!\n"
        )
    else:
        result_text = (
            "🙈 *NOT QUITE RIGHT!*\n\n"
            f"👉 Correct answer: *{correct_letter} — {correct_text}*\n"
        )

    if remaining == 0:
        result_text += (
            "\n━━━━━━━━━━━━━━━━━━\n"
            "🔥 *YOU'VE COMPLETED YOUR 5 FREE GAMES!*\n\n"
            "But why stop here when the real excitement is just beginning? 🚀\n\n"
            "🏆 *READY TO PLAY FOR REAL PRIZES?*\n\n"
            "Step into PAID TRIVIA and put your knowledge to the test!\n\n"
            "🎁 *PLAY. WIN. CELEBRATE!*\n\n"
            "Amazing prizes are waiting, including:\n\n"
            "📱 iPhone 18 Pro Max\n"
            "📱 Samsung Galaxy S26 Ultra\n"
            "💸 Instant Airtime\n"
            "🎧 AirPods\n"
            "🔊 Bluetooth Speakers\n"
            "⌚ Smart Watches\n"
            "📱 Android Smartphones\n\n"
            "Earn Premium Points with correct paid answers, "
            "unlock exciting rewards and climb the Reward Season Leaderboard!\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "💰 *TURN YOUR FRIENDS INTO REWARDS!*\n\n"
            "Why keep NaijaPrizeGate to yourself?\n\n"
            "Invite your friends, grow your referral network and earn "
            "referral rewards when your qualifying referrals participate "
            "in paid Trivia!\n\n"
            "🔥 *EARN ₦50,000 OR MORE A DAY!*\n\n"
            "Depending on your eligible referral activity, you could earn "
            "₦50,000 or more in a day. There is no fixed maximum to your "
            "potential referral earnings.\n\n"
            "💵 Minimum withdrawal: *₦2,000*.\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🚀 *YOUR NEXT MOVE STARTS HERE!*\n\n"
            "🏆 Play Paid Trivia. Win exciting prizes.\n\n"
            "👥 Invite friends. Earn referral rewards.\n\n"
            "💰 Grow your network. Grow your earnings.\n\n"
            "*NaijaPrizeGate — PLAY SMART. WIN BIG. EARN MORE!*"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🏆 Play Paid Trivia – Win Prizes",
                        callback_data="playtrivia",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "👥 Invite Friends & Earn Rewards",
                        callback_data="finance:invite",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back to Other Menu",
                        callback_data="menu:other",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 Back to Main Menu",
                        callback_data="menu:main",
                    )
                ],
            ]
        )

    else:
        result_text += (
            f"\n🎮 *Free games remaining today: {remaining}/{DAILY_FREE_TRIVIA_LIMIT}*\n\n"
            "Keep going! Your next question is waiting.\n\n"
            "🏆 Ready for bigger rewards? Try Paid Trivia!"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🆓 Play Another Free Game",
                        callback_data="free_trivia:start",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏆 Play Paid Trivia – Win Prizes",
                        callback_data="playtrivia",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back to Other Menu",
                        callback_data="menu:other",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 Back to Main Menu",
                        callback_data="menu:main",
                    )
                ],
            ]
        )

    try:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=message_id,
            text=result_text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    except Exception:
        logger.exception("Failed to display Free Trivia result.")


# ================================================================
# FREE TRIVIA — TIMEOUT PROCESSOR
# ================================================================
async def free_trivia_timeout_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_id: int,
    timeout_seconds: int,
    question_id: str,
):
    await asyncio.sleep(timeout_seconds)

    question = context.user_data.get("free_trivia_question")

    if (
        not question
        or str(question.get("id")) != str(question_id)
        or context.user_data.get("free_trivia_answered", False)
        or context.user_data.get("free_trivia_processing_lock", False)
    ):
        return

    context.user_data["free_trivia_processing_lock"] = True
    context.user_data["free_trivia_answered"] = True

    try:
        await context.bot.edit_message_reply_markup(
            chat_id=update.effective_chat.id,
            message_id=message_id,
            reply_markup=None,
        )
    except Exception:
        pass

    correct_letter = str(question.get("answer", ""))
    correct_text = str(
        question.get("options", {}).get(correct_letter, "")
    )

    await finish_free_trivia_round(
        update,
        context,
        message_id,
        is_correct=False,
        correct_letter=correct_letter,
        correct_text=correct_text,
        timed_out=True,
    )


# ================================================================
# FREE TRIVIA — ANSWER HANDLER
# ================================================================
async def free_trivia_answer_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    if context.user_data.get("trivia_mode") != "free":
        return

    if (
        context.user_data.get("free_trivia_answered", False)
        or context.user_data.get("free_trivia_processing_lock", False)
    ):
        return

    question = context.user_data.get("free_trivia_question")

    if not question:
        return

    try:
        _, question_id, selected = query.data.split("_", 2)
    except Exception:
        return

    if str(question.get("id")) != str(question_id):
        await query.answer("This question has expired.", show_alert=True)
        return

    context.user_data["free_trivia_processing_lock"] = True
    context.user_data["free_trivia_answered"] = True

    for timer_key in (
        "free_trivia_timer",
        "free_trivia_timeout_task",
    ):
        timer = context.user_data.pop(timer_key, None)

        if isinstance(timer, asyncio.Task) and not timer.done():
            timer.cancel()

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    correct_letter = str(question["answer"])
    correct_text = str(question["options"][correct_letter])
    is_correct = selected == correct_letter

    await finish_free_trivia_round(
        update,
        context,
        query.message.message_id,
        is_correct=is_correct,
        correct_letter=correct_letter,
        correct_text=correct_text,
    )


# ================================================================
# STEP 3 — Answer handler (lock + evaluate)
# ================================================================
async def trivia_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    # Validate the submitted answer before locking or cancelling the timer.
    try:
        _, qid, selected = query.data.split("_", 2)
    except Exception:
        await query.answer(
            "Invalid answer data. Please use the buttons on your active question.",
            show_alert=True,
        )
        return

    question = context.user_data.get("pending_trivia_question")

    if not question or str(question.get("id")) != str(qid):
        await query.answer(
            "This question has expired or is no longer active.",
            show_alert=True,
        )
        return

    if context.user_data.get("trivia_answered", False):
        await query.answer(
            "This trivia round is already being processed.",
            show_alert=True,
        )
        return

    if context.user_data.get("trivia_processing_lock", False):
        await query.answer(
            "This trivia round is already being processed.",
            show_alert=True,
        )
        return

    # Lock immediately after validation to prevent duplicate answers.
    context.user_data["trivia_processing_lock"] = True
    context.user_data["trivia_answered"] = True

    try:
        await query.answer()
    except Exception:
        context.user_data["trivia_processing_lock"] = False
        context.user_data["trivia_answered"] = False
        raise

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    timer = context.user_data.pop("trivia_timer", None)
    if isinstance(timer, asyncio.Task) and not timer.done():
        timer.cancel()

    correct_letter = question["answer"]
    correct_text = question["options"][correct_letter]
    is_correct = (selected == correct_letter)

    context.user_data["is_correct_answer"] = is_correct
    context.user_data["trivia_question_id"] = str(qid)

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


# -----------------------------------
# Return To Withdrawal Qualification
# -----------------------------------
async def _return_to_withdrawal_qualification(
    update,
    context,
    spin_message=None,
    target_message_id=None,
) -> bool:
    """
    Return the user to the active withdrawal qualification screen.

    This function only handles navigation/UI.
    Premium Point awarding remains handled by resolve_trivia_attempt().
    """

    if context.user_data.get("trivia_origin") != "withdrawal":
        return False

    try:
        # Remove the temporary spinning message before
        # displaying the Withdrawal Eligibility screen.
        if spin_message:
            try:
                await spin_message.delete()
            except Exception:
                logger.exception(
                    "Failed to delete trivia spinning message."
                )

        from handlers.finance import show_progress

        await show_progress(
            update,
            context,
            target_message_id=target_message_id,
        )

        return True

    except Exception:
        logger.exception(
            "Failed to return to withdrawal qualification screen."
        )
        return False


# ================================================================
# STEP 4 — Spin animation + DB resolve + UI apply
# ================================================================
async def _run_spin_and_apply_reward_inner(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    trivia_message_id=None,
):
    tg = update.effective_user
    tg_id = tg.id
    username = tg.username
    player_name = tg.first_name or "Player"

    correct = bool(context.user_data.pop("is_correct_answer", False))

    withdrawal_return_requested = (
        context.user_data.get("trivia_origin") == "withdrawal"
    )

    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🎡 Spinning...",
    )

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
                    withdrawal_session_id=context.user_data.get(
                        "finance_eligibility_session_id"
                    ),
                    trivia_question_id=context.user_data.get(
                        "trivia_question_id"
                    ),
                )

                cycle_id = int(outcome.cycle_id or 1)
                points = int(outcome.points or 0)

                if outcome.type == "no_tries":
                    return await msg.edit_text(
                        "🚫 You have no trivia attempts left.\n\n"
                        "Use *Get More Trivia Attempts* or *Earn Free Trivia Attempts* to continue.",
                        parse_mode="Markdown",
                        reply_markup=make_play_keyboard(),
                    )

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
                        payout_id = payout["payout_id"]

                        # -------------------------------------------------
                        # Next milestone after this reward
                        # -------------------------------------------------

                        next_reward = _next_reward(points)

                        next_target = next_reward.get("target")
                        next_reward_name = next_reward.get("reward")
                        remaining = next_reward.get("remaining")



                        keyboard = InlineKeyboardMarkup(
                            [
                                [InlineKeyboardButton("⚡ Claim Airtime Reward", callback_data=f"claim_airtime:{payout_id}")],
                                [InlineKeyboardButton("⬅️ Back to Other Menu", callback_data="menu:other")],
                                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
                            ]
                        )

                        if next_target is None:
                            next_target_text = "🏆 You've unlocked every milestone reward this Reward Season!"
                        else:
                            next_target_text = (
                                f"🏁 *Unlocks At*\n"
                                f"{next_target} Premium Points"
                            )

                        if next_reward_name is None:
                            next_reward_text = "🎉 All milestone rewards unlocked!"
                        else:
                            next_reward_text = next_reward_name

                        # -------------------------------------------------
                        # Dynamic encouragement
                        # -------------------------------------------------

                        if remaining is None:

                            progress_text = (
                                "👑 You've unlocked every milestone reward this Reward Season!"
                            )

                        elif remaining == 1:

                            progress_text = (
                                "🎉 Your next correct answer unlocks your next reward!"
                            )

                        elif remaining == 2:

                            progress_text = (
                                "🔥 Just *2* more Premium Points to your next reward!"
                            )

                        elif remaining <= 5:

                            progress_text = (
                                f"🔥 You're very close! Only *{remaining}* Premium Points to go!"
                            )

                        elif remaining <= 10:

                            progress_text = (
                                f"💪 Only *{remaining}* Premium Points left. Keep the momentum going!"
                            )

                        elif remaining <= 25:

                            progress_text = (
                                f"🚀 Only *{remaining}* Premium Points away from your next reward."
                            )

                        else:

                            progress_text = (
                                f"🎯 Keep going! Only *{remaining}* Premium Points until your next reward."
                            )

                        await msg.edit_text(
                            f"🎉🎉 *CONGRATULATIONS!* 🎉🎉\n\n"
                            f"🏆 *REWARD UNLOCKED*\n\n"
                            f"💸 *Reward Earned*\n"
                            f"₦{outcome.airtime_amount} Airtime\n\n"
                            f"⭐ *Premium Points*\n"
                            f"{points}\n\n"
                            "━━━━━━━━━━━━━━━━━━\n\n"
                            f"🎁 *NEXT REWARD*\n"
                            f"{next_reward_text}\n\n"
                            f"{next_target_text}\n\n"
                            f"{progress_text}\n\n"
                            "━━━━━━━━━━━━━━━━━━\n\n"
                            "👑 *GRAND PRIZE*\n\n"
                            "Keep climbing the Reward Season Leaderboard\n"
                            "to become the *Season Champion* and win:\n\n"
                            "📱 *iPhone 18 Pro Max*\n\n"
                            "📱 *Samsung Galaxy S26 Ultra*\n\n"
                            "🏆 *The winner chooses one of the two phones.*\n\n\n\n"
                            "*Other Milestone Rewards*\n\n"
                            "💸 *Instant Airtime Rewards*\n\n"
                            "🎧 *AirPods*\n\n"
                            "🔊 *Bluetooth Speakers*\n\n"
                            "⌚ *Smart Watch*\n\n"
                            "📱 *Android Smartphone*\n\n\n\n"
                            "🏆 Keep climbing. The Season Champion takes it all!\n\n"
                            "💪 *Your journey continues!*\n\n\n\n"
                            "👇 Tap below to claim your Airtime Reward.",
                            parse_mode="Markdown",
                            reply_markup=keyboard,
                        )

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

                    if not BASE_URL:
                        await update.effective_chat.send_message(
                            "⚠️ Server URL missing. Please contact support.",
                            parse_mode="Markdown",
                            reply_markup=make_back_menu_keyboard(),
                        )
                    else:
                        token = generate_signed_token(
                            tgid=tg_id,
                            choice=prize_label,
                            expires_seconds=3600,
                        )
                        link = f"{BASE_URL}/winner-form?token={token}"
                        await update.effective_chat.send_message(
                            f"<a href='{link}'>📝 Fill Delivery Form</a>",
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                            reply_markup=make_back_menu_keyboard(),
                        )

                else:

                    # -------------------------------------------------
                    # Reward progress (for motivational messages)
                    # -------------------------------------------------

                    reward_rank = _reward_rank(points)
                    next_reward = _next_reward(points)

                    if outcome.paid_spin:

                        remaining = next_reward.get("remaining")
                        target = next_reward.get("target")
                        reward = next_reward.get("reward")

                        if target is None:
                            unlock_text = "🏆 All milestone rewards unlocked!"
                        else:
                            unlock_text = (
                                f"🏁 *Unlocks At:* {target} Premium Points"
                            )

                        if reward is None:
                            reward_text = "🎉 All milestone rewards unlocked!"
                        else:
                            reward_text = reward

                        if correct:
                            if remaining == 1:
                                progress_msg = (
                                    "🎉 Your next correct answer unlocks your next reward!"
                                )
                            elif remaining is not None:
                                progress_msg = (
                                    f"🔥 You have just *{remaining}* points "
                                    f"to the next reward!"
                                )
                            else:
                                progress_msg = (
                                    "👑 You've unlocked every milestone reward "
                                    "this Reward Season!"
                                )

                            result_title = "✅ *Correct!*"
                            encouragement = (
                                "💪 Every correct answer gets you closer!"
                            )

                        else:
                            if remaining == 1:
                                progress_msg = (
                                    "🔥 You need just *1* more point to unlock "
                                    "your next reward!"
                                )
                            elif remaining is not None:
                                progress_msg = (
                                    f"🔥 You still need just *{remaining}* points "
                                    f"to the next reward!"
                                )
                            else:
                                progress_msg = (
                                    "👑 You've unlocked every milestone reward "
                                    "this Reward Season!"
                                )

                            result_title = (
                                "❌ *Not quite! Better luck next time.*"
                            )
                            encouragement = (
                                "💪 Don't give up! Your previous progress "
                                "is saved and every new question is another opportunity."
                            )

                        if not withdrawal_return_requested:
                            await msg.edit_text(
                                f"{result_title}\n\n"
                                f"🏆 *YOUR REWARD STATUS*\n\n"
                                f"⭐ *Premium Points*\n"
                                f"{points}\n\n"
                                f"🏅 *Reward Rank*\n"
                                f"{reward_rank}\n\n"
                                f"🎁 *NEXT REWARD*\n"
                                f"{reward_text}\n\n"
                                f"{unlock_text}\n\n"
                                f"{progress_msg}\n\n"
                                f"👑 *GRAND PRIZE*\n"
                                f"Keep climbing the Reward Season Leaderboard "
                                f"to become the Season Champion and win the Grand Prize!\n\n"
                                f"{encouragement}",
                                parse_mode="Markdown",
                                reply_markup=make_play_keyboard(),
                            )

                    elif correct:

                        if not withdrawal_return_requested:
                            await msg.edit_text(
                                "✅ *Correct!*\n\n"
                                "🎁 This was a free/bonus attempt, so no leaderboard "
                                "points were added.\n\n"
                                "Use paid attempts to increase your Premium Points "
                                "and compete for the Grand Prize.",
                                parse_mode="Markdown",
                                reply_markup=make_play_keyboard(),
                            )

                    else:

                        if not withdrawal_return_requested:
                            await msg.edit_text(
                                "❌ *Not Correct!*\n\n"
                                "Don't give up! Your next correct paid answer "
                                "will increase your Premium Points and move you "
                                "closer to your next reward.",
                                parse_mode="Markdown",
                                reply_markup=make_play_keyboard(),
                            )

                if bool(outcome.cycle_ended) and outcome.winner_tg_id:
                    winner_tg = int(outcome.winner_tg_id)
                    winner_points = int(outcome.winner_points or 0)

                    if winner_tg == tg_id:
                        await update.effective_chat.send_message(
                            f"🎉 *Congratulations, {player_name}!* 🎉\n\n"
                            f"You finished *Cycle {cycle_id}* at the top of the leaderboard 🏆🔥\n"
                            f"Winning points: *{winner_points}*\n\n"
                            "Please choose your smartphone reward below 👇",
                            parse_mode="Markdown",
                        )

                        keyboard = InlineKeyboardMarkup(
                            [
                                [InlineKeyboardButton("📱 iPhone 18 Pro Max", callback_data="choose_iphone18")],
                                [InlineKeyboardButton("📱 Samsung Galaxy S26 Ultra", callback_data="choose_s26ultra")],
                                [InlineKeyboardButton("⬅️ Back to Other Menu", callback_data="menu:other")],
                                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
                            ]
                        )
                        await update.effective_chat.send_message(
                            "🎁 Select your reward option 👇",
                            reply_markup=keyboard,
                            parse_mode="Markdown",
                        )

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
                        await update.effective_chat.send_message(
                            f"🏁 *Cycle {cycle_id} ended!*\n\n"
                            "A new cycle has started. Keep playing to top the leaderboard 🔥",
                            parse_mode="Markdown",
                            reply_markup=make_play_keyboard(),
                        )

        # ---------------------------------------------------------
        # WITHDRAWAL QUALIFICATION RETURN
        #
        # The normal trivia result screens are intentionally
        # suppressed when the user came from Finance withdrawal
        # qualification. Return to the qualification progress screen
        # after the DB transaction has successfully completed.
        # ---------------------------------------------------------
        if withdrawal_return_requested:
            returned = await _return_to_withdrawal_qualification(
                update,
                context,
                msg,
                target_message_id=trivia_message_id,
            )
            if returned:
                return

    except Exception:
        logger.exception("❌ Reward processing failure")
        return await msg.edit_text(
            "⚠️ Reward processing error. Please try again.",
            parse_mode="Markdown",
            reply_markup=make_play_keyboard(),
        )


# ==========================================
# Run Spin and Apply Reward (wrapper)
# ==========================================
async def run_spin_and_apply_reward(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    trivia_message_id=None,
):
    """Run paid Trivia reward processing and always release question locks."""

    try:
        await _run_spin_and_apply_reward_inner(
            update,
            context,
            trivia_message_id=trivia_message_id,
        )
    finally:
        context.user_data.pop("pending_trivia_question", None)
        context.user_data.pop("pending_trivia_category", None)
        context.user_data.pop("pending_trivia_category_key", None)
        context.user_data.pop("trivia_deadline", None)
        context.user_data.pop("trivia_question_id", None)

        context.user_data["trivia_answered"] = False
        context.user_data["trivia_processing_lock"] = False
        context.user_data["trivia_category_selection_lock"] = False


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
        "choose_iphone18": "Smartphone Option 1",
        "choose_s26ultra": "Smartphone Option 2",
    }
    user_choice = mapping.get(choice)
    if not user_choice:
        return await query.edit_message_text(
            "⚠️ Invalid choice",
            reply_markup=make_back_menu_keyboard(),
        )

    if not BASE_URL:
        return await query.edit_message_text(
            "⚠️ Server URL missing",
            reply_markup=make_back_menu_keyboard(),
        )

    token = generate_signed_token(
        tgid=tg_id,
        choice=user_choice,
        expires_seconds=3600,
    )
    link = f"{BASE_URL}/winner-form?token={token}"

    await query.edit_message_text(
        f"🎉 You selected <b>{user_choice}</b>!\n\n"
        f"<a href='{link}'>📝 Fill Delivery Form</a>\n\n"
        "📌 Rewards are promotional, subject to verification.",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=make_back_menu_keyboard(),
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

    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        (
            f"📊 *Available Trivia Attempts*\n\n"
            f"🎟️ Paid: {paid}\n"
            f"🎁 Bonus: {bonus}\n"
            f"💫 Total: {paid + bonus}"
        ),
        parse_mode="Markdown",
        reply_markup=make_show_tries_keyboard(),
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

    if tg.id != ADMIN_USER_ID:
        return await update.effective_message.reply_text("❌ Not authorized.")

    async with get_async_session() as session:
        async with session.begin():
            user = await get_or_create_user(
                session,
                tg_id=tg.id,
                username=tg.username,
                full_name=getattr(tg, "full_name", None),
            )

            gs = await session.get(GameState, 1)
            cycle_id = int(gs.current_cycle or 1) if gs else 1

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
            user = await get_or_create_user(
                session,
                tg_id=tg_id,
                username=tg.username,
                full_name=getattr(tg, "full_name", None),
            )

            user.tries_paid = int(user.tries_paid or 0) + count

            gc = await session.get(GlobalCounter, 1)
            if not gc:
                gc = GlobalCounter(id=1, paid_tries_total=0)
                session.add(gc)

            gc.paid_tries_total = int(gc.paid_tries_total or 0) + count

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
    application.add_handler(CallbackQueryHandler(free_trivia_start_handler, pattern=r"^free_trivia:start$"))
    application.add_handler(CallbackQueryHandler(free_trivia_category_handler, pattern=r"^freecat_"))
    application.add_handler(CallbackQueryHandler(free_trivia_answer_handler, pattern=r"^freeans_.+_[A-D]$"))

    application.add_handler(CallbackQueryHandler(trivia_category_handler, pattern=r"^cat_"))
    application.add_handler(CallbackQueryHandler(trivia_answer_handler, pattern=r"^ans_.+_[A-D]$"))
    application.add_handler(CommandHandler("playtrivia", playtrivia_handler))

    application.add_handler(CommandHandler("testpoints", testpoints_handler))
    application.add_handler(CommandHandler("resetpoints", resetpoints_handler))
    application.add_handler(CommandHandler("addtries", addtries_handler))
    application.add_handler(CommandHandler("resetcycle", resetcycle_handler))

    application.add_handler(CallbackQueryHandler(finance_playtrivia_handler, pattern=r"^finance:playtrivia$"))

    application.add_handler(CallbackQueryHandler(playtrivia_handler, pattern=r"^playtrivia$"))
    application.add_handler(CallbackQueryHandler(handle_phone_choice, pattern=r"^choose_"))
    application.add_handler(CallbackQueryHandler(show_tries_callback, pattern=r"^show_tries$"))

    if handle_buy_callback:
        application.add_handler(CallbackQueryHandler(handle_buy_callback, pattern=r"^buy$"))
    if free_menu:
        application.add_handler(CallbackQueryHandler(free_menu, pattern=r"^free$"))


