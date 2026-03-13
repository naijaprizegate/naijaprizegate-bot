# =====================================================================
# handlers/battle.py
# =====================================================================
from __future__ import annotations

import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from db import AsyncSessionLocal
from logger import logger
from services.battle_service import (
    create_battle_room,
    save_host_lobby_message,
    join_battle_room,
    get_battle_room,
    get_battle_players,
    build_battle_lobby_text,
    start_battle_room,
    get_trivia_question_by_id,
    get_player_battle_state,
    get_current_battle_question_for_player,
    has_player_answered_question,
    record_battle_answer,
    mark_player_finished_if_done,
    cancel_battle_room,
    create_or_reset_battle_draft,
    set_battle_draft_category,
    set_battle_draft_question_count,
    set_battle_draft_duration,
    set_battle_draft_max_players,
    get_battle_draft,
    delete_battle_draft,
)

# ============================================================
# Conversation states
# ============================================================
BATTLE_JOIN_CODE = 3005

# ============================================================
# Battle categories
# ============================================================
BATTLE_CATEGORIES = [
    "nigeria_history",
    "geography",
    "nigeria_entertainment",
    "sciences",
    "mathematics",
    "english",
    "football",
]

BATTLE_CATEGORY_LABELS = {
    "nigeria_history": "Nigeria History",
    "geography": "Geography",
    "nigeria_entertainment": "Entertainment",
    "sciences": "Sciences",
    "mathematics": "Mathematics",
    "english": "English",
    "football": "Football",
}

# ============================================================
# Keyboards
# ============================================================
def battle_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Create Battle Room", callback_data="battle:create")],
        [InlineKeyboardButton("🔑 Join with Room Code", callback_data="battle:join_code")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")],
    ])


def battle_category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇳🇬 Nigeria History", callback_data="battlecat:nigeria_history")],
        [InlineKeyboardButton("🌍 Geography", callback_data="battlecat:geography")],
        [InlineKeyboardButton("🎬 Entertainment", callback_data="battlecat:nigeria_entertainment")],
        [InlineKeyboardButton("🔬 Sciences", callback_data="battlecat:sciences")],
        [InlineKeyboardButton("➗ Mathematics", callback_data="battlecat:mathematics")],
        [InlineKeyboardButton("📘 English", callback_data="battlecat:english")],
        [InlineKeyboardButton("⚽ Football", callback_data="battlecat:football")],
        [InlineKeyboardButton("❌ Cancel", callback_data="battle:cancel")],
    ])


def battle_question_count_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("5 Questions", callback_data="battleq:5")],
        [InlineKeyboardButton("10 Questions", callback_data="battleq:10")],
        [InlineKeyboardButton("15 Questions", callback_data="battleq:15")],
        [InlineKeyboardButton("❌ Cancel", callback_data="battle:cancel")],
    ])


def battle_question_keyboard(room_code: str, question_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("A", callback_data=f"battleans:{room_code}:{question_id}:A"),
            InlineKeyboardButton("B", callback_data=f"battleans:{room_code}:{question_id}:B"),
        ],
        [
            InlineKeyboardButton("C", callback_data=f"battleans:{room_code}:{question_id}:C"),
            InlineKeyboardButton("D", callback_data=f"battleans:{room_code}:{question_id}:D"),
        ],
        [
            InlineKeyboardButton("⏭ Skip", callback_data=f"battleskip:{room_code}:{question_id}")
        ],
    ])


def battle_next_keyboard(room_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➡️ Next Question", callback_data=f"battlenext:{room_code}")],
    ])


def battle_duration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("30 Seconds", callback_data="battlet:30")],
        [InlineKeyboardButton("60 Seconds", callback_data="battlet:60")],
        [InlineKeyboardButton("90 Seconds", callback_data="battlet:90")],
        [InlineKeyboardButton("120 Seconds", callback_data="battlet:120")],
        [InlineKeyboardButton("❌ Cancel", callback_data="battle:cancel")],
    ])


def battle_max_players_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("2 Players", callback_data="battlep:2")],
        [InlineKeyboardButton("3 Players", callback_data="battlep:3")],
        [InlineKeyboardButton("4 Players", callback_data="battlep:4")],
        [InlineKeyboardButton("5 Players", callback_data="battlep:5")],
        [InlineKeyboardButton("6 Players", callback_data="battlep:6")],
        [InlineKeyboardButton("❌ Cancel", callback_data="battle:cancel")],
    ])


