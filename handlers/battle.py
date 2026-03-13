# =====================================================================
# handlers/battle.py
# =====================================================================
from __future__ import annotations

import os

from telegram.error import BadRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
)

# ============================================================
# Conversation states
# ============================================================
BATTLE_CATEGORY = 3001
BATTLE_QUESTION_COUNT = 3002
BATTLE_DURATION = 3003
BATTLE_MAX_PLAYERS = 3004
BATTLE_JOIN_CODE = 3005


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
        [InlineKeyboardButton("🎬 Nigeria Entertainment", callback_data="battlecat:nigeria_entertainment")],
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
    bot_username = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot")
    invite_link = f"https://t.me/{bot_username}?start=battle_{room_code}"

    buttons = [
        [InlineKeyboardButton("📨 Invite Friends", url=invite_link)],
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
# Silent host lobby refresh
# ============================================================
async def refresh_host_lobby(bot, room_code: str):
    bot_username = os.getenv("BOT_USERNAME", "YourBotUsername")

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
    if not query:
        return ConversationHandler.END

    await query.answer()

    context.user_data.pop("battle_create_category", None)
    context.user_data.pop("battle_create_question_count", None)
    context.user_data.pop("battle_create_duration", None)
    context.user_data.pop("battle_create_max_players", None)

    await query.edit_message_text(
        "🔥 *Create Battle Room*\n\n"
        "First, choose a category:",
        parse_mode="Markdown",
        reply_markup=battle_category_keyboard(),
    )
    return BATTLE_CATEGORY


# ============================================================
# Category selected
# ============================================================
async def battle_category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    await query.answer()

    data = query.data or ""
    if not data.startswith("battlecat:"):
        return BATTLE_CATEGORY

    category = data.split(":", 1)[1].strip()
    context.user_data["battle_create_category"] = category

    pretty_category = category.replace("_", " ").title()

    await query.edit_message_text(
        f"✅ Category selected: *{pretty_category}*\n\n"
        "Now choose how many questions the battle should have:",
        parse_mode="Markdown",
        reply_markup=battle_question_count_keyboard(),
    )
    return BATTLE_QUESTION_COUNT


# ============================================================
# Question count selected
# ============================================================
async def battle_question_count_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    await query.answer()

    data = query.data or ""
    if not data.startswith("battleq:"):
        return BATTLE_QUESTION_COUNT

    try:
        question_count = int(data.split(":", 1)[1].strip())
    except Exception:
        await query.answer("Invalid question count.", show_alert=False)
        return BATTLE_QUESTION_COUNT

    context.user_data["battle_create_question_count"] = question_count

    await query.edit_message_text(
        f"✅ Questions selected: *{question_count}*\n\n"
        "Now choose the battle timer:",
        parse_mode="Markdown",
        reply_markup=battle_duration_keyboard(),
    )
    return BATTLE_DURATION


# ============================================================
# Duration selected
# ============================================================
async def battle_duration_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    await query.answer()

    data = query.data or ""
    if not data.startswith("battlet:"):
        return BATTLE_DURATION

    try:
        duration_seconds = int(data.split(":", 1)[1].strip())
    except Exception:
        await query.answer("Invalid duration.", show_alert=False)
        return BATTLE_DURATION

    context.user_data["battle_create_duration"] = duration_seconds

    await query.edit_message_text(
        f"✅ Timer selected: *{duration_seconds} seconds*\n\n"
        "Now choose the maximum number of players\n"
        "*(including you, the host)*:",
        parse_mode="Markdown",
        reply_markup=battle_max_players_keyboard(),
    )
    return BATTLE_MAX_PLAYERS


# ============================================================
# Max players selected -> Create room
# ============================================================
async def battle_max_players_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return ConversationHandler.END

    await query.answer()

    data = query.data or ""
    if not data.startswith("battlep:"):
        return BATTLE_MAX_PLAYERS

    try:
        max_players = int(data.split(":", 1)[1].strip())
    except Exception:
        await query.answer("Invalid player count.", show_alert=False)
        return BATTLE_MAX_PLAYERS

    category = context.user_data.get("battle_create_category")
    question_count = context.user_data.get("battle_create_question_count")
    duration_seconds = context.user_data.get("battle_create_duration")

    if not category or not question_count or not duration_seconds:
        await query.edit_message_text(
            "⚠️ Battle setup expired.\n\nPlease start again.",
            parse_mode="Markdown",
            reply_markup=battle_mode_keyboard(),
        )
        return ConversationHandler.END

    display_name = user.full_name or user.username or str(user.id)
    bot_username = os.getenv("BOT_USERNAME", "YourBotUsername")

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                room = await create_battle_room(
                    session,
                    host_tg_id=user.id,
                    host_display_name=display_name,
                    category=category,
                    max_players=max_players,
                    question_count=question_count,
                    duration_seconds=duration_seconds,
                )

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

    except Exception:
        logger.exception("❌ Failed to create battle room | host_tg_id=%s", user.id)
        await query.edit_message_text(
            "❌ Could not create battle room right now.\n\nPlease try again.",
            parse_mode="Markdown",
            reply_markup=battle_mode_keyboard(),
        )
        return ConversationHandler.END

    context.user_data.pop("battle_create_category", None)
    context.user_data.pop("battle_create_question_count", None)
    context.user_data.pop("battle_create_duration", None)
    context.user_data.pop("battle_create_max_players", None)

    return ConversationHandler.END


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
    bot_username = os.getenv("BOT_USERNAME", "YourBotUsername")

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
            players = await get_battle_players(session, str(room["id"]))

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

    context.user_data.pop("battle_create_category", None)
    context.user_data.pop("battle_create_question_count", None)
    context.user_data.pop("battle_create_duration", None)
    context.user_data.pop("battle_create_max_players", None)

    return ConversationHandler.END


# ===========================================================
# Send Battle Question To Player
# ===========================================================
async def send_battle_question_to_player(bot, room_code: str, tg_id: int):
    async with AsyncSessionLocal() as session:
        current = await get_current_battle_question_for_player(
            session,
            room_code=room_code,
            tg_id=tg_id,
        )
        if not current:
            return

        state = current["state"]

        # Stop if room is no longer active
        if state.get("status") != "active":
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text="⏳ This battle has ended. Please wait for the final result.",
                    parse_mode="Markdown",
                )
            except Exception:
                logger.exception(
                    "❌ Failed to send inactive-battle notice | room_code=%s | tg_id=%s",
                    room_code,
                    tg_id,
                )
            return

        # Stop if battle time has expired
        if state.get("ends_at") is not None:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            ends_at = state["ends_at"]
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=timezone.utc)

            if now >= ends_at:
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text="⏳ Time is up for this battle. Please wait for the final result.",
                        parse_mode="Markdown",
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
                        "✅ *You have finished all questions.*\n\n"
                        "Please wait for the final battle result."
                    ),
                    parse_mode="Markdown",
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

        if isinstance(options, str):
            import json
            options = json.loads(options)

        option_a = options.get("A", "N/A")
        option_b = options.get("B", "N/A")
        option_c = options.get("C", "N/A")
        option_d = options.get("D", "N/A")

        try:
            await bot.send_message(
                chat_id=tg_id,
                text=(
                    "🔥 *Battle Mode*\n\n"
                    f"*Question {question_index + 1}/{state['question_count']}*\n"
                    f"⏳ *Time Limit:* {state['duration_seconds']} seconds\n\n"
                    f"{q['question']}\n\n"
                    f"A. {option_a}\n"
                    f"B. {option_b}\n"
                    f"C. {option_c}\n"
                    f"D. {option_d}"
                ),
                parse_mode="Markdown",
                reply_markup=battle_question_keyboard(room_code, question_id),
            )
        except Exception:
            logger.exception(
                "❌ Failed to send battle question | room_code=%s | tg_id=%s",
                room_code,
                tg_id,
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

        if player_finished:
            if is_correct:
                await query.edit_message_text(
                    "✅ *Correct!*\n\n"
                    "🎉 You have finished all your questions.\n"
                    "Please wait for the final battle result.",
                    parse_mode="Markdown",
                )
            else:
                await query.edit_message_text(
                    "❌ *Incorrect.*\n\n"
                    "✅ You have finished all your questions.\n"
                    "Please wait for the final battle result.",
                    parse_mode="Markdown",
                )
            return

        if is_correct:
            await query.edit_message_text(
                "✅ *Correct!*\n\n"
                "Tap below to continue.",
                parse_mode="Markdown",
                reply_markup=battle_next_keyboard(room_code),
            )
        else:
            await query.edit_message_text(
                "❌ *Incorrect.*\n\n"
                "Tap below to continue.",
                parse_mode="Markdown",
                reply_markup=battle_next_keyboard(room_code),
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

        if player_finished:
            await query.edit_message_text(
                "⏭ *Question skipped.*\n\n"
                "✅ You have finished all your questions.\n"
                "Please wait for the final battle result.",
                parse_mode="Markdown",
            )
            return

        await query.edit_message_text(
            "⏭ *Question skipped.*\n\n"
            "Tap below to continue.",
            parse_mode="Markdown",
            reply_markup=battle_next_keyboard(room_code),
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
# Cancel-room placeholder
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
    battle_conv = ConversationHandler(
        entry_points=[
            CommandHandler("battle", battle_mode_entry_handler),
            CallbackQueryHandler(battle_mode_entry_handler, pattern=r"^battle:menu$"),
            CallbackQueryHandler(battle_create_start_handler, pattern=r"^battle:create$"),
            CallbackQueryHandler(battle_join_code_handler, pattern=r"^battle:join_code$"),
        ],
        states={
            BATTLE_CATEGORY: [
                CallbackQueryHandler(battle_category_handler, pattern=r"^battlecat:")
            ],
            BATTLE_QUESTION_COUNT: [
                CallbackQueryHandler(battle_question_count_handler, pattern=r"^battleq:")
            ],
            BATTLE_DURATION: [
                CallbackQueryHandler(battle_duration_handler, pattern=r"^battlet:")
            ],
            BATTLE_MAX_PLAYERS: [
                CallbackQueryHandler(battle_max_players_handler, pattern=r"^battlep:")
            ],
            BATTLE_JOIN_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, battle_receive_room_code_handler)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(battle_cancel_handler, pattern=r"^battle:cancel$"),
            CommandHandler("cancel", battle_cancel_handler),
        ],
        allow_reentry=True,
        per_user=True,
        per_chat=True,
    )

    application.add_handler(battle_conv, group=-3)

    application.add_handler(
        CallbackQueryHandler(battle_start_handler, pattern=r"^battle:start:"), group=-3
    )
    application.add_handler(
        CallbackQueryHandler(battle_cancel_room_handler, pattern=r"^battle:cancel_room:"), group=-3
    )

    application.add_handler(
        CallbackQueryHandler(battle_answer_handler, pattern=r"^battleans:"), group=-3
    )
    application.add_handler(
        CallbackQueryHandler(battle_skip_handler, pattern=r"^battleskip:"), group=-3
    )
    application.add_handler(
        CallbackQueryHandler(battle_next_question_handler, pattern=r"^battlenext:"), group=-3
    )    
