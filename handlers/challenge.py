# ==========================================================
# handlers/challenge.py
# ==========================================================

import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from sqlalchemy import text
from urllib.parse import quote
from db import AsyncSessionLocal


# ==========================================================
# CONFIG
# ==========================================================

TOTAL_QUESTIONS = 700


# ==========================================================
# Create Challenge
# ==========================================================

async def create_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    query = update.callback_query
    message = update.message or (query.message if query else None)

    if query:
        await query.answer()

    if not message:
        return

    # ----------------------------------------------
    # Pick random question slice
    # ----------------------------------------------

    start_q = random.randint(1, TOTAL_QUESTIONS - 5)
    end_q = start_q + 4

    try:

        async with AsyncSessionLocal() as session:

            result = await session.execute(
                text("""
                    INSERT INTO challenges
                    (creator_id, question_start, question_end, status)
                    VALUES (:creator_id, :start_q, :end_q, 'active')
                    RETURNING id
                """),
                {
                    "creator_id": int(user.id),
                    "start_q": start_q,
                    "end_q": end_q,
                },
            )

            challenge_id = result.scalar()

            await session.commit()

    except Exception:

        await message.reply_text(
            "❌ Could not create challenge. Please try again."
        )
        return

    # ----------------------------------------------
    # Generate invite link
    # ----------------------------------------------

    bot_username = context.bot.username

    invite_link = f"https://t.me/{bot_username}?start=challenge_{challenge_id}"

    # ----------------------------------------------
    # Viral share text
    # ----------------------------------------------

    share_text = (
        "🔥 Can you beat my score on NaijaPrizeGate?\n\n"
        "Join my trivia challenge and let's see who is smarter 🧠\n\n"
    )

    encoded_link = quote(invite_link)
    encoded_text = quote(share_text)

    keyboard = [
        [
            InlineKeyboardButton(
                "📨 Share Challenge",
                url=f"https://t.me/share/url?url={encoded_link}&text={encoded_text}",
            )
        ]
    ]

    markup = InlineKeyboardMarkup(keyboard)

    # ----------------------------------------------
    # Send invite message
    # ----------------------------------------------

    await message.reply_text(

        f"⚔️ <b>Friend Challenge Created!</b>\n\n"
        f"Invite your friends to compete with you.\n\n"
        f"<b>Challenge Questions:</b> 5\n\n"
        f"Share this link with friends:\n\n"
        f"{invite_link}",

        parse_mode="HTML",
        reply_markup=markup,
    )


# ==========================================================
# Join Challenge
# ==========================================================

async def join_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        return False

    arg = context.args[0]

    if not arg.startswith("challenge_"):
        return False

    challenge_id = int(arg.split("_")[1])
    user = update.effective_user

    async with AsyncSessionLocal() as session:

        await session.execute(
            text("""
                INSERT INTO challenge_players (challenge_id, user_id, score, completed)
                VALUES (:challenge_id, :user_id, 0, false)
                ON CONFLICT DO NOTHING
            """),
            {
                "challenge_id": challenge_id,
                "user_id": user.id,
            },
        )

        await session.commit()

    await show_challenge_lobby(update, context, challenge_id)

    return True


# ==========================================================
# Show Challenge Lobby
# ==========================================================

async def show_challenge_lobby(update, context, challenge_id):

    async with AsyncSessionLocal() as session:

        result = await session.execute(
            text("""
                SELECT u.username, u.full_name
                FROM challenge_players cp
                JOIN users u ON u.tg_id = cp.user_id
                WHERE cp.challenge_id = :cid
            """),
            {"cid": challenge_id},
        )

        players = result.fetchall()

    player_lines = []

    for p in players:

        name = p.username if p.username else p.full_name
        player_lines.append(f"• {name}")

    player_text = "\n".join(player_lines)

    keyboard = [
        [
            InlineKeyboardButton(
                "▶ Start Challenge",
                callback_data=f"challenge_start_{challenge_id}",
            )
        ]
    ]

    await update.effective_chat.send_message(

        f"⚔️ <b>FRIEND CHALLENGE</b>\n\n"
        f"<b>Players Joined:</b>\n"
        f"{player_text}\n\n"
        f"<b>Questions:</b> 5",

        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ==========================================================
# Start Challenge
# ==========================================================

async def start_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    challenge_id = int(query.data.split("_")[2])
    user = query.from_user

    async with AsyncSessionLocal() as session:

        result = await session.execute(
            text("""
                SELECT creator_id
                FROM challenges
                WHERE id = :cid
            """),
            {"cid": challenge_id},
        )

        row = result.fetchone()

    if not row or row.creator_id != user.id:

        await query.answer(
            "Only the challenge creator can start the game.",
            show_alert=True
        )

        return

    await query.message.edit_text(

        "🧠 <b>Challenge Started!</b>\n\n"
        "You will now receive 5 trivia questions.\n"
        "Answer them as fast as possible!",

        parse_mode="HTML",
    )

    # Placeholder trigger for trivia engine
    await query.message.reply_text("▶ Starting questions...")


# ==========================================================
# Show Challenge Result
# ==========================================================

async def show_challenge_result(update: Update, context: ContextTypes.DEFAULT_TYPE, challenge_id):

    async with AsyncSessionLocal() as session:

        result = await session.execute(
            text("""
                SELECT u.username, u.full_name, cp.score
                FROM challenge_players cp
                JOIN users u ON u.tg_id = cp.user_id
                WHERE cp.challenge_id = :challenge_id
                ORDER BY cp.score DESC
            """),
            {"challenge_id": challenge_id},
        )

        rows = result.fetchall()

    if not rows:
        return

    result_lines = []

    for r in rows:

        name = r.username if r.username else r.full_name
        result_lines.append(f"{name} — {r.score}/5")

    winner = rows[0].username if rows[0].username else rows[0].full_name

    message = (
        "⚔️ <b>CHALLENGE RESULT</b>\n\n"
        + "\n".join(result_lines)
        + f"\n\n🏆 Winner: <b>{winner}</b>\n\n"
        "🔥 Want to climb the global leaderboard?\n\n"
        "Play Trivia to compete for:\n\n"
        "📱 <b>iPhone 17 Pro Max</b>\n"
        "📱 <b>Samsung Z Flip</b>\n"
        "🎧 <b>AirPods</b>\n"
        "🔊 <b>Bluetooth Speakers</b>\n"
        "📞 Instant <b>Airtime Rewards</b> for Premium Points Milestones"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🧠 Play Trivia Questions",
                callback_data="playtrivia",
            )
        ]
    ]

    await update.effective_chat.send_message(
        message,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ==========================================================
# Register Handlers
# ==========================================================

def register_handlers(application):

    application.add_handler(
        CommandHandler("challenge", create_challenge)
    )

    application.add_handler(
        CallbackQueryHandler(
            create_challenge,
            pattern="^challenge:start$",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex("^⚔️ Challenge Friends$"),
            create_challenge,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            start_challenge,
            pattern="^challenge_start_",
        )
    )