def battle_lobby_keyboard(room_code: str, is_host: bool = True) -> InlineKeyboardMarkup:
    import urllib.parse

    bot_username = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")
    invite_link = f"https://t.me/{bot_username}?start=battle_{room_code}"

    share_text = (
        "🔥 Join my NaijaPrizeGate Battle Room!\n\n"
        f"Room Code: {room_code}\n"
        "Tap the link below to join:"
    )

    share_url = (
        "https://t.me/share/url?"
        f"url={urllib.parse.quote(invite_link)}"
        f"&text={urllib.parse.quote(share_text)}"
    )

    buttons = [
        [InlineKeyboardButton("📨 Invite Friends", url=share_url)],
    ]

    if is_host:
        buttons.append([InlineKeyboardButton("🚀 Start Battle", callback_data=f"battle:start:{room_code}")])
        buttons.append([InlineKeyboardButton("❌ Cancel Battle", callback_data=f"battle:cancel_room:{room_code}")])

    return InlineKeyboardMarkup(buttons)


def battle_waiting_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")],
    ])

# ============================================================
# Battle question/result text builders
# ============================================================
def build_battle_question_text(
    question_order: int,
    question_count: int,
    category: str,
    question_text: str,
    option_a: str,
    option_b: str,
    option_c: str,
    option_d: str,
    seconds_left: int,
) -> str:
    return (
        f"🔥 <b>Battle Question {question_order}/{question_count}</b>\n\n"
        f"<b>Category:</b> {BATTLE_CATEGORY_LABELS.get(category, category)}\n\n"
        f"{question_text}\n\n"
        f"A. {option_a}\n"
        f"B. {option_b}\n"
        f"C. {option_c}\n"
        f"D. {option_d}\n\n"
        f"⏳ <b>Battle ends in:</b> {seconds_left}s"
    )

def build_battle_timeout_text(
    question_order: int,
    question_count: int,
    category: str,
    question_text: str,
    option_a: str,
    option_b: str,
    option_c: str,
    option_d: str,
) -> str:
    return (
        f"🔥 <b>Battle Question {question_order}/{question_count}</b>\n\n"
        f"<b>Category:</b> {BATTLE_CATEGORY_LABELS.get(category, category)}\n\n"
        f"{question_text}\n\n"
        f"A. {option_a}\n"
        f"B. {option_b}\n"
        f"C. {option_c}\n"
        f"D. {option_d}\n\n"
        f"⏰ <b>TIME UP</b>"
    )


def build_battle_answer_result_text(
    question_order: int,
    question_count: int,
    category: str,
    question_text: str,
    option_a: str,
    option_b: str,
    option_c: str,
    option_d: str,
    is_correct: bool,
    correct_option: str,
) -> str:
    status_line = (
        "✅ <b>CORRECT</b>"
        if is_correct
        else f"❌ <b>WRONG</b>\n<b>Correct Answer:</b> {correct_option}"
    )

    return (
        f"🔥 <b>Battle Question {question_order}/{question_count}</b>\n\n"
        f"<b>Category:</b> {BATTLE_CATEGORY_LABELS.get(category, category)}\n\n"
        f"{question_text}\n\n"
        f"A. {option_a}\n"
        f"B. {option_b}\n"
        f"C. {option_c}\n"
        f"D. {option_d}\n\n"
        f"{status_line}"
    )

