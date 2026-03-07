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
        "🔥 <b>Can you beat my score</b>\n" 
        "<b>on NaijaPrizeGate?</b>\n\n"
        "Join my trivia challenge and let's see who is smarter 🧠\n\n"
    )

    encoded_text = quote(share_text)
    encoded_link = quote(invite_link)

    share_url = f"https://t.me/share/url?url={encoded_link}&text={encoded_text}"

    keyboard = [
        [
            InlineKeyboardButton(
                "📨 Share Challenge",
                url=share_url
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

    await update.message.reply_text(

        "⚔️ *Friend Challenge Joined!*\n\n"
        "You have joined a trivia challenge.\n\n"
        "Both players will answer the same 5 questions.\n\n"
        "Press *Play Trivia Questions* to begin!",

        parse_mode="Markdown",
    )

    return True


# ==========================================================
# Show Challenge Result
# ==========================================================

async def show_challenge_result(update: Update, context: ContextTypes.DEFAULT_TYPE, challenge_id):

    async with AsyncSessionLocal() as session:

        result = await session.execute(
            text("""
                SELECT user_id, score
                FROM challenge_players
                WHERE challenge_id = :challenge_id
                ORDER BY score DESC
            """),
            {"challenge_id": challenge_id},
        )

        rows = result.fetchall()

    if not rows:
        return

    result_lines = []
    winner_score = rows[0].score

    for r in rows:
        result_lines.append(f"{r.user_id} — {r.score}/5")

    winner = rows[0].user_id

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
        "📞 Instant <b>Airtime</b> Rewards for Premium Points Milestones"
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

    # /challenge command
    application.add_handler(
        CommandHandler("challenge", create_challenge)
    )

    # Challenge button
    application.add_handler(
        CallbackQueryHandler(
            create_challenge,
            pattern="^challenge:start$",
        )
    )

    # Text fallback
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex("^⚔️ Challenge Friends$"),
            create_challenge,
        )
    )
