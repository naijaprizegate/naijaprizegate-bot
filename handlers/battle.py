# =====================================================================
# handlers/battle.py
# =====================================================================
from __future__ import annotations

import os

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
        [InlineKeyboardButton("⚽ Sports", callback_data="battlecat:Sports")],
        [InlineKeyboardButton("🎬 Entertainment", callback_data="battlecat:Entertainment")],
        [InlineKeyboardButton("🌍 Current Affairs", callback_data="battlecat:Current Affairs")],
        [InlineKeyboardButton("🔬 Science", callback_data="battlecat:Science")],
        [InlineKeyboardButton("❌ Cancel", callback_data="battle:cancel")],
    ])


def battle_question_count_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("5 Questions", callback_data="battleq:5")],
        [InlineKeyboardButton("10 Questions", callback_data="battleq:10")],
        [InlineKeyboardButton("15 Questions", callback_data="battleq:15")],
        [InlineKeyboardButton("❌ Cancel", callback_data="battle:cancel")],
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
    buttons = []

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
                parse_mode="Markdown",
                reply_markup=battle_lobby_keyboard(room_code, is_host=True),
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("❌ Failed to silently refresh host lobby | room_code=%s", room_code)


# ============================================================
# Entry point
# ============================================================
async def battle_mode_entry_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    await query.edit_message_text(
        f"✅ Category selected: *{category}*\n\n"
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

        sent = await query.edit_message_text(
            lobby_text,
            parse_mode="Markdown",
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

        await msg.reply_text(
            "✅ You joined the battle room.\n\n"
            f"*Room Code:* `{room['room_code']}`\n"
            f"*Category:* {room['category']}\n"
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

        await msg.reply_text(
            "✅ You joined the battle room.\n\n"
            f"*Room Code:* `{room['room_code']}`\n"
            f"*Category:* {room['category']}\n"
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


# ============================================================
# Start-battle placeholder
# ============================================================
async def battle_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer("Battle start is the next step we will build.", show_alert=False)


# ============================================================
# Cancel-room placeholder
# ============================================================
async def battle_cancel_room_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer("Battle room cancellation will be wired next.", show_alert=False)


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

    application.add_handler(battle_conv, group=1)

    application.add_handler(
        CallbackQueryHandler(battle_start_handler, pattern=r"^battle:start:")
    )
    application.add_handler(
        CallbackQueryHandler(battle_cancel_room_handler, pattern=r"^battle:cancel_room:")
    )