# ============================================================
# Battle question timeout/countdown
# ============================================================
async def handle_battle_question_timeout(
    context: ContextTypes.DEFAULT_TYPE,
    room_code: str,
    user_id: int,
    question_id: int,
    question_order: int,
    question_count: int,
    message_id: int,
    category: str,
    question_text: str,
    option_a: str,
    option_b: str,
    option_c: str,
    option_d: str,
):
    from datetime import datetime, timezone

    while True:
        await asyncio.sleep(1)

        async with AsyncSessionLocal() as session:
            state = await get_player_battle_state(
                session,
                room_code=room_code,
                tg_id=user_id,
            )
            if not state or state.get("status") != "active":
                return

            already_answered = await has_player_answered_question(
                session,
                battle_id=str(state["battle_id"]),
                tg_id=user_id,
                question_id=question_id,
            )
            if already_answered:
                return

            ends_at = state.get("ends_at")
            if not ends_at:
                return

            now = datetime.now(timezone.utc)
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=timezone.utc)

            seconds_left = int((ends_at - now).total_seconds())

            current = await get_current_battle_question_for_player(
                session,
                room_code=room_code,
                tg_id=user_id,
            )
            if not current or current["done"]:
                return

            expected_question_id = int(current["question_id"])
            if expected_question_id != question_id:
                return

            if seconds_left <= 0:
                async with session.begin():
                    await record_battle_answer(
                        session,
                        battle_id=str(state["battle_id"]),
                        tg_id=user_id,
                        question_id=question_id,
                        question_index=int(current["question_index"]),
                        selected_option=None,
                        is_correct=False,
                        was_skipped=True,
                    )

                    player_finished = await mark_player_finished_if_done(
                        session,
                        battle_id=str(state["battle_id"]),
                        tg_id=user_id,
                        question_count=int(state["question_count"]),
                    )

                try:
                    await context.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=message_id,
                        text=build_battle_timeout_text(
                            question_order=question_order,
                            question_count=question_count,
                            category=category,
                            question_text=question_text,
                            option_a=option_a,
                            option_b=option_b,
                            option_c=option_c,
                            option_d=option_d,
                        ),
                        parse_mode="HTML",
                        reply_markup=None,
                    )
                except Exception:
                    pass

                await asyncio.sleep(1.0)

                if player_finished:
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text="🏁 You have completed your battle questions.\n\nPlease wait for the final result.",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass

                return

        try:
            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=message_id,
                text=build_battle_question_text(
                    question_order=question_order,
                    question_count=question_count,
                    category=category,
                    question_text=question_text,
                    option_a=option_a,
                    option_b=option_b,
                    option_c=option_c,
                    option_d=option_d,
                    seconds_left=seconds_left,
                ),
                parse_mode="HTML",
                reply_markup=battle_question_keyboard(room_code, question_id),
            )
        except Exception:
            pass

# ============================================================
# Silent host lobby refresh
# ============================================================
async def refresh_host_lobby(bot, room_code: str):
    bot_username = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")

    async with AsyncSessionLocal() as session:
        room = await get_battle_room(session, room_code)
        if not room:
            return

        players = await get_battle_players(session, str(room["id"]))
        text = build_battle_lobby_text(room, players, bot_username)

        host_chat_id = room.get("host_chat_id")
        host_lobby_message_id = room.get("host_lobby_message_id")

        if not host_chat_id or not host_lobby_message_id:
            return

        try:
            await bot.edit_message_text(
                chat_id=host_chat_id,
                message_id=host_lobby_message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=battle_lobby_keyboard(room_code, is_host=True),
                disable_web_page_preview=True,
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return
            logger.exception(
                "❌ Failed to silently refresh host lobby | room_code=%s",
                room_code,
            )
        except Exception:
            logger.exception(
                "❌ Failed to silently refresh host lobby | room_code=%s",
                room_code,
            )


# ============================================================
# Entry point
# ============================================================
async def battle_mode_entry_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

    msg = update.effective_message
    if not msg:
        return ConversationHandler.END

    await msg.reply_text(
        "🔥 *Battle Mode*\n\n"
        "Create a multiplayer timed battle room and invite friends.\n\n"
        "You can choose:\n"
        "• category\n"
        "• number of questions\n"
        "• time limit\n"
        "• max players",
        parse_mode="Markdown",
        reply_markup=battle_mode_keyboard(),
    )
    return ConversationHandler.END


# ============================================================
# Start create flow
# ============================================================
async def battle_create_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    await query.answer("Processing...", show_alert=False)

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await create_or_reset_battle_draft(session, user.id)

        await query.edit_message_text(
            "🔥 *Create Battle Room*\n\n"
            "First, choose a category:",
            parse_mode="Markdown",
            reply_markup=battle_category_keyboard(),
        )
        return
    except Exception:
        logger.exception("❌ Failed to start battle draft | tg_id=%s", user.id)
        await query.answer("Could not open battle setup right now.", show_alert=True)
        return


# ============================================================
# Category selected
# ============================================================
async def battle_category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    await query.answer("Processing...", show_alert=False)

    data = query.data or ""
    if not data.startswith("battlecat:"):
        return

    category = data.split(":", 1)[1].strip()
    pretty_category = category.replace("_", " ").title()

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await set_battle_draft_category(
                    session,
                    host_tg_id=user.id,
                    category=category,
                )

        await query.edit_message_text(
            f"✅ Category selected: *{pretty_category}*\n\n"
            "Now choose how many questions the battle should have:",
            parse_mode="Markdown",
            reply_markup=battle_question_count_keyboard(),
        )
        return
    except Exception:
        logger.exception("❌ Failed to save battle draft category | tg_id=%s", user.id)
        await query.answer("Could not save category right now.", show_alert=True)
        return


# ============================================================
# Question count selected
# ============================================================
async def battle_question_count_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    await query.answer("Processing...", show_alert=False)

    data = query.data or ""
    if not data.startswith("battleq:"):
        return

    try:
        question_count = int(data.split(":", 1)[1].strip())
    except Exception:
        await query.answer("Invalid question count.", show_alert=False)
        return

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await set_battle_draft_question_count(
                    session,
                    host_tg_id=user.id,
                    question_count=question_count,
                )

        await query.edit_message_text(
            f"✅ Questions selected: *{question_count}*\n\n"
            "Now choose the battle timer:",
            parse_mode="Markdown",
            reply_markup=battle_duration_keyboard(),
        )
        return
    except Exception:
        logger.exception("❌ Failed to save battle draft question count | tg_id=%s", user.id)
        await query.answer("Could not save question count right now.", show_alert=True)
        return


# ============================================================
# Duration selected
# ============================================================
async def battle_duration_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    await query.answer("Processing...", show_alert=False)

    data = query.data or ""
    if not data.startswith("battlet:"):
        return

    try:
        duration_seconds = int(data.split(":", 1)[1].strip())
    except Exception:
        await query.answer("Invalid duration.", show_alert=False)
        return

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await set_battle_draft_duration(
                    session,
                    host_tg_id=user.id,
                    duration_seconds=duration_seconds,
                )

        await query.edit_message_text(
            f"✅ Timer selected: *{duration_seconds} seconds*\n\n"
            "Now choose the maximum number of players\n"
            "*(including you, the host)*:",
            parse_mode="Markdown",
            reply_markup=battle_max_players_keyboard(),
        )
        return
    except Exception:
        logger.exception("❌ Failed to save battle draft duration | tg_id=%s", user.id)
        await query.answer("Could not save timer right now.", show_alert=True)
        return


# ============================================================
# Max players selected -> Create room
# ============================================================
async def battle_max_players_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    await query.answer("Processing...", show_alert=False)

    data = query.data or ""
    if not data.startswith("battlep:"):
        return

    try:
        max_players = int(data.split(":", 1)[1].strip())
    except Exception:
        await query.answer("Invalid player count.", show_alert=False)
        return

    display_name = user.full_name or user.username or str(user.id)
    bot_username = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await set_battle_draft_max_players(
                    session,
                    host_tg_id=user.id,
                    max_players=max_players,
                )

                draft = await get_battle_draft(session, user.id)
                if not draft:
                    await query.answer("Battle setup expired. Please start again.", show_alert=True)
                    return

                category = draft.get("category")
                question_count = draft.get("question_count")
                duration_seconds = draft.get("duration_seconds")
                max_players = draft.get("max_players")

                if not category or not question_count or not duration_seconds or not max_players:
                    await query.answer("Battle setup is incomplete. Please start again.", show_alert=True)
                    return

                room = await create_battle_room(
                    session,
                    host_tg_id=user.id,
                    host_display_name=display_name,
                    category=category,
                    max_players=int(max_players),
                    question_count=int(question_count),
                    duration_seconds=int(duration_seconds),
                )

                await delete_battle_draft(session, user.id)

            players = await get_battle_players(session, str(room["id"]))

        lobby_text = build_battle_lobby_text(room, players, bot_username)

        await query.edit_message_text(
            lobby_text,
            parse_mode="HTML",
            reply_markup=battle_lobby_keyboard(room["room_code"], is_host=True),
            disable_web_page_preview=True,
        )

        async with AsyncSessionLocal() as session:
            async with session.begin():
                await save_host_lobby_message(
                    session,
                    room_code=room["room_code"],
                    host_chat_id=query.message.chat_id,
                    host_lobby_message_id=query.message.message_id,
                )

        logger.info(
            "🔥 Battle room lobby shown | room_code=%s | host_tg_id=%s",
            room["room_code"],
            user.id,
        )
        return

    except Exception:
        logger.exception("❌ Failed to create battle room from draft | host_tg_id=%s", user.id)
        try:
            await query.edit_message_text(
                "❌ Could not create battle room right now.\n\nPlease try again.",
                parse_mode="Markdown",
                reply_markup=battle_mode_keyboard(),
            )
        except Exception:
            await query.answer("Could not create battle room right now.", show_alert=True)
        return


# ============================================================
# Join by room code prompt
# ============================================================
async def battle_join_code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    await query.answer()

    await query.edit_message_text(
        "🔑 *Join Battle Room*\n\n"
        "Send the room code now.\n\n"
        "Example: `A7K92Q`\n\n"
        "Send /cancel to stop.",
        parse_mode="Markdown",
    )
    return BATTLE_JOIN_CODE


# ============================================================
# Receive room code and join
# ============================================================
async def battle_receive_room_code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = update.effective_user

    if not msg or not user or not msg.text:
        return BATTLE_JOIN_CODE

    room_code = msg.text.strip().upper()
    display_name = user.full_name or user.username or str(user.id)

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await join_battle_room(
                    session,
                    room_code=room_code,
                    tg_id=user.id,
                    display_name=display_name,
                )

            if not result["ok"]:
                await msg.reply_text(
                    f"⚠️ {result['error']}",
                    parse_mode="Markdown",
                    reply_markup=battle_mode_keyboard(),
                )
                return ConversationHandler.END

            room = await get_battle_room(session, room_code)

        pretty_category = str(room["category"]).replace("_", " ").title()

        await msg.reply_text(
            "✅ You joined the battle room.\n\n"
            f"*Room Code:* `{room['room_code']}`\n"
            f"*Category:* {pretty_category}\n"
            f"*Questions:* {room['question_count']}\n"
            f"*Time:* {room['duration_seconds']} seconds\n\n"
            "Waiting for the host to start the battle.",
            parse_mode="Markdown",
            reply_markup=battle_waiting_keyboard(),
        )

        await refresh_host_lobby(context.bot, room_code)

    except Exception:
        logger.exception("❌ Failed to join battle room | tg_id=%s | room_code=%s", user.id, room_code)
        await msg.reply_text(
            "❌ Could not join battle room right now.\n\nPlease try again.",
            parse_mode="Markdown",
            reply_markup=battle_mode_keyboard(),
        )

    return ConversationHandler.END


# ============================================================
# Join from deep link payload
# ============================================================
async def battle_join_from_payload(update: Update, context: ContextTypes.DEFAULT_TYPE, room_code: str):
    msg = update.effective_message
    user = update.effective_user

    if not msg or not user:
        return

    room_code = room_code.strip().upper()
    display_name = user.full_name or user.username or str(user.id)

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await join_battle_room(
                    session,
                    room_code=room_code,
                    tg_id=user.id,
                    display_name=display_name,
                )

            if not result["ok"]:
                await msg.reply_text(
                    f"⚠️ {result['error']}",
                    parse_mode="Markdown",
                    reply_markup=battle_mode_keyboard(),
                )
                return

            room = await get_battle_room(session, room_code)

        pretty_category = str(room["category"]).replace("_", " ").title()

        await msg.reply_text(
            "✅ You joined the battle room.\n\n"
            f"*Room Code:* `{room['room_code']}`\n"
            f"*Category:* {pretty_category}\n"
            f"*Questions:* {room['question_count']}\n"
            f"*Time:* {room['duration_seconds']} seconds\n\n"
            "Waiting for the host to start the battle.",
            parse_mode="Markdown",
            reply_markup=battle_waiting_keyboard(),
        )

        await refresh_host_lobby(context.bot, room_code)

    except Exception:
        logger.exception("❌ Failed payload join | tg_id=%s | room_code=%s", user.id, room_code)
        await msg.reply_text(
            "❌ Could not join battle room right now.",
            parse_mode="Markdown",
            reply_markup=battle_mode_keyboard(),
        )


# ============================================================
# Cancel create flow
# ============================================================
async def battle_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    try:
        if user:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    await delete_battle_draft(session, user.id)
    except Exception:
        logger.exception("❌ Failed to delete battle draft on cancel | tg_id=%s", getattr(user, "id", None))

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "❌ Battle setup cancelled.",
            reply_markup=battle_mode_keyboard(),
        )
    elif update.message:
        await update.message.reply_text(
            "❌ Battle setup cancelled.",
            reply_markup=battle_mode_keyboard(),
        )

    return ConversationHandler.END


# ===========================================================
# Send Battle Question To Player
# ===========================================================
async def send_battle_question_to_player(
    bot,
    room_code: str,
    tg_id: int,
    context: ContextTypes.DEFAULT_TYPE | None = None,
):
    from datetime import datetime, timezone

    async with AsyncSessionLocal() as session:
        current = await get_current_battle_question_for_player(
            session,
            room_code=room_code,
            tg_id=tg_id,
        )
        if not current:
            return

        state = current["state"]

        if state.get("status") != "active":
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text="⏳ This battle has ended. Please wait for the final result.",
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception(
                    "❌ Failed to send inactive-battle notice | room_code=%s | tg_id=%s",
                    room_code,
                    tg_id,
                )
            return

        ends_at = state.get("ends_at")
        if not ends_at:
            return

        now = datetime.now(timezone.utc)
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=timezone.utc)

        seconds_left = int((ends_at - now).total_seconds())

        if seconds_left <= 0:
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text="⏳ Time is up for this battle. Please wait for the final result.",
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception(
                    "❌ Failed to send battle-time-up notice | room_code=%s | tg_id=%s",
                    room_code,
                    tg_id,
                )
            return

        if current["done"]:
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text=(
                        "✅ <b>You have finished all questions.</b>\n\n"
                        "Please wait for the final battle result."
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception(
                    "❌ Failed to send finished message | room_code=%s | tg_id=%s",
                    room_code,
                    tg_id,
                )
            return

        q = current["question"]
        question_index = int(current["question_index"])
        question_id = int(current["question_id"])

        options = q.get("options") or {}
        option_a = options.get("A", "N/A")
        option_b = options.get("B", "N/A")
        option_c = options.get("C", "N/A")
        option_d = options.get("D", "N/A")

        try:
            sent_message = await bot.send_message(
                chat_id=tg_id,
                text=build_battle_question_text(
                    question_order=question_index + 1,
                    question_count=int(state["question_count"]),
                    category=q["category"],
                    question_text=q["question"],
                    option_a=option_a,
                    option_b=option_b,
                    option_c=option_c,
                    option_d=option_d,
                    seconds_left=seconds_left,
                ),
                parse_mode="HTML",
                reply_markup=battle_question_keyboard(room_code, question_id),
            )
        except Exception:
            logger.exception(
                "❌ Failed to send battle question | room_code=%s | tg_id=%s",
                room_code,
                tg_id,
            )
            return

    if context is not None:
        asyncio.create_task(
            handle_battle_question_timeout(
                context=context,
                room_code=room_code,
                user_id=tg_id,
                question_id=question_id,
                question_order=question_index + 1,
                question_count=int(state["question_count"]),
                message_id=sent_message.message_id,
                category=q["category"],
                question_text=q["question"],
                option_a=option_a,
                option_b=option_b,
                option_c=option_c,
                option_d=option_d,
            )
        )

# ============================================================
# Start Battle Handler
# ============================================================
async def battle_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    await query.answer()

    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3:
        await query.answer("Invalid start request.", show_alert=False)
        return

    room_code = parts[2].strip().upper()

    logger.info(
        "🚀 battle_start_handler hit | room_code=%s | tg_id=%s",
        room_code,
        user.id,
    )

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await start_battle_room(
                    session,
                    room_code=room_code,
                    requester_tg_id=user.id,
                )

        if not result["ok"]:
            logger.info(
                "⚠️ battle_start_handler blocked | room_code=%s | tg_id=%s | reason=%s",
                room_code,
                user.id,
                result["error"],
            )

            try:
                await query.answer(result["error"], show_alert=True)
            except Exception:
                pass

            try:
                await query.message.reply_text(
                    f"⚠️ {result['error']}",
                    parse_mode="Markdown",
                )
            except Exception:
                logger.exception(
                    "❌ Failed to send visible start-blocked message | room_code=%s | tg_id=%s",
                    room_code,
                    user.id,
                )
            return

        logger.info(
            "✅ battle_start_handler passed | room_code=%s | tg_id=%s | players=%s",
            room_code,
            user.id,
            len(result["players"]),
        )

        await query.edit_message_text(
            "🚀 *Battle Started!*\n\n"
            "All players are now receiving Question 1.",
            parse_mode="Markdown",
        )

        for player in result["players"]:
            tg_id = int(player["tg_id"])
            await send_battle_question_to_player(context.bot, room_code, tg_id)

    except Exception:
        logger.exception(
            "❌ Failed to start battle | room_code=%s | host_tg_id=%s",
            room_code,
            user.id,
        )

        try:
            await query.answer("Could not start battle right now.", show_alert=True)
        except Exception:
            pass

        try:
            await query.message.reply_text(
                "⚠️ Could not start battle right now. Please try again.",
                parse_mode="Markdown",
            )
        except Exception:
            pass


# ===========================================================
# Battle Answer Handler
# ===========================================================
async def battle_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    await query.answer()

    data = query.data or ""
    parts = data.split(":")

    if len(parts) != 4:
        await query.answer("Invalid answer data.", show_alert=False)
        return

    _, room_code, question_id_raw, selected_option = parts

    try:
        question_id = int(question_id_raw)
    except Exception:
        await query.answer("Invalid question id.", show_alert=False)
        return

    selected_option = selected_option.strip().upper()

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                state = await get_player_battle_state(
                    session,
                    room_code=room_code,
                    tg_id=user.id,
                )
                if not state:
                    await query.answer("Battle state not found.", show_alert=True)
                    return

                if state["status"] != "active":
                    await query.answer("This battle is no longer active.", show_alert=True)
                    return

                if state.get("ends_at") is not None:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    ends_at = state["ends_at"]
                    if ends_at.tzinfo is None:
                        ends_at = ends_at.replace(tzinfo=timezone.utc)

                    if now >= ends_at:
                        await query.answer("⏳ Time is up for this battle.", show_alert=True)
                        return

                already_answered = await has_player_answered_question(
                    session,
                    battle_id=str(state["battle_id"]),
                    tg_id=user.id,
                    question_id=question_id,
                )
                if already_answered:
                    await query.answer("You already answered this question.", show_alert=False)
                    return

                current = await get_current_battle_question_for_player(
                    session,
                    room_code=room_code,
                    tg_id=user.id,
                )
                if not current or current["done"]:
                    await query.answer("No active question found.", show_alert=False)
                    return

                expected_question_id = int(current["question_id"])
                if expected_question_id != question_id:
                    await query.answer("This is not your current question.", show_alert=False)
                    return

                q = current["question"]
                options = q.get("options") or {}
                correct_answer = str(q["answer"]).strip().upper()
                is_correct = selected_option == correct_answer

                await record_battle_answer(
                    session,
                    battle_id=str(state["battle_id"]),
                    tg_id=user.id,
                    question_id=question_id,
                    question_index=int(current["question_index"]),
                    selected_option=selected_option,
                    is_correct=is_correct,
                    was_skipped=False,
                )

                player_finished = await mark_player_finished_if_done(
                    session,
                    battle_id=str(state["battle_id"]),
                    tg_id=user.id,
                    question_count=int(state["question_count"]),
                )

        await query.edit_message_text(
            text=build_battle_answer_result_text(
                question_order=int(current["question_index"]) + 1,
                question_count=int(state["question_count"]),
                category=q["category"],
                question_text=q["question"],
                option_a=options.get("A", "N/A"),
                option_b=options.get("B", "N/A"),
                option_c=options.get("C", "N/A"),
                option_d=options.get("D", "N/A"),
                is_correct=is_correct,
                correct_option=correct_answer,
            ),
            parse_mode="HTML",
            reply_markup=None,
        )

        await asyncio.sleep(1.5)

        if player_finished:
            await query.message.reply_text(
                "🏁 You have completed your battle questions.\n\nPlease wait for the final result.",
                parse_mode="HTML",
            )
            return

        await send_battle_question_to_player(
            context.bot,
            room_code,
            user.id,
            context=context,
        )

    except Exception:
        logger.exception(
            "❌ Failed to process battle answer | room_code=%s | tg_id=%s | question_id=%s",
            room_code,
            user.id,
            question_id,
        )
        await query.answer("Could not process answer right now.", show_alert=True)


# ===========================================================
# Battle Skip Handler
# ===========================================================
async def battle_skip_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    await query.answer()

    data = query.data or ""
    parts = data.split(":")

    if len(parts) != 3:
        await query.answer("Invalid skip data.", show_alert=False)
        return

    _, room_code, question_id_raw = parts

    try:
        question_id = int(question_id_raw)
    except Exception:
        await query.answer("Invalid question id.", show_alert=False)
        return

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                state = await get_player_battle_state(
                    session,
                    room_code=room_code,
                    tg_id=user.id,
                )
                if not state:
                    await query.answer("Battle state not found.", show_alert=True)
                    return

                if state["status"] != "active":
                    await query.answer("This battle is no longer active.", show_alert=True)
                    return

                if state.get("ends_at") is not None:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    ends_at = state["ends_at"]
                    if ends_at.tzinfo is None:
                        ends_at = ends_at.replace(tzinfo=timezone.utc)

                    if now >= ends_at:
                        await query.answer("⏳ Time is up for this battle.", show_alert=True)
                        return

                already_answered = await has_player_answered_question(
                    session,
                    battle_id=str(state["battle_id"]),
                    tg_id=user.id,
                    question_id=question_id,
                )
                if already_answered:
                    await query.answer("You already handled this question.", show_alert=False)
                    return

                current = await get_current_battle_question_for_player(
                    session,
                    room_code=room_code,
                    tg_id=user.id,
                )
                if not current or current["done"]:
                    await query.answer("No active question found.", show_alert=False)
                    return

                expected_question_id = int(current["question_id"])
                if expected_question_id != question_id:
                    await query.answer("This is not your current question.", show_alert=False)
                    return

                await record_battle_answer(
                    session,
                    battle_id=str(state["battle_id"]),
                    tg_id=user.id,
                    question_id=question_id,
                    question_index=int(current["question_index"]),
                    selected_option=None,
                    is_correct=False,
                    was_skipped=True,
                )

                player_finished = await mark_player_finished_if_done(
                    session,
                    battle_id=str(state["battle_id"]),
                    tg_id=user.id,
                    question_count=int(state["question_count"]),
                )

        await query.edit_message_text(
            "⏭️ <b>Question skipped.</b>",
            parse_mode="HTML",
            reply_markup=None,
        )

        await asyncio.sleep(1.0)

        if player_finished:
            await query.message.reply_text(
                "🏁 You have completed your battle questions.\n\nPlease wait for the final result.",
                parse_mode="HTML",
            )
            return

        await send_battle_question_to_player(
            context.bot,
            room_code,
            user.id,
            context=context,
        )

    except Exception:
        logger.exception(
            "❌ Failed to process battle skip | room_code=%s | tg_id=%s | question_id=%s",
            room_code,
            user.id,
            question_id,
        )
        await query.answer("Could not skip question right now.", show_alert=True)

# ===========================================================
# Battle Next Question Handler
# ===========================================================
async def battle_next_question_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    await query.answer()

    data = query.data or ""
    parts = data.split(":")

    if len(parts) != 2:
        await query.answer("Invalid next request.", show_alert=False)
        return

    _, room_code = parts

    try:
        await query.edit_message_text(
            "⏳ Loading next question...",
            parse_mode="Markdown",
        )
    except Exception:
        pass

    await send_battle_question_to_player(context.bot, room_code, user.id)


# ============================================================
# Cancel Battle Room Handler
# ============================================================
async def battle_cancel_room_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    await query.answer()

    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3:
        await query.answer("Invalid cancel request.", show_alert=False)
        return

    room_code = parts[2].strip().upper()

    logger.info("❌ battle_cancel_room_handler hit | room_code=%s | tg_id=%s", room_code, user.id)

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await cancel_battle_room(
                    session,
                    room_code=room_code,
                    requester_tg_id=user.id,
                )

            if not result["ok"]:
                await query.answer(result["error"], show_alert=True)
                return

        await query.edit_message_text(
            "❌ *Battle cancelled.*\n\n"
            "This room has been closed by the host.",
            parse_mode="Markdown",
            reply_markup=battle_mode_keyboard(),
        )

    except Exception:
        logger.exception("❌ Failed to cancel battle | room_code=%s | host_tg_id=%s", room_code, user.id)
        await query.answer("Could not cancel battle right now.", show_alert=True)


# ============================================================
# Register handlers
# ============================================================
def register_handlers(application):
    application.add_handler(
        CommandHandler("battle", battle_mode_entry_handler),
        group=-3,
    )
    application.add_handler(
        CallbackQueryHandler(battle_mode_entry_handler, pattern=r"^battle:menu$"),
        group=-3,
    )

    application.add_handler(
        CallbackQueryHandler(battle_create_start_handler, pattern=r"^battle:create$"),
        group=-3,
    )
    application.add_handler(
        CallbackQueryHandler(battle_category_handler, pattern=r"^battlecat:"),
        group=-3,
    )
    application.add_handler(
        CallbackQueryHandler(battle_question_count_handler, pattern=r"^battleq:"),
        group=-3,
    )
    application.add_handler(
        CallbackQueryHandler(battle_duration_handler, pattern=r"^battlet:"),
        group=-3,
    )
    application.add_handler(
        CallbackQueryHandler(battle_max_players_handler, pattern=r"^battlep:"),
        group=-3,
    )

    battle_join_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(battle_join_code_handler, pattern=r"^battle:join_code$")
        ],
        states={
            BATTLE_JOIN_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, battle_receive_room_code_handler)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", battle_cancel_handler),
            CallbackQueryHandler(battle_cancel_handler, pattern=r"^battle:cancel$"),
        ],
        allow_reentry=True,
        per_user=True,
        per_chat=True,
    )

    application.add_handler(battle_join_conv, group=-3)

    application.add_handler(
        CallbackQueryHandler(battle_start_handler, pattern=r"^battle:start:"),
        group=-3,
    )
    application.add_handler(
        CallbackQueryHandler(battle_cancel_room_handler, pattern=r"^battle:cancel_room:"),
        group=-3,
    )
    application.add_handler(
        CallbackQueryHandler(battle_answer_handler, pattern=r"^battleans:"),
        group=-3,
    )
    application.add_handler(
        CallbackQueryHandler(battle_skip_handler, pattern=r"^battleskip:"),
        group=-3,
    )
    application.add_handler(
        CallbackQueryHandler(battle_next_question_handler, pattern=r"^battlenext:"),
        group=-3,
    )


